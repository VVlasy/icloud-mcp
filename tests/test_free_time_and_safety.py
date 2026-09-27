"""Free-time finding, safe retries on create, and mail uids that cannot silently point at the wrong message."""
import contextlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import caldav
import httpx
import icalendar
import pytest

from icloud_mcp.cal import CalendarError, CalendarService, free_slots, not_busy_reason, retry_uid
from icloud_mcp.config import Settings
from icloud_mcp.contacts import ContactsError, ContactsService
from icloud_mcp.mail import MailError, MailService
from icloud_mcp.mailbulk import expand_uid_set

TZ = ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, DEFAULT_TIMEZONE="Europe/Amsterdam",
                     ALLOW_CALENDAR_INVITES="true").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def vevent(summary, start, end=None, **props):
    ev = icalendar.Event()
    ev.add("uid", f"{summary}@test")
    ev.add("summary", summary)
    ev.add("dtstart", start)
    if end is not None:
        ev.add("dtend", end)
    for k, v in props.items():
        if k == "attendees":
            for addr, partstat in v:
                a = icalendar.vCalAddress(f"mailto:{addr}")
                a.params["PARTSTAT"] = partstat
                ev.add("attendee", a)
        else:
            ev.add(k.replace("_", "-"), v)
    return ev


def at(day, h, m=0):
    return datetime(2030, 3, day, h, m, tzinfo=TZ)          # March 2030: always in the future, 4 March is a Monday


# ------------------------------------------------------------------ what counts as busy
def test_free_cancelled_and_declined_events_do_not_block_time():
    me = {"me@icloud.com"}
    assert not_busy_reason(vevent("x", at(4, 9), at(4, 10), status="CANCELLED"), me) == "cancelled"
    assert not_busy_reason(vevent("x", at(4, 9), at(4, 10), transp="TRANSPARENT"), me) == "marked as free"
    assert not_busy_reason(vevent("x", at(4, 9), at(4, 10), attendees=[("me@icloud.com", "DECLINED")]), me) == "declined"
    assert not_busy_reason(vevent("x", at(4, 9), at(4, 10), attendees=[("other@example.org", "DECLINED")]), me) is None
    assert not_busy_reason(vevent("x", at(4, 9), at(4, 10), attendees=[("me@icloud.com", "NEEDS-ACTION")]), me) is None


def test_free_slot_arithmetic_merges_overlaps_and_respects_the_minimum():
    busy = [(at(4, 10), at(4, 11)), (at(4, 10, 30), at(4, 12)), (at(4, 14), at(4, 14, 20))]
    slots = free_slots(busy, [(at(4, 9), at(4, 17))], timedelta(minutes=45))
    assert slots == [(at(4, 9), at(4, 10)), (at(4, 12), at(4, 14)), (at(4, 14, 20), at(4, 17))]
    assert free_slots(busy, [(at(4, 9), at(4, 17))], timedelta(hours=3)) == []
    assert free_slots([], [(at(4, 9), at(4, 10))], timedelta(minutes=60)) == [(at(4, 9), at(4, 10))]


# ------------------------------------------------------------------ find_free_time end to end (fake calendar)
@pytest.fixture
def cal(s, monkeypatch):
    svc = CalendarService(s)
    events = [
        vevent("Dentist", at(4, 10), at(4, 11), x_apple_travel_duration="PT30M"),
        vevent("Focus block", at(4, 13), at(4, 15), transp="TRANSPARENT"),
        vevent("Declined sync", at(4, 15), at(4, 16), attendees=[("me@icloud.com", "DECLINED")]),
        vevent("Birthday Anna", datetime(2030, 3, 5).date(), datetime(2030, 3, 6).date()),
        vevent("Gym", at(5, 9), at(5, 17)),
    ]
    monkeypatch.setattr(CalendarService, "_principal", lambda self: contextlib.nullcontext(object()))
    monkeypatch.setattr(CalendarService, "_occurrences",
                        lambda self, p, calendar, s_dt, e_dt, not_read=None, skipped=None: iter([("Home", e) for e in events]))
    return svc


