"""Warm connections: the CalDAV pool, keep-alives, the folder-list cache, SMTP and CardDAV reuse, warm-up and the tool workers.
Every test counts what crosses the (fake) network, so "reused" means one login or one connection, provably."""
import asyncio
import dataclasses
import smtplib
import threading
from email.message import EmailMessage

import httpx
import pytest

import caldav_fakes
import icloud_mcp.cal as cal_mod
import icloud_mcp.mail as mail_mod
from icloud_mcp.cal import CalendarError, CalendarService
from icloud_mcp.config import Settings
from icloud_mcp.contacts import ContactsService
from icloud_mcp.mail import MailError, MailService
from icloud_mcp.safety import clean_deep


class NoTicker:
    def add(self, fn):
        pass


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cal_mod, "TICKER", NoTicker())          # tests drive the keep-alive by hand
    monkeypatch.setattr(mail_mod, "TICKER", NoTicker())
    return Settings.from_env()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------------------------------------ CalDAV pool
class FakeCal:
    def __init__(self, client, name, url=None):
        self.client, self.name, self.url = client, name, url or f"https://caldav.example/1/calendars/{name.lower()}/"


class FakePrincipal:
    def __init__(self, client, url=None):
        self.client, self.url = client, url or "https://caldav.example/1/principal/"

    def calendars(self):
        self.client.net.append("calendars")
        if self.client.dead:
            raise ConnectionResetError("reset by peer")
        return [FakeCal(self.client, "Personal"), FakeCal(self.client, "Work")]


class FakeDAV:
    """Logs what crosses the network: 'principal' (discovery), 'home-set', 'calendars' (the Depth:1 list) and 'propfind' (a
    Depth:0 warm-up or keep-alive ping, whose URL goes to `pinged`)."""
    made = []
    home = "https://caldav.example/1/calendars/"
    hrefs = ["https://caldav.example/1/calendars/personal/", "https://caldav.example/1/calendars/work/"]

    def __init__(self, **kw):
        self.kw, self.net, self.pinged, self.dead, self.closed = kw, ["connect"], [], False, False
        FakeDAV.made.append(self)

    def principal(self, url=None):
        if url is None:
            self.net.append("principal")
        return FakePrincipal(self, url)

    def propfind(self, url, props=None, depth=0):
        kind = "calendars" if depth == 1 else "home-set" if props else "propfind"
        self.net.append(kind)
        if kind == "propfind":
            self.pinged.append(url)
        if self.dead:
            raise ConnectionResetError("reset by peer")
        if kind == "calendars":
            return caldav_fakes.calendar_list(url, [FakeCal(self, n, h) for n, h in zip(("Personal", "Work"), FakeDAV.hrefs)])
        return caldav_fakes.home_set(url, FakeDAV.home) if kind == "home-set" else caldav_fakes.Reply([])

    def calendar(self, url, name=None):
        return FakeCal(self, name, url if "://" in url else "https://caldav.example" + url)

    def close(self):
        self.closed = True


@pytest.fixture
def cal(s, monkeypatch):
    FakeDAV.made = []
    monkeypatch.setattr(cal_mod.caldav, "DAVClient", FakeDAV)
    return CalendarService(s)


def test_one_connection_serves_calls_from_different_threads(cal):
    names = []
    for _ in range(3):
        t = threading.Thread(target=lambda: names.append([c["name"] for c in cal.list_calendars()]))
        t.start()
        t.join(5)
    assert names == [["Personal", "Work"]] * 3
    assert len(FakeDAV.made) == 1                                   # reuse no longer depends on which worker thread runs the call
    assert FakeDAV.made[0].net.count("calendars") == 1              # and the calendar list is kept on that connection


def test_parallel_calls_get_their_own_connections_and_the_pool_is_capped(cal):
    cal.s = dataclasses.replace(cal.s, caldav_pool_size=2)
    gate, done = threading.Barrier(3), []

    def work():
        with cal._principal():
            gate.wait(5)
        done.append(1)
    threads = [threading.Thread(target=work) for _ in range(3)]
    [t.start() for t in threads]
    [t.join(5) for t in threads]
    assert len(done) == 3 and len(FakeDAV.made) == 3               # never two calls on one connection
    assert len(cal._pool) == 2 and sum(c.closed for c in FakeDAV.made) == 1


