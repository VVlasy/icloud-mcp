"""Integration tests against a local Dovecot (IMAP) + SMTP sink + Radicale (CalDAV).

Start the stack with dev/start_local_stack.sh first; otherwise these tests are skipped.
They exercise the real IMAP/SMTP/CalDAV code paths but NOT iCloud itself (see README, "What is and isn't verified").
"""
import base64
import contextlib
import email
import json
import pathlib
import socket
import time
from email import policy
from email.message import EmailMessage

import pytest
from imapclient import IMAPClient

from icloud_mcp.cal import CalendarError, CalendarService
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailError, MailService

SINK = pathlib.Path("/tmp/icmcp-dev/smtp-out")


def _up(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not (_up(1143) and _up(1025) and _up(5232)), reason="local stack not running (dev/start_local_stack.sh)")

ENV = dict(
    ICLOUD_USERNAME="test", ICLOUD_APP_PASSWORD="testpass", ICLOUD_EMAIL_ADDRESS="test@icloud.test", ICLOUD_DISPLAY_NAME="Test User",
    IMAP_HOST="127.0.0.1", IMAP_PORT="1143", IMAP_SECURITY="none", SMTP_HOST="127.0.0.1", SMTP_PORT="1025", SMTP_SECURITY="none",
    CALDAV_URL="http://127.0.0.1:5232", CALDAV_REQUIRE_TLS="false", DEFAULT_TIMEZONE="Europe/Berlin",
    EMAIL_SIGNATURE="Test User", MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16,
    # This suite exercises the raw send / calendar mechanics on the wire. The owner-approval gate and the calendar-invite
    # guard (both on by default in production) are covered offline in tests/test_outbox.py.
    SEND_REQUIRES_APPROVAL="false", ALLOW_CALENDAR_INVITES="true",
)


@pytest.fixture(scope="module")
def settings():
    import os
    old = {k: os.environ.get(k) for k in ENV}
    os.environ.update(ENV)
    yield Settings.from_env()
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture(scope="module")
def mail(settings):
    return MailService(settings)


def raw(**h):
    body = h.pop("body", "hello")
    m = EmailMessage()
    for k, v in h.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m


def seed(msgs):
    with IMAPClient("127.0.0.1", port=1143, ssl=False) as c:
        c.login("test", "testpass")
        for name in ("INBOX", "Sent Messages", "Drafts", "Archive", "Deleted Messages"):
            c.select_folder(name)
            uids = c.search(["ALL"])
            if uids:
                c.delete_messages(uids)
                c.expunge()
        out = []
        for folder, m, flags in msgs:
            c.append(folder, m.as_bytes(policy=policy.SMTP), flags=flags)
        c.select_folder("INBOX")
        return sorted(c.search(["ALL"]))


def sink_files():
    return sorted(SINK.glob("*.eml"))


@pytest.fixture(scope="module")
def inbox(mail):
    a = raw(From="Jane Doe <jane@example.org>", To="Test User <test@icloud.test>", Cc="Bob <bob@example.org>", Subject="Lunch on Friday?",
            Date="Sat, 19 Sep 2026 10:05:00 +0200", Message_ID="<a@example.org>", body="Are you free?\nJane")
    b = raw(From="Ann <ann@example.org>", To="test@icloud.test", Subject="Q3 report", Date="Sun, 20 Sep 2026 09:00:00 +0200",
            Message_ID="<b@example.org>", body="Report attached.")
    b.add_attachment(b"%PDF-1.4 fake pdf bytes", maintype="application", subtype="pdf", filename="q3.pdf")
    c = EmailMessage()
    c["From"], c["To"], c["Subject"], c["Date"], c["Message-ID"] = "News <news@example.org>", "test@icloud.test", "Café ☕ update", "Mon, 21 Sep 2026 08:00:00 +0200", "<c@example.org>"
    c.set_content("<html><body><p>Hello <b>world</b></p></body></html>", subtype="html")
    seed([("INBOX", a, ()), ("INBOX", b, ()), ("INBOX", c, ())])
    for f in SINK.glob("*"):
        f.unlink()
    return None


def uid_of(mail, subject):
    return mail.search("INBOX", subject=subject)["messages"][0]["uid"]