def test_find_free_time_counts_travel_ignores_free_events_and_lists_all_day(cal):
    r = cal.find_free_time("2030-03-04", "2030-03-05", 60, day_start="09:00", day_end="17:00")
    slots = [(x["start"][11:16], x["end"][11:16], x["start"][:10]) for x in r["free_slots"]]
    # Monday: dentist 10-11 plus 30 min travel blocks 9:30-11; the free focus block and the declined sync do not count
    assert ("11:00", "17:00", "2030-03-04") in slots and all(not (s == "09:00" and d == "2030-03-04") for s, _, d in slots)
    # Tuesday: gym fills the whole window, so nothing is offered
    assert not any(d == "2030-03-05" for _, _, d in slots)
    assert [e["summary"] for e in r["all_day_events"]] == ["Birthday Anna"]
    assert {e["reason"] for e in r["not_counted_as_busy"]} == {"marked as free", "declined"}
    assert "All-day events are listed separately" in r["rules"]
    without_travel = cal.find_free_time("2030-03-04", "2030-03-04", 30, include_travel=False)
    assert without_travel["free_slots"][0]["start"][11:16] == "09:00" and without_travel["free_slots"][0]["end"][11:16] == "10:00"


def test_find_free_time_filters_weekdays_and_validates_input(cal):
    r = cal.find_free_time("2030-03-04", "2030-03-10", 60, weekdays=["sat", "sun"])
    days = {x["start"][:10] for x in r["free_slots"]}
    assert days == {"2030-03-09", "2030-03-10"}
    with pytest.raises(CalendarError, match="day_start"):
        cal.find_free_time("2030-03-04", "2030-03-05", 60, day_start="9am")
    with pytest.raises(CalendarError, match="later than day_start"):
        cal.find_free_time("2030-03-04", "2030-03-05", 60, day_start="18:00", day_end="09:00")
    with pytest.raises(CalendarError, match="weekdays"):
        cal.find_free_time("2030-03-04", "2030-03-05", 60, weekdays=["funday"])
    with pytest.raises(CalendarError, match="Range too large"):
        cal.find_free_time("2030-03-04", "2030-07-04", 60)
    with pytest.raises(CalendarError, match="past"):
        cal.find_free_time("2001-01-01", "2001-01-02", 60)


# ------------------------------------------------------------------ safe retries on create
def test_retry_uid_is_stable_per_account_and_request():
    assert retry_uid("me@icloud.com", "lunch-1") == retry_uid("ME@icloud.com", "lunch-1")
    assert retry_uid("me@icloud.com", "lunch-1") != retry_uid("me@icloud.com", "lunch-2")
    assert retry_uid("me@icloud.com", "lunch-1") != retry_uid("you@icloud.com", "lunch-1")
    with pytest.raises(CalendarError):
        retry_uid("me@icloud.com", "  ")


class FakeCal:
    url = "https://caldav.example/cal/home/"
    name = "Home"

    def __init__(self):
        self.store = {}

    def save_event(self, ical):
        uid = str(icalendar.Calendar.from_ical(ical).walk("VEVENT")[0]["uid"])
        self.store[uid] = ical

    def search(self, **kw):                           # create_event reads the calendar for clashes first
        return [type("Obj", (), {"data": d})() for d in self.store.values()]

    def event_by_url(self, url):
        uid = url.rsplit("/", 1)[-1].removesuffix(".ics")
        data = self.store.get(uid)
        if data is None:
            raise caldav.error.NotFoundError("404")
        return type("Obj", (), {"data": data, "load": lambda self: None})()


