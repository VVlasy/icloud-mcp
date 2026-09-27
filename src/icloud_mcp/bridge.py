"""Bridge to a helper running on the owner's Mac (Reminders and Notes).

Reminders and Notes cannot be reached from a Linux server: Apple only exposes them through their own apps, which are scriptable on a
Mac. So a small helper runs on the Mac and this module hands it work:

* The helper connects OUT to a private HTTPS port of this server (never the public one) and long-polls for jobs, so the Mac opens no
  listening port and works from wherever it can reach the server (home network or VPN).
* Jobs are structured requests for a FIXED list of operations (OPS). The Mac never receives script text to run, only an operation
  name and validated arguments, and it refuses anything not in its own copy of the list.
* The channel is TLS with a self-signed certificate that the helper pins by fingerprint, plus a bearer token.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .config import Settings

log = logging.getLogger("icloud_mcp.bridge")

ONLINE_WINDOW = 45          # seconds after the last poll during which the helper counts as online (polls last <= 30 s)
MAX_BODY = 1_048_576        # largest request body accepted on the bridge port
MAX_RESULT_BODY = 12 * 1_048_576   # a job result (only after the token check): drive_get_file carries a file of up to 7 MB, base64
_MAX_FAILURES, _FAILURE_WINDOW = 20, 900


# Operations that need a newer Mac helper than the first one that had them, with that version. A call is refused with an
# update message when the helper has reported an older version; an unknown version (not polled yet) is let through, so the
# helper itself can still refuse an operation it does not know.
OP_MIN_HELPER: dict[str, str] = {
    "maps_travel_time": "0.6.0", "maps_search": "0.6.0",
    "imessage_chats": "0.6.0", "imessage_read": "0.6.0", "imessage_search": "0.6.0", "imessage_send": "0.6.0",
    **{op: "0.7.0" for op in ("health_summary", "health_day", "health_status", "health_refresh")},
    **{op: "0.5.0" for op in ("reminder_list_create", "reminder_list_update", "reminder_list_delete")},
    # 0.8.0: the list-delete preview counts every reminder in the list itself, and the delete checks that count
    **{f"reminder_list_delete.{arg}": "0.8.0" for arg in ("preview", "expected")},
    # "op.argument": an argument an older helper would reject as unknown
    **{f"{op}.{arg}": "0.5.0" for op, args in (("reminders_list", ("completed", "completed_since", "completed_before")),
                                               ("reminder_create", ("repeat", "alerts_before", "alerts_at")),
                                               ("reminder_update", ("repeat", "clear_repeat", "alerts_before", "alerts_at")))
       for arg in args},
}


def _version(text: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:3]) if parts else None


class BridgeError(Exception):
    pass


# ---------------------------------------------------------------------------
# The operations the Mac may be asked to perform. The Mac helper carries its own copy of this table (tests keep them identical).
# arg spec: name -> (type, required, max). Types: str, int, bool, iso (ISO 8601 date-time or None).
# ---------------------------------------------------------------------------
OPS: dict[str, dict[str, tuple[str, bool, int]]] = {
    # Reminders
    "reminder_lists": {},
    "reminders_list": {"list": ("str", False, 200), "list_id": ("str", False, 200), "query": ("str", False, 200), "refresh": ("bool", False, 0),
                       "limit": ("int", False, 200), "completed": ("str", False, 4), "completed_since": ("iso", False, 40),
                       "completed_before": ("iso", False, 40)},
    "reminder_create": {"title": ("str", True, 500), "list": ("str", False, 200), "list_id": ("str", False, 200), "notes": ("str", False, 20000), "due": ("iso", False, 40),
                        "priority": ("int", False, 9), "repeat": ("str", False, 300), "alerts_before": ("str", False, 200),
                        "alerts_at": ("str", False, 600)},
    "reminder_update": {"id": ("str", True, 500), "title": ("str", False, 500), "notes": ("str", False, 20000), "due": ("iso", False, 40),
                        "clear_due": ("bool", False, 0), "priority": ("int", False, 9), "repeat": ("str", False, 300),
                        "clear_repeat": ("bool", False, 0), "alerts_before": ("str", False, 200), "alerts_at": ("str", False, 600)},
    "reminder_complete": {"id": ("str", True, 500), "completed": ("bool", False, 0)},
    "reminder_delete": {"id": ("str", True, 500)},
    "reminder_move": {"id": ("str", True, 500), "list": ("str", False, 200), "list_id": ("str", False, 200)},
    "reminder_list_create": {"name": ("str", True, 200), "account": ("str", False, 200)},
    "reminder_list_update": {"list_id": ("str", True, 200), "name": ("str", True, 200)},
    "reminder_list_delete": {"list_id": ("str", True, 200), "name": ("str", True, 200), "delete_reminders": ("bool", False, 0),
                             "preview": ("bool", False, 0), "expected": ("int", False, 10000000)},
    # Notes
    "note_folders": {},
    "notes_list": {"folder": ("str", False, 200), "query": ("str", False, 200), "search_body": ("bool", False, 0), "limit": ("int", False, 100)},
    "note_read": {"id": ("str", True, 500), "max_chars": ("int", False, 100000)},
    "note_create": {"title": ("str", True, 500), "body": ("str", False, 100000), "folder": ("str", False, 200)},
    "note_delete": {"id": ("str", True, 500), "title": ("str", True, 500)},
    "note_folder_create": {"name": ("str", True, 200), "account": ("str", False, 200), "parent_id": ("str", False, 500)},
    "note_move": {"id": ("str", True, 500), "title": ("str", True, 500), "folder_id": ("str", False, 500), "folder": ("str", False, 200)},
    "note_update": {"id": ("str", True, 500), "title": ("str", True, 500), "expected_hash": ("str", True, 8), "text": ("str", True, 100000),
                    "mode": ("str", True, 7)},
    # iCloud Drive (paths are relative to the Drive; see ops/drive.py)
    "drive_list": {"path": ("str", False, 1000), "include_hidden": ("bool", False, 0), "limit": ("int", False, 1000)},
    "drive_search": {"query": ("str", True, 200), "path": ("str", False, 1000), "limit": ("int", False, 200)},
    "drive_search_content": {"query": ("str", True, 200), "path": ("str", False, 1000), "limit": ("int", False, 100),
                             "download": ("bool", False, 0)},
    "drive_info": {"path": ("str", True, 1000)},
    "drive_read": {"path": ("str", True, 1000), "max_chars": ("int", False, 200000), "offset": ("int", False, 50000000)},
    "drive_get_file": {"path": ("str", True, 1000), "max_bytes": ("int", False, 7340032)},
    "drive_write": {"path": ("str", True, 1000), "content": ("str", False, 500000), "overwrite": ("bool", False, 0)},
    "drive_mkdir": {"path": ("str", True, 1000)},
    "drive_move": {"path": ("str", True, 1000), "to": ("str", True, 1000)},
    "drive_trash": {"path": ("str", True, 1000)},
    # Apple Maps (bin/maps-cli, MapKit; addresses, names or "lat,lon" given by the agent, never the Mac's own location)
    "maps_travel_time": {"origin": ("str", True, 500), "destination": ("str", True, 500), "mode": ("str", False, 10),
                         "depart_at": ("iso", False, 40), "arrive_at": ("iso", False, 40), "alternatives": ("bool", False, 0)},
    "maps_search": {"query": ("str", True, 200), "near": ("str", False, 500), "limit": ("int", False, 20)},
    # iMessage (ops/imessage.py; the owner's own ~/Library/Messages/chat.db, read-only)
    "imessage_chats": {"limit": ("int", False, 1000), "since": ("iso", False, 40), "include_archived": ("bool", False, 0),
                       "exclude": ("str", False, 4000)},
    "imessage_read": {"chat_id": ("str", True, 300), "limit": ("int", False, 500), "before_id": ("int", False, 9007199254740991),
                      "since": ("iso", False, 40), "exclude": ("str", False, 4000)},
    "imessage_search": {"query": ("str", True, 200), "chat_id": ("str", False, 300), "limit": ("int", False, 200),
                        "since": ("iso", False, 40), "before": ("iso", False, 40), "exclude": ("str", False, 4000)},
    "imessage_send": {"chat_id": ("str", False, 300), "handle": ("str", False, 300), "text": ("str", True, 10000)},
    # Shortcuts (only names on BOTH the server's SHORTCUTS_ALLOW and the Mac's own shortcuts-allow.txt run; see ops/shortcut.py)
    "shortcut_run": {"name": ("str", True, 200), "input": ("str", False, 20000)},
    # Apple Health (ops/health.py on the Mac: daily figures from the owner's iPhone exports, kept in a private store there)
    "health_summary": {"start": ("iso", True, 40), "end": ("iso", False, 40)},
    "health_day": {"date": ("iso", True, 40), "metric": ("str", True, 40)},
    "health_status": {},
    "health_refresh": {},
}

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?\Z")


def _iso_ok(value: str) -> bool:
    """A well-formed ISO 8601 date or date-time that is also a REAL date and time (no 2026-13-45, 2026-02-30 or 25:00)."""
    m = _ISO.match(value)
    if not m:
        return False
    try:
        dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return False
    if m[4] is not None and not (int(m[4]) <= 23 and int(m[5]) <= 59 and (m[6] is None or int(m[6]) <= 59)):
        return False
    return not (m[7] and m[7] != "Z" and (int(m[7][1:3]) > 23 or int(m[7][-2:]) > 59))


def validate_args(op: str, args: dict[str, Any] | None) -> dict[str, Any]:
    spec = OPS.get(op)
    if spec is None:
        raise BridgeError(f"Unknown Mac operation '{op}'.")
    args = {} if args is None else args
    if not isinstance(args, dict):
        raise BridgeError("Arguments must be an object.")
    clean: dict[str, Any] = {}
    for key, value in args.items():
        if key not in spec:
            raise BridgeError(f"Operation '{op}' does not take '{key}'.")
        kind, _, limit = spec[key]
        if value is None:
            clean[key] = None
        elif kind == "str":
            if not isinstance(value, str) or len(value) > limit:
                raise BridgeError(f"'{key}' must be text of at most {limit} characters.")
            clean[key] = value
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
                raise BridgeError(f"'{key}' must be a whole number between 0 and {limit}.")
            clean[key] = value
        elif kind == "bool":
            if not isinstance(value, bool):
                raise BridgeError(f"'{key}' must be true or false.")
            clean[key] = value
        elif kind == "iso":
            if not isinstance(value, str) or len(value) > 40 or not _iso_ok(value):
                raise BridgeError(f"'{key}' must be an ISO 8601 date or date-time such as 2026-09-21 or 2026-09-21T15:00:00+02:00.")
            clean[key] = value
        else:  # pragma: no cover - a mistake in OPS itself
            raise BridgeError(f"Unsupported argument type {kind}.")
    missing = [k for k, (_, required, _) in spec.items() if required and clean.get(k) is None]
    if missing:
        raise BridgeError(f"Operation '{op}' needs: {', '.join(missing)}.")
    return clean


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    op: str
    args: dict[str, Any]
    deadline: float
    state: str = "queued"                 # queued | running | done | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    ok: bool = False
    result: Any = None
    error: str = ""
    event: threading.Event = field(default_factory=threading.Event)


JOB_MARGIN = 3     # seconds: the helper stops a job this much before the server stops waiting, so its report still arrives


class MacBridge:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self._cond = threading.Condition()
        self._queue: list[Job] = []
        self._jobs: dict[str, Job] = {}
        self.last_seen: float | None = None
        self.agent: dict[str, str] = {}
        self._durations: list[float] = []      # seconds from request to answer, last 20 jobs

    # -- state --------------------------------------------------------------------
    def _online_locked(self) -> bool:
        now = time.time()
        if any(j.state == "running" and j.deadline > now for j in self._jobs.values()):
            return True                                        # busy running a job counts as present
        return self.last_seen is not None and now - self.last_seen < ONLINE_WINDOW

    def status(self) -> dict[str, Any]:
        with self._cond:
            ago = None if self.last_seen is None else round(time.time() - self.last_seen)
            waiting = sum(1 for j in self._queue if j.state == "queued")
            recent = sorted(self._durations)
            median = round(recent[len(recent) // 2], 2) if recent else None
            return {"online": self._online_locked(), "last_seen_seconds_ago": ago, "helper": dict(self.agent),
                    "jobs_waiting": waiting, "queue_length": waiting + sum(1 for j in self._jobs.values() if j.state == "running"),
                    "median_job_seconds": median}

    def _offline_message(self) -> str:
        if self.last_seen is None:
            when = "has not connected since the server started"
        else:
            mins = (time.time() - self.last_seen) / 60
            when = f"was last seen {mins:.0f} minute(s) ago" if mins >= 1 else "was last seen moments ago"
        return (f"The Mac helper {when}. Reminders and Notes work through the owner's Mac, which must be on, awake and connected to "
                "the home network or VPN. Tell the user instead of retrying repeatedly.")

    # -- called by tools (worker threads) ---------------------------------------------
    def call(self, op: str, args: dict[str, Any] | None = None) -> Any:
        from .callctx import stage
        stage(f"waiting for the Mac helper ({op})")
        clean = validate_args(op, args)
        with self._cond:
            if not self._online_locked():
                raise BridgeError(self._offline_message())
            have = self.agent.get("version", "")
            for key in (op, *(f"{op}.{a}" for a in clean)):
                need = OP_MIN_HELPER.get(key)
                if need and _version(have) is not None and _version(have) < _version(need):
                    what = op if key == op else f"{key.split('.', 1)[1]} on {op}"
                    raise BridgeError(f"The Mac helper is {have} but {what} needs {need} or newer: update the helper on the Mac "
                                      "(mac-helper/install.sh), then try again.")
            job = Job(id=secrets.token_urlsafe(9), op=op, args=clean, deadline=time.time() + self.timeout)
            self._jobs[job.id] = job
            self._queue.append(job)
            self._cond.notify_all()
        if not job.event.wait(self.timeout):
            with self._cond:
                picked_up = job.state == "running"
                if job.state in ("queued", "running"):
                    job.state = "cancelled"
                    if job in self._queue:
                        self._queue.remove(job)
            if picked_up:
                raise BridgeError(f"The Mac picked up the request but did not finish within {self.timeout}s: the Mac is slow or busy "
                                  "(Notes in particular can be). The operation may still complete there, so check before repeating a "
                                  "change; try again in a minute, or run icloud_get_helper_status to see how long jobs take.")
            raise BridgeError(f"The Mac did not pick up the request within {self.timeout}s: it may have just gone to sleep or lost its "
                              "connection. Run icloud_get_helper_status, and tell the user instead of retrying repeatedly.")
        if not job.ok:
            err = job.error or "The Mac helper reported an error."
            if "-1728" in err:                                     # Apple's "object not found"
                err += (" The reminder, note or list is not there any more (deleted or moved on another device): list again "
                        "(reminders_list_reminders, notes_list_notes) for current ids.")
            raise BridgeError(err)
        return job.result

    # -- called by the bridge HTTP app -----------------------------------------------------
    def _touch(self, meta: dict[str, str]) -> None:
        self.last_seen = time.time()
        if meta:                    # the hostname a helper reports is never kept: the Mac's name would reach the model
            self.agent = {k: v for k, v in meta.items() if k in ("version", "os")}

    def next_job(self, meta: dict[str, str], wait: float) -> Job | None:
        end = time.monotonic() + wait
        with self._cond:
            self._touch(meta)
            while True:
                now = time.time()
                while self._queue:
                    job = self._queue.pop(0)
                    if job.state == "queued" and job.deadline > now:
                        job.state, job.started = "running", now
                        return job
                remaining = end - time.monotonic()
                if remaining <= 0:
                    self._touch(meta)
                    self._forget_old_locked()
                    return None
                self._cond.wait(remaining)

    def complete(self, job_id: str, ok: bool, result: Any, error: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.state != "running":
                return False
            job.state, job.ok, job.result, job.error = "done", bool(ok), result, str(error or "")[:500]
            self.last_seen = time.time()
            self._durations = (self._durations + [self.last_seen - job.created])[-20:]
            job.event.set()
            return True

    def _forget_old_locked(self) -> None:
        if len(self._jobs) > 200:
            keep = sorted(self._jobs.values(), key=lambda j: j.deadline)[-100:]
            self._jobs = {j.id: j for j in keep}


# ---------------------------------------------------------------------------
# TLS: a self-signed certificate created once and pinned by the helper
# ---------------------------------------------------------------------------
def ensure_tls(data_dir: str, extra_names: tuple[str, ...] = ()) -> tuple[str, str, str]:
    """Return (cert_path, key_path, sha256_fingerprint_hex). Created on first use; the fingerprint is stable across restarts."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = d / "bridge-cert.pem", d / "bridge-key.pem"
    if not (cert_path.exists() and key_path.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "icloud-mcp-bridge")])
        sans: list[x509.GeneralName] = [x509.DNSName("localhost")]
        for n in extra_names:
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(n)))
            except ValueError:
                sans.append(x509.DNSName(n))
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .sign(key, hashes.SHA256()))
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    (d / "bridge_fingerprint.txt").write_text(fingerprint + "\n")
    return str(cert_path), str(key_path), fingerprint