def test_folders_and_special_use(mail, inbox):
    names = {f["name"]: f for f in mail.list_folders()}
    assert {"INBOX", "Sent Messages", "Drafts", "Deleted Messages", "Junk", "Archive"} <= set(names)
    assert names["Sent Messages"]["special_use"] == "\\Sent"
    assert names["INBOX"]["total"] == 3 and names["INBOX"]["unseen"] == 3
    with mail.imap() as c:
        assert mail.resolve_folder(c, "Sent") == "Sent Messages"
        assert mail.resolve_folder(c, "trash") == "Deleted Messages"


def test_search_filters_and_paging(mail, inbox):
    r = mail.search("INBOX")
    assert r["total_matches"] == 3 and [m["subject"] for m in r["messages"]][0] == "Café ☕ update"  # newest uid first
    assert mail.search("INBOX", from_="jane")["total_matches"] == 1
    assert mail.search("INBOX", subject="Q3")["messages"][0]["has_attachments"] is True
    assert mail.search("INBOX", text="free")["total_matches"] == 1
    assert mail.search("INBOX", subject="Café")["total_matches"] == 1  # non-ASCII search
    assert mail.search("INBOX", since="2026-01-01")["total_matches"] == 3
    assert mail.search("INBOX", before="2026-01-01")["total_matches"] == 0
    assert mail.search("INBOX", unread=True)["total_matches"] == 3
    page = mail.search("INBOX", limit=2, offset=2)
    assert page["returned"] == 1 and page["total_matches"] == 3
    with pytest.raises(MailError):
        mail.search("INBOX", since="yesterday")


def test_get_message_does_not_mark_read(mail, inbox):
    uid = uid_of(mail, "Lunch")
    m = mail.get_message("INBOX", uid)
    assert m["text"].startswith("Are you free?") and m["from"][0]["email"] == "jane@example.org" and m["unread"] is True
    assert "untrusted" in m["notice"]
    assert mail.get_message("INBOX", uid)["unread"] is True
    assert mail.get_message("INBOX", uid, mark_read=True)["unread"] is False
    mail.mark("INBOX", [uid], read=False)
    assert mail.get_message("INBOX", uid)["unread"] is True


def test_html_only_and_attachments(mail, inbox):
    c = mail.get_message("INBOX", uid_of(mail, "Café"), include_html=True)
    assert "Hello **world**" in c["text"] and "<b>world</b>" in c["html"]
    b_uid = uid_of(mail, "Q3")
    b = mail.get_message("INBOX", b_uid)
    assert b["attachments"][0]["filename"] == "q3.pdf" and b["attachments"][0]["size"] == 23
    att = mail.get_attachment("INBOX", b_uid, 0)
    assert base64.b64decode(att["content_base64"]) == b"%PDF-1.4 fake pdf bytes"
    with pytest.raises(MailError):
        mail.get_attachment("INBOX", b_uid, 5)


def test_reply_threading_sent_copy_and_answered(mail, inbox):
    a_uid = uid_of(mail, "Lunch")
    before = len(sink_files())
    res = mail.reply("INBOX", a_uid, "Yes, noon works.", reply_all=True)
    assert res["status"] == "sent" and res["saved_to"] == "Sent Messages" and res["original_marked_answered"] is True
    assert sorted(res["recipients"]) == ["bob@example.org", "jane@example.org"]

    # what went over SMTP
    files = sink_files()
    assert len(files) == before + 1
    wire = email.message_from_bytes(files[-1].read_bytes(), policy=policy.default)
    assert wire["Subject"] == "Re: Lunch on Friday?" and wire["In-Reply-To"] == "<a@example.org>" and wire["References"] == "<a@example.org>"
    assert wire["Bcc"] is None
    body = wire.get_body(("plain",)).get_content().replace("\r\n", "\n")
    assert body.startswith("Yes, noon works.") and "-- \nTest User" in body and "> Are you free?" in body

    # copy in Sent, exactly once, same Message-ID as on the wire
    sent = mail.search("Sent", limit=10)
    assert sent["total_matches"] == 1 and sent["messages"][0]["message_id"] == wire["Message-ID"]
    assert mail.get_message("Sent", sent["messages"][0]["uid"])["unread"] is False
    # original flagged answered
    assert mail.get_message("INBOX", a_uid)["answered"] is True