def test_create_event_with_request_id_never_duplicates(s, monkeypatch):
    svc, fake = CalendarService(s), FakeCal()
    monkeypatch.setattr(CalendarService, "_principal", lambda self: contextlib.nullcontext(object()))
    monkeypatch.setattr(CalendarService, "_pick", lambda self, p, calendar: [fake])
    first = svc.create_event(summary="Lunch", start="2030-03-04T12:30", request_id="lunch-anna")
    again = svc.create_event(summary="Lunch", start="2030-03-04T12:30", request_id="lunch-anna")
    assert first["created"] is True and again["created"] is False and again["already_existed"] is True
    assert first["uid"] == again["uid"] and len(fake.store) == 1
    other = svc.create_event(summary="Lunch", start="2030-03-04T12:30")          # no request_id: a new event every time
    assert other["created"] is True and len(fake.store) == 2


class CardStore:
    """Just enough CardDAV for create: discovery answers from the shared fake, cards by name."""

    def __init__(self, base):
        self.base, self.cards, self.puts = base, {}, 0

    def __call__(self, request):
        m = re.match(r"^/123/carddavhome/card/(.+)\.vcf$", request.url.path)
        if not m:
            return self.base(request)
        name = m.group(1)
        if request.method == "GET":
            return httpx.Response(200, text=self.cards[name]) if name in self.cards else httpx.Response(404)
        if request.method == "PUT":
            if request.headers.get("if-none-match") == "*" and name in self.cards:
                return httpx.Response(412)
            self.cards[name] = request.content.decode()
            self.puts += 1
            return httpx.Response(201)
        return httpx.Response(405)


def test_create_contact_with_request_id_never_duplicates(s):
    from test_contacts import FakeICloud          # pytest puts tests/ on sys.path (no __init__.py)
    import dataclasses
    store = CardStore(FakeICloud())
    svc = ContactsService(dataclasses.replace(s, carddav_url="https://contacts.example.test"), transport=httpx.MockTransport(store))
    first = svc.create(given_name="Ada", family_name="Lovelace", request_id="add-ada")
    again = svc.create(given_name="Ada", family_name="Lovelace", request_id="add-ada")
    assert first["created"] is True and again == {**again, "created": False, "already_existed": True, "uid": first["uid"]}
    assert store.puts == 1


@pytest.mark.parametrize("stored_kind", ["same", "different", "invalid", "missing"])
def test_create_contact_412_checks_stored_uid(s, stored_kind):
    from test_contacts import FakeICloud
    import dataclasses

    class RacingStore(CardStore):
        def __init__(self, base):
            super().__init__(base)
            self.gets = 0

        def __call__(self, request):
            if request.url.path.endswith(".vcf") and request.method == "GET":
                self.gets += 1
                if self.gets == 1 or stored_kind == "missing":
                    return httpx.Response(404)
            if request.url.path.endswith(".vcf") and request.method == "PUT":
                raw = request.content.decode()
                uid = request.url.path.rsplit("/", 1)[-1].removesuffix(".vcf")
                self.cards[uid] = (raw if stored_kind == "same" else raw.replace(f"UID:{uid}", "UID:another-contact")
                                   if stored_kind == "different" else "invalid vcard")
                return httpx.Response(412)
            return super().__call__(request)

    store = RacingStore(FakeICloud())
    svc = ContactsService(dataclasses.replace(s, carddav_url="https://contacts.example.test"), transport=httpx.MockTransport(store))
    if stored_kind == "same":
        result = svc.create(given_name="Ada", request_id="racing-create")
        assert result["created"] is False and result["already_existed"] is True
        assert result["uid"] in store.cards
    else:
        with pytest.raises(ContactsError, match="changed since it was read"):
            svc.create(given_name="Ada", request_id="racing-create")
    assert store.gets == 2


def test_create_contact_preflight_requires_matching_uid(s):
    from test_contacts import FakeICloud
    import dataclasses

    store = CardStore(FakeICloud())
    svc = ContactsService(dataclasses.replace(s, carddav_url="https://contacts.example.test"), transport=httpx.MockTransport(store))
    first = svc.create(given_name="Ada", request_id="existing-contact")
    store.cards[first["uid"]] = store.cards[first["uid"]].replace(f'UID:{first["uid"]}', "UID:another-contact")
    with pytest.raises(ContactsError, match="changed since it was read"):
        svc.create(given_name="Ada", request_id="existing-contact")


