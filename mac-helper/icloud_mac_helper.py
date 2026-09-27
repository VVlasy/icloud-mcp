#!/usr/bin/env python3
"""icloud-mac-helper: lets the icloud-mcp server use Reminders and Notes on this Mac.

It connects OUT to the server's private HTTPS port and asks for work. It opens no listening port. It performs only the fixed
operations in OPS. Reminders operations run bin/reminders-eventkit (EventKit, built from eventkit/ by install.sh); Notes operations
run a static script in ops/. Either way the arguments travel as ONE JSON argv entry, never as code.
The server is identified by a pinned certificate fingerprint and authenticated with a bearer token.

Portions of the ops/ scripts are adapted from MrGo2/icloud-mcp (MIT), see THIRD_PARTY_NOTICES.md.
Only the Python standard library is used, so it runs on the python3 that ships with the macOS command line tools.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import hmac
import http.client
import io
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

VERSION = "0.8.0"
HERE = os.path.dirname(os.path.abspath(__file__))
OPS_DIR = os.path.join(HERE, "ops")
DEFAULT_CONFIG = os.path.expanduser("~/.config/icloud-mac-helper/config.json")
OSASCRIPT, LAUNCHCTL = "/usr/bin/osascript", "/bin/launchctl"    # Apple's own binaries by absolute path, never resolved through PATH
MAX_OUTPUT = 12 * 1024 * 1024       # largest script result accepted (drive_get_file hands over files of up to 7 MB, base64-encoded)
MAX_RESPONSE = 4 * 1024 * 1024      # largest server response accepted (a drive_write job carries up to 500,000 characters, UTF-8)

# The fixed operations, identical to the server's table (tests keep them in sync): name -> {argument: (type, required, max)}.
OPS = {
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
    # Apple Health (ops/health.py; exports the owner's iPhone writes to the Shortcuts app's iCloud folder, kept in a private store here)
    "health_summary": {"start": ("iso", True, 40), "end": ("iso", False, 40)},
    "health_day": {"date": ("iso", True, 40), "metric": ("str", True, 40)},
    "health_status": {},
    "health_refresh": {},
}
# Reminders: one EventKit binary, one process per operation (measured ~21 ms fixed cost, 20-40 ms per operation end to end, against
# 0.5-22 s for the JXA scripts, which scan a whole list per request). There is deliberately NO fallback to the JXA Reminders scripts:
# they use "x-apple-reminder://" ids and a position cache, so a silent fallback would reject ids handed out by EventKit and hide a broken
# install behind 20-second requests. The JXA Reminders scripts stay in ops/ only as reference for what EventKit cannot do (subtasks, tags,
# sections, attachments); nothing here runs them.
EVENTKIT_BIN = os.path.join(HERE, "bin", "reminders-eventkit")
# Apple Maps: travel times and place search through MapKit, a small program built by install.sh like the Reminders one.
MAPS_BIN = os.path.join(HERE, "bin", "maps-cli")
MAPS_OPS = frozenset({"maps_travel_time", "maps_search"})
# iMessage: the owner's own Messages history, read by a fixed script with Apple's Python, like iCloud Drive.
IMESSAGE_SCRIPT = os.path.join(OPS_DIR, "imessage.py")
IMESSAGE_OPS = frozenset({"imessage_chats", "imessage_read", "imessage_search", "imessage_send"})
# iCloud Drive: plain file operations, run by Apple's own Python (the one running this helper) from a fixed script with one JSON argument.
DRIVE_SCRIPT = os.path.join(OPS_DIR, "drive.py")
DRIVE_OPS = frozenset(op for op in OPS if op.startswith("drive_"))
# Shortcuts: one fixed script, run by the same Apple Python, which checks the Mac's own allowlist before `shortcuts run`.
SHORTCUT_SCRIPT = os.path.join(OPS_DIR, "shortcut.py")
SHORTCUT_OPS = frozenset({"shortcut_run"})
# Apple Health: one fixed script, run by the same Apple Python; the refresh command it may run is set on the Mac only.
HEALTH_SCRIPT = os.path.join(OPS_DIR, "health.py")
HEALTH_OPS = frozenset(op for op in OPS if op.startswith("health_"))
EVENTKIT_OPS = frozenset({"reminder_lists", "reminders_list", "reminder_create", "reminder_update", "reminder_complete", "reminder_delete", "reminder_move",
                          "reminder_list_create", "reminder_list_update", "reminder_list_delete"})
REMINDERS_GRANT = 'Full Access to Reminders for "iCloud Mac Helper (Reminders)" (System Settings > Privacy & Security > Reminders)'
OP_FILES = {
    "note_folders": "note_folders.js", "notes_list": "notes_list.js", "note_read": "note_read.js", "note_create": "note_create.js",
    "note_delete": "note_delete.js", "note_folder_create": "note_folder_create.js", "note_move": "note_move.js", "note_update": "note_update.js",
}

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?\Z")


def _iso_ok(value):
    """A well-formed ISO 8601 date or date-time that is also a REAL date and time (identical to the server's check)."""
    m = _ISO.match(value)
    if not m:
        return False
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    if m.group(4) is not None and not (int(m.group(4)) <= 23 and int(m.group(5)) <= 59 and (m.group(6) is None or int(m.group(6)) <= 59)):
        return False
    off = m.group(7)
    return not (off and off != "Z" and (int(off[1:3]) > 23 or int(off[-2:]) > 59))


class HelperError(Exception):
    pass


def validate_args(op, args):
    spec = OPS.get(op)
    if spec is None:
        raise HelperError("unknown operation %r" % (op,))
    args = {} if args is None else args
    if not isinstance(args, dict):
        raise HelperError("arguments must be an object")
    clean = {}
    for key, value in args.items():
        if key not in spec:
            raise HelperError("operation %s does not take %r" % (op, key))
        kind, _required, limit = spec[key]
        if value is None:
            clean[key] = None
        elif kind == "str":
            if not isinstance(value, str) or len(value) > limit:
                raise HelperError("%s must be text of at most %d characters" % (key, limit))
            clean[key] = value
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
                raise HelperError("%s must be a whole number between 0 and %d" % (key, limit))
            clean[key] = value
        elif kind == "bool":
            if not isinstance(value, bool):
                raise HelperError("%s must be true or false" % key)
            clean[key] = value
        elif kind == "iso":
            if not isinstance(value, str) or len(value) > 40 or not _iso_ok(value):
                raise HelperError("%s must be an ISO 8601 date or date-time" % key)
            clean[key] = value
        else:
            raise HelperError("unsupported argument type %s" % kind)
    missing = [k for k, (_t, required, _m) in spec.items() if required and clean.get(k) is None]
    if missing:
        raise HelperError("operation %s needs: %s" % (op, ", ".join(missing)))
    return clean


def _friendly(stderr):
    text = stderr.strip()
    if "-1743" in text or "Not authorized" in text:
        return ("macOS has not allowed this helper to control the app. Open System Settings > Privacy & Security > Automation and "
                "enable it for the helper (python3 / osascript), then try again.")
    if "-1728" in text or "-1719" in text:
        return "the requested list, folder or item was not found"
    # Our own scripts raise readable errors ("several lists are named ...", "invalid due date"): show that text without osascript's wrapping.
    last = text.splitlines()[-1] if text else ""
    m = re.search(r"execution error: (?:Error: )?(.+?)(?: \(-?\d+\))?\s*$", last)
    return (m.group(1) if m else text)[:300] or "the script failed"


def explain(e):
    """Turn a low-level network error into something a person can act on."""
    if isinstance(e, OSError) and getattr(e, "errno", None) in (65, 113):        # EHOSTUNREACH on macOS / Linux
        return ("%s. On macOS this usually means the local network is blocked for this program (macOS does that to third-party Python). "
                "Use Apple's own /usr/bin/python3 (install it with: xcode-select --install), or allow it in System Settings > Privacy & "
                "Security > Local Network. Also check the server address and that this Mac is on the home network or VPN." % e)
    return str(e)


def build_command(op, args):
    """The exact command line: a fixed program (the EventKit binary, or osascript with a static script file) plus ONE JSON argument.
    No -e, no generated source."""
    payload = json.dumps(args, separators=(",", ":"))
    if op in EVENTKIT_OPS:
        return [EVENTKIT_BIN, op, payload]
    if op in MAPS_OPS:
        return [MAPS_BIN, op, payload]
    if op in DRIVE_OPS:
        return [sys.executable, "-I", DRIVE_SCRIPT, op, payload]
    if op in IMESSAGE_OPS:
        return [sys.executable, "-I", IMESSAGE_SCRIPT, op, payload]
    if op in SHORTCUT_OPS:
        return [sys.executable, "-I", SHORTCUT_SCRIPT, op, payload]
    if op in HEALTH_OPS:
        return [sys.executable, "-I", HEALTH_SCRIPT, op, payload]
    return [OSASCRIPT, "-l", "JavaScript", os.path.join(OPS_DIR, OP_FILES[op]), payload]


_NOTES_LIST_SECONDS = 30.0      # Notes listing through JXA is the slow path (0.5 to 22 s); a repeat within this time is served from here
_NOTES_WRITES = frozenset({"note_create", "note_delete", "note_move", "note_update", "note_folder_create"})
_NOTES_CACHE = {}


def run_op(op, args, timeout=60, extra=None):
    """run_one(), with notes_list results kept for _NOTES_LIST_SECONDS and dropped by any Notes write."""
    if op in _NOTES_WRITES:
        _NOTES_CACHE.clear()
    if op == "notes_list" and extra is None:
        key = json.dumps(args, sort_keys=True, default=str)
        hit = _NOTES_CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] < _NOTES_LIST_SECONDS:
            return hit[1]
        out = run_one(op, args, timeout)
        if out[0]:
            _NOTES_CACHE[key] = (time.monotonic(), out)
        return out
    return run_one(op, args, timeout, extra)


