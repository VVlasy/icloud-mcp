"""The tools and parameters that replace hand-written agent rules: icloud_get_time, needs_reply and starting_within_minutes,
conflict and duplicate detection on create, add/remove_attendees, people_only / unanswered_only / since_hours, mail_list_awaiting_reply,
layout_warnings, prefilled booking request_ids and add_emails."""
import asyncio
import contextlib
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage

import caldav
import pytest

import icloud_mcp.cal as cal_mod
import icloud_mcp.mail as mail_mod
from icloud_mcp.cal import CalendarError, CalendarService
from icloud_mcp.config import Settings
from icloud_mcp.contacts import ContactsError, _append_vcard_items
from icloud_mcp.extract import extract
from icloud_mcp.mail import MailService, layout_warnings
from icloud_mcp.server import create_server


class NoTicker:
    def add(self, fn):
        pass


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, DEFAULT_TIMEZONE="UTC",
                     OWNER_ADDRESSES="me.alias@example.net", ALLOW_CALENDAR_INVITES="true").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cal_mod, "TICKER", NoTicker())
    monkeypatch.setattr(mail_mod, "TICKER", NoTicker())
    return Settings.from_env()


def call(mcp, tool, args):
    return json.loads(asyncio.run(mcp.call_tool(tool, args)).content[0].text)


def test_icloud_now_gives_the_owners_date_and_time(s):
    mcp, _ = create_server(dataclasses.replace(s, enable_mail=False, enable_contacts=False))
    now = call(mcp, "icloud_get_time", {})
    assert now["timezone"] == "UTC" and now["date"] == datetime.now(timezone.utc).date().isoformat() and now["weekday"]


# ------------------------------------------------------------------------------------------------ calendar
def vevent(uid, start, hours=1, summary="Thing", extra=""):
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:{start:%Y%m%dT%H%M%S}Z\r\n"
            f"DTEND:{start + timedelta(hours=hours):%Y%m%dT%H%M%S}Z\r\nSUMMARY:{summary}\r\n{extra}END:VEVENT\r\n")


