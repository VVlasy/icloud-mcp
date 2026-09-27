"""Fewer round trips and smaller results: calendars read in parallel, limit before conversion, compact event lists, capped
descriptions, parallel all-folder search, lighter mail results, and short untrusted-content notices."""
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from email import policy

import caldav
import pytest

import caldav_fakes
import icloud_mcp.cal as cal_mod
import icloud_mcp.mail as mail_mod
import icloud_mcp.server as server_mod
from icloud_mcp.cal import CalendarService
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailService, declared_size, extract_bodies, list_attachments


class NoTicker:
    def add(self, fn):
        pass


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, DEFAULT_TIMEZONE="Europe/Berlin").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cal_mod, "TICKER", NoTicker())
    monkeypatch.setattr(mail_mod, "TICKER", NoTicker())
    return Settings.from_env()


# ------------------------------------------------------------------------------------------------ calendar
def ics(uid, start, summary, description=""):
    end = start + timedelta(hours=1)
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:{start:%Y%m%dT%H%M%S}Z\r\nDTEND:{end:%Y%m%dT%H%M%S}Z\r\n"
            f"SUMMARY:{summary}\r\n" + (f"DESCRIPTION:{description}\r\n" if description else "") +
            "ATTENDEE:mailto:anna@example.org\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


BASE = datetime(2026, 10, 5, 8, 0)
DATA = {  # calendar name -> [(uid, hour offset)]
    "Personal": [("p1", 3), ("p2", 30)],
    "Work": [("w1", 1), ("w2", 26), ("w3", 50)],
    "Health": [("h1", 5)],
}


class Obj:
    def __init__(self, data, client):
        self.data, self.client = data, client

    def load(self):
        return self


class Cal:
    def __init__(self, client, name):
        self.client, self.name, self.url = client, name, f"https://caldav.example/1/calendars/{name.lower()}/"

    def get_supported_components(self):
        return ["VEVENT"]

    def search(self, **kw):
        self.client.log.append(("search", self.name, threading.current_thread().name))
        return [Obj(ics(uid, BASE + timedelta(hours=h), f"{self.name} {uid}"), self.client) for uid, h in DATA[self.name]]

    def event_by_url(self, url):
        self.client.log.append(("get", self.name))
        uid = url.rsplit("/", 1)[-1][:-4]
        hit = next((h for u, h in DATA[self.name] if u == uid), None)
        if hit is None:
            raise caldav.error.NotFoundError("404")
        return Obj(ics(uid, BASE + timedelta(hours=hit), uid), self.client)

    def events(self):
        self.client.log.append(("scan", self.name))
        return []


class Principal:
    def __init__(self, client):
        self.client, self.url = client, "https://caldav.example/1/principal/"


class DAV:
    made = []
    log = []

    def __init__(self, **kw):
        DAV.made.append(self)
        self.log = DAV.log

    def principal(self, url=None):
        return Principal(self)

    def propfind(self, url, props=None, depth=0):
        home = "https://caldav.example/1/calendars/"
        if depth == 1:
            return caldav_fakes.calendar_list(home, [Cal(self, n) for n in DATA])
        return caldav_fakes.home_set(url, home) if props else caldav_fakes.Reply([])

    def calendar(self, url, name=None):
        return Cal(self, next(n for n in DATA if url.endswith(f"/{n.lower()}/")))

    def close(self):
        pass


@pytest.fixture
def cal(s, monkeypatch):
    DAV.made, DAV.log = [], []
    monkeypatch.setattr(cal_mod.caldav, "DAVClient", DAV)
    return CalendarService(s)


def test_calendars_are_read_in_parallel_on_spare_connections_and_keep_their_order(cal):
    cal.prewarm(2)                                                         # what the server does at start
    opened = len(DAV.made)
    DAV.log.clear()
    out = cal.list_events("2026-10-05", "2026-10-08")
    assert [e["uid"] for e in out["events"]] == ["w1", "p1", "h1", "w2", "p2", "w3"]      # merged by start time
    threads = [t for kind, _, t in DAV.log if kind == "search"]
    assert len(threads) == 3 and any(t.startswith("icloud-caldav") for t in threads)
    assert len(DAV.made) == opened                                         # parallel, but not one new connection


def test_without_spare_connections_calendars_are_read_on_the_calls_own(cal):
    cal.list_events("2026-10-05", "2026-10-08")
    assert len(DAV.made) == 1 and all(t == "MainThread" for kind, _, t in DAV.log if kind == "search")


def test_a_failing_helper_hands_its_calendar_back(cal, monkeypatch):
    cal.prewarm(2)
    helpers = set(DAV.made[1:])
    real = Cal.search

    def flaky(self, **kw):
        if self.client in helpers:
            raise ConnectionResetError("reset")
        return real(self, **kw)
    monkeypatch.setattr(Cal, "search", flaky)
    out = cal.list_events("2026-10-05", "2026-10-08")
    assert len(out["events"]) == 6                                          # every calendar still read, on the call's connection


def test_limit_is_applied_before_events_are_converted(cal, monkeypatch):
    converted = []
    real = cal_mod.event_to_dict
    monkeypatch.setattr(cal_mod, "event_to_dict", lambda comp, name: converted.append(1) or real(comp, name))
    out = cal.list_events("2026-10-05", "2026-10-08", limit=2)
    assert out["total"] == 6 and len(out["events"]) == 2 and len(converted) == 2