def test_a_dead_pooled_connection_is_retried_once_on_a_fresh_one(cal):
    cal.list_calendars()
    first = FakeDAV.made[0]
    first.dead = True
    cal._pool[0].cals = cal._cal_list = None                        # force a request on the dead socket
    assert [c["name"] for c in cal.list_calendars()] == ["Personal", "Work"]
    assert len(FakeDAV.made) == 2 and first.closed                  # the dead one is closed, never pooled again
    assert [c.client for c in cal._pool] == [FakeDAV.made[1]]


class Writer(CalendarService):
    @cal_mod._reconnecting
    def write(self):
        with self._principal() as p:
            self._tl.mutated = True                                 # the PUT went out: a transport error must not replay it
            p.calendars()

    @cal_mod._reconnecting
    def read(self):
        with self._principal() as p:
            return p.calendars()


def test_a_write_is_never_retried_and_a_fresh_failure_is_real(s, monkeypatch):
    FakeDAV.made = []
    monkeypatch.setattr(cal_mod.caldav, "DAVClient", FakeDAV)
    w = Writer(s)
    w.read()
    FakeDAV.made[0].dead = True
    with pytest.raises(ConnectionResetError):
        w.write()
    assert len(FakeDAV.made) == 1                                   # no second connection: nothing was replayed
    real = FakeDAV.__init__

    def dead_init(self, **kw):
        real(self, **kw)
        self.dead = True
    monkeypatch.setattr(FakeDAV, "__init__", dead_init)
    with pytest.raises(CalendarError, match="Could not connect") as err:
        w.read()                                                    # a brand-new connection that fails is not retried
    assert len(FakeDAV.made) == 2 and isinstance(err.value.__cause__, ConnectionResetError)   # it failed at its sign-in request


def test_idle_or_old_connections_are_not_handed_out(cal, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cal_mod.time, "monotonic", clock)
    cal.list_calendars()
    clock.t += cal_mod._IDLE_TTL_SECONDS + 1
    cal.list_calendars()
    assert len(FakeDAV.made) == 2 and FakeDAV.made[0].closed


def test_keepalive_pings_while_in_use_replaces_old_and_drops_after_the_window(cal, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cal_mod.time, "monotonic", clock)
    cal.list_calendars()
    conn = FakeDAV.made[0]
    clock.t += 5
    cal._keepalive(clock.t)
    assert conn.net.count("propfind") == 0                          # not idle long enough to need a ping
    for _ in range(4):                                              # 40 s of idle time, kept warm the whole way
        clock.t += cal_mod._PING_AFTER_SECONDS
        cal._keepalive(clock.t)
    assert conn.net.count("propfind") == 4
    cal.list_calendars()
    assert len(FakeDAV.made) == 1                                   # still the same connection: no cold start
    clock.t += cal_mod._MAX_AGE_SECONDS
    cal._keepalive(clock.t)
    assert len(FakeDAV.made) == 2 and conn.closed                   # too old: replaced in the background, not at call time
    clock.t += cal.s.caldav_keepalive_seconds + 1
    cal._keepalive(clock.t)
    assert cal._pool == [] and FakeDAV.made[1].closed               # unused for the whole window: everything closed


def test_keepalive_off_and_a_dead_ping(cal, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cal_mod.time, "monotonic", clock)
    cal.list_calendars()
    FakeDAV.made[0].dead = True
    clock.t += cal_mod._PING_AFTER_SECONDS
    cal._keepalive(clock.t)
    assert cal._pool == [] and FakeDAV.made[0].closed               # a failed ping takes the connection out
    cal.s = dataclasses.replace(cal.s, caldav_keepalive_seconds=0)
    cal.list_calendars()
    warmed = FakeDAV.made[1].net.count("propfind")                 # the new connection's one warm-up request
    clock.t += cal_mod._PING_AFTER_SECONDS
    cal._keepalive(clock.t)
    assert warmed == 1 and FakeDAV.made[1].net.count("propfind") == warmed


def test_the_calendar_list_expires_and_an_auth_failure_is_reported(cal, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cal_mod.time, "monotonic", clock)
    cal.list_calendars()
    clock.t += 5                                                   # still warm, so the same connection is used
    cal._cal_list = (cal._cal_list[0], clock.t - cal_mod._CALENDARS_SECONDS - 1, cal._cal_list[2])
    cal._pool[0].cals_at -= cal_mod._CALENDARS_SECONDS + 1
    cal.list_calendars()
    assert FakeDAV.made[0].net.count("calendars") == 2 and FakeDAV.made[0].net.count("home-set") == 1   # the home is kept

    def denied(self, *a, **kw):
        raise cal_mod.caldav.error.AuthorizationError("401")
    monkeypatch.setattr(FakeDAV, "principal", denied)
    monkeypatch.setattr(FakeDAV, "propfind", denied)                # a wrong password: the kept URLs' warm-up is refused too
    cal.close_pool()
    with pytest.raises(CalendarError, match="authentication failed"):
        cal.list_calendars()
    assert cal._principal_url is None                               # forgotten; the next connection discovers afresh


