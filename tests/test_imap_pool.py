"""Reusing logged-in IMAP connections: fewer logins, never CLOSE, and nothing reused whose state is unknown."""
import dataclasses
import errno
import imaplib
import threading

import pytest

import icloud_mcp.mail as mail_mod
from icloud_mcp import config
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailError, MailService


class FakeImaplib:
    def __init__(self):
        self.state = "AUTH"
        self.is_readonly = False                         # imaplib records whether the last SELECT was an EXAMINE


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, seconds):
        self.timeouts.append(seconds)


class FakeClient:
    made = []

    def __init__(self, *a, **kw):
        self._imap = FakeImaplib()
        self.kw = kw
        self.calls = []
        self.alive = True
        self.noop_error = None                           # raised by NOOP: a timeout (dead path) or an EOF (one dead session)
        self.caps = {b"UNSELECT"}
        self.sock = FakeSocket()
        FakeClient.made.append(self)

    def socket(self):
        return self.sock

    def login(self, *a):
        self.calls.append("login")

    def logout(self):
        self.calls.append("logout")

    def shutdown(self):                                  # closes the socket: no LOGOUT, and never CLOSE
        self.calls.append("shutdown")

    def select_folder(self, name, readonly=False):
        self.calls.append(f"{'examine' if readonly else 'select'} {name}")
        self._imap.state, self._imap.is_readonly = "SELECTED", readonly
        return {b"UIDVALIDITY": 1, b"UIDNEXT": 2, b"EXISTS": 1, **({b"HIGHESTMODSEQ": 5} if "enable" in self.calls else {})}

    def unselect_folder(self):
        self.calls.append("unselect")
        self._imap.state = "AUTH"

    def close_folder(self):                              # must never be called: it expunges every \Deleted message
        raise AssertionError("CLOSE used")

    def has_capability(self, cap):
        return cap.encode() in self.caps

    def enable(self, *caps):
        if self._imap.state != "AUTH":                   # like imapclient: ENABLE is illegal with a folder open
            raise RuntimeError("ENABLE command illegal in state SELECTED")
        self.calls.append("enable")

    def list_folders(self):
        self.calls.append("list")
        if not self.alive:
            raise OSError("connection reset")
        return [((b"\\HasNoChildren",), b"/", "INBOX"), ((b"\\HasNoChildren", b"\\sent"), b"/", "Sent Messages"),
                ((b"\\HasNoChildren",), b"/", "Deleted Messages")]

    def find_special_folder(self, flag):
        self.calls.append("find_special")
        return None

    def folder_status(self, name, what):
        self.calls.append(f"status {name}")
        return {b"MESSAGES": 1, b"UNSEEN": 0}

    def search(self, crit, charset=None):
        self.calls.append("search")
        return []

    def noop(self):
        self.calls.append("noop")
        if self.noop_error is not None:
            raise self.noop_error
        if not self.alive:
            raise OSError("connection reset")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    FakeClient.made = []
    monkeypatch.setattr(mail_mod, "IMAPClient", FakeClient)
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16).items():
        monkeypatch.setenv(k, v)
    return MailService(Settings.from_env())


def use(svc, readonly=False, **kw):
    with svc.imap(**kw) as c:
        c.select_folder("INBOX", readonly=readonly)
        return c


def test_a_connection_is_reused_and_left_with_no_folder_selected(svc):
    first = use(svc)
    second = use(svc)
    assert first is second and len(FakeClient.made) == 1
    assert first.calls.count("login") == 1 and first.calls.count("unselect") == 2 and "logout" not in first.calls


def test_a_folder_opened_read_only_is_left_open_but_a_read_write_one_is_unselected(svc):
    c = use(svc, readonly=True)
    assert "unselect" not in c.calls and svc._pool[0][0] is c              # EXAMINE: nothing to expunge, no round trip
    assert use(svc) is c and c.calls.count("unselect") == 1                 # SELECT: unselected before it goes back


