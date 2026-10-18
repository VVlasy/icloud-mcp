"""Files an agent uploads itself, so a document it generated reaches a message without its bytes passing through the model.

mail_create_upload_url mints a one-time https URL on this server (<MCP_PUBLIC_URL>/upload/<token>). The agent sends the file's
bytes to it from a shell (curl -T report.docx <url>) and then passes the same URL in attachment_urls, which the server answers
from this store (urlattach.fetch_all) instead of downloading anything. The token, 32 random bytes kept on disk only as a hash,
is the only credential an upload needs, and only a signed-in MCP client can mint one. A URL takes one file: of the type its
name says (or the one given), on ATTACHMENT_URL_CONTENT_TYPES, within ATTACHMENT_URL_MAX_BYTES, starting as such a file does
(urlattach.wrong_signature) and, when the agent gave the file's sha256 when minting, with that hash, so a mistyped or swapped
upload is refused before it can reach a message. Nothing is ever served back: the URL answers PUT and POST only. A URL and its
file expire after ATTACHMENT_UPLOAD_TTL_SECONDS; at most MAX_PENDING URLs are live at once and their files together stay under
MAX_STORE_BYTES, whatever a caller does.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import urlattach

log = logging.getLogger("icloud_mcp.uploads")

MAX_PENDING = 20                                   # upload URLs live at once (minted, not yet expired)
MAX_STORE_BYTES = 100 * 1024 * 1024                # all uploaded files together
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")          # what secrets.token_urlsafe(32) looks like
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class UploadError(urlattach.AttachmentSourceError):
    """Why an upload URL cannot be used, worded for the agent; `status` is the HTTP answer the upload endpoint gives."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    expected_sha256: str | None
    created_at: float
    expires_at: float
    size: int | None = None            # set once the file has arrived
    sha256: str | None = None

    @property
    def ready(self) -> bool:
        return self.size is not None


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).replace(microsecond=0).isoformat()


