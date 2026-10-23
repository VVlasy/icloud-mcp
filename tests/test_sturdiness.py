"""Sturdiness: one retry policy (once, only on a dead reused connection, never after a write), timeouts that name the slow step,
the health check as the one diagnostic, errors that end with a next step, and partial reads that say what they could not read."""
import ast
import asyncio
import contextlib
import dataclasses
import pathlib
import time
from datetime import datetime, timedelta, timezone

import caldav
import pytest

import icloud_mcp.cal as cal_mod
import icloud_mcp.mail as mail_mod
import icloud_mcp.server as server_mod
from icloud_mcp import callctx
from icloud_mcp.bridge import BridgeError, MacBridge
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailError, MailService

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "icloud_mcp"


class NoTicker:
    def add(self, fn):
        pass


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cal_mod, "TICKER", NoTicker())
    monkeypatch.setattr(mail_mod, "TICKER", NoTicker())
    return Settings.from_env()


# ------------------------------------------------------------------------------------------------ the retry policy
class Svc:
    def __init__(self, failures, reused=True, write=False):
        self._tl, self.calls, self.failures, self.reused, self.write = callctx.CallState(), [], failures, reused, write

    @callctx.retry_once_if_safe(lambda e: isinstance(e, OSError))
    def op(self):
        self.calls.append(self._tl.fresh)
        self._tl.reused = self.reused and not self._tl.fresh
        self._tl.fresh = False
        if self.write:
            self._tl.mutated = True
        if self.failures:
            self.failures -= 1
            raise ConnectionResetError("reset")
        return "ok"


def test_retry_once_only_for_a_dead_reused_connection_and_never_after_a_write():
    assert Svc(1).op() == "ok"                                                   # reused + transport: one retry, on a fresh one
    with pytest.raises(ConnectionResetError):
        Svc(2).op()                                                              # never twice
    with pytest.raises(ConnectionResetError):
        Svc(1, reused=False).op()                                                # a brand-new connection that fails is real
    with pytest.raises(ConnectionResetError):
        Svc(1, write=True).op()                                                  # a write went out: never replayed
    svc = Svc(1)
    svc.op()
    assert svc.calls == [False, True]                                            # the retry asked for a brand-new connection


class DeadOnce:
    made = []

    def __init__(self, *a, **kw):
        self.dead, self._imap = False, type("S", (), {"state": "AUTH"})()
        DeadOnce.made.append(self)

    def login(self, *a):
        pass

    def logout(self):
        pass

    def noop(self):
        pass

    def has_capability(self, c):
        return True

    def unselect_folder(self):
        self._imap.state = "AUTH"

    def select_folder(self, name, readonly=False):
        if self.dead:
            raise mail_mod.imaplib.IMAP4.abort("socket error: EOF")
        self._imap.state = "SELECTED"
        return {b"UIDVALIDITY": 3}

    def search(self, crit, charset=None):
        return []

    def fetch(self, uids, items):
        return {}

    def add_flags(self, *a, **k):
        self.dead = True                                                         # dies right after the write went out


def test_a_pooled_imap_connection_that_died_is_retried_once_on_a_new_login(s, monkeypatch):
    DeadOnce.made = []
    monkeypatch.setattr(mail_mod, "IMAPClient", DeadOnce)
    m = MailService(s)
    m.search("INBOX")
    DeadOnce.made[0].dead = True                                                 # the server dropped the pooled session
    assert m.search("INBOX")["complete"] is True and len(DeadOnce.made) == 2


# ------------------------------------------------------------------------------------------------ timeouts and health
def test_a_timeout_names_the_slow_step(s, monkeypatch):
    def slow(self, folder="INBOX", **kw):
        callctx.stage("IMAP SEARCH in Archive")
        time.sleep(1.5)
        return {}
    monkeypatch.setattr(MailService, "search", slow)
    mcp, _ = server_mod.create_server(dataclasses.replace(s, tool_timeout=1))
    with pytest.raises(Exception, match="slow step was: IMAP SEARCH in Archive"):
        asyncio.run(mcp.call_tool("mail_search_messages", {"folder": "Archive"}))


def test_health_reports_uptime_and_connection_state_before_checking(s, monkeypatch):
    monkeypatch.setattr(MailService, "health", lambda self: {"inbox_messages": 1})
    mcp, _ = server_mod.create_server(dataclasses.replace(s, enable_calendar=False, enable_contacts=False))
    import json
    out = json.loads(asyncio.run(mcp.call_tool("icloud_check_health", {})).content[0].text)
    assert out["ok"] and out["since_start_seconds"] >= 0
    assert out["areas"]["mail"]["connections"] == {"warm": False, "imap_kept": 0, "imap_pool_size": 3, "smtp_connected": False}