# ---------------------------------------------------------------------------
# The private HTTPS app the Mac helper talks to
# ---------------------------------------------------------------------------
def build_bridge_app(bridge: MacBridge, s: Settings) -> Starlette:
    failures: dict[str, list[float]] = {}     # client address -> times of wrong tokens; a lockout is per address

    def locked_out(addr: str) -> bool:
        now = time.time()
        recent = [t for t in failures.get(addr, []) if now - t < _FAILURE_WINDOW]
        if recent:
            failures[addr] = recent
        else:
            failures.pop(addr, None)
        if len(failures) > 1000:                    # never let strangers grow this table without bound
            for stale in [a for a, ts in failures.items() if now - ts[-1] >= _FAILURE_WINDOW]:
                failures.pop(stale, None)
        return len(recent) >= _MAX_FAILURES

    def denied(request: Request) -> Response | None:
        """The correct token is always accepted: a lockout only ever answers wrong tokens, and only for the address that sent
        them, so nobody on the network can knock the real helper offline by guessing."""
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if hmac.compare_digest(supplied.encode(), s.bridge_token.encode()):
            return None
        addr = request.client.host if request.client else "?"
        if locked_out(addr):
            return JSONResponse({"error": "too many failed attempts"}, status_code=429)
        failures.setdefault(addr, []).append(time.time())
        log.warning("bridge: request refused (bad or missing token)")
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    async def read_json(request: Request, limit: int = MAX_BODY) -> Any:
        body = await request.body()
        if len(body) > limit:
            raise BridgeError("The request body is too large.")
        return json.loads(body or b"{}")

    async def ping(request: Request) -> Response:
        return denied(request) or JSONResponse({"ok": True, "server_time": int(time.time()), "bridge": bridge.status()})

    async def poll(request: Request) -> Response:
        if (bad := denied(request)) is not None:
            return bad
        try:
            body = await read_json(request)
            wait = min(max(float(body.get("wait", 25)), 0.0), 30.0)
        except (BridgeError, ValueError, TypeError, AttributeError):
            return JSONResponse({"error": "bad request"}, status_code=400)
        meta = {"version": str(body.get("version", ""))[:20], "os": str(body.get("os", ""))[:40]}
        job = await asyncio.to_thread(bridge.next_job, meta, wait)
        if job is None:
            return Response(status_code=204)
        return JSONResponse({"id": job.id, "op": job.op, "args": job.args, "seconds": max(1, int(job.deadline - time.time()) - JOB_MARGIN)})

    async def result(request: Request) -> Response:
        if (bad := denied(request)) is not None:
            return bad
        try:
            body = await read_json(request, MAX_RESULT_BODY)
            job_id, ok = str(body["job"]), bool(body["ok"])
        except (BridgeError, ValueError, TypeError, KeyError, AttributeError):
            return JSONResponse({"error": "bad request"}, status_code=400)
        return JSONResponse({"accepted": bridge.complete(job_id, ok, body.get("result"), body.get("error", ""))})

    return Starlette(routes=[Route("/bridge/ping", ping, methods=["GET"]), Route("/bridge/poll", poll, methods=["POST"]),
                             Route("/bridge/result", result, methods=["POST"])])


def start_bridge_listener(app: Starlette, port: int, certfile: str, keyfile: str, host: str = "0.0.0.0"):
    """Serve the bridge app over TLS on its own port, in a background thread. Returns the uvicorn server (for tests)."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, ssl_certfile=certfile, ssl_keyfile=keyfile,
                                           log_level="warning", access_log=False, timeout_keep_alive=30))   # the helper reuses its connection
    threading.Thread(target=server.run, name="mac-bridge", daemon=True).start()
    return server