def test_the_calendar_list_is_shared_but_calendar_objects_never_are(cal):
    cal.list_calendars()                                            # what prewarm does, without its worker threads: the list once,
    for spare in [cal._open(), cal._open()]:                        # then two spares
        cal._checkin(spare)
    assert [d.net for d in FakeDAV.made] == [["connect", "principal", "home-set", "calendars"],
                                             ["connect", "propfind"], ["connect", "propfind"]]   # a spare: one warm-up request
    seen = []
    for conn in list(cal._pool):
        cal._tl.conn = conn
        try:
            seen.append(cal._event_calendars(conn.principal))
        finally:
            cal._tl.conn = None
    assert all(c.client is conn.client for conn, cals in zip(cal._pool, seen) for c in cals)   # each built on its own client
    assert sum(d.net.count("calendars") for d in FakeDAV.made) == 1                            # from the one list, no request


@pytest.mark.parametrize("hrefs, pinged", [
    (["https://p42.example.net/1/calendars/personal/"], "https://p42.example.net/1/calendars/"),   # calendars on the home host
    (["/1/calendars/personal/"], "https://caldav.example/1/principal/"),   # relative (caldav 3): on the client's host, as iCloud
])
def test_the_keepalive_pings_the_host_the_event_requests_use(cal, monkeypatch, hrefs, pinged):
    monkeypatch.setattr(FakeDAV, "home", "https://p42.example.net/1/calendars/")
    monkeypatch.setattr(FakeDAV, "hrefs", hrefs)
    clock = Clock()
    monkeypatch.setattr(cal_mod.time, "monotonic", clock)
    cal.list_calendars()
    clock.t += cal_mod._PING_AFTER_SECONDS
    cal._keepalive(clock.t)
    assert FakeDAV.made[0].pinged == [pinged]
    clock.t += cal_mod._MAX_AGE_SECONDS
    cal._keepalive(clock.t)                                         # too old: replaced, and the new one is warmed on the same host
    assert FakeDAV.made[1].net == ["connect", "propfind"] and cal_mod._host(FakeDAV.made[1].pinged[0]) == cal_mod._host(pinged)


def test_basic_auth_is_sent_up_front_only_to_icloud_over_https_or_to_loopback(s, cal):
    auth = lambda url, mode="auto": cal_mod._auth_type(dataclasses.replace(s, caldav_url=url, caldav_auth=mode))   # noqa: E731
    assert auth("https://caldav.icloud.com") == auth("https://p42-caldav.icloud.com:443/") == "basic"
    assert auth("http://127.0.0.1:5232") == auth("http://localhost:5232") == auth("http://[::1]:5232") == "basic"
    assert auth("https://dav.example.com") is None and auth("https://dav.example.com", "basic") == "basic"
    assert auth("http://caldav.icloud.com") is None and auth("http://dav.example.com", "basic") is None   # never over plain http
    assert auth("https://icloud.com.example.net") is None
    cal.list_calendars()
    assert FakeDAV.made[0].kw["auth_type"] == "basic"


# ------------------------------------------------------------------------------------------------ CalDAV round trips
@pytest.fixture
def dav(s, monkeypatch):
    """The real caldav client against an iCloud-shaped in-memory server: every count below is an HTTP request."""
    server = caldav_fakes.Server(password=s.app_password)

    class Client(cal_mod.caldav.DAVClient):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.session.request = server
    monkeypatch.setattr(cal_mod.caldav, "DAVClient", Client)
    return CalendarService(dataclasses.replace(s, default_timezone="Europe/Berlin")), server


def test_request_counts_per_calendar_operation(dav):
    svc, server = dav
    assert svc.list_calendars() == [{"name": "Calendar", "id": "home"}, {"name": "Work", "id": "work"}]
    assert server.log == [("PROPFIND", "caldav.icloud.com", ""), ("PROPFIND", "caldav.icloud.com", "/123/principal/"),
                          ("PROPFIND", "p42-caldav.icloud.com", "/123/calendars/")]   # no 401 first, no PROPFIND per calendar
    server.log.clear()
    made = svc.create_event(summary="Dentist", start="2026-10-05T10:00", end="2026-10-05T11:00", calendar="home")
    svc.get_event(made["uid"], calendar="Calendar")
    assert [m for m, *_ in server.log] == ["REPORT", "REPORT", "PUT", "GET"]            # get_event: one GET
    server.log.clear()
    svc.update_event(made["uid"], summary="Dentist (moved)")
    svc.delete_event(made["uid"])
    assert [m for m, *_ in server.log] == ["GET", "PUT", "GET", "DELETE"]              # create + update + delete: 7 in all
    assert server.events["home"] == {}