def test_changes_on_a_connection_left_in_examine_still_enables_condstore(svc):
    c = use(svc, readonly=True)
    assert svc.changes("INBOX")["first_call"]                              # the fake reports HIGHESTMODSEQ only once enabled
    assert c.calls[2:] == ["unselect", "status INBOX", "enable", "examine INBOX", "search"]   # no HIGHESTMODSEQ in STATUS: EXAMINE
    svc.list_folders()
    assert c.calls[7:10] == ["unselect", "list", "status INBOX"]           # STATUS never names the open folder


def test_a_retried_read_pools_the_new_session_and_closes_the_dead_one(svc):
    dead = use(svc)
    dead.alive = False                                                      # died while idle, noticed only by the next command
    assert [f["name"] for f in svc.list_folders()] == ["INBOX", "Sent Messages", "Deleted Messages"]
    new = FakeClient.made[-1]
    assert new is not dead and "logout" not in new.calls and [p[0] for p in svc._pool] == [new]
    assert "shutdown" in dead.calls and "logout" not in dead.calls          # a dead socket gets no LOGOUT to wait on


def test_special_folders_come_from_the_cached_list_at_no_extra_cost(svc):
    with svc.imap() as c:
        svc._list(c)
        before = list(c.calls)
        assert svc.resolve_folder(c, "sent") == "Sent Messages"             # by its special-use flag (any case)
        assert svc.resolve_folder(c, "trash") == "Deleted Messages"         # by name: no flag on this server
        assert c.calls == before                                            # no LIST, no find_special_folder
        with pytest.raises(MailError, match="Could not locate"):
            svc.resolve_folder(c, "archive")
        assert c.calls == before + ["find_special"]                         # only a miss asks the server itself
    svc._folder_cache["trash"] = "Old"
    svc._forget_folder("Old")
    assert "trash" not in svc._folder_cache and svc._folder_cache["sent"] == "Sent Messages"


def test_a_queued_send_borrows_no_connection_and_keeps_every_gate(svc, monkeypatch):
    monkeypatch.setattr(MailService, "imap", lambda self, **kw: pytest.fail("a queued send opened IMAP"))
    svc.s = dataclasses.replace(svc.s, send_allowlist=("@example.org",), max_recipients=2)
    with pytest.raises(MailError, match="SEND_ALLOWLIST"):
        svc.send(to="someone@example.com", subject="Hi", body="Hello")
    with pytest.raises(MailError, match="Too many recipients"):
        svc.send(to=["a@example.org", "b@example.org", "c@example.org"], subject="Hi", body="Hello")
    r = svc.send(to="a@example.org", subject="Hi", body="Hello")
    assert r["status"] == "queued_for_owner_approval" and not r["sent"] and len(svc.outbox.pending()) == 1 and not FakeClient.made


def test_a_direct_send_without_approval_keeps_every_gate(svc, monkeypatch):
    """With approval off the send skips the outbox, never the gates: ALLOW_SEND, the allowlist and the recipient cap."""
    sent = []
    monkeypatch.setattr(MailService, "_smtp_send", lambda self, msg, recipients: sent.append(recipients) or {})
    svc.s = dataclasses.replace(svc.s, require_approval=False, save_sent_copy=False, send_allowlist=("@example.org",), max_recipients=2)
    with pytest.raises(MailError, match="SEND_ALLOWLIST"):
        svc.send(to="someone@example.com", subject="Hi", body="Hello")
    with pytest.raises(MailError, match="Too many recipients"):
        svc.send(to=["a@example.org", "b@example.org", "c@example.org"], subject="Hi", body="Hello")
    assert sent == []
    r = svc.send(to="a@example.org", subject="Hi", body="Hello")
    assert r["status"] == "sent" and sent == [["a@example.org"]]
    svc.s = dataclasses.replace(svc.s, allow_send=False)
    with pytest.raises(MailError, match="ALLOW_SEND=false"):
        svc.send(to="a@example.org", subject="Hi", body="Hello")
    assert len(sent) == 1


