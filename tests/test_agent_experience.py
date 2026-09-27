"""What agents see and how forgiving the inputs are: 'create this event and invite Anna' must be one easy call."""
import asyncio
import contextlib
import dataclasses

import icalendar
import pytest

from icloud_mcp.cal import CalendarError, CalendarService, build_event, get_tz, parse_attendees
from icloud_mcp.config import Settings
from icloud_mcp.mailbulk import expand_uid_set
from icloud_mcp.server import build_instructions, create_server


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaabbbbccccdddd", MCP_PUBLIC_URL="https://mcp.example.com",
                     MCP_OWNER_PASSWORD="x" * 16, DATA_DIR=str(tmp_path), ICLOUD_DISPLAY_NAME="Alex", DEFAULT_TIMEZONE="Europe/Berlin",
                     DEFAULT_CALENDAR="Calendar", ALLOW_SEND="true", SEND_REQUIRES_APPROVAL="false", ALLOW_CALENDAR_INVITES="true").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


# ------------------------------------------------------------------ attendee input
def test_attendees_accept_the_forms_agents_actually_send():
    got = parse_attendees(["anna@example.org", "Bob Jones <bob@example.org>", "mailto:carol@example.org", "ANNA@example.org"])
    assert got == [("", "anna@example.org"), ("Bob Jones", "bob@example.org"), ("", "carol@example.org")]   # deduped, case-insensitive


@pytest.mark.parametrize("bad", ["Anna", "anna@", "anna@nowhere", "a@b.com, c@d.com", "", "<>"])
def test_attendees_reject_junk_with_a_helpful_error(bad):
    with pytest.raises(CalendarError, match="mail_search_messages"):
        parse_attendees([bad])


def test_attendee_name_and_rsvp_reach_the_ical():
    _, ical = build_event(summary="Lunch", start="2026-09-21T12:30", end=None, tz=get_tz("Europe/Berlin"), location=None, description=None,
                          rrule=None, attendees=["Anna <anna@example.org>"], alarms_minutes_before=None, url=None,
                          organizer_email="me@icloud.com", organizer_name="Alex")
    ev = icalendar.Calendar.from_ical(ical).walk("VEVENT")[0]
    att = ev.get("attendee")
    assert str(att) == "mailto:anna@example.org" and att.params["CN"] == "Anna" and att.params["RSVP"] == "TRUE"


# ------------------------------------------------------------------ default calendar + create result
class FakeCal:
    def __init__(self, name):
        self.name, self.url, self.saved = name, f"https://x/{name}/", []

    def get_supported_components(self):
        return ["VEVENT"]

    def get_display_name(self):
        return self.name

    def save_event(self, ical):
        self.saved.append(ical)

    def search(self, **kw):                           # create_event reads the calendar for clashes first
        return [type("Obj", (), {"data": d})() for d in self.saved]


def _svc(s, monkeypatch, names):
    cals = [FakeCal(n) for n in names]
    principal = type("P", (), {"calendars": lambda self: cals})()

    @contextlib.contextmanager
    def fake_principal(self):
        yield principal

    monkeypatch.setattr(CalendarService, "_principal", fake_principal)
    return CalendarService(s), cals


def test_new_events_go_to_the_default_calendar_not_whatever_is_listed_first(s, monkeypatch):
    svc, cals = _svc(s, monkeypatch, ["Work", "Personal", "Calendar"])
    assert svc.create_event(summary="x", start="2026-09-21T10:00")["calendar"] == "Calendar"
    svc, cals = _svc(dataclasses.replace(s, default_calendar="Personal"), monkeypatch, ["Work", "Personal", "Calendar"])
    assert svc.create_event(summary="x", start="2026-09-21T10:00")["calendar"] == "Personal"
    svc, cals = _svc(dataclasses.replace(s, default_calendar=""), monkeypatch, ["Work", "Calendar"])
    assert svc.create_event(summary="x", start="2026-09-21T10:00")["calendar"] == "Calendar"        # auto: 'Calendar'/'Home'
    svc, cals = _svc(dataclasses.replace(s, default_calendar=""), monkeypatch, ["Work", "Personal"])
    assert svc.create_event(summary="x", start="2026-09-21T10:00")["calendar"] == "Work"            # else the first
    assert svc.create_event(summary="x", start="2026-09-21T10:00", calendar="personal")["calendar"] == "Personal"   # explicit wins


