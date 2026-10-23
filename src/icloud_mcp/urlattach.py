"""Attachments the server takes in itself (attachment_urls on mail_send_message, mail_reply_to_message and mail_update_draft), so a
file reaches the message without its bytes passing through the model: one another tool links to (an Alza invoice: alza-mcp's
order_document.href), downloaded here, or one the agent generated and sent to an upload URL of this server (uploads.py), taken
from that store.

Downloads are off unless ATTACHMENT_URL_ALLOWLIST lists URL prefixes. Every URL, and every redirect hop, must:
- be https, carry no user name or password, and sit on the host and port of an allowlist entry, under its path in whole
  segments (/Apps/pdfdoc.asp matches /Apps/pdfdoc.asp?d=1, never /Apps/pdfdoc.aspx). The check is made on the URL as it is
  sent, after dot segments are resolved ("/Apps/pdfdoc.asp/../x" is "/x"); encoded dots and slashes and backslashes are refused.
- resolve to public addresses only, and the connection goes to the address that was checked (netguard.PinnedTransport).
Redirects are followed by hand, at most MAX_REDIRECTS. The body is read as it arrives and abandoned past ATTACHMENT_URL_MAX_BYTES
(and past MAX_TOTAL_BYTES for all the files of a call); its type must be one of ATTACHMENT_URL_CONTENT_TYPES, and a type with a
known signature (PDF, Office and OpenDocument files, zip, images) must start with it. A sha256 given with a URL must be the file's.
All the URLs of one call share TIMEOUT_SECONDS, well inside the tool time limit, so a message is never sent after the agent was
told the call failed. If any URL fails nothing is returned, and the error names that URL and why.
Logs and errors name a URL by scheme, host and path only: query strings carry access keys (Alza's x=). An upload URL is not
named at all: its token is its only credential.
"""
from __future__ import annotations

import email.message
import hashlib
import logging
import mimetypes
import re
import time
import unicodedata
import zlib
from dataclasses import dataclass
from typing import Any

import httpx

from .netguard import PinnedTransport

log = logging.getLogger("icloud_mcp.urlattach")
for _noisy in ("httpx", "httpcore"):                 # httpx logs every request URL, query string and all, at INFO
    logging.getLogger(_noisy).setLevel(logging.WARNING)

MAX_URLS = 20
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 20.0                       # every URL of one call together, redirects included
MAX_TOTAL_BYTES = 20 * 1024 * 1024           # iCloud Mail sends no message over 20 MB, so more could never go out
MAX_FILENAME_CHARS = 120
_UNSAFE_PATH = re.compile(r"%2e|%2f|%5c|\\", re.I)    # encoded dots or slashes, backslashes: a server may read them as separators
_UA = "Mozilla/5.0 (compatible; icloud-mcp attachment fetch)"
_SHA256 = re.compile(r"[0-9a-f]{64}")

_OOXML = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",       # .docx
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",             # .xlsx
          "application/vnd.openxmlformats-officedocument.presentationml.presentation")     # .pptx