def run_one(op, args, timeout=60, extra=None):
    """Run one operation. Returns (ok, result, error). The child is killed if it exceeds the timeout.
    `extra` is added AFTER validation and only by the helper itself; it is the one sanctioned way to add anything post-validation."""
    if op not in OPS or (op not in OP_FILES and op not in EVENTKIT_OPS and op not in DRIVE_OPS and op not in SHORTCUT_OPS
                         and op not in MAPS_OPS and op not in IMESSAGE_OPS and op not in HEALTH_OPS):
        return False, None, "unknown operation"
    try:
        clean = validate_args(op, args)
    except HelperError as e:
        return False, None, str(e)
    if op in DRIVE_OPS or op in SHORTCUT_OPS or op in IMESSAGE_OPS or op in HEALTH_OPS:        # how long the script may take within the job
        clean = dict(clean, budget=max(1, timeout - 10))
    if extra:
        clean = dict(clean, **extra)
    try:
        proc = subprocess.run(build_command(op, clean), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, "the script did not finish within %ds and was stopped" % timeout
    except (FileNotFoundError, PermissionError):
        if op in EVENTKIT_OPS:
            return False, None, "the Reminders program (bin/reminders-eventkit) is missing or not executable; run install.sh again on this Mac"
        if op in MAPS_OPS:
            return False, None, "the Maps program (bin/maps-cli) is missing or not executable; run install.sh again on this Mac"
        return False, None, "osascript was not found (this helper only runs on macOS)"
    if proc.returncode != 0:
        return False, None, _friendly(proc.stderr.decode("utf-8", "replace"))
    if len(proc.stdout) > MAX_OUTPUT:
        return False, None, "the result was too large"
    try:
        return True, json.loads(proc.stdout.decode("utf-8")), ""
    except ValueError:
        return False, None, "the script returned something that is not JSON"


# ---------------------------------------------------------------------------
# Talking to the server (TLS with a pinned certificate fingerprint + bearer token)
# ---------------------------------------------------------------------------
def load_config(path=None):
    path = path or os.environ.get("ICLOUD_MAC_HELPER_CONFIG") or DEFAULT_CONFIG
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise HelperError("cannot read the config file %s (%s). Run install.sh first." % (path, e))
    for key in ("server", "token", "fingerprint"):
        if not cfg.get(key):
            raise HelperError("the config file is missing %r" % key)
    try:
        if os.stat(path).st_mode & 0o077:
            print("warning: %s is readable by other users; run: chmod 600 %s" % (path, path), file=sys.stderr)
    except OSError:
        pass
    return cfg


def _norm_fp(value):
    return re.sub(r"[^0-9a-f]", "", value.lower().replace("sha256:", ""))


def _connect(cfg, timeout):
    parts = urlsplit(cfg["server"])
    if parts.scheme != "https" or not parts.hostname:
        raise HelperError("the server address must start with https://")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                                   # identity is checked by fingerprint below, not by a CA
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout, context=ctx)
    conn.connect()
    seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
    if not hmac.compare_digest(seen, _norm_fp(cfg["fingerprint"])):
        conn.close()
        raise HelperError("the server's certificate does not match the pinned fingerprint. Nothing was sent. If you reinstalled the "
                          "server, copy its new fingerprint into the config.")
    return conn