def test_a_new_connection_skips_discovery_and_a_moved_home_is_found_again(dav):
    svc, server = dav
    svc.list_calendars()
    server.log.clear()
    svc._checkin(svc._open())
    assert server.log == [("PROPFIND", "caldav.icloud.com", "/123/principal/")]       # one authenticated warm-up request
    server.missing.add(("p42-caldav.icloud.com", "/123/calendars/"))
    server.home_host = "p43-caldav.icloud.com"
    svc._calendars_changed()
    server.log.clear()
    assert [c["name"] for c in svc.list_calendars()] == ["Calendar", "Work"]
    assert [(h, p) for _, h, p in server.log] == [("p42-caldav.icloud.com", "/123/calendars/"),
                                                  ("caldav.icloud.com", "/123/principal/"), ("p43-caldav.icloud.com", "/123/calendars/")]


def test_a_wrong_password_is_still_an_authentication_error(dav):
    svc, server = dav
    svc.list_calendars()
    svc.close_pool()
    server.password = "changed"
    with pytest.raises(CalendarError, match="CalDAV authentication failed"):
        svc.list_calendars()
    assert all(host == "caldav.icloud.com" for _, host, _ in server.log[3:])


def test_get_tz_is_cached():
    assert cal_mod.get_tz("Europe/Berlin") is cal_mod.get_tz("Europe/Berlin")


# ------------------------------------------------------------------------------------------------ IMAP
class FakeIMAP:
    made = []

    def __init__(self, *a, **kw):
        self.calls, self.alive = [], True
        self._imap = type("S", (), {"state": "AUTH"})()
        FakeIMAP.made.append(self)

    def login(self, *a):
        self.calls.append("login")

    def logout(self):
        self.calls.append("logout")

    def noop(self):
        self.calls.append("noop")
        if not self.alive:
            raise OSError("reset")

    def has_capability(self, cap):
        return True

    def list_folders(self):
        self.calls.append("list")
        return [((b"\\HasNoChildren",), b"/", "INBOX"), ((b"\\Sent",), b"/", "Sent Messages")]

    def folder_status(self, name, what):
        return {b"MESSAGES": 1, b"UNSEEN": 0}

    def folder_exists(self, name):
        return False

    def create_folder(self, name):
        self.calls.append("create")


@pytest.fixture
def mail(s, monkeypatch):
    FakeIMAP.made = []
    monkeypatch.setattr(mail_mod, "IMAPClient", FakeIMAP)
    return MailService(s)


def test_default_pool_is_three_and_keepalive_noops_only_while_in_use(mail, monkeypatch):
    assert mail.s.imap_pool_size == 3
    clock = Clock()
    monkeypatch.setattr(mail_mod.time, "monotonic", clock)
    mail.list_folders()
    c = FakeIMAP.made[0]
    clock.t += 100
    mail._keepalive(clock.t)
    assert "noop" not in c.calls                                     # idle, but not yet five minutes
    clock.t += mail_mod._IMAP_PING_SECONDS
    mail._keepalive(clock.t)
    assert c.calls.count("noop") == 1 and len(mail._pool) == 1
    clock.t += mail.s.imap_idle_seconds + 1
    mail._keepalive(clock.t)
    assert mail._pool == [] and "logout" in c.calls                 # unused for IMAP_IDLE_SECONDS: logged out


def test_folder_list_is_cached_for_a_minute_and_cleared_by_create(mail, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(mail_mod.time, "monotonic", clock)
    mail.list_folders()
    mail.list_folders()
    assert FakeIMAP.made[0].calls.count("list") == 1
    mail.create_folder("Receipts")
    mail.list_folders()
    assert FakeIMAP.made[0].calls.count("list") == 2
    clock.t += mail_mod._FOLDERS_SECONDS + 1
    mail.list_folders()
    assert FakeIMAP.made[0].calls.count("list") == 3


# ------------------------------------------------------------------------------------------------ SMTP
class FakeSMTP:
    made = []

    def __init__(self, *a, **kw):
        self.calls, self.alive, self.refuse = [], True, False
        FakeSMTP.made.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, *a):
        self.calls.append("login")

    def noop(self):
        self.calls.append("noop")
        if not self.alive:
            raise smtplib.SMTPServerDisconnected("gone")
        return (250, b"OK")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.calls.append("send")
        if self.refuse:
            raise smtplib.SMTPDataError(554, b"rejected")
        return {}

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