def wrap(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"


class Cal:
    def __init__(self, name, events):
        self.name, self.url, self.events_ics, self.saved = name, f"https://caldav.example/1/{name.lower()}/", events, []

    def search(self, **kw):
        return [type("O", (), {"data": wrap(e)})() for e in self.events_ics]

    def save_event(self, ical):
        self.saved.append(ical)

    def event_by_url(self, url):
        raise caldav.error.NotFoundError("404")

    def get_supported_components(self):
        return ["VEVENT"]


@pytest.fixture
def calsvc(s, monkeypatch):
    soon = datetime.now(timezone.utc).replace(microsecond=0, second=0) + timedelta(minutes=30)
    day = datetime(2016, 10, 5, 9, 0)        # a fixed day that is never today, so events relative to now never meet it
    work = Cal("Work", [
        vevent("w1", day, summary="Review", extra="X-APPLE-TRAVEL-DURATION;VALUE=DURATION:PT30M\r\n"),
        vevent("inv", day + timedelta(hours=5), summary="Invite",
               extra="ORGANIZER:mailto:anna@example.org\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:ME.ALIAS@example.net\r\n"),
        vevent("dec", day + timedelta(hours=3), summary="Declined",
               extra="ORGANIZER:mailto:anna@example.org\r\nATTENDEE;PARTSTAT=DECLINED:mailto:me@icloud.com\r\n"),
        vevent("mine", day + timedelta(hours=7), summary="Mine",
               extra="ORGANIZER:mailto:me@icloud.com\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:me@icloud.com\r\n"),
        vevent("soon", soon, summary="Soon"),
    ])
    personal = Cal("Personal", [vevent("p1", day + timedelta(hours=1, minutes=30), summary="Dentist")])
    cals = [personal, work]
    svc = CalendarService(s)
    monkeypatch.setattr(svc, "_principal", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(svc, "_pick", lambda p, name: [c for c in cals if not name or c.name == name])
    monkeypatch.setattr(svc, "_cal_name", lambda c: c.name)
    return svc, {c.name: c for c in cals}, day


def test_needs_reply_finds_invitations_to_any_owner_address_only(calsvc):
    svc, _, day = calsvc
    got = svc.list_events("2016-10-05", "2016-10-05", needs_reply=True)
    assert [e["uid"] for e in got["events"]] == ["inv"]           # an alias counts; declined and self-organised do not
    assert "now" in got


def test_starting_within_minutes_is_a_window_from_now(calsvc):
    svc, _, _ = calsvc
    got = svc.list_events(starting_within_minutes=60)
    assert [e["uid"] for e in got["events"]] == ["soon"]
    with pytest.raises(CalendarError, match="starting_within_minutes"):
        svc.list_events()


def test_create_reports_conflicts_counting_travel_and_duplicates(calsvc):
    svc, cals, day = calsvc
    out = svc.create_event(summary="Call", start=(day - timedelta(minutes=20)).isoformat(), end=day.isoformat(), calendar="Personal")
    assert out["created"] and [c["uid"] for c in out["conflicts"]] == ["w1"]     # inside the review's 30 minutes of travel
    out = svc.create_event(summary="Lunch", start=(day + timedelta(hours=3)).isoformat(), calendar="Personal")
    assert out["conflicts"] == []                                                 # a declined event blocks nothing
    dup = svc.create_event(summary="dentist ", start=(day + timedelta(hours=1, minutes=30)).isoformat(), calendar="Personal",
                           on_duplicate="refuse")
    assert dup["created"] is False and dup["possible_duplicate"]["uid"] == "p1"
    before = len(cals["Personal"].saved)
    refused = svc.create_event(summary="Overlap", start=(day + timedelta(minutes=15)).isoformat(), calendar="Personal",
                               on_conflict="refuse")
    assert refused["created"] is False and refused["conflicts"] and len(cals["Personal"].saved) == before


def test_add_and_remove_one_guest_keep_the_rest_and_respect_the_gate(s, monkeypatch):
    series = wrap(vevent("g1", datetime(2026, 10, 5, 9), extra="ORGANIZER:mailto:me@icloud.com\r\n"
                         "ATTENDEE;PARTSTAT=ACCEPTED:mailto:anna@example.org\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:ben@example.org\r\n"))
    saved = []

    class Obj:
        data = series

        def save(self, **kw):
            saved.append(self.data)
    svc = CalendarService(s)
    obj = Obj()
    monkeypatch.setattr(svc, "_principal", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(svc, "_find", lambda p, uid, cal: (type("C", (), {"name": "Work", "url": "u"})(), obj))
    monkeypatch.setattr(svc, "_cal_name", lambda c: "Work")
    monkeypatch.setattr(svc, "_delivery", lambda *a, **k: [])
    svc.update_event("g1", add_attendees=["carol@example.org"], remove_attendees=["ben@example.org"])
    text = saved[-1] if saved else obj.data
    assert "anna@example.org" in text and "PARTSTAT=ACCEPTED" in text and "carol@example.org" in text and "ben@example.org" not in text
    with pytest.raises(CalendarError, match="not both"):
        svc.update_event("g1", attendees=["x@example.org"], add_attendees=["y@example.org"])
    blocked = CalendarService(dataclasses.replace(s, allow_calendar_invites=False))
    monkeypatch.setattr(blocked, "_principal", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(blocked, "_find", lambda p, uid, cal: (None, Obj()))
    with pytest.raises(CalendarError, match="ALLOW_CALENDAR_INVITES"):
        blocked.update_event("g1", remove_attendees=["ben@example.org"])


# ------------------------------------------------------------------------------------------------ mail
def msg(n, frm, to, when, subject="Hi", bulk=False, refs=""):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, when.strftime("%a, %d %b %Y %H:%M:%S +0000")
    m["Message-ID"] = f"<m{n}@example.org>"
    if refs:
        m["In-Reply-To"] = refs
    if bulk:
        m["List-Unsubscribe"] = "<https://news.example.com/u>"
    m.set_content("text")
    return m.as_bytes(policy=policy.SMTP)


class MultiIMAP:
    def __init__(self, folders):
        self.folders, self.cur, self.searches = folders, None, []

    def select_folder(self, name, readonly=False):
        self.cur = name
        return {b"UIDVALIDITY": 1}

    def list_folders(self):
        return [((), b"/", n) for n in self.folders]

    def search(self, crit, charset=None):
        self.searches.append(list(crit))
        return sorted(self.folders[self.cur])

    def fetch(self, uids, items):
        out = {}
        for u in uids:
            raw = self.folders[self.cur][u]
            out[u] = {b"FLAGS": (), b"RFC822.SIZE": len(raw), b"INTERNALDATE": None, b"BODYSTRUCTURE": (),
                      b"BODY[HEADER.FIELDS (X)]": raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n"}
        return out

    def find_special_folder(self, flag):
        return {b"\\Sent": "Sent Messages", b"\\Drafts": "Drafts", b"\\Trash": "Deleted Messages", b"\\Junk": "Junk"}.get(flag)


def mailsvc(s, monkeypatch, folders):
    fake = MultiIMAP(folders)

    @contextlib.contextmanager
    def imap(self, fresh=False):
        yield fake
    monkeypatch.setattr(MailService, "imap", imap)
    return MailService(s), fake


def test_awaiting_reply_lists_only_real_people_who_have_not_answered(s, monkeypatch):
    now = datetime.now(timezone.utc)
    d = lambda days: now - timedelta(days=days)                                  # noqa: E731
    folders = {
        "Sent Messages": {1: msg(1, "me@icloud.com", "Anna <anna@example.org>", d(10), "Invoice?"),
                          2: msg(2, "me@icloud.com", "ben@example.net", d(9), "Lunch"),
                          3: msg(3, "me@icloud.com", "noreply@shop.example.com", d(8), "Order"),
                          4: msg(4, "me@icloud.com", "me.alias@example.net", d(7), "Note to self"),
                          5: msg(5, "me@icloud.com", "carol@example.com", d(6), "Contract"),
                          6: msg(6, "me@icloud.com", "Anna <anna@example.org>", d(5), "Invoice? (follow-up)")},
        "INBOX": {10: msg(10, "ben@example.net", "me@icloud.com", d(8), "Re: Lunch"),
                  11: msg(11, "assistant@example.com", "me@icloud.com", d(4), "Re: Contract", refs="<m5@example.org>"),
                  12: msg(12, "anna@example.org", "me@icloud.com", d(20), "Earlier")},
        "Archive": {},
    }
    m, fake = mailsvc(s, monkeypatch, folders)
    out = m.awaiting_reply(days=30)
    assert [w["subject"] for w in out["awaiting"]] == ["Invoice? (follow-up)"]     # the follow-up replaced the first message
    assert out["awaiting"][0]["days_waiting"] == 5 and out["complete"] is True
    assert out["awaiting"][0]["last_seen_from_them"].startswith(d(20).date().isoformat())
    assert len(fake.searches) == 3                                              # Sent, INBOX, Archive: one search each


def test_people_only_unanswered_and_since_hours(s, monkeypatch):
    now = datetime.now(timezone.utc)
    folders = {"INBOX": {1: msg(1, "news@news.example.com", "me@icloud.com", now - timedelta(hours=2), bulk=True),
                         2: msg(2, "anna@example.org", "me@icloud.com", now - timedelta(hours=3)),
                         3: msg(3, "ben@example.net", "me@icloud.com", now - timedelta(hours=30))}}
    m, fake = mailsvc(s, monkeypatch, folders)
    got = m.search("INBOX", people_only=True, since_hours=24, unanswered_only=True)
    assert [x["uid"] for x in got["messages"]] == [2] and got["total_matches"] == 1
    assert "UNANSWERED" in fake.searches[-1] and "SINCE" in fake.searches[-1]
    with pytest.raises(Exception, match="since_hours"):
        m.search("INBOX", since_hours=0)


def test_layout_warnings_never_change_the_body():
    assert layout_warnings("Hi Anna,\n\nThanks.\n\nSam") == []
    assert len(layout_warnings("<p>Hi</p>\r\n" + "word " * 200)) == 3


def test_bookings_carry_a_hashed_request_id():
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "trains@example.com", "Ticket", "<secret-ticket-42@rail.example>"
    m.set_content('<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"TrainReservation",'
                  '"reservationNumber":"X1","reservationFor":{"@type":"TrainTrip","departureTime":"2026-10-05T09:00:00+02:00",'
                  '"arrivalTime":"2026-10-05T11:00:00+02:00","departureStation":{"name":"A"},"arrivalStation":{"name":"B"}}}'
                  '</script></html>', subtype="html")
    items = extract(m.as_bytes(policy=policy.SMTP))["items"]
    rid = items[0]["calendar_event"]["request_id"]
    assert rid.startswith("booking:") and rid.endswith(":0") and "secret" not in rid and len(rid) < 200


def test_add_emails_keeps_labels_and_skips_duplicates():
    raw = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u1\r\nFN:Ann\r\nitem1.EMAIL;TYPE=INTERNET:ann@example.org\r\n"
           "item1.X-ABLabel:work\r\nEND:VCARD\r\n")
    new, added = _append_vcard_items(raw, ["ANN@example.org", "ann.home@example.org"], ["+31 20 123 4567"])
    assert added == ["ann.home@example.org", "+31 20 123 4567"] and "item1.X-ABLabel:work" in new
    with pytest.raises(ContactsError):
        _append_vcard_items(raw, ["not an address"])
