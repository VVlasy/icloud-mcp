"""Reusing logged-in IMAP connections: fewer logins, never CLOSE, and nothing reused whose state is unknown."""
import dataclasses
import threading

import pytest

import icloud_mcp.mail as mail_mod
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailError, MailService


class FakeImaplib:
    def __init__(self):
        self.state = "AUTH"
        self.is_readonly = False                         # imaplib records whether the last SELECT was an EXAMINE


class FakeClient:
    made = []

    def __init__(self, *a, **kw):
        self._imap = FakeImaplib()
        self.calls = []
        self.alive = True
        self.caps = {b"UNSELECT"}
        FakeClient.made.append(self)

    def login(self, *a):
        self.calls.append("login")

    def logout(self):
        self.calls.append("logout")

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


def test_a_retried_read_pools_the_new_session_and_logs_out_the_dead_one(svc):
    dead = use(svc)
    dead.alive = False                                                      # died while idle, noticed only by the next command
    assert [f["name"] for f in svc.list_folders()] == ["INBOX", "Sent Messages", "Deleted Messages"]
    new = FakeClient.made[-1]
    assert new is not dead and "logout" in dead.calls and "logout" not in new.calls and [p[0] for p in svc._pool] == [new]


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
    assert fresh is not c and "noop" in c.calls and "logout" in c.calls


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