def test_thread_spans_inbox_and_sent(mail, inbox):
    t = mail.get_thread("INBOX", uid_of(mail, "Lunch"))
    assert t["count"] == 2
    assert {m["folder"] for m in t["messages"]} == {"INBOX", "Sent Messages"}


def test_every_result_that_hands_out_uids_hands_out_their_uidvalidity(mail, inbox):
    """mail_mark_messages, mail_move_messages and mail_delete_messages require uidvalidity, so each uid an agent is given must come with one: on the
    message itself (all-folder search, threads) or once on the result's folder. Each pair is then used for real."""
    lunch = uid_of(mail, "Lunch")
    results = {
        "search": mail.search("INBOX"),
        "search all folders": mail.search("INBOX", all_folders=True),
        "thread": mail.get_thread("INBOX", lunch),
        "get_messages": mail.get_messages("INBOX", [lunch]),
    }
    assert isinstance(mail.changes("INBOX")["uidvalidity"], int)             # later calls list uids under this folder header
    for name, r in results.items():
        msgs = r.get("messages") or []
        assert msgs, name
        for m in msgs:
            folder = m.get("folder") or r["folder"]
            uv = m.get("uidvalidity") or r.get("uidvalidity")
            assert isinstance(uv, int), (name, m)
            mail.mark(folder, [m["uid"]], flagged=True, uidvalidity=uv)          # accepted: the pair is right
            mail.mark(folder, [m["uid"]], flagged=False, uidvalidity=uv)


def test_draft_forward_and_new_message(mail, inbox):
    b_uid = uid_of(mail, "Q3")
    d = mail.reply("INBOX", b_uid, "Thanks, will review.", draft=True)
    assert d["status"] == "draft_saved"
    drafts = mail.search("Drafts")
    assert drafts["total_matches"] == 1 and drafts["messages"][0]["draft"] is True
    assert len(sink_files()) == 1  # drafting sent nothing

    f = mail.forward("INBOX", b_uid, ["boss@example.org"], note="FYI")
    assert f["status"] == "sent"
    wire = email.message_from_bytes(sink_files()[-1].read_bytes(), policy=policy.default)
    assert wire["Subject"] == "Fwd: Q3 report"
    assert [p.get_filename() for p in wire.iter_attachments()] == ["q3.pdf"]
    assert mail.get_message("INBOX", b_uid)["forwarded"] is True

    n = mail.send(to=["x@example.org"], cc=["y@example.org"], bcc=["secret@example.org"], subject="Grüße", body="Hallo ☕",
                  attachments=[{"filename": "n.txt", "content_base64": base64.b64encode(b"note").decode()}])
    assert sorted(n["recipients"]) == ["secret@example.org", "x@example.org", "y@example.org"]
    last = sink_files()[-1]
    assert sorted(last.with_suffix(".rcpt").read_text().split()) == sorted(n["recipients"])
    wire = email.message_from_bytes(last.read_bytes(), policy=policy.default)
    assert wire["Bcc"] is None and wire["Subject"] == "Grüße"
    sent_uid = mail.search("Sent", subject="Grüße")["messages"][0]["uid"]
    stored = mail.get_message("Sent", sent_uid)
    assert stored["attachments"][0]["filename"] == "n.txt" and stored["text"].startswith("Hallo ☕")


def test_move_delete_and_safety(mail, inbox, settings):
    c_uid = uid_of(mail, "Café")
    assert mail.move("INBOX", [c_uid], "Archive")["to"] == "Archive"
    assert mail.search("Archive")["total_matches"] == 1
    a_uid = mail.search("Archive")["messages"][0]["uid"]
    assert mail.delete("Archive", [a_uid])["moved_to_trash"] == [a_uid]
    trash = mail.search("trash")
    assert trash["total_matches"] == 1
    with pytest.raises(MailError, match="permanently"):
        mail.delete("trash", [trash["messages"][0]["uid"]])
    assert mail.create_folder("Projects")["name"] == "Projects"
    assert mail.create_folder("Projects")["created"] is False  # idempotent
    assert "Projects" in {f["name"] for f in mail.list_folders()}


