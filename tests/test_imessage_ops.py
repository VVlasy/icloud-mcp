"""The Mac helper's iMessage reader against a fixture chat.db with the real table layout: text that lives only in attributedBody,
SMS and iMessage rows of one person as one conversation, tapbacks folded into the message they react to, retracted, system and spam
rows left out, attachments by name, paging, and search that ignores case and accents. It only ever opens the owner's own database."""
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "mac-helper" / "ops" / "imessage.py"
NS = 1_000_000_000
BASE = 800_000_000                                   # seconds after 2001-01-01: late 2026


def typedstream(text: str) -> bytes:
    """An NSAttributedString typedstream as Messages writes it, with the three length encodings."""
    raw = text.encode("utf-8")
    n = len(raw)
    length = bytes([n]) if n < 0x80 else (b"\x81" + n.to_bytes(2, "little") if n < 0x10000 else b"\x82" + n.to_bytes(4, "little"))
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92"
            b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw + b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00")


SCHEMA = """
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT, service_name TEXT, style INTEGER, is_archived INTEGER);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, date INTEGER, date_delivered INTEGER,
  date_read INTEGER, is_from_me INTEGER, handle_id INTEGER, service TEXT, date_edited INTEGER, cache_has_attachments INTEGER,
  reply_to_guid TEXT, thread_originator_guid TEXT, associated_message_type INTEGER, associated_message_guid TEXT,
  associated_message_emoji TEXT, item_type INTEGER, is_spam INTEGER, date_retracted INTEGER, error INTEGER, is_read INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, transfer_name TEXT, mime_type TEXT, total_bytes INTEGER);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
"""


def build(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO chat VALUES (?,?,?,?,?,?,?)", [
        (1, "any;-;+31600000001", "+31600000001", "", "iMessage", 45, 0),
        (2, "SMS;-;+31600000001", "+31600000001", "", "SMS", 45, 0),         # the same person over SMS: one conversation
        (3, "any;+;chat900", "chat900", "Hiking crew", "iMessage", 43, 0),
        (4, "any;-;anna@example.org", "anna@example.org", "", "iMessage", 45, 1),   # archived
    ])
    db.executemany("INSERT INTO handle VALUES (?,?)", [(1, "+31600000001"), (2, "anna@example.org"), (3, "+31600000002")])
    db.executemany("INSERT INTO chat_handle_join VALUES (?,?)", [(1, 1), (2, 1), (3, 1), (3, 3), (4, 2)])
    long_text = "Café " + "x" * 300                                              # two-byte length form
    rows = [  # rowid, guid, text, body, t, from_me, handle, chat, extra
        (1, "g1", None, typedstream("Hoi! Zin in koffie morgen?"), 10, 0, 1, 1, {}),
        (2, "g2", None, typedstream("Ja leuk, 10 uur?"), 20, 1, None, 1, {"date_delivered": (BASE + 21) * NS, "date_read": (BASE + 30) * NS}),
        (3, "g3", "Top ￼", None, 30, 0, 1, 2, {"cache_has_attachments": 1}),        # SMS row of the same person, with a photo
        (4, "g4", None, None, 31, 0, 1, 1, {"associated_message_type": 2000, "associated_message_guid": "p:0/g2"}),   # love g2
        (5, "g5", None, None, 32, 0, 1, 1, {"associated_message_type": 2003, "associated_message_guid": "p:0/g2"}),   # laugh
        (6, "g6", None, None, 33, 0, 1, 1, {"associated_message_type": 3003, "associated_message_guid": "p:0/g2"}),   # laugh taken back
        (7, "g7", None, typedstream("oops"), 34, 0, 1, 1, {"date_retracted": (BASE + 35) * NS}),
        (8, "g8", None, None, 36, 0, 1, 1, {"item_type": 1}),                                                          # system item
        (9, "g9", None, typedstream("WIN A PRIZE"), 37, 0, 1, 1, {"is_spam": 1}),
        (10, "g10", None, typedstream(long_text), 40, 0, 3, 3, {"is_read": 0}),
        (11, "g11", None, typedstream("See you at the station"), 50, 0, 1, 3, {"is_read": 0}),
        (12, "g12", None, typedstream("Old archived chat"), 5, 0, 2, 4, {}),
    ]
    for rowid, guid, text, body, t, from_me, handle, chat, extra in rows:
        cols = {"ROWID": rowid, "guid": guid, "text": text, "attributedBody": body, "date": (BASE + t) * NS, "is_from_me": from_me,
                "handle_id": handle, "service": "SMS" if chat == 2 else "iMessage", "associated_message_type": 0, "item_type": 0,
                "is_spam": 0, "date_retracted": 0, "error": 0, "is_read": 1, "cache_has_attachments": 0, **extra}
        db.execute(f"INSERT INTO message ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
        db.execute("INSERT INTO chat_message_join VALUES (?,?,?)", (chat, rowid, (BASE + t) * NS))
    db.execute("INSERT INTO attachment VALUES (1, 'IMG_0001.HEIC', 'image/heic', 123456)")
    db.execute("INSERT INTO message_attachment_join VALUES (3, 1)")
    db.commit()
    return long_text


FAKE_OSASCRIPT = """#!{python}
import json, sqlite3, sys
payload = json.loads(sys.argv[-1])
mode = open({mode!r}).read().strip()
if mode == "deny":
    sys.stderr.write("execution error: Not authorized to send Apple events to Messages. (-1743)"); sys.exit(1)
db = sqlite3.connect({db!r})
chat = {{"any;-;+31600000001": 1, "any;+;chat900": 3}}.get(payload.get("guid"), 5)
if chat == 5 and not db.execute("select 1 from chat where ROWID=5").fetchone():
    db.execute("insert into chat values (5, 'any;-;new@example.org', 'new@example.org', '', 'iMessage', 45, 0)")
if mode != "silent":
    rowid = db.execute("select max(ROWID)+1 from message").fetchone()[0]
    db.execute("insert into message (ROWID, guid, text, date, is_from_me, is_sent, is_delivered, error, associated_message_type, item_type, is_spam, date_retracted, is_read) "
               "values (?, 'sent-guid', ?, 1, 1, ?, ?, ?, 0, 0, 0, 0, 1)", (rowid, payload["text"], 0 if mode == "fail" else 1,
                                                                            0 if mode == "fail" else 1, 22 if mode == "fail" else 0))
    db.execute("insert into chat_message_join values (?, ?, 1)", (chat, rowid))
db.commit()
print("sent")
"""


@pytest.fixture
def run(tmp_path):
    path = tmp_path / "chat.db"
    long_text = build(path)
    sqlite3.connect(path).executescript("ALTER TABLE message ADD COLUMN is_sent INTEGER DEFAULT 0; "
                                        "ALTER TABLE message ADD COLUMN is_delivered INTEGER DEFAULT 0;")
    mode = tmp_path / "mode"
    mode.write_text("ok")
    fake = tmp_path / "osascript"
    fake.write_text(FAKE_OSASCRIPT.format(python=sys.executable, db=str(path), mode=str(mode)))
    fake.chmod(0o755)
    never = tmp_path / "never.txt"
    never.write_text("# the owner's assistant\nBot@Example.org\n")
    env = {**os.environ, "ICLOUD_MAC_HELPER_TESTING": "1", "ICLOUD_IMESSAGE_DB": str(path), "ICLOUD_IMESSAGE_OSASCRIPT": str(fake),
           "ICLOUD_IMESSAGE_NEVER_SEND_FILE": str(never), "ICLOUD_IMESSAGE_CONFIRM_SECONDS": "1"}

    def go(op, args):
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), op, json.dumps(args)], env=env, capture_output=True, text=True, timeout=60)
        if p.returncode:
            raise RuntimeError(p.stderr.strip())
        return json.loads(p.stdout)
    go.long_text = long_text
    go.mode = mode
    return go