def message():
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "me@icloud.com", "anna@example.org", "hi"
    m.set_content("hello")
    return m


def test_two_sends_share_one_smtp_login(mail, monkeypatch):
    FakeSMTP.made = []
    monkeypatch.setattr(mail_mod.smtplib, "SMTP", FakeSMTP)
    mail._smtp_send(message(), ["anna@example.org"])
    mail._smtp_send(message(), ["anna@example.org"])
    assert len(FakeSMTP.made) == 1 and FakeSMTP.made[0].calls.count("login") == 1
    assert FakeSMTP.made[0].calls.count("send") == 2 and "noop" in FakeSMTP.made[0].calls


def test_a_dead_or_idle_smtp_connection_is_replaced_and_a_failed_send_is_not_retried(mail, monkeypatch):
    FakeSMTP.made = []
    monkeypatch.setattr(mail_mod.smtplib, "SMTP", FakeSMTP)
    clock = Clock()
    monkeypatch.setattr(mail_mod.time, "monotonic", clock)
    mail._smtp_send(message(), ["anna@example.org"])
    FakeSMTP.made[0].alive = False
    mail._smtp_send(message(), ["anna@example.org"])
    assert len(FakeSMTP.made) == 2                                   # NOOP failed: a new login before sending
    clock.t += mail_mod._SMTP_IDLE_SECONDS + 1
    mail._smtp_send(message(), ["anna@example.org"])
    assert len(FakeSMTP.made) == 3 and "noop" not in FakeSMTP.made[1].calls[-3:]   # idle too long: not even pinged
    FakeSMTP.made[2].refuse = True
    with pytest.raises(MailError, match="SMTP send failed"):
        mail._smtp_send(message(), ["anna@example.org"])
    assert FakeSMTP.made[2].calls.count("send") == 2 and mail._smtp is None   # one attempt, then the connection is dropped


def test_read_only_never_opens_smtp(mail, monkeypatch):
    FakeSMTP.made = []
    monkeypatch.setattr(mail_mod.smtplib, "SMTP", FakeSMTP)
    ro = MailService(dataclasses.replace(mail.s, read_only=True, allow_send=False))
    with pytest.raises(MailError, match="disabled"):
        ro._deliver(FakeIMAP(), message(), draft=False)
    assert FakeSMTP.made == []


# ------------------------------------------------------------------------------------------------ CardDAV
def test_carddav_keeps_one_http_client_and_replaces_it_after_a_transport_error(s, monkeypatch):
    made = []
    real = httpx.Client

    def counting(*a, **kw):
        made.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "Client", counting)
    svc = ContactsService(dataclasses.replace(s, enable_contacts=True), transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with svc._client() as a:
        pass
    with svc._client() as b:
        pass
    assert a is b and len(made) == 1
    with pytest.raises(httpx.ConnectError):
        with svc._client():
            raise httpx.ConnectError("down")
    with svc._client() as c:
        pass
    assert c is not a and len(made) == 2


# ------------------------------------------------------------------------------------------------ server
def test_warmup_runs_every_area_and_never_raises(s, caplog):
    from icloud_mcp.server import start_warmup

    ran = []

    class M:
        _icloud_warmups = {"mail": lambda: ran.append("mail"), "calendar": lambda: 1 / 0, "contacts": lambda: ran.append("contacts")}
    t = start_warmup(M(), s)
    t.join(5)
    assert ran == ["mail", "contacts"] and "calendar failed (ZeroDivisionError)" in caplog.text
    assert start_warmup(M(), dataclasses.replace(s, warmup_on_start=False)) is None


def test_tool_workers_size_the_executor(s):
    import icloud_mcp.server as server_mod

    server_mod.create_server(dataclasses.replace(s, tool_workers=5))
    assert server_mod._executor._max_workers == 5
    names = asyncio.run(server_mod.create_server(s)[0].list_tools())
    assert names and server_mod._executor._max_workers == s.tool_workers == 8


def test_large_base64_passes_through_and_text_is_still_cleaned():
    big = "QUJD" * 100_000
    assert clean_deep({"data": big})["data"] is big
    assert clean_deep({"t": "a​b"})["t"] == "ab"
    mixed = "QUJD" * 100_000 + "​"
    assert clean_deep(mixed) == "QUJD" * 100_000