def test_send_guards(settings, inbox):
    import dataclasses
    strict = MailService(dataclasses.replace(settings, send_allowlist=("example.org",), max_recipients=2))
    with pytest.raises(MailError, match="ALLOWLIST"):
        strict.send(to=["a@evil.net"], subject="s", body="b")
    with pytest.raises(MailError, match="Too many"):
        strict.send(to=["a@example.org", "b@example.org", "c@example.org"], subject="s", body="b")
    off = MailService(dataclasses.replace(settings, allow_send=False))
    with pytest.raises(MailError, match="disabled"):
        off.send(to=["a@example.org"], subject="s", body="b")
    with pytest.raises(MailError):
        MailService(dataclasses.replace(settings, app_password="wrong")).list_folders()


# ------------------------------------------------------------------------ calendar
def test_a_saved_draft_is_updated_then_sent_as_saved(mail, inbox):
    before = set(SINK.glob("*.eml"))                    # the sink runs as root in CI: read only what this test adds
    mail.send(to=["Anna <anna@example.org>"], bcc=["boss@example.org"], subject="Plan", body="Draft one", draft=True)
    drafts = mail.search("Drafts")
    uid = next(m["uid"] for m in drafts["messages"] if m["subject"] == "Plan")
    upd = mail.update_draft(uid, uidvalidity=drafts["uidvalidity"], body="Draft two")
    assert upd["status"] == "draft_updated" and upd["uid"] != uid
    r = mail.send_draft(upd["uid"], uidvalidity=upd["uidvalidity"])
    assert r["status"] == "sent" and r["draft_moved_to_trash"] is True
    eml = sorted(set(SINK.glob("*.eml")) - before)
    assert len(eml) == 1
    wire, rcpt = eml[0].read_bytes(), eml[0].with_suffix(".rcpt").read_text().split()
    assert b"Draft two" in wire and b"Draft one" not in wire
    assert "boss@example.org" in rcpt and b"boss@example.org" not in wire         # Bcc: delivered, never shown
    assert not [m for m in mail.search("Drafts")["messages"] if m["subject"] == "Plan"]


def test_folders_are_renamed_and_deleted_without_losing_mail(mail, inbox):
    with contextlib.suppress(Exception):
        mail.delete_folder("Scratch renamed")
    mail.create_folder("Scratch")
    assert mail.update_folder("Scratch", "Scratch renamed")["renamed"] is True
    with pytest.raises(MailError, match="own folders"):
        mail.update_folder("INBOX", "Other")
    with IMAPClient("127.0.0.1", port=1143, ssl=False) as c:
        c.login("test", "testpass")
        for i in range(3):
            c.append("Scratch renamed", raw(From="x@example.org", To="test@icloud.test", Subject=f"old {i}").as_bytes(policy=policy.SMTP))
    trash_before = mail.search("trash")["total_matches"]
    preview = mail.delete_folder("Scratch renamed")
    assert preview["deleted"] is False and preview["messages"] == 3
    done = mail.delete_folder("Scratch renamed", confirm_token=preview["confirm_token"])
    assert done["deleted"] is True and done["messages_moved_to_trash"] == 3
    assert mail.search("trash")["total_matches"] == trash_before + 3
    assert "Scratch renamed" not in [f["name"] for f in mail.list_folders()]


@pytest.fixture(scope="module")
def cal(settings):
    import caldav
    with caldav.DAVClient(url=settings.caldav_url, username="test", password="testpass", require_tls=False) as client:
        p = client.principal()
        if not p.calendars():
            p.make_calendar(name="Personal")
    return CalendarService(settings)