class UploadStore:
    """Upload URLs and the files sent to them, under DATA_DIR/uploads: <hash>.json (what was minted, and what arrived) and <hash>.bin."""

    def __init__(self, data_dir: str, public_url: str, *, ttl: int, max_bytes: int, content_types: tuple[str, ...]) -> None:
        self.dir = Path(data_dir) / "uploads"
        self.prefix = public_url.rstrip("/") + "/upload/"
        self.ttl, self.max_bytes, self.content_types = ttl, max_bytes, content_types
        self._lock = threading.RLock()

    # -- URLs ------------------------------------------------------------------
    def url(self, token: str) -> str:
        return self.prefix + token

    def match(self, url: str) -> str | None:
        """The token when `url` is an upload URL of this server (same scheme, host and port, the path and nothing more), else None."""
        try:
            u, base = httpx.URL(url), httpx.URL(self.prefix)
        except httpx.InvalidURL:
            return None
        if u.scheme != base.scheme or (u.host or "").lower() != (base.host or "").lower() or u.port != base.port:
            return None
        if u.userinfo or u.query or u.fragment or not u.path.startswith(base.path):
            return None
        token = u.path[len(base.path):]
        return token if _TOKEN.fullmatch(token) else None

    def describe(self, token: str, up: Upload) -> dict[str, Any]:
        """What the agent is told about a URL: where to send the file, what it may be, and until when."""
        out = {"url": self.url(token), "filename": up.filename, "content_type": up.content_type, "max_bytes": self.max_bytes,
               "expires_at": _iso(up.expires_at)}
        if up.expected_sha256:
            out["sha256"] = up.expected_sha256
        return out

    # -- files -----------------------------------------------------------------
    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.dir / f"{key}.json", self.dir / f"{key}.bin"

    def _read(self, key: str) -> Upload | None:
        """The record, or None when there is none, it is unreadable, or it has expired (then it is removed)."""
        meta, _ = self._paths(key)
        try:
            up = Upload(**json.loads(meta.read_text()))
        except (OSError, ValueError, TypeError):
            return None
        if up.expires_at <= time.time():
            self._remove(key)
            return None
        return up

    def _write(self, key: str, up: Upload, data: bytes | None = None) -> None:
        """The bytes (when given) and then the record, each written whole and renamed into place, readable by the owner only."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        meta, blob = self._paths(key)
        for path, content in ((blob, data), (meta, json.dumps(asdict(up)).encode())):
            if content is None:
                continue
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".upload-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(content)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def _remove(self, key: str) -> None:
        for path in self._paths(key):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def prune(self) -> list[tuple[str, Upload]]:
        """Drop what has expired (and a file whose record is gone); the live records, by key."""
        live: list[tuple[str, Upload]] = []
        with self._lock:
            if not self.dir.is_dir():
                return live
            keys = {p.stem for p in self.dir.glob("*.json")} | {p.stem for p in self.dir.glob("*.bin")}
            for key in sorted(keys):
                up = self._read(key)
                if up is None:
                    self._remove(key)
                else:
                    live.append((key, up))
        return live

    def pending(self) -> list[Upload]:
        return [up for _, up in self.prune()]

    # -- operations ------------------------------------------------------------
    def create(self, filename: str, content_type: str | None = None, sha256: str | None = None) -> tuple[str, Upload]:
        """A new upload URL's token and record. UploadError when the name, type or sha256 cannot be used, or too many URLs are live."""
        name = urlattach.safe_filename(filename)
        if not name or "." not in name.strip("."):
            raise UploadError("filename must be a plain file name with an extension, such as report.docx")
        ctype = (content_type or "").split(";", 1)[0].strip().lower() or urlattach.type_of(name) or ""
        if not ctype:
            raise UploadError(f"the type of '{name}' is not known from its extension; pass content_type")
        if ctype not in self.content_types:
            raise UploadError(f"{ctype} is not on this server's ATTACHMENT_URL_CONTENT_TYPES ({', '.join(self.content_types)}); "
                              "the owner can add it")
        want = None
        if sha256:
            try:
                want = urlattach.normalize_sha256(sha256)
            except ValueError as e:
                raise UploadError(f"sha256: {e}") from None
        with self._lock:
            if len(self.prune()) >= MAX_PENDING:
                raise UploadError(f"{MAX_PENDING} upload URLs are already live on this server (each lasts {self.ttl} seconds); "
                                  "use them, or wait for them to expire", 429)
            token = secrets.token_urlsafe(32)
            now = time.time()
            up = Upload(name, ctype, want, now, now + self.ttl)
            self._write(_h(token), up)
        log.info("upload %s: URL created for %s (%s)", _h(token)[:8], name, ctype)
        return token, up

    def check(self, token: str) -> Upload:
        """The record of a URL that can still take a file; UploadError (404 unknown or expired, 409 filled already) otherwise."""
        up = self._read(_h(token)) if _TOKEN.fullmatch(token or "") else None
        if up is None:
            raise UploadError("this upload URL is unknown or has expired; create a new one with mail_create_upload_url", 404)
        if up.ready:
            raise UploadError("this upload URL has its file already; a URL takes one file, create another URL for another file", 409)
        return up

    def put(self, token: str, data: bytes) -> Upload:
        """Store the file sent to a URL once it checks out: not empty, within ATTACHMENT_URL_MAX_BYTES and the store's room,
        starting as a file of its type does, and with the sha256 given at creation if there was one."""
        up = self.check(token)
        if not data:
            raise UploadError("the request body is empty: send the file's bytes as the body (curl -T <file> <url>)")
        if len(data) > self.max_bytes:
            raise UploadError(f"the file is {len(data)} bytes, larger than {self.max_bytes} (ATTACHMENT_URL_MAX_BYTES)", 413)
        if why := urlattach.wrong_signature(up.content_type, data):
            raise UploadError(f"{why}; the URL was created for {up.filename}", 415)
        digest = hashlib.sha256(data).hexdigest()
        if up.expected_sha256 and digest != up.expected_sha256:
            raise UploadError(f"the file's sha256 is {digest}, not the {up.expected_sha256} given when the URL was created: this is "
                              "not the file meant, or it changed", 422)
        with self._lock:
            live = self.prune()
            self.check(token)                                        # still free: two uploads to one URL at once
            if sum(u.size or 0 for _, u in live) + len(data) > MAX_STORE_BYTES:
                raise UploadError(f"the uploaded files waiting on this server would pass {MAX_STORE_BYTES} bytes together; "
                                  "attach some first, or wait for them to expire", 413)
            up = replace(up, size=len(data), sha256=digest)
            self._write(_h(token), up, data)
        log.info("upload %s: received %s, %d bytes", _h(token)[:8], up.filename, len(data))
        return up

    def get(self, token: str) -> dict[str, Any]:
        """{filename, content_type, content} of the file a URL holds, for attachment_urls; UploadError when the URL is unknown or
        expired, or no file has been sent to it yet."""
        key = _h(token)
        up = self._read(key) if _TOKEN.fullmatch(token or "") else None
        if up is None:
            raise UploadError("this upload URL is unknown or has expired (ATTACHMENT_UPLOAD_TTL_SECONDS); create a new one with "
                              "mail_create_upload_url and upload the file again", 404)
        if not up.ready:
            raise UploadError("no file has been sent to this upload URL yet (curl -T <file> <url>)", 409)
        try:
            data = self._paths(key)[1].read_bytes()
        except OSError:
            data = b""
        if hashlib.sha256(data).hexdigest() != up.sha256:
            self._remove(key)
            raise UploadError("the uploaded file failed its integrity check on disk; upload it again", 500)
        return {"filename": up.filename, "content_type": up.content_type, "content": data}


def register_upload_route(mcp: Any, store: UploadStore) -> None:
    """PUT or POST /upload/<token> with the file's bytes as the request body. Answers JSON; never serves a file (no GET)."""

    def answer(body: dict[str, Any], status: int = 200) -> Response:
        return JSONResponse(body, status_code=status, headers=_HEADERS)

    @mcp.custom_route("/upload/{token}", methods=["PUT", "POST"])
    async def upload(request: Request) -> Response:
        token = str(request.path_params.get("token", ""))
        try:
            await asyncio.to_thread(store.check, token)            # before the body is read: a dead URL costs nothing
        except UploadError as e:
            return answer({"error": str(e)}, e.status)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "multipart/form-data":
            return answer({"error": "send the file's bytes as the request body (curl -T <file> <url>), not as a form field"}, 415)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > store.max_bytes:
            return answer({"error": f"the file is {declared} bytes, larger than {store.max_bytes} (ATTACHMENT_URL_MAX_BYTES)"}, 413)
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > store.max_bytes:
                return answer({"error": f"the file is larger than {store.max_bytes} bytes (ATTACHMENT_URL_MAX_BYTES)"}, 413)
        try:
            up = await asyncio.to_thread(store.put, token, bytes(buf))
        except UploadError as e:
            log.warning("upload %s refused: %s", _h(token)[:8], e)
            return answer({"error": str(e)}, e.status)
        return answer({"status": "uploaded", "filename": up.filename, "content_type": up.content_type, "size": up.size,
                       "sha256": up.sha256, "url": store.url(token), "expires_at": _iso(up.expires_at),
                       "next": "Pass this url in 'attachment_urls' of mail_send_message, mail_reply_to_message or mail_update_draft."})