def test_conversations_merge_sms_and_imessage_and_leave_archived_out(run):
    chats = run("imessage_chats", {})["chats"]
    assert [c["chat_id"] for c in chats] == ["chat900", "+31600000001"]                 # newest first, archived left out
    one = chats[1]
    assert one["services"] == ["SMS", "iMessage"] and one["group"] is False and one["participants"] == ["+31600000001"]
    assert chats[0]["group"] is True and chats[0]["name"] == "Hiking crew" and chats[0]["unread"] == 2
    assert chats[0]["last_text"] == "See you at the station"
    assert "anna@example.org" in [c["chat_id"] for c in run("imessage_chats", {"include_archived": True})["chats"]]
    assert [c["chat_id"] for c in run("imessage_chats", {"exclude": "chat900"})["chats"]] == ["+31600000001"]


def test_reading_decodes_text_folds_reactions_and_leaves_noise_out(run):
    r = run("imessage_read", {"chat_id": "+31600000001"})
    texts = [m["text"] for m in r["messages"]]
    assert texts == ["Hoi! Zin in koffie morgen?", "Ja leuk, 10 uur?", "Top [attachment]"]     # oldest first; no oops, system or spam
    mine = r["messages"][1]
    assert mine["from_me"] is True and mine["sender"] is None and mine["delivered_at"] and mine["read_at"]
    assert mine["reactions"] == {"love": 1}                                                 # the laugh was taken back
    assert r["messages"][2]["attachments"] == [{"name": "IMG_0001.HEIC", "type": "image/heic", "bytes": 123456}]
    assert r["messages"][0]["sender"] == "+31600000001" and r["complete"] is True


def test_long_messages_and_paging(run):
    r = run("imessage_read", {"chat_id": "chat900", "limit": 1})
    assert [m["text"] for m in r["messages"]] == ["See you at the station"] and r["complete"] is False
    older = run("imessage_read", {"chat_id": "chat900", "limit": 5, "before_id": r["older_before_id"]})
    assert older["messages"][0]["text"] == run.long_text                                   # 0x81 two-byte length decoded
    with pytest.raises(RuntimeError, match="no conversation"):
        run("imessage_read", {"chat_id": "+31699999999"})
    with pytest.raises(RuntimeError, match="no conversation"):
        run("imessage_read", {"chat_id": "chat900", "exclude": "chat900"})