def test_create_returns_the_event_and_who_was_invited(s, monkeypatch):
    svc, cals = _svc(s, monkeypatch, ["Calendar"])
    r = svc.create_event(summary="Lunch with Anna", start="2026-09-21T12:30", end="2026-09-21T13:30", location="Cafe X",
                         attendees=["Anna <anna@example.org>", "me@icloud.com"], alarms_minutes_before=[30], url="https://example.org")
    assert r["created"] and r["event"]["summary"] == "Lunch with Anna" and r["event"]["location"] == "Cafe X"
    assert r["event"]["alarms_minutes_before"] == [30] and r["event"]["url"] == "https://example.org/"  or r["event"]["url"] == "https://example.org"
    assert r["invited"] == ["anna@example.org"]                       # the owner is not listed as someone who was invited
    assert "separate email" in r["note"] and len(cals[0].saved) == 1


def test_bad_attendee_fails_before_anything_is_saved(s, monkeypatch):
    svc, cals = _svc(s, monkeypatch, ["Calendar"])
    with pytest.raises(CalendarError):
        svc.create_event(summary="x", start="2026-09-21T10:00", attendees=["Anna"])
    assert cals[0].saved == []


# ------------------------------------------------------------------ what agents are shown
def test_every_parameter_of_every_tool_has_a_description(s):
    async def go():
        mcp, _ = create_server(s)
        return await mcp.list_tools()
    tools = asyncio.run(go())
    assert len(tools) >= 18
    missing = [f"{t.name}.{k}" for t in tools for k, v in (getattr(t, "input_schema", None) or getattr(t, "inputSchema", {})).get("properties", {}).items()
               if not v.get("description")]
    assert missing == []


def test_instructions_tell_agents_who_the_owner_is_and_how_to_invite(s):
    text = build_instructions(s)
    assert "Alex <me@icloud.com>" in text and "Europe/Berlin" in text and "New events go to Calendar" in text
    assert "INVITING PEOPLE" in text and "contacts_search_contacts" in text and "ONE calendar_create_event call" in text
    assert "INVITING PEOPLE" not in build_instructions(dataclasses.replace(s, allow_calendar_invites=False))
    assert "CALENDAR:" not in build_instructions(dataclasses.replace(s, enable_calendar=False))


# ------------------------------------------------------------------ speed: don't re-ask iCloud what does not change
def test_calendar_facts_are_cached_between_calls_and_expire(s, monkeypatch):
    calls = {"components": 0}

    class CountingCal(FakeCal):
        def get_supported_components(self):
            calls["components"] += 1
            return ["VEVENT"]

    cals = [CountingCal("Calendar"), CountingCal("Work")]
    principal = type("P", (), {"calendars": lambda self: cals})()
    svc = CalendarService(s)
    for _ in range(3):
        assert [svc._cal_name(c) for c in svc._event_calendars(principal)] == ["Calendar", "Work"]
    assert calls["components"] == 2                               # once per calendar, not once per call
    import icloud_mcp.cal as calmod
    monkeypatch.setattr(calmod.time, "monotonic", lambda t0=calmod.time.monotonic(): t0 + 10_000)
    svc._event_calendars(principal)
    assert calls["components"] == 4                               # refreshed after the cache lifetime


# ------------------------------------------------------------------ mail: recipients are never silently dropped
from icloud_mcp.mail import MailError, MailService, parse_recipients