class Link(object):
    """One pinned HTTPS connection to the server, kept open across polls and results: one TLS handshake per session instead
    of two per job. http.client's own silent reconnect is switched off (auto_open = 0), because it would skip the certificate
    pinning in _connect(); every new connection goes through _connect(). A reused connection that turns out to be dead is
    replaced once; a fresh one that fails is a real failure."""

    def __init__(self):
        self.conn, self.key = None, None

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.conn = None

    def request(self, cfg, method, path, payload=None, timeout=40):
        key = (cfg["server"], _norm_fp(cfg["fingerprint"]), cfg["token"])
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json", "User-Agent": "icloud-mac-helper/" + VERSION}
        for attempt in (0, 1):
            fresh = self.conn is None or self.key != key
            if fresh:
                self.close()
                self.conn = _connect(cfg, timeout)
                self.conn.auto_open = 0
                self.key = key
            try:
                self.conn.timeout = timeout
                if self.conn.sock is not None:
                    self.conn.sock.settimeout(timeout)
                self.conn.request(method, path, body=body, headers=headers)
                resp = self.conn.getresponse()
                raw = resp.read(MAX_RESPONSE + 1)
                if resp.will_close:
                    self.close()
            except (OSError, ssl.SSLError, http.client.HTTPException):
                self.close()
                if fresh or attempt:
                    raise
                continue                                               # the kept connection had died: once more, freshly
            if len(raw) > MAX_RESPONSE:
                self.close()
                raise HelperError("the server response was too large")
            try:
                data = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                data = None
            return resp.status, data
        raise HelperError("could not reach the server")               # pragma: no cover