# ------------------------------------------------------------------ mail: uid + uidvalidity
def uids_of(seq):
    """What the server reads from a uid argument: a uid set such as b"3:5,9", or a plain list."""
    return expand_uid_set(seq, 10 ** 6) if isinstance(seq, (bytes, str)) else list(seq)


class FakeIMAP:
    def __init__(self, uidvalidity=5):
        self.uidvalidity, self.flag_calls = uidvalidity, []

    def select_folder(self, folder, readonly=False):
        return {b"UIDVALIDITY": self.uidvalidity, b"EXISTS": 1}

    def search(self, crit, charset=None):
        return [7]

    def fetch(self, uids, parts):
        raw = b"From: a@example.org\r\nTo: me@icloud.com\r\nSubject: Hi\r\nMessage-ID: <m@x>\r\n\r\nbody\r\n"
        return {u: {b"BODY[]": raw, b"FLAGS": (), b"INTERNALDATE": None, b"RFC822.SIZE": len(raw),
                    b"BODY[HEADER.FIELDS (FROM)]": raw.split(b"\r\n\r\n")[0]} for u in uids}

    def add_flags(self, uids, flags, silent=False):
        self.flag_calls.append(("add", uids_of(uids)))

    def remove_flags(self, uids, flags, silent=False):
        self.flag_calls.append(("remove", uids_of(uids)))


@pytest.fixture
def mail(s, monkeypatch):
    fake = FakeIMAP()

    @contextlib.contextmanager
    def fake_imap(self):
        yield fake

    monkeypatch.setattr(MailService, "imap", fake_imap)
    monkeypatch.setattr(MailService, "resolve_folder", lambda self, c, name: "INBOX")
    return MailService(s), fake


def test_uids_come_with_uidvalidity_and_stale_ones_are_refused(mail):
    svc, fake = mail
    found = svc.search("INBOX")
    assert found["uidvalidity"] == 5 and "uidvalidity" not in found["messages"][0]     # given once for the one folder
    assert svc.get_message("INBOX", 7, uidvalidity=5)["uidvalidity"] == 5
    got = svc.get_messages("INBOX", [7], uidvalidity=5)
    assert got["uidvalidity"] == 5 and "uidvalidity" not in got["messages"][0]
    fake.uidvalidity = 6                                                  # the server renumbered the folder
    with pytest.raises(MailError, match="out of date"):
        svc.get_message("INBOX", 7, uidvalidity=5)
    with pytest.raises(MailError, match="out of date"):
        svc.mark("INBOX", [7], read=True, uidvalidity=5)
    assert fake.flag_calls == []                                          # nothing was changed on the stale ids
    svc.mark("INBOX", [7], read=True)                                     # without uidvalidity: old behaviour
    assert fake.flag_calls == [("add", [7])]


def test_missing_uidvalidity_rejects_expected_uids(mail):
    svc, fake = mail
    fake.uidvalidity = None
    with pytest.raises(MailError, match="out of date"):
        svc.get_message("INBOX", 7, uidvalidity=5)
    with pytest.raises(MailError, match="out of date"):
        svc.mark("INBOX", [7], read=True, uidvalidity=5)
    assert fake.flag_calls == []


def test_unthreaded_seed_summary_keeps_uidvalidity(mail, monkeypatch):
    svc, fake = mail
    raw = b"From: a@example.org\r\nSubject: Hi\r\n\r\nbody"
    monkeypatch.setattr(fake, "fetch", lambda uids, parts: {uid: {b"BODY[]": raw,
                                                                 b"BODY[HEADER.FIELDS (FROM)]": raw} for uid in uids})
    result = svc.get_thread("INBOX", 7, uidvalidity=5)
    assert result["root_message_id"] is None
    assert result["messages"][0]["uidvalidity"] == 5