def test_a_failed_call_never_returns_its_connection_to_the_pool(svc):
    with pytest.raises(RuntimeError):
        with svc.imap() as c:
            c.select_folder("INBOX")
            raise RuntimeError("boom halfway")
    assert "logout" in c.calls
    assert use(svc) is not c and len(FakeClient.made) == 2


def test_a_session_that_died_while_idle_is_replaced(svc, monkeypatch):
    c = use(svc)
    c.alive = False
    clock = [1000.0]
    monkeypatch.setattr(mail_mod.time, "monotonic", lambda: clock[0])
    svc._pool = [(c, 1000.0 - 45)]                          # idle for 45 s: checked with NOOP, which fails
    fresh = use(svc)
    assert fresh is not c and "noop" in c.calls and "shutdown" in c.calls and "logout" not in c.calls


def test_connections_idle_too_long_are_closed_not_reused(svc, monkeypatch):
    c = use(svc)
    monkeypatch.setattr(mail_mod.time, "monotonic", lambda: 10_000.0)
    svc._pool = [(c, 10_000.0 - svc.s.imap_idle_seconds - 1)]
    assert use(svc) is not c and "logout" in c.calls and "noop" not in c.calls


def test_a_server_without_unselect_gets_no_reuse(svc):
    with svc.imap() as c:
        c.caps = set()
        c.select_folder("INBOX")
    assert "logout" in c.calls and svc._pool == []


def test_pool_size_zero_and_fresh_always_log_in(svc):
    fresh = use(svc, fresh=True)
    assert "logout" in fresh.calls and svc._pool == []
    svc.s = dataclasses.replace(svc.s, imap_pool_size=0)
    a, b = use(svc), use(svc)
    assert a is not b and "logout" in a.calls and "logout" in b.calls


def test_parallel_calls_never_share_a_connection_and_the_pool_stays_capped(svc):
    held, gate = [], threading.Barrier(4)

    def work():
        with svc.imap() as c:
            gate.wait(5)
            held.append(c)
            c.select_folder("INBOX")
    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(5) for t in threads]
    assert len({id(c) for c in held}) == 4                     # four calls at once, four connections
    assert len(svc._pool) == svc.s.imap_pool_size == 3          # only three kept (the default), the rest logged out
    assert sum("logout" in c.calls for c in held) == 1         # four minus the three kept


def test_health_proves_a_real_login_and_close_pool_logs_everything_out(svc):
    pooled = use(svc)
    svc.health = MailService.health.__get__(svc)
    with svc.imap(fresh=True) as c:
        assert c is not pooled
    svc.close_pool()
    assert "logout" in pooled.calls and svc._pool == []


def test_login_failure_is_a_clear_mail_error(svc, monkeypatch):
    def bad_login(self, *a):
        raise OSError("AUTHENTICATIONFAILED")
    monkeypatch.setattr(FakeClient, "login", bad_login)
    with pytest.raises(MailError, match="login failed"):
        use(svc)


def test_settings_are_read_and_bounded(monkeypatch, svc):
    monkeypatch.setenv("IMAP_POOL_SIZE", "50")
    monkeypatch.setenv("IMAP_IDLE_SECONDS", "5")
    s = Settings.from_env()
    assert s.imap_pool_size == 8 and s.imap_idle_seconds == 30


# ------------------------------------------------------------------------------------------------ dead networks fail fast
def pooled(svc, n, idle, monkeypatch):
    """n logged-in sessions in the pool, each idle for `idle` seconds (the clock stands still at 1000)."""
    svc._ticking = True                                     # the tests drive the keep-alive themselves
    monkeypatch.setattr(mail_mod.time, "monotonic", lambda: 1000.0)
    clients = [FakeClient() for _ in range(n)]
    svc._pool = [(c, 1000.0 - idle) for c in clients]
    return clients