_LINK = Link()


def request(cfg, method, path, payload=None, timeout=40):
    return _LINK.request(cfg, method, path, payload, timeout)


def handle_one(cfg, runner=run_op, wait=25):
    """One poll cycle. Returns 'idle', 'done' or 'refused'."""
    meta = {"wait": wait, "host": socket.gethostname(), "version": VERSION, "os": "macOS " + platform.mac_ver()[0]}
    status, job = request(cfg, "POST", "/bridge/poll", meta, timeout=wait + 15)
    if status == 204:
        return "idle"
    if status in (401, 429):
        return "refused"
    if status != 200 or not isinstance(job, dict) or "id" not in job:
        raise HelperError("unexpected answer from the server (HTTP %s)" % status)
    ok, result, error = runner(str(job.get("op", "")), job.get("args") or {}, max(1, min(int(job.get("seconds", 60)), 120)))
    request(cfg, "POST", "/bridge/result", {"job": job["id"], "ok": ok, "result": result, "error": error})
    return "done"


_CFG = {"key": None, "cfg": None}


def current_config(path=None):
    """load_config(), re-read only when the file changed (checked by modification time and size on every poll)."""
    where = path or os.environ.get("ICLOUD_MAC_HELPER_CONFIG") or DEFAULT_CONFIG
    try:
        st = os.stat(where)
        key = (where, st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is None or key != _CFG["key"] or _CFG["cfg"] is None:
        _CFG["cfg"], _CFG["key"] = load_config(path), key
    return _CFG["cfg"]


NOTE_BACKUPS = os.path.expanduser("~/Library/Application Support/icloud-mac-helper/note-backups")
NOTE_BACKUP_DAYS = 30
_PRUNED = {"at": 0.0}


def prune_note_backups(folder=NOTE_BACKUPS, days=NOTE_BACKUP_DAYS, trash=None, now=None):
    """Move note backups older than `days` to the macOS Trash (never a permanent delete; they stay recoverable there).
    Returns how many were moved. Runs at most once a day from the main loop; a failure is reported and skipped."""
    now = time.time() if now is None else now
    old = []
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.endswith(".html") and os.path.isfile(path) and now - os.path.getmtime(path) > days * 86400:
                old.append(path)
    except OSError:
        return 0
    if not old:
        return 0
    trash = trash or (lambda paths: subprocess.run(["/usr/bin/trash"] + paths, capture_output=True, timeout=60).returncode == 0)
    moved = 0
    for i in range(0, len(old), 50):
        batch = old[i:i + 50]
        if trash(batch):
            moved += len(batch)
    return moved


def run_forever(cfg_path=None):
    backoff = 2
    while True:
        if time.time() - _PRUNED["at"] > 86400:
            _PRUNED["at"] = time.time()
            try:
                n = prune_note_backups()
                if n:
                    print("moved %d note backup(s) older than %d days to the Trash" % (n, NOTE_BACKUP_DAYS), file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001 - housekeeping never stops the helper
                print("note backup clean-up skipped: %s" % e, file=sys.stderr, flush=True)
        try:
            cfg = current_config(cfg_path)
            outcome = handle_one(cfg, runner=run_op)
            backoff = 2
            if outcome == "refused":
                print("the server refused the token; retrying in 60s", file=sys.stderr, flush=True)
                time.sleep(60)
        except (HelperError, OSError, ssl.SSLError, http.client.HTTPException) as e:
            print("cannot reach the server: %s (retrying in %ds)" % (explain(e), backoff), file=sys.stderr, flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# Self-test: prints ONE JSON document with pass/fail, counts and timings. No secrets and no reminder or note content.
# ---------------------------------------------------------------------------
HOSTILE = [
    'plain', 'quote " and \\ backslash', "single ' quote", "line1\nline2\ttab", "unicode: café 日本語 \U0001F642",
    '"); do shell script "touch {marker}"; ("', "'); $.system('touch {marker}'); ('", "${1+1} `date` $(date)", "\\u0022; throw 1; \\u0022",
]


def selftest(cfg_path=None):
    report = {"helper_version": VERSION, "python": sys.version.split()[0], "macos": platform.mac_ver()[0], "checks": {}}
    checks = report["checks"]
    osa = os.path.exists(OSASCRIPT)
    checks["platform"] = {"ok": platform.system() == "Darwin" and bool(osa), "osascript": bool(osa)}
    if not checks["platform"]["ok"]:
        print(json.dumps(report, indent=2))
        return 1

    marker = os.path.join(tempfile.gettempdir(), "icloud-helper-injection-marker-%d" % os.getpid())
    payload = [s.replace("{marker}", marker) for s in HOSTILE]
    try:
        proc = subprocess.run([OSASCRIPT, "-l", "JavaScript", os.path.join(OPS_DIR, "selftest_echo.js"), json.dumps(payload)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        echoed = json.loads(proc.stdout.decode("utf-8")) if proc.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        echoed = None
    checks["argument_passing"] = {"ok": echoed == payload and not os.path.exists(marker), "round_trip_exact": echoed == payload,
                                  "injection_marker_created": os.path.exists(marker), "cases": len(payload)}
    if os.path.exists(marker):
        os.unlink(marker)

    # Reminders goes through EventKit, which needs its OWN grant (Full Access to Reminders for the helper's binary), separate from the
    # Automation grant osascript uses for Notes. The binary reports every state other than "granted" as an error naming that grant.
    checks["platform"]["reminders_binary"] = os.access(EVENTKIT_BIN, os.X_OK)
    checks["platform"]["maps_binary"] = os.access(MAPS_BIN, os.X_OK)
    t0 = time.time()
    ok, result, error = run_op("reminder_lists", {}, timeout=60)
    checks["reminders_access"] = {"ok": ok, "seconds": round(time.time() - t0, 2)}
    if ok:
        checks["reminders_access"]["lists"] = len(result)
    else:
        checks["reminders_access"].update(error=error, status=_access_status(error), needs=REMINDERS_GRANT)

    # Reading reminders: one live read of the active reminders in every list. Only counts and timings, never names or content.
    t0 = time.time()
    ok, result, error = run_op("reminders_list", {"limit": 200}, timeout=60) if checks["reminders_access"]["ok"] else (False, None, "skipped: no Reminders access")
    checks["reminders_read"] = {"ok": ok, "seconds": round(time.time() - t0, 2)}
    if ok:
        checks["reminders_read"]["active"] = len(result["reminders"])
    else:
        checks["reminders_read"]["error"] = error

    # Notes is optional: it only matters if the server has ENABLE_NOTES on, so a refusal here is reported but does not fail the self-test.
    t0 = time.time()
    ok, result, error = run_op("note_folders", {}, timeout=60)
    checks["notes_access"] = {"ok": True, "available": ok, "seconds": round(time.time() - t0, 2)}
    if ok:
        checks["notes_access"]["folders"] = len(result)
    else:
        checks["notes_access"]["note"] = "Notes is not available yet (%s). Only needed if you enable Reminders AND Notes on the server." % error

    try:
        cfg = load_config(cfg_path)
        t0 = time.time()
        status, data = request(cfg, "GET", "/bridge/ping", timeout=15)
        checks["server"] = {"ok": status == 200, "http_status": status, "seconds": round(time.time() - t0, 2)}
        if status == 401:
            checks["server"]["error"] = "the server rejected the token"
    except (HelperError, OSError, ssl.SSLError, http.client.HTTPException) as e:
        checks["server"] = {"ok": False, "error": explain(e)}
    report["overall"] = "pass" if all(c["ok"] for c in checks.values()) else "fail"
    print(json.dumps(report, indent=2))
    return 0 if report["overall"] == "pass" else 1


def _access_status(error):
    """Which Reminders permission state an EventKit failure reports (the binary's wording), for the self-test report."""
    for phrase, status in (("not yet asked", "notDetermined"), ("denied", "denied"), ("device policy", "restricted"), ("write-only", "writeOnly")):
        if phrase in (error or ""):
            return status
    return "unknown"


def selftest_write():
    """Round-trips a temporary reminder and a temporary note through the real operations, then removes them. Touches your data, so it is
    opt-in. It also proves that a reminder id in the old JXA form ("x-apple-reminder://...") still resolves."""
    stamp = "icloud-mac-helper selftest %d" % int(time.time())
    steps = []
    rid = nid = None

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), **({"detail": detail} if detail else {})})
        return ok

    try:
        ok, r, e = run_op("reminder_create", {"title": stamp, "notes": "temporary, safe to delete", "due": "2099-01-01"}, 120)
        if step("reminder_create", ok, None if ok else e):
            rid = r["id"]
            ok, r2, e = run_op("reminders_list", {"list_id": r["list_id"], "query": stamp}, 120)
            step("reminders_list finds it", ok and any(x["id"] == rid for x in r2["reminders"]), None if ok else e)
            ok, r2, e = run_op("reminder_update", {"id": rid, "title": stamp + " (edited)", "priority": 1}, 120)
            step("reminder_update", ok, None if ok else e)
            ok, r2, e = run_op("reminder_update", {"id": "x-apple-reminder://" + rid, "notes": "edited through an old-style id"}, 120)
            step("reminder_update with an old-style id", ok and r2["id"] == rid, None if ok else e)
            ok, r2, e = run_op("reminder_complete", {"id": rid}, 120)
            step("reminder_complete", ok, None if ok else e)
    finally:
        if rid:
            ok, r2, e = run_op("reminder_delete", {"id": rid}, 120)
            step("reminder_delete (cleanup)", ok, None if ok else e)

    # 0.5.0: a scratch list holding a repeating reminder with two alerts, completed and listed as done, then list renamed and deleted
    lid = None
    try:
        ok, lst, e = run_op("reminder_list_create", {"name": stamp + " list"}, 120)
        if step("reminder_list_create", ok, None if ok else e):
            lid = lst["id"]
            ok, r, e = run_op("reminder_create", {"title": stamp + " weekly", "list_id": lid, "due": "2099-01-05T09:00:00",
                                                  "repeat": "FREQ=WEEKLY;BYDAY=MO;COUNT=4", "alerts_before": "1440,30"}, 120)
            step("reminder_create with repeat and alerts",
                 ok and r.get("repeat", "").startswith("FREQ=WEEKLY") and len(r.get("alerts", [])) >= 2, None if ok else e)
            if ok:
                ok, r2, e = run_op("reminder_update", {"id": r["id"], "clear_repeat": True, "alerts_before": "", "alerts_at": ""}, 120)
                step("reminder_update clears repeat and alerts", ok and "repeat" not in r2, None if ok else e)
                ok, r2, e = run_op("reminder_complete", {"id": r["id"]}, 120)
                ok, r2, e = run_op("reminders_list", {"list_id": lid, "completed": "only"}, 120)
                step("reminders_list completed=only finds it", ok and any(x["id"] == r["id"] and x.get("completed_at") for x in r2["reminders"]),
                     None if ok else e)
            ok, r2, e = run_op("reminder_list_update", {"list_id": lid, "name": stamp + " list renamed"}, 120)
            step("reminder_list_update", ok, None if ok else e)
            ok, r2, e = run_op("reminder_list_delete", {"list_id": lid, "name": stamp + " list renamed", "preview": True}, 120)
            step("reminder_list_delete preview counts the completed reminder", ok and r2.get("reminders") == 1, None if ok else e)
            ok, r2, e = run_op("reminder_list_delete", {"list_id": lid, "name": stamp + " list renamed"}, 120)
            step("reminder_list_delete refuses a list that holds reminders", not ok and "holds" in (e or ""), e if ok else None)
            ok, r2, e = run_op("reminder_list_delete", {"list_id": lid, "name": stamp + " list renamed", "delete_reminders": True, "expected": 2}, 120)
            step("reminder_list_delete refuses when the count differs from the confirmed one", not ok and "holds" in (e or ""), e if ok else None)
    finally:
        if lid:
            name = stamp + " list renamed"
            ok, r2, e = run_op("reminder_list_delete", {"list_id": lid, "name": name, "delete_reminders": True}, 120)
            if not ok:
                ok, r2, e = run_op("reminder_list_delete", {"list_id": lid, "name": stamp + " list", "delete_reminders": True}, 120)
            step("reminder_list_delete (cleanup)", ok, None if ok else e)

    try:
        ok, r, e = run_op("note_create", {"title": stamp, "body": "temporary, safe to delete <b>& \"quotes\"</b>"}, timeout=60)
        if step("note_create", ok, None if ok else e):
            nid = r["id"]
            ok, r, e = run_op("note_read", {"id": nid}, timeout=60)
            step("note_read round trip", ok and stamp in json.dumps(r), None if ok else e)
    finally:
        if nid:
            try:
                proc = subprocess.run([OSASCRIPT, "-l", "JavaScript", os.path.join(OPS_DIR, "selftest_note_delete.js"), json.dumps({"id": nid})],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                step("note delete (cleanup)", proc.returncode == 0, None if proc.returncode == 0 else proc.stderr.decode("utf-8", "replace")[:200])
            except subprocess.TimeoutExpired:
                step("note delete (cleanup)", False, "timed out; delete the note called '%s' by hand" % stamp)
    overall = "pass" if all(s["ok"] for s in steps) else "fail"
    print(json.dumps({"helper_version": VERSION, "steps": steps, "overall": overall}, indent=2))
    return 0 if overall == "pass" else 1


# ---------------------------------------------------------------------------
# Running the self-tests the way the LaunchAgent runs
#
# macOS attributes a privacy grant to the RESPONSIBLE process. Started from Terminal, the EventKit binary and osascript are judged as
# Terminal: the prompt names Terminal and the grant does nothing for the LaunchAgent. Started by launchd, the EventKit binary is judged as
# itself (verified on macOS 27), so the prompt names "iCloud Mac Helper (Reminders)" and the grant is the one the agent uses. So a
# self-test started anywhere but launchd re-runs itself as a one-shot launchd job and prints that job's report.
# ---------------------------------------------------------------------------
AGENT_LABEL = "local.icloud-mac-helper"              # the LaunchAgent install.sh creates
PROBE_LABEL = AGENT_LABEL + ".selftest"               # the one-shot job the self-tests run in
IN_LAUNCHD = "ICLOUD_MAC_HELPER_IN_LAUNCHD"


def _agent_python():
    """The interpreter the LaunchAgent uses, so the self-test is judged exactly as the agent is (install.sh passes it before the agent exists)."""
    if os.environ.get("ICLOUD_MAC_HELPER_PYTHON"):
        return os.environ["ICLOUD_MAC_HELPER_PYTHON"]
    try:
        with open(os.path.expanduser("~/Library/LaunchAgents/%s.plist" % AGENT_LABEL), "rb") as f:
            return plistlib.load(f)["ProgramArguments"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError, plistlib.InvalidFileException):
        return "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def run_in_launchd(helper_args, timeout=300):
    """Run this helper once as a launchd job in the login session; returns (exit code, the job's stdout)."""
    uid = os.getuid()
    work = tempfile.mkdtemp(prefix="icloud-mac-helper-selftest-")
    out, err = os.path.join(work, "result.json"), os.path.join(work, "stderr.log")
    plist = os.path.join(work, PROBE_LABEL + ".plist")
    with open(plist, "wb") as f:
        plistlib.dump({"Label": PROBE_LABEL, "ProgramArguments": [_agent_python(), os.path.abspath(__file__)] + list(helper_args) + ["--agent-out", out],
                       "EnvironmentVariables": {IN_LAUNCHD: "1"}, "RunAtLoad": True, "StandardErrorPath": err}, f)
    target = "gui/%d/%s" % (uid, PROBE_LABEL)
    subprocess.run([LAUNCHCTL, "bootout", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        boot = subprocess.run([LAUNCHCTL, "bootstrap", "gui/%d" % uid, plist], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if boot.returncode != 0:
            raise HelperError("could not start the self-test through launchd: %s" % (boot.stderr.decode("utf-8", "replace").strip() or boot.returncode))
        deadline = time.time() + timeout
        while not os.path.exists(out):
            state = subprocess.run([LAUNCHCTL, "print", target], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
            if re.search(r"last exit code = -?\d", state) and not os.path.exists(out):     # it ran and exited without a report
                try:
                    with open(err, errors="replace") as f:
                        detail = f.read().strip()[-300:]
                except OSError:
                    detail = ""
                raise HelperError("the self-test stopped without a report: %s" % (detail or "no output"))
            if time.time() > deadline:
                raise HelperError("the self-test did not finish within %ds" % timeout)
            time.sleep(0.25)
        with open(out) as f:
            res = json.load(f)
        return res["exit"], res["stdout"]
    finally:
        subprocess.run([LAUNCHCTL, "bootout", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="icloud-mac-helper")
    ap.add_argument("--config", help="path to the config file")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--run", action="store_true", help="run until stopped (what the LaunchAgent does)")
    g.add_argument("--selftest", action="store_true", help="check this Mac and the connection, print a JSON report")
    g.add_argument("--selftest-write", action="store_true", help="also create, edit and delete a temporary reminder and note (touches your data)")
    g.add_argument("--selftest-imessage-send", metavar="HANDLE",
                   help="send one iMessage 'icloud-mac-helper self-test' to HANDLE (your own address), so macOS asks once for "
                        "permission to control Messages")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--agent-out", help=argparse.SUPPRESS)          # internal: where a launchd-run self-test leaves its report
    a = ap.parse_args()
    if a.version:
        print(VERSION)
        return 0
    if a.selftest_imessage_send:
        if platform.system() == "Darwin" and os.environ.get(IN_LAUNCHD) != "1":
            try:
                code, text = run_in_launchd(["--selftest-imessage-send", a.selftest_imessage_send])
            except HelperError as e:
                print(json.dumps({"helper_version": VERSION, "overall": "fail", "error": str(e)}, indent=2))
                return 1
            sys.stdout.write(text)
            return code
        ok, r, e = run_op("imessage_send", {"handle": a.selftest_imessage_send, "text": "icloud-mac-helper self-test"}, 60)
        report = json.dumps({"helper_version": VERSION, "imessage_send": r if ok else {"error": e}, "overall": "pass" if ok else "fail"}, indent=2)
        if a.agent_out:
            with open(a.agent_out + ".tmp", "w") as f:
                json.dump({"exit": 0 if ok else 1, "stdout": report + "\n"}, f)
            os.replace(a.agent_out + ".tmp", a.agent_out)
        else:
            print(report)
        return 0 if ok else 1
    if a.selftest or a.selftest_write:
        if platform.system() == "Darwin" and os.environ.get(IN_LAUNCHD) != "1":
            # The job gets launchd's environment, not this one: pass the config path as resolved HERE.
            config = a.config or os.environ.get("ICLOUD_MAC_HELPER_CONFIG") or DEFAULT_CONFIG
            args = ["--selftest" if a.selftest else "--selftest-write", "--config", config]
            try:
                code, text = run_in_launchd(args)
            except HelperError as e:
                print(json.dumps({"helper_version": VERSION, "overall": "fail", "error": str(e)}, indent=2))
                return 1
            sys.stdout.write(text)
            return code
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf) if a.agent_out else contextlib.nullcontext():
            code = selftest(a.config) if a.selftest else selftest_write()
        if a.agent_out:
            with open(a.agent_out + ".tmp", "w") as f:
                json.dump({"exit": code, "stdout": buf.getvalue()}, f)
            os.replace(a.agent_out + ".tmp", a.agent_out)
        return code
    if a.run:
        run_forever(a.config)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
