from datetime import date, datetime, timedelta

import icalendar
import pytest

from icloud_mcp.cal import CalendarError, build_event, event_to_dict, get_tz, parse_when

LOCAL_TZ = get_tz("Europe/Berlin")


def vevent(ical: str) -> icalendar.Event:
    return list(icalendar.Calendar.from_ical(ical).walk("VEVENT"))[0]


def test_timed_event_has_tzid_and_default_hour():
    uid, ical = build_event(summary="Standup", start="2026-09-21T09:30", end=None, tz=LOCAL_TZ)
    assert "BEGIN:VTIMEZONE" in ical and "TZID=Europe/Berlin" in ical
    d = event_to_dict(vevent(ical), "Work")
    assert d["uid"] == uid and d["all_day"] is False
    assert d["start"].startswith("2026-09-21T09:30") and d["end"].startswith("2026-09-21T10:30")


def test_aware_input_is_converted_to_tz():
    _, ical = build_event(summary="x", start="2026-09-21T12:00:00+00:00", end="2026-09-21T13:00:00+00:00", tz=LOCAL_TZ)
    assert vevent(ical)["dtstart"].dt.hour == 14  # CEST = UTC+2


def test_all_day_end_is_inclusive_in_and_exclusive_out():
    _, ical = build_event(summary="Trip", start="2026-10-01", end="2026-10-03", tz=LOCAL_TZ)
    ev = vevent(ical)
    assert ev["dtstart"].dt == date(2026, 10, 1) and ev["dtend"].dt == date(2026, 10, 4)
    assert "VTIMEZONE" not in ical
    assert event_to_dict(ev, None)["all_day"] is True


def test_single_all_day_default_one_day():
    _, ical = build_event(summary="Holiday", start="2026-12-25", end=None, tz=LOCAL_TZ)
    assert vevent(ical)["dtend"].dt == date(2026, 12, 26)


def test_recurrence_alarms_attendees():
    _, ical = build_event(
        summary="Sync", start="2026-09-21T10:00", end="2026-09-21T10:30", tz=LOCAL_TZ, rrule="FREQ=WEEKLY;BYDAY=MO,WE;COUNT=6",
        alarms_minutes_before=[15, 60], attendees=["a@x.org", "b@x.org"], organizer_email="me@icloud.com", location="Room 1",
    )
    d = event_to_dict(vevent(ical), "Work")
    assert set(d["rrule"].split(";")) == {"FREQ=WEEKLY", "BYDAY=MO,WE", "COUNT=6"}
    assert d["alarms_minutes_before"] == [15, 60]
    assert [a["email"] for a in d["attendees"]] == ["a@x.org", "b@x.org"]
    assert d["organizer"]["email"] == "me@icloud.com" and d["location"] == "Room 1"


def test_validation_errors():
    with pytest.raises(CalendarError):
        build_event(summary="x", start="tomorrow", end=None, tz=LOCAL_TZ)
    with pytest.raises(CalendarError):
        build_event(summary="x", start="2026-09-21T10:00", end="2026-09-21T09:00", tz=LOCAL_TZ)
    with pytest.raises(CalendarError):
        build_event(summary="x", start="2026-09-21", end="2026-09-22T10:00", tz=LOCAL_TZ)
    with pytest.raises(CalendarError):
        build_event(summary="x", start="2026-09-21T10:00", end=None, tz=LOCAL_TZ, rrule="FREQ=SOMETIMES")
    with pytest.raises(CalendarError):
        get_tz("Mars/Olympus")


def test_parse_when():
    assert parse_when("2026-09-21", LOCAL_TZ) == (date(2026, 9, 21), True)
    v, is_date = parse_when("2026-09-21T09:00", LOCAL_TZ)
    assert not is_date and v.tzinfo is not None and v.utcoffset() == timedelta(hours=2)


# ---------------------------------------------------------------- uid lookup on iCloud
class _Obj:
    def __init__(self, uid, url="u"):
        self.url = url
        self.data = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:20260919T160000Z\r\nSUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"

    def load(self):
        return self


class _ICloudLikeCal:
    """Behaves like iCloud: UID-filtered REPORT -> 412, GET by <uid>.ics works only for events stored under that name."""
    url = "https://caldav.icloud.com/1/calendars/ABC/"

    def __init__(self, by_name, others=()):
        self.by_name, self.others = by_name, list(others)
        self.event_by_uid_called = False

    def event_by_uid(self, uid):
        import caldav
        self.event_by_uid_called = True
        raise caldav.error.ReportError("412 Precondition Failed")

    def event_by_url(self, href):
        import caldav
        name = href.rsplit("/", 1)[-1].removesuffix(".ics")
        if name not in self.by_name:
            raise caldav.error.NotFoundError("404")          # as caldav does: event_by_url is the GET (Event(...).load())
        return self.by_name[name]

    def events(self):
        return list(self.by_name.values()) + self.others