def test_recipients_accept_the_forms_agents_send():
    assert parse_recipients(["anna@example.org", "Bob Jones <bob@example.org>", "mailto:c@example.org"], "to") == [
        ("", "anna@example.org"), ("Bob Jones", "bob@example.org"), ("", "c@example.org")]
    assert [a for _, a in parse_recipients(["a@example.org, b@example.org"], "to")] == ["a@example.org", "b@example.org"]   # one string, two people
    assert parse_recipients(None, "cc") == [] and parse_recipients([], "bcc") == []


@pytest.mark.parametrize("bad", ["Bob", "anna@", "anna@nowhere", "a@example.org, Bob", "<>", "   "])
def test_bad_recipient_is_an_error_not_a_silent_drop(bad):
    with pytest.raises(MailError, match="mail_search_messages"):
        parse_recipients(["anna@example.org", bad], "to")


def test_send_reply_forward_fail_before_touching_the_server_on_a_bad_recipient(s):
    mail = MailService(s)                     # no IMAP/SMTP is reachable in tests: reaching the network would raise a different error
    with pytest.raises(MailError, match="'Bob' in 'to'"):
        mail.send(to=["anna@example.org", "Bob"], subject="x", body="y")
    with pytest.raises(MailError, match="'cc'"):
        mail.send(to=["anna@example.org"], cc=["nobody"], subject="x", body="y")
    with pytest.raises(MailError, match="'bcc'"):
        mail.send(to=["anna@example.org"], bcc=["nobody"], subject="x", body="y")


def test_instructions_carry_the_mail_workflow(s):
    text = build_instructions(s)
    assert "MAIL:" in text and "Answer with mail_reply_to_message (it keeps the thread" in text and "uidvalidity" in text
    assert "MAIL:" not in build_instructions(dataclasses.replace(s, enable_mail=False))


# ------------------------------------------------------------------ iCloud has no IMAP MOVE: move/delete must still work, safely
def uids_of(seq):
    """What the server reads from a uid argument: a uid set such as b"3:5,9", or a plain list."""
    return expand_uid_set(seq, 10 ** 6) if isinstance(seq, (bytes, str)) else list(seq)


class _MoveIMAP:
    def __init__(self, caps):
        self.caps, self.log = set(caps), []

    def has_capability(self, c):
        return c in self.caps

    def select_folder(self, f, readonly=False):
        self.log.append(("select", f))

    def move(self, uids, dst):
        self.log.append(("move", uids_of(uids), dst))

    def copy(self, uids, dst):
        self.log.append(("copy", uids_of(uids), dst))

    def add_flags(self, uids, flags, silent=False):
        self.log.append(("flag", uids_of(uids), list(flags)))

    def expunge(self, uids=None):
        self.log.append(("expunge", uids_of(uids) if uids is not None else None))


def _mail_with(s, monkeypatch, caps):
    fake = _MoveIMAP(caps)

    @contextlib.contextmanager
    def fake_imap(self):
        yield fake

    monkeypatch.setattr(MailService, "imap", fake_imap)
    monkeypatch.setattr(MailService, "resolve_folder", lambda self, c, n: {"trash": "Deleted Messages", "archive": "Archive"}.get(n.lower(), n))
    return MailService(s), fake


def test_move_uses_copy_flag_uid_expunge_when_the_server_has_no_move(s, monkeypatch):
    mail, fake = _mail_with(s, monkeypatch, {"UIDPLUS"})                    # what iCloud offers
    assert mail.move("INBOX", [5, 6], "Archive") == {"moved": [5, 6], "from": "INBOX", "to": "Archive"}
    assert fake.log == [("select", "INBOX"), ("copy", [5, 6], "Archive"), ("flag", [5, 6], ["\\Deleted"]), ("expunge", [5, 6])]