def test_the_default_timeout_is_a_minute(s):
    assert s.tool_timeout == 60


# ------------------------------------------------------------------------------------------------ errors
ERRORS = {"MailError", "CalendarError", "ContactsError", "BridgeError", "_MailError"}


def _message_end(node):
    """The literal text a raised message ends with, or None when it is built from variables only."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[-1], ast.Constant):
        return node.values[-1].value
    if isinstance(node, ast.BinOp):
        return _message_end(node.right)
    return None


def test_every_error_message_ends_with_a_full_stop():
    bad = []
    for path in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and getattr(node.exc.func, "id", "") in ERRORS and node.exc.args:
                end = _message_end(node.exc.args[0])
                if end is not None and not end.rstrip().endswith((".", "?", ")")):
                    bad.append(f"{path.name}:{node.lineno}: ...{end[-40:]!r}")
    assert not bad, "error messages without a full stop:\n" + "\n".join(bad)


def test_errors_point_at_the_next_step():
    import inspect
    for fn, needle in [(MailService._fetch_raw, "Run mail_search_messages again"), (MailService._attachment_file, "of mail_get_message"),
                       (cal_mod.CalendarService._find, "comes from calendar_list_events"),
                       (MailService.resolve_folder, "Call mail_list_folders"), (MailService._select, "Call mail_list_folders"),
                       (MailService._login, "Run icloud_check_health")]:
        assert needle in inspect.getsource(inspect.unwrap(fn)), (fn.__name__, needle)


def test_bulk_mistakes_are_ordinary_mail_errors():
    from icloud_mcp import mailbulk

    with pytest.raises(MailError, match="action must be one of"):
        mailbulk.bulk_action(None, "INBOX", "explode")


def test_the_macs_not_found_code_comes_with_what_to_do():
    b = MacBridge(timeout=5)
    import threading
    b.next_job({}, 0)
    got = []
    t = threading.Thread(target=lambda: got.append(b.next_job({}, 5)))
    t.start()
    errors = []

    def call():
        try:
            b.call("reminder_lists")
        except BridgeError as e:
            errors.append(str(e))
    c = threading.Thread(target=call)
    c.start()
    t.join(5)
    b.complete(got[0].id, False, None, "reminder not found (-1728)")
    c.join(5)
    assert "list again" in errors[0] and "reminders_list_reminders" in errors[0]


# ------------------------------------------------------------------------------------------------ partial reads
def test_a_calendar_that_cannot_be_read_is_named_not_hidden(s, monkeypatch):
    class Cal:
        def __init__(self, name):
            self.name, self.url = name, f"https://caldav.example/1/{name.lower()}/"

        def search(self, **kw):
            if self.name == "Broken":
                raise caldav.error.ReportError("500 Internal Server Error")
            return []

    cals = [Cal("Personal"), Cal("Broken"), Cal("Work")]
    svc = cal_mod.CalendarService(s)
    monkeypatch.setattr(svc, "_principal", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(svc, "_pick", lambda p, name: cals)
    monkeypatch.setattr(svc, "_cal_name", lambda c: c.name)
    day = (datetime.now(timezone.utc) + timedelta(days=7)).date()      # always ahead of now: free time is never looked for in the past
    out = svc.list_events(day.isoformat(), (day + timedelta(days=1)).isoformat())
    assert out["complete"] is False and out["not_read"] == ["Broken"] and "missing" in out["warning"]
    free = svc.find_free_time(f"{day}T09:00", f"{day + timedelta(days=1)}T17:00", 30)
    assert free["complete"] is False and free["not_read"] == ["Broken"] and "may not really be free" in free["warning"]


def test_the_bridge_gives_up_before_the_tool_timeout_so_its_message_arrives(s):
    on = dataclasses.replace(s, enable_reminders=True, enable_calendar=False, enable_contacts=False, bridge_token="t" * 40,
                             tool_timeout=8, bridge_job_timeout=60)
    mcp, _ = server_mod.create_server(on)
    bridge = mcp._icloud_bridge
    assert bridge.timeout == 3                                                   # min(60, 8 - 5)
    bridge.next_job({}, 0)                                                       # the Mac was seen, then never picks anything up
    with pytest.raises(Exception, match="did not pick up the request"):
        asyncio.run(mcp.call_tool("reminders_list_lists", {}))