def test_summary_fields_and_capped_descriptions(cal, monkeypatch):
    compact = cal.list_events("2026-10-05", "2026-10-08", fields="summary")["events"][0]
    assert set(compact) <= {"uid", "calendar", "summary", "start", "end", "all_day", "location", "status", "has_attendees"}
    assert {"uid", "calendar", "summary", "start", "end", "all_day", "has_attendees"} <= set(compact)   # empty ones left out
    assert compact["has_attendees"] is True
    long = "x" * 5000
    monkeypatch.setitem(DATA, "Health", [("h1", 5)])
    real_ics = ics
    monkeypatch.setattr(__import__(__name__), "ics", lambda uid, start, summary, description="": real_ics(uid, start, summary, long))
    out = cal.list_events("2026-10-05", "2026-10-08")
    e = out["events"][0]
    assert len(e["description"]) == cal_mod._DESCRIPTION_CHARS and e["description_truncated"] is True
    assert "calendar_get_event" in out["hint"]


def test_find_asks_every_calendar_at_once_then_reads_on_its_own_connection(cal):
    cal.prewarm(2)
    DAV.log.clear()
    got = cal.get_event("h1")
    assert got["uid"] == "h1" and got["calendar"] == "Health"
    gets = [x for x in DAV.log if x[0] == "get"]
    assert len(gets) == 4 and not [x for x in DAV.log if x[0] == "scan"]    # three at once, then one on this connection
    DAV.log.clear()
    cal.get_event("h1")                                                      # remembered: one GET
    assert [x for x in DAV.log if x[0] == "get"] == [("get", "Health")]


# ------------------------------------------------------------------------------------------------ mail
def raw_message(i, attachment=b""):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Anna <anna@example.org>", "me@icloud.com", f"S{i}"
    m["Date"] = f"Mon, {5 + i:02d} Oct 2026 10:00:00 +0000"
    m["Message-ID"] = f"<m{i}@example.org>"
    m.set_content("hello")
    if attachment:
        m.add_attachment(attachment, maintype="application", subtype="pdf", filename="a.pdf")
    return m.as_bytes(policy=policy.SMTP)


class FolderIMAP:
    folders = {"INBOX": {1: 0, 2: 3}, "Archive": {1: 1}, "Broken": {}, "Sent Messages": {4: 2}}
    made = []

    def __init__(self, *a, **kw):
        self.cur, self._imap = None, type("S", (), {"state": "AUTH"})()
        FolderIMAP.made.append(self)

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

    def list_folders(self):
        return [((b"\\HasNoChildren",), b"/", n) for n in self.folders] + [((b"\\Noselect",), b"/", "[Gmail]")]

    def select_folder(self, name, readonly=False):
        if name == "Broken":
            raise OSError("cannot open")
        self.cur, self._imap.state = name, "SELECTED"
        return {b"UIDVALIDITY": 7}

    def search(self, crit, charset=None):
        return list(self.folders[self.cur])

    def fetch(self, uids, items):
        out = {}
        for u in uids:
            raw = raw_message(self.folders[self.cur][u])
            head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            out[u] = {b"FLAGS": (b"\\Seen",), b"RFC822.SIZE": len(raw), b"INTERNALDATE": None, b"BODYSTRUCTURE": (),
                      b"BODY[HEADER.FIELDS (X)]": head}
        return out


def test_all_folders_search_runs_folders_in_parallel_and_names_the_unreadable_one(s, monkeypatch):
    FolderIMAP.made = []
    monkeypatch.setattr(mail_mod, "IMAPClient", FolderIMAP)
    out = MailService(s).search(all_folders=True, limit=10)
    assert [m["subject"] for m in out["messages"]] == ["S3", "S2", "S1", "S0"]             # newest first across folders
    assert out["matches_per_folder"] == {"INBOX": 2, "Archive": 1, "Sent Messages": 1}
    assert out["folders_not_searched"] == ["Broken"]
    assert all(m["uidvalidity"] == 7 for m in out["messages"])                            # kept per message: folders differ
    assert "flags" not in out["messages"][0] and out["messages"][0]["unread"] is False    # booleans only
    assert 2 <= len(FolderIMAP.made) <= 1 + s.imap_pool_size


def test_attachment_size_without_decoding_and_bounded_html(monkeypatch):
    pdf = bytes(range(256)) * 41 + b"xy"                                                  # 10,498 bytes, not a multiple of 3
    msg = mail_mod.email.message_from_bytes(raw_message(0, pdf), policy=policy.default)
    part = mail_mod.iter_attachment_parts(msg)[0]
    assert declared_size(part) == len(pdf) == list_attachments(msg)[0]["size"]
    seen = []
    monkeypatch.setattr(mail_mod, "html_to_text", lambda h: seen.append(len(h)) or "text")
    m = EmailMessage()
    m.set_content("<html><body>" + "<p>paragraph</p>" * 10000 + "</body></html>", subtype="html")
    extract_bodies(m, html_chars=4000)
    assert seen and seen[0] <= 4000 and m.get_body().get_content()[: seen[0]].endswith(">")


def test_notices_are_one_short_line_and_the_rules_cover_every_area(s):
    from icloud_mcp import contacts, imessage

    for text in (mail_mod.UNTRUSTED_NOTICE, cal_mod.UNTRUSTED_NOTICE, contacts.UNTRUSTED_NOTICE,
                 server_mod._DRIVE_NOTICE, server_mod._MAC_NOTICE, server_mod._MAPS_NOTICE, imessage.NOTICE):
        assert len(text) <= 90 and "\n" not in text and "instructions" in text
    rules = server_mod.build_instructions(s)
    assert all(w in rules for w in ("Email, message, calendar, contact, reminder, note and file text", "untrusted DATA"))


def test_a_forwarded_message_says_so():
    from icloud_mcp.mail import _flag_view
    assert _flag_view((b"\\Seen", b"$Forwarded"))["forwarded"] is True
    assert _flag_view((b"\\Seen",))["forwarded"] is False and _flag_view(("$forwarded",))["forwarded"] is True