def test_timeouts_are_split_and_a_retried_read_fits_in_the_default_tool_timeout(svc, monkeypatch):
    monkeypatch.delenv("TOOL_TIMEOUT_SECONDS", raising=False)
    budget = Settings.from_env().tool_timeout
    connect, read, probe = config.CONNECT_TIMEOUT, config.READ_TIMEOUT, config.PROBE_TIMEOUT
    use(svc)
    assert FakeClient.made[0].kw["timeout"] == mail_mod.SocketTimeout(connect=connect, read=read)
    assert read >= 25                                       # per read, not per request: a large fetch is not cut short
    assert connect + read < budget                          # a call on a new connection (never retried)
    assert read + connect + read < budget                   # a dead pooled connection, then the one retry on a new login
    assert probe + connect + read < budget                  # a probe that times out, then a new login


@pytest.mark.parametrize("error", [TimeoutError("timed out"), OSError(errno.EHOSTUNREACH, "No route to host")])
def test_a_probe_that_times_out_closes_every_idle_session_without_logout(svc, monkeypatch, error):
    a, b, c = pooled(svc, 3, 45, monkeypatch)
    c.noop_error = error                                    # the network path is gone, not just this session
    fresh = use(svc)
    assert fresh not in (a, b, c) and fresh.calls[0] == "login" and [p[0] for p in svc._pool] == [fresh]
    assert all("shutdown" in x.calls and "logout" not in x.calls for x in (a, b, c))
    assert "noop" not in a.calls + b.calls                  # no one waits on the others' probes as well


def test_an_eof_on_a_probe_discards_only_that_session_and_the_read_timeout_is_restored(svc, monkeypatch):
    a, b = pooled(svc, 2, 45, monkeypatch)
    b.noop_error = imaplib.IMAP4.abort("socket error: EOF")  # this session expired; the path is fine
    assert use(svc) is a
    assert "shutdown" in b.calls and "logout" not in b.calls
    assert a.calls[0] == "noop" and "shutdown" not in a.calls and svc._pool[0][0] is a
    assert a.sock.timeouts == [config.PROBE_TIMEOUT, config.READ_TIMEOUT]    # a short probe, then the normal read timeout


def test_the_keepalive_probe_is_short_and_a_timeout_closes_the_whole_idle_pool(svc, monkeypatch):
    due = mail_mod._IMAP_PING_SECONDS + 1
    a, b, c = pooled(svc, 3, due, monkeypatch)
    svc._last_activity = 1000.0
    svc._keepalive(1000.0)
    assert [p[0] for p in svc._pool] == [a, b, c]
    assert all(x.sock.timeouts == [config.PROBE_TIMEOUT, config.READ_TIMEOUT] for x in (a, b, c))
    svc._pool = [(x, 1000.0 - due) for x in (a, b, c)]
    a.noop_error = TimeoutError("timed out")
    svc._keepalive(1000.0)
    assert svc._pool == [] and all("shutdown" in x.calls and "logout" not in x.calls for x in (a, b, c))
    assert b.calls.count("noop") == c.calls.count("noop") == 1        # not probed again after the path died


def test_a_healthy_session_closed_for_age_is_logged_out_with_a_short_timeout(svc, monkeypatch):
    a, b = pooled(svc, 2, svc.s.imap_idle_seconds + 1, monkeypatch)

    def stuck():
        raise TimeoutError("timed out")
    a.logout = stuck
    fresh = use(svc)
    assert fresh not in (a, b)
    assert b.calls == ["logout"] and b.sock.timeouts == [config.LOGOUT_TIMEOUT]
    assert a.calls == ["shutdown"] and a.sock.timeouts == [config.LOGOUT_TIMEOUT]   # imaplib leaves the socket open otherwise