def test_calendar_crud_and_recurrence(cal):
    names = [c["name"] for c in cal.list_calendars()]
    assert names
    created = cal.create_event(summary="Team sync", start="2026-09-21T10:00", end="2026-09-21T10:30", location="Room 1",
                               description="Weekly", rrule="FREQ=WEEKLY;COUNT=4", alarms_minutes_before=[15], attendees=["a@example.org"])
    uid = created["uid"]
    r = cal.list_events("2026-09-21", "2026-10-31")
    occ = [e for e in r["events"] if e["uid"] == uid]
    assert len(occ) == 4 and occ[0]["start"].startswith("2026-09-21T10:00") and occ[1]["start"].startswith("2026-09-28T10:00")
    assert cal.list_events("2026-09-21", "2026-09-27", query="team")["total"] == 1
    assert cal.list_events("2026-09-21", "2026-09-27", query="nothing-here")["total"] == 0

    got = cal.get_event(uid)
    assert got["rrule"] and got["alarms_minutes_before"] == [15] and got["attendees"][0]["email"] == "a@example.org" and got["location"] == "Room 1"

    upd = cal.update_event(uid, start="2026-09-21T11:00", location="", summary="Team sync (moved)")
    assert upd["event"]["summary"] == "Team sync (moved)" and upd["event"]["location"] is None
    assert upd["event"]["start"].startswith("2026-09-21T11:00") and upd["event"]["end"].startswith("2026-09-21T11:30")  # duration kept
    again = cal.list_events("2026-09-21", "2026-10-31")
    assert len([e for e in again["events"] if e["uid"] == uid]) == 4

    assert cal.delete_event(uid)["deleted"] is True
    with pytest.raises(CalendarError):
        cal.get_event(uid)


def test_calendars_are_created_renamed_and_deleted_behind_a_preview(cal):
    names = lambda: [c["name"] for c in cal.list_calendars()]          # noqa: E731
    for n in ("Scratch cal", "Scratch cal renamed"):
        if n in names():
            with contextlib.suppress(Exception):
                cal.delete_calendar(n, confirm_token=cal.delete_calendar(n).get("confirm_token"))
    assert cal.create_calendar("Scratch cal")["created"] is True and "Scratch cal" in names()
    with pytest.raises(CalendarError, match="already a calendar"):
        cal.create_calendar("scratch cal")
    assert cal.update_calendar("Scratch cal", "Scratch cal renamed")["renamed"] is True and "Scratch cal renamed" in names()
    cal.create_event(summary="Scratch event", start="2026-11-02T10:00", end="2026-11-02T11:00", calendar="Scratch cal renamed")
    preview = cal.delete_calendar("Scratch cal renamed")
    assert preview["deleted"] is False and preview["events"] == 1 and "Scratch cal renamed" in names()
    done = cal.delete_calendar("Scratch cal renamed", confirm_token=preview["confirm_token"])
    assert done["deleted"] is True and "Scratch cal renamed" not in names()


def test_an_all_day_series_expands_to_dates(cal):
    """The reason expansion is client-side: server-side expansion turns all-day events into UTC date-times."""
    uid = cal.create_event(summary="Bins out", start="2026-10-05", end="2026-10-05", rrule="FREQ=WEEKLY;COUNT=3")["uid"]
    try:
        occ = [e for e in cal.list_events("2026-10-01", "2026-10-31")["events"] if e["uid"] == uid]
        assert [e["start"] for e in occ] == ["2026-10-05", "2026-10-12", "2026-10-19"]
    finally:
        cal.delete_event(uid)
    assert cal.list_events("2026-09-21", "2026-10-31")["total"] == 0


def test_calendar_all_day_and_errors(cal):
    uid = cal.create_event(summary="Trip", start="2026-10-01", end="2026-10-03")["uid"]
    ev = cal.list_events("2026-10-02", "2026-10-02")["events"]
    assert len(ev) == 1 and ev[0]["all_day"] is True and ev[0]["end"] == "2026-10-04"
    cal.update_event(uid, end="2026-10-05")
    assert cal.get_event(uid)["end"] == "2026-10-06"
    cal.delete_event(uid)
    with pytest.raises(CalendarError):
        cal.list_events("2026-10-05", "2026-10-01")
    with pytest.raises(CalendarError):
        cal.create_event(summary="x", start="2026-10-01", calendar="No such calendar")


# --------------------------------------------------------------- through the MCP layer
async def test_tools_via_mcp_layer(settings, inbox, cal):
    from icloud_mcp.server import create_server

    mcp, _ = create_server(settings)
    res = await mcp.call_tool("mail_search_messages", {"folder": "INBOX", "limit": 5})
    assert len(res.content) == 1 and res.structured_content is None               # one compact JSON text, no structured copy
    assert "messages" in json.loads(res.content[0].text)
    with pytest.raises(Exception, match="No message with uid"):
        await mcp.call_tool("mail_get_message", {"folder": "INBOX", "uid": 99999})
    res = await mcp.call_tool("calendar_list_calendars", {})
    assert "name" in str(res)