def test_delete_goes_to_trash_the_same_way_and_never_does_a_plain_expunge(s, monkeypatch):
    mail, fake = _mail_with(s, monkeypatch, {"UIDPLUS"})
    assert mail.delete("INBOX", [7])["moved_to_trash"] == [7]
    assert ("copy", [7], "Deleted Messages") in fake.log and ("expunge", None) not in fake.log


def test_native_move_is_used_when_available(s, monkeypatch):
    mail, fake = _mail_with(s, monkeypatch, {"MOVE", "UIDPLUS"})
    mail.move("INBOX", [1], "Archive")
    assert fake.log == [("select", "INBOX"), ("move", [1], "Archive")]


def test_no_move_and_no_uidplus_refuses_instead_of_risking_other_messages(s, monkeypatch):
    mail, fake = _mail_with(s, monkeypatch, set())
    with pytest.raises(MailError, match="UIDPLUS"):
        mail.move("INBOX", [1], "Archive")
    assert not any(step[0] in ("copy", "flag", "expunge") for step in fake.log)


# ------------------------------------------------------------------ connector icon (optional files in static/)
import httpx

import icloud_mcp.server as server_mod


async def _get(app, path):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com") as c:
            return await c.get(path)


@pytest.fixture
def icon_dir(tmp_path, monkeypatch):
    d = tmp_path / "static"
    d.mkdir()
    (d / "favicon.ico").write_bytes(b"\x00\x00\x01\x00" + b"\x01" * 200)
    (d / "icon-180.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x02" * 200)
    (d / "icon-32.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x03" * 200)
    monkeypatch.setattr(server_mod, "_STATIC", d)
    return d


@pytest.mark.parametrize("path,ctype,magic", [
    ("/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00"),
    ("/apple-touch-icon.png", "image/png", b"\x89PNG"),
    ("/apple-touch-icon-precomposed.png", "image/png", b"\x89PNG"),
    ("/icon.png", "image/png", b"\x89PNG"),
    ("/icon-32.png", "image/png", b"\x89PNG"),
])
def test_icons_are_served_publicly_without_login(s, icon_dir, path, ctype, magic):
    mcp, _ = create_server(s)
    r = asyncio.run(_get(mcp.streamable_http_app(host="0.0.0.0"), path))
    assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
    assert r.content.startswith(magic) and len(r.content) > 100
    assert "max-age" in r.headers["cache-control"]


def test_home_page_links_the_icons_and_leaks_nothing(s, icon_dir):
    mcp, _ = create_server(s)
    r = asyncio.run(_get(mcp.streamable_http_app(host="0.0.0.0"), "/"))
    assert r.status_code == 200
    for needle in ('rel="icon"', 'rel="apple-touch-icon"', "/favicon.ico", "/mcp"):
        assert needle in r.text
    assert "x" * 16 not in r.text and "aaaabbbbcccc" not in r.text            # neither the owner password nor the app password


def test_server_info_advertises_the_icon(s, icon_dir):
    mcp, _ = create_server(s)
    icons = getattr(mcp, "icons", None) or getattr(getattr(mcp, "_lowlevel_server", None), "icons", None)
    assert icons and any(str(i.src).endswith("/icon.png") for i in icons)


def test_without_icon_files_nothing_is_advertised_and_nothing_breaks(s, tmp_path, monkeypatch):
    empty = tmp_path / "empty-static"
    empty.mkdir()
    monkeypatch.setattr(server_mod, "_STATIC", empty)

    def fetch(path):                                                            # a fresh app per request (its lifespan runs once)
        mcp, _ = create_server(s)                                               # must not raise without icon files
        return mcp, asyncio.run(_get(mcp.streamable_http_app(host="0.0.0.0"), path))

    mcp, favicon = fetch("/favicon.ico")
    assert favicon.status_code == 404
    assert not (getattr(mcp, "icons", None) or getattr(getattr(mcp, "_lowlevel_server", None), "icons", None))
    _, home = fetch("/")
    assert home.status_code == 200 and 'rel="icon"' not in home.text and "apple-touch-icon" not in home.text