def test_a_one_message_read_gives_the_chat_record_the_chat_list_gives(run, tmp_path):
    """The server resolves a send target with imessage_read(limit=1): the same chat_id, name, group and participants as
    imessage_chats, for any chat (archived, or one with no messages yet that the chat list leaves out), and hidden ones stay hidden."""
    fields = ("chat_id", "name", "group", "participants")
    listed = run("imessage_chats", {"limit": 1000, "include_archived": True})["chats"]
    assert len(listed) == 3
    for c in listed:
        assert {k: run("imessage_read", {"chat_id": c["chat_id"], "limit": 1})["chat"][k] for k in fields} == {k: c[k] for k in fields}
    db = sqlite3.connect(tmp_path / "chat.db")
    db.execute("insert into chat values (7, 'any;+;chat950', 'chat950', 'Empty', 'iMessage', 43, 0)")
    db.executemany("insert into chat_handle_join values (?,?)", [(7, 2), (7, 3)])
    db.commit()
    assert "chat950" not in [c["chat_id"] for c in run("imessage_chats", {"limit": 1000, "include_archived": True})["chats"]]
    one = run("imessage_read", {"chat_id": "chat950", "limit": 1})
    assert one["chat"]["participants"] == ["+31600000002", "anna@example.org"] and one["messages"] == []
    with pytest.raises(RuntimeError, match="no conversation"):
        run("imessage_read", {"chat_id": "chat950", "limit": 1, "exclude": "chat950"})


def test_search_ignores_case_and_accents_and_honours_the_window(run):
    hits = run("imessage_search", {"query": "cafe"})["matches"]
    assert [h["chat_id"] for h in hits] == ["chat900"] and hits[0]["chat_name"] == "Hiking crew"
    assert run("imessage_search", {"query": "KOFFIE"})["matches"][0]["text"].startswith("Hoi!")
    assert run("imessage_search", {"query": "prize"})["matches"] == []                     # spam is never searched
    assert run("imessage_search", {"query": "koffie", "since": "2030-01-01"})["matches"] == []


def test_the_three_length_forms_decode():
    spec = importlib.util.spec_from_file_location("imessage_ops", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for text in ("short", "é" * 200, "y" * 70000):
        assert mod.decode_attributed_body(typedstream(text)) == text
    assert mod.decode_attributed_body(b"not a typedstream") is None and mod.decode_attributed_body(None) is None


def test_only_the_owners_own_database_is_opened_outside_tests(monkeypatch):
    monkeypatch.delenv("ICLOUD_MAC_HELPER_TESTING", raising=False)
    monkeypatch.setenv("ICLOUD_IMESSAGE_DB", "/Users/someone-else/Library/Messages/chat.db")   # privacy-ok
    spec = importlib.util.spec_from_file_location("imessage_ops_prod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DB == os.path.expanduser("~/Library/Messages/chat.db")


def test_sending_is_confirmed_from_the_row_messages_writes(run):
    r = run("imessage_send", {"chat_id": "+31600000001", "text": "See you at 10"})
    assert r == {"status": "sent", "message_id": "sent-guid", "delivered": True}
    assert run("imessage_send", {"handle": "new@example.org", "text": "Hello"})["status"] == "sent"      # a new conversation
    run.mode.write_text("fail")
    assert run("imessage_send", {"chat_id": "+31600000001", "text": "x"})["status"] == "failed"
    run.mode.write_text("silent")
    assert run("imessage_send", {"chat_id": "+31600000001", "text": "y"})["status"] == "unconfirmed"


def test_the_mac_itself_never_sends_to_its_blocked_handles_or_by_sms(run, tmp_path):
    with pytest.raises(RuntimeError, match="never sends to bot@example.org"):
        run("imessage_send", {"handle": "bot@example.org", "text": "run this"})
    db = sqlite3.connect(tmp_path / "chat.db")
    db.execute("insert into chat values (6, 'SMS;-;+31600000009', '+31600000009', '', 'SMS', 45, 0)")
    db.commit()
    with pytest.raises(RuntimeError, match="only as SMS"):
        run("imessage_send", {"chat_id": "+31600000009", "text": "x"})
    with pytest.raises(RuntimeError, match="exactly one"):
        run("imessage_send", {"chat_id": "+31600000001", "handle": "a@example.org", "text": "x"})
    run.mode.write_text("deny")
    with pytest.raises(RuntimeError, match="not allowed to control Messages"):
        run("imessage_send", {"chat_id": "+31600000001", "text": "x"})


def test_the_mac_matches_handles_however_they_are_written(run, tmp_path):
    # national form in the setting, international in chat.db; mixed case in chat.db, lower case in the file
    assert [c["chat_id"] for c in run("imessage_chats", {"exclude": "06 00000001"})["chats"]] == ["chat900"]
    (tmp_path / "never.txt").write_text("bot@example.org\n0600000001\n")
    with pytest.raises(RuntimeError, match="never sends to"):
        run("imessage_send", {"chat_id": "+31600000001", "text": "x"})
    with pytest.raises(RuntimeError, match="never sends to"):
        run("imessage_send", {"handle": "BOT@Example.ORG", "text": "x"})
