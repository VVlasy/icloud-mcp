"""Attachments the server downloads itself (attachment_urls on mail_send_message, mail_reply_to_message and mail_update_draft), so
a file another tool links to (an Alza invoice: alza-mcp's order_document.href) reaches the message without its bytes passing
through the model.

Off unless ATTACHMENT_URL_ALLOWLIST lists URL prefixes. Every URL, and every redirect hop, must:
- be https, carry no user name or password, and sit on the host and port of an allowlist entry, under its path in whole
  segments (/Apps/pdfdoc.asp matches /Apps/pdfdoc.asp?d=1, never /Apps/pdfdoc.aspx). The check is made on the URL as it is
  sent, after dot segments are resolved ("/Apps/pdfdoc.asp/../x" is "/x"); encoded dots and slashes and backslashes are refused.
- resolve to public addresses only, and the connection goes to the address that was checked (netguard.PinnedTransport).
Redirects are followed by hand, at most MAX_REDIRECTS. The body is read as it arrives and abandoned past ATTACHMENT_URL_MAX_BYTES
(and past MAX_TOTAL_BYTES for all the files of a call); its type must be one of ATTACHMENT_URL_CONTENT_TYPES, and a PDF must start
with %PDF-. All the URLs of one call share TIMEOUT_SECONDS, well inside the tool time limit, so a message is never sent after the
agent was told the call failed. If any URL fails nothing is returned, and the error names that URL and why.
Logs and errors name a URL by scheme, host and path only: query strings carry access keys (Alza's x=).
"""
from __future__ import annotations

import email.message
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
_MAGIC = {"application/pdf": b"%PDF-"}
_EXTENSION = {"application/pdf": ".pdf"}
_UNSAFE_PATH = re.compile(r"%2e|%2f|%5c|\\", re.I)    # encoded dots or slashes, backslashes: a server may read them as separators
_UA = "Mozilla/5.0 (compatible; icloud-mcp attachment fetch)"


class AttachmentUrlError(ValueError):
    """A URL that cannot be attached; the message says which one and why."""


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
            magic = _MAGIC.get(ctype)
            if magic and not body.startswith(magic):
                raise _Fail(f"the body is not a real {ctype} file (it does not start with {magic.decode()})")
            name = _filename(given, r.headers.get("content-disposition", ""), url, ctype)
            return {"filename": name, "content_type": ctype, "content": body, "hops": hop}
    raise _Fail(f"more than {MAX_REDIRECTS} redirects")


def fetch_all(items: list[dict[str, Any]], *, allowlist: tuple[Prefix, ...], max_bytes: int, content_types: tuple[str, ...],
              transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """Download every {url, filename?} in order: [{filename, content_type, content}]. All or nothing: the first URL that fails
    raises AttachmentUrlError naming it (its position, scheme, host and path) and why, and nothing is returned."""
    if not items:
        return []
    if not allowlist:
        raise AttachmentUrlError("attachment_urls is off on this server (ATTACHMENT_URL_ALLOWLIST is empty). Pass the file in "
                                 "'attachments' instead. Nothing was sent or saved.")
    if len(items) > MAX_URLS:
        raise AttachmentUrlError(f"attachment_urls takes at most {MAX_URLS} URLs, not {len(items)}. Nothing was sent or saved.")
    urls = []
    for i, item in enumerate(items):                      # every URL is checked before any is downloaded
        try:
            url = httpx.URL(str(item.get("url") or "").strip())
        except httpx.InvalidURL:
            raise AttachmentUrlError(f"attachment_urls[{i}] is not a valid URL. Nothing was sent or saved.") from None
        if not url.raw_host or not allowed(url, allowlist):
            label = f" ({describe(url)})" if url.raw_host else ""
            raise AttachmentUrlError(f"attachment_urls[{i}]{label} is not on ATTACHMENT_URL_ALLOWLIST: only https URLs under "
                                     f"{', '.join(_prefix_text(p) for p in allowlist)} are fetched. Nothing was sent or saved.")
        urls.append(url)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    out, total = [], 0
    # trust_env=False: a proxy from the environment would make its own connection and skip the pinned, checked address.
    with httpx.Client(follow_redirects=False, trust_env=False, transport=transport if transport is not None else PinnedTransport(),
                      headers={"User-Agent": _UA, "Accept": ", ".join(content_types), "Accept-Encoding": "identity"}) as client:
        for i, (url, item) in enumerate(zip(urls, items)):
            label = f"attachment_urls[{i}] ({describe(url)})"
            try:
                got = _fetch_one(client, url, given=item.get("filename"), prefixes=allowlist, max_bytes=max_bytes,
                                 budget=MAX_TOTAL_BYTES - total, content_types=content_types, deadline=deadline)
            except _Fail as e:
                why = str(e)
            except PermissionError as e:
                why = f"refused: {e}"
            except (TimeoutError, httpx.TimeoutException):
                why = f"the downloads took longer than {TIMEOUT_SECONDS:g} seconds"
            except httpx.HTTPError as e:
                why = f"it could not be downloaded ({type(e).__name__})"
            else:
                total += len(got["content"])
                log.info("attachment_urls: fetched %s, %d bytes, %d redirect(s)", describe(url), len(got["content"]), got["hops"])
                out.append({k: got[k] for k in ("filename", "content_type", "content")})
                continue
            log.warning("attachment_urls: %s failed: %s", describe(url), why)
            raise AttachmentUrlError(f"{label} could not be attached: {why}. Nothing was sent or saved.")
    return out


def _prefix_text(p: Prefix) -> str:
    return f"https://{p.host}{'' if p.port == 443 else f':{p.port}'}{p.path}"