def _svc(monkeypatch, cal):
    import os
    from icloud_mcp.cal import CalendarService
    from icloud_mcp.config import Settings
    os.environ.update(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaabbbbccccdddd")
    svc = CalendarService(Settings.from_env())
    monkeypatch.setattr(svc, "_pick", lambda principal, name: [cal])
    return svc


def test_find_by_uid_never_uses_the_report_icloud_rejects(monkeypatch):
    cal = _ICloudLikeCal({"abc@icloud-mcp": _Obj("abc@icloud-mcp")})
    found_cal, obj = _svc(monkeypatch, cal)._find(None, "abc@icloud-mcp", None)
    assert found_cal is cal and "UID:abc@icloud-mcp" in obj.data and not cal.event_by_uid_called


def test_find_by_uid_falls_back_to_scan_when_resource_name_differs(monkeypatch):
    odd = _Obj("REAL-UID-1")                       # stored as some-other-name.ics
    cal = _ICloudLikeCal({"some-other-name": odd}, others=[_Obj("unrelated")])
    _, obj = _svc(monkeypatch, cal)._find(None, "REAL-UID-1", None)
    assert obj is odd


def test_find_by_uid_reports_missing_events(monkeypatch):
    from icloud_mcp.cal import CalendarError
    with pytest.raises(CalendarError, match="No event with uid"):
        _svc(monkeypatch, _ICloudLikeCal({}))._find(None, "nope", None)


def test_by_href_is_one_get_and_a_miss_is_none():
    gets = []

    class Cal:
        url = "https://caldav.icloud.com/1/calendars/ABC/"

        def event_by_url(self, href):
            import caldav
            gets.append(href)
            if "gone" in href:
                raise caldav.error.NotFoundError("404")
            return _Obj("abc@icloud-mcp", href)
    from icloud_mcp.cal import CalendarService
    assert CalendarService._by_href(Cal(), "abc@icloud-mcp").url.endswith("/ABC/abc@icloud-mcp.ics") and len(gets) == 1
    assert CalendarService._by_href(Cal(), "gone") is None and len(gets) == 2


def test_pick_takes_a_short_id_a_full_url_or_a_name_and_never_guesses(monkeypatch):
    import os
    from icloud_mcp.cal import CalendarError, CalendarService
    from icloud_mcp.config import Settings
    os.environ.update(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaabbbbccccdddd")
    svc = CalendarService(Settings.from_env())
    cal = lambda name, cid: type("C", (), {"name": name, "url": f"https://caldav.icloud.com/1/calendars/{cid}/"})()   # noqa: E731
    home, work, odd = cal("Home", "home"), cal("Work", "A1B2-C3"), cal("home2", "x-home2")
    monkeypatch.setattr(svc, "_event_calendars", lambda p: [home, work, odd])
    assert [svc._cal_id(c) for c in (home, work)] == ["home", "A1B2-C3"]
    assert svc._pick(None, "a1b2-c3") == [work]                                  # the id from calendar_list_calendars, any case
    assert svc._pick(None, "https://caldav.icloud.com/1/calendars/A1B2-C3/") == [work]   # an old full-URL id still works
    assert svc._pick(None, " WORK ") == [work] and svc._pick(None, "home") == [home]     # a name, any case; name and id agree
    with pytest.raises(CalendarError, match="No calendar named"):
        svc._pick(None, "C3")                                                    # only a whole segment matches, not a suffix
    monkeypatch.setattr(svc, "_event_calendars", lambda p: [home, work, cal("Trips", "home2"), odd])
    with pytest.raises(CalendarError, match="name of one calendar and the id of another"):
        svc._pick(None, "home2")


def test_addr_uses_email_param_when_icloud_rewrites_the_address_to_a_principal_path():
    from icloud_mcp.cal import _addr
    v = icalendar.vCalAddress("/aMjA0Mz/principal/")
    v.params["EMAIL"] = "me@icloud.com"
    v.params["CN"] = "Me"
    assert _addr(v)["email"] == "me@icloud.com" and _addr(v)["name"] == "Me"
    assert _addr(icalendar.vCalAddress("mailto:bob@example.org"))["email"] == "bob@example.org"