_ODF = ("application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation")
_OLE = ("application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint")
# What a file of a type starts with, for the types that have a signature: a login page sent as application/pdf, or as a .docx,
# is refused. type -> (a pattern over the first bytes, how an error names it)
_MAGIC: dict[str, tuple[re.Pattern[bytes], str]] = {}
for _types, _pattern, _text in (
    (("application/pdf",), rb"%PDF-", "%PDF-"),
    (_OOXML + _ODF + ("application/zip",), rb"PK\x03\x04", "PK (a zip container)"),
    (_OLE, rb"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "the OLE2 header of a .doc, .xls or .ppt"),
    (("image/png",), rb"\x89PNG\r\n\x1a\n", "the PNG signature"),
    (("image/jpeg",), rb"\xff\xd8\xff", "the JPEG marker FF D8 FF"),
    (("image/gif",), rb"GIF8[79]a", "GIF87a or GIF89a"),
    (("image/webp",), rb"RIFF.{4}WEBP", "RIFF....WEBP"),
    (("image/tiff",), rb"II\*\x00|MM\x00\*", "a TIFF header"),
    (("image/bmp",), rb"BM", "BM"),
    (("application/gzip",), rb"\x1f\x8b", "the gzip header"),
):
    for _t in _types:
        _MAGIC[_t] = (re.compile(_pattern, re.S), _text)
# The extension a type's file gets when its name has none, and the type a name's extension means. mimetypes knows most of
# these too, but not on every system, and names .jpg differently from one Python version to the next.
_EXTENSION = {"application/pdf": ".pdf", _OOXML[0]: ".docx", _OOXML[1]: ".xlsx", _OOXML[2]: ".pptx",
              _ODF[0]: ".odt", _ODF[1]: ".ods", _ODF[2]: ".odp", _OLE[0]: ".doc", _OLE[1]: ".xls", _OLE[2]: ".ppt",
              "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/tiff": ".tiff",
              "image/bmp": ".bmp", "application/zip": ".zip", "application/gzip": ".gz",
              "text/plain": ".txt", "text/csv": ".csv", "text/markdown": ".md", "application/json": ".json"}
_TYPE_OF = {ext: ctype for ctype, ext in _EXTENSION.items()} | {".jpeg": "image/jpeg", ".tif": "image/tiff"}


class AttachmentUrlError(ValueError):
    """A URL that cannot be attached; the message says which one and why."""


class AttachmentSourceError(Exception):
    """Raised by a local source of files (uploads.UploadStore) when one of its own URLs has no usable file; the message says why."""


def wrong_signature(ctype: str, body: bytes) -> str | None:
    """Why `body` is not a real file of `ctype`, or None when it starts as such a file does (or the type has no known signature)."""
    sig = _MAGIC.get(ctype)
    if sig and not sig[0].match(body[:32]):
        return f"the body is not a real {ctype} file (it does not start with {sig[1]})"
    return None


def type_of(filename: str) -> str | None:
    """The media type a file name's extension means, or None when it has none or it is unknown."""
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem:
        return None
    return _TYPE_OF.get("." + ext.lower()) or mimetypes.guess_type(filename)[0]


def normalize_sha256(value: Any) -> str:
    """A sha256 as 64 lower-case hex characters (a 'sha256:' prefix and surrounding space dropped); ValueError for anything else."""
    text = str(value or "").strip().lower().removeprefix("sha256:").strip()
    if not _SHA256.fullmatch(text):
        raise ValueError("a sha256 is 64 hexadecimal characters, as sha256sum prints it")
    return text


@dataclass(frozen=True)
class Prefix:
    host: str          # lower-case ASCII (IDNA) name
    port: int
    path: str          # percent-encoded path, at least "/"


def parse_allowlist(entries: tuple[str, ...] | list[str]) -> tuple[Prefix, ...]:
    """The ATTACHMENT_URL_ALLOWLIST entries as prefixes. Raises ValueError naming an entry that is not an https URL prefix."""
    out = []
    for entry in entries:
        try:
            url = httpx.URL(entry)
        except httpx.InvalidURL as e:
            raise ValueError(f"'{entry}' is not a URL ({e})") from e
        if url.scheme != "https" or not url.raw_host:
            raise ValueError(f"'{entry}' must start with https:// and name a host")
        if url.userinfo or url.query or url.fragment:
            raise ValueError(f"'{entry}' must be a scheme, host and path only (no user name, query or fragment)")
        path = url.raw_path.decode("ascii") or "/"
        if _UNSAFE_PATH.search(path):
            raise ValueError(f"'{entry}' has an encoded dot, slash or a backslash in its path")
        out.append(Prefix(url.raw_host.decode("ascii").lower(), url.port or 443, path))
    return tuple(out)


def describe(url: httpx.URL) -> str:
    """scheme://host[:port]/path, without the query string (it can carry an access key) or the fragment."""
    port = f":{url.port}" if url.port else ""
    return f"{url.scheme}://{url.raw_host.decode('ascii')}{port}{url.raw_path.split(b'?', 1)[0].decode('ascii')}"


def allowed(url: httpx.URL, prefixes: tuple[Prefix, ...]) -> bool:
    """True when the URL, as it will be sent, is https and under one of the prefixes (same host and port, whole path segments)."""
    if url.scheme != "https" or url.userinfo:
        return False
    path = url.raw_path.split(b"?", 1)[0].decode("ascii") or "/"
    if _UNSAFE_PATH.search(path):
        return False
    host, port = url.raw_host.decode("ascii").lower(), url.port or 443
    for p in prefixes:
        if host == p.host and port == p.port:
            if path == p.path or path.startswith(p.path if p.path.endswith("/") else p.path + "/"):
                return True
    return False


def safe_filename(name: str | None) -> str:
    """The last component of name, without control or format characters (a right-to-left override can disguise an extension),
    spaces collapsed, no leading or trailing dots or spaces, at most MAX_FILENAME_CHARS with the extension kept. '' if nothing is left."""
    if not name:
        return ""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(" " if unicodedata.category(ch) in ("Zl", "Zp") else ch for ch in name
                   if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", name)).strip(" .")
    if len(name) > MAX_FILENAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        if dot and stem and 0 < len(ext) <= 10:
            name = stem[:MAX_FILENAME_CHARS - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            name = name[:MAX_FILENAME_CHARS].rstrip(" .")
    return name


def _disposition_name(header: str) -> str:
    """The filename in a Content-Disposition header (filename*= or filename=), or ''."""
    if not header:
        return ""
    m = email.message.Message()
    m["Content-Disposition"] = header
    try:
        return m.get_filename() or ""
    except Exception:  # noqa: BLE001 - a malformed header has no usable name
        return ""


def _filename(given: str | None, disposition: str, url: httpx.URL, ctype: str) -> str:
    """The name given, else the one the server sent, else the last path segment with the type's extension added."""
    for name in (given, _disposition_name(disposition)):
        if clean := safe_filename(name):
            return clean
    ext = _EXTENSION.get(ctype) or mimetypes.guess_extension(ctype) or ""
    segment = safe_filename(httpx.URL(url).path.rstrip("/").rsplit("/", 1)[-1]) or "attachment"
    if ext and not segment.lower().endswith(ext):
        segment = safe_filename(segment[:MAX_FILENAME_CHARS - len(ext)] + ext)
    return segment


class _Fail(Exception):
    pass


def _read(r: httpx.Response, limit: int, limit_text: str, deadline: float) -> bytes:
    """The body, abandoned as soon as it passes `limit` bytes (counted after decompression) or the deadline."""
    enc = r.headers.get("content-encoding", "").strip().lower()
    if r.is_stream_consumed:                       # built in memory (a test transport): httpx has unpacked it already
        chunks, unpack = iter([r.content]), None
    elif enc in ("gzip", "x-gzip", "deflate"):
        chunks, unpack = r.iter_raw(), zlib.decompressobj(zlib.MAX_WBITS | 32)   # a gzip or a zlib header, whichever
    elif enc in ("", "identity"):
        chunks, unpack = r.iter_raw(), None
    else:
        raise _Fail(f"the server sent the file compressed as {enc}, which is not read")
    buf = bytearray()
    try:
        for chunk in chunks:
            if unpack:
                buf += unpack.decompress(chunk, limit + 1 - len(buf))
                over = len(buf) > limit or bool(unpack.unconsumed_tail)
            else:
                buf += chunk
                over = len(buf) > limit
            if over:
                raise _Fail(f"the file is larger than {limit_text}")
            if time.monotonic() > deadline:
                raise TimeoutError
    except zlib.error as e:
        raise _Fail("the compressed file is damaged") from e
    return bytes(buf)


def _fetch_one(client: httpx.Client, url: httpx.URL, *, given: str | None, prefixes: tuple[Prefix, ...], max_bytes: int,
               budget: int, content_types: tuple[str, ...], deadline: float) -> dict[str, Any]:
    for hop in range(MAX_REDIRECTS + 1):
        if not allowed(url, prefixes):
            raise _Fail(f"it redirected to {describe(url)}, which is not on ATTACHMENT_URL_ALLOWLIST" if hop else
                        "it is not on ATTACHMENT_URL_ALLOWLIST (https only, under one of the listed prefixes)")
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        with client.stream("GET", url, timeout=left) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                if not r.headers.get("location"):
                    raise _Fail(f"the server answered HTTP {r.status_code} without saying where to")
                url = url.join(r.headers["location"])
                continue
            if not 200 <= r.status_code < 300:
                raise _Fail(f"the server answered HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if ctype not in content_types:
                raise _Fail(f"the server sent {ctype or 'no content type'}, not {' or '.join(content_types)} "
                            "(ATTACHMENT_URL_CONTENT_TYPES)")
            limit = min(max_bytes, budget)
            limit_text = (f"{limit} bytes (ATTACHMENT_URL_MAX_BYTES)" if limit == max_bytes else
                          f"the {limit} bytes left of the {MAX_TOTAL_BYTES}-byte limit for all the files of one message")
            declared = r.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise _Fail(f"the file is {declared} bytes, larger than {limit_text}")
            body = _read(r, limit, limit_text, deadline)
            if why := wrong_signature(ctype, body):
                raise _Fail(why)
            name = _filename(given, r.headers.get("content-disposition", ""), url, ctype)
            return {"filename": name, "content_type": ctype, "content": body, "hops": hop}
    raise _Fail(f"more than {MAX_REDIRECTS} redirects")


def _uploaded(uploads: Any, token: str, given: str | None, *, budget: int, content_types: tuple[str, ...]) -> dict[str, Any]:
    """A file from this server's own upload store, held to the same type and total-size limits as a downloaded one."""
    got = uploads.get(token)                       # AttachmentSourceError when the URL is unknown, expired or still empty
    if got.get("source", "upload") != "mail" and got["content_type"] not in content_types:   # a mailbox's own file keeps its type
        raise _Fail(f"its type {got['content_type']} is not on ATTACHMENT_URL_CONTENT_TYPES any more")
    if len(got["content"]) > budget:
        raise _Fail(f"the file is {len(got['content'])} bytes, more than the {budget} bytes left of the {MAX_TOTAL_BYTES}-byte limit "
                    "for all the files of one message")
    return {"filename": safe_filename(given) or got["filename"], "content_type": got["content_type"], "content": got["content"], "hops": 0}


def fetch_all(items: list[dict[str, Any]], *, allowlist: tuple[Prefix, ...], max_bytes: int, content_types: tuple[str, ...],
              transport: httpx.BaseTransport | None = None, uploads: Any = None) -> list[dict[str, Any]]:
    """Every {url, filename?, sha256?} in order, as [{filename, content_type, content}]: downloaded from an allowlisted site, or
    taken from `uploads` (uploads.UploadStore: match(url) names its own URLs, get(token) gives the file) when the URL is one of
    this server's own upload URLs. All or nothing: the first URL that fails raises AttachmentUrlError naming it (its position and,
    for a download, scheme, host and path) and why, and nothing is returned. A sha256 given with a URL must be the file's."""
    if not items:
        return []
    if len(items) > MAX_URLS:
        raise AttachmentUrlError(f"attachment_urls takes at most {MAX_URLS} URLs, not {len(items)}. Nothing was sent or saved.")
    own = " or an upload URL from mail_create_upload_url" if uploads is not None else ""
    plan: list[tuple[httpx.URL, str | None, str | None]] = []       # (url, upload token, the sha256 it must have)
    for i, item in enumerate(items):                      # every URL is checked before any is downloaded
        try:
            url = httpx.URL(str(item.get("url") or "").strip())
        except httpx.InvalidURL:
            raise AttachmentUrlError(f"attachment_urls[{i}] is not a valid URL. Nothing was sent or saved.") from None
        want = None
        if item.get("sha256"):
            try:
                want = normalize_sha256(item["sha256"])
            except ValueError as e:
                raise AttachmentUrlError(f"attachment_urls[{i}]: sha256: {e}. Nothing was sent or saved.") from None
        token = uploads.match(str(url)) if uploads is not None else None
        if token is None and not allowlist:
            raise AttachmentUrlError("attachment_urls downloads nothing from other sites on this server (ATTACHMENT_URL_ALLOWLIST is "
                                     "empty)" + (": pass an upload URL from mail_create_upload_url, or the file in 'attachments'."
                                                 if uploads is not None else ". Pass the file in 'attachments' instead.")
                                     + " Nothing was sent or saved.")
        if token is None and (not url.raw_host or not allowed(url, allowlist)):
            label = f" ({describe(url)})" if url.raw_host else ""
            raise AttachmentUrlError(f"attachment_urls[{i}]{label} is not on ATTACHMENT_URL_ALLOWLIST: only https URLs under "
                                     f"{', '.join(_prefix_text(p) for p in allowlist)}{own} are fetched. Nothing was sent or saved.")
        plan.append((url, token, want))
    deadline = time.monotonic() + TIMEOUT_SECONDS
    out, total = [], 0
    # trust_env=False: a proxy from the environment would make its own connection and skip the pinned, checked address.
    with httpx.Client(follow_redirects=False, trust_env=False, transport=transport if transport is not None else PinnedTransport(),
                      headers={"User-Agent": _UA, "Accept": ", ".join(content_types), "Accept-Encoding": "identity"}) as client:
        for i, ((url, token, want), item) in enumerate(zip(plan, items)):
            shown = "an upload URL of this server" if token else describe(url)     # an upload URL's token is its only credential
            label = f"attachment_urls[{i}] ({shown})"
            try:
                if token is not None:
                    got = _uploaded(uploads, token, item.get("filename"), budget=MAX_TOTAL_BYTES - total, content_types=content_types)
                else:
                    got = _fetch_one(client, url, given=item.get("filename"), prefixes=allowlist, max_bytes=max_bytes,
                                     budget=MAX_TOTAL_BYTES - total, content_types=content_types, deadline=deadline)
                if want is not None and (have := hashlib.sha256(got["content"]).hexdigest()) != want:
                    raise _Fail(f"its sha256 is {have}, not the {want} given, so it is not the file you meant")
            except (_Fail, AttachmentSourceError) as e:
                why = str(e)
            except PermissionError as e:
                why = f"refused: {e}"
            except (TimeoutError, httpx.TimeoutException):
                why = f"the downloads took longer than {TIMEOUT_SECONDS:g} seconds"
            except httpx.HTTPError as e:
                why = f"it could not be downloaded ({type(e).__name__})"
            else:
                total += len(got["content"])
                if token is not None:
                    log.info("attachment_urls: attached uploaded file %s, %d bytes", got["filename"], len(got["content"]))
                else:
                    log.info("attachment_urls: fetched %s, %d bytes, %d redirect(s)", shown, len(got["content"]), got["hops"])
                out.append({k: got[k] for k in ("filename", "content_type", "content")})
                continue
            log.warning("attachment_urls: %s failed: %s", shown, why)
            raise AttachmentUrlError(f"{label} could not be attached: {why}. Nothing was sent or saved.")
    return out


def _prefix_text(p: Prefix) -> str:
    return f"https://{p.host}{'' if p.port == 443 else f':{p.port}'}{p.path}"
