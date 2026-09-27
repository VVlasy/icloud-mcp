"""mail_get_messages: a batch of messages in one IMAP round trip, never marking anything read."""
import asyncio
import contextlib
import dataclasses
from email.message import EmailMessage

import pytest

from icloud_mcp.config import Settings
from icloud_mcp.mail import MAX_BULK_MESSAGES, UNTRUSTED_NOTICE, MailError, MailService
from icloud_mcp.server import create_server


def _raw(n: int, body: str) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = f"Sender {n} <s{n}@example.org>", "me@icloud.com", f"Message {n}", f"<m{n}@example.org>"
    m.set_content(body)
    return bytes(m)


class FakeIMAP:
    def __init__(self, store):
        self.store, self.fetches, self.selected = store, [], []

    def select_folder(self, folder, readonly=False):
        self.selected.append((folder, readonly))
        return {}

    def fetch(self, uids, parts):
        self.fetches.append((list(uids), list(parts)))
        return {u: {b"BODY[]": self.store[u], b"FLAGS": (b"\\Flagged",) if u == 2 else (), b"INTERNALDATE": None}
                for u in uids if u in self.store}


@pytest.fixture
def mail(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, MAX_BODY_CHARS="30000").items():
        monkeypatch.setenv(k, v)
    fake = FakeIMAP({1: _raw(1, "short"), 2: _raw(2, "x" * 9000), 3: _raw(3, "third")})

    @contextlib.contextmanager
    def fake_imap(self):
        yield fake

    monkeypatch.setattr(MailService, "imap", fake_imap)
    monkeypatch.setattr(MailService, "resolve_folder", lambda self, c, name: "INBOX" if name.upper() == "INBOX" else name)
    return MailService(Settings.from_env()), fake


def test_one_fetch_in_the_requested_order_and_read_only(mail):
    svc, fake = mail
    r = svc.get_messages("inbox", [3, 1, 2, 1])
    assert [m["uid"] for m in r["messages"]] == [3, 1, 2] and r["returned"] == 3      # duplicates dropped, order kept
    bodies = [items for _, items in fake.fetches if "BODY.PEEK[]" in items]
    assert len(bodies) == 1                                                          # all bodies in one FETCH (PEEK: nothing read)
    assert all(i in ("BODYSTRUCTURE", "FLAGS", "INTERNALDATE") or i.startswith("BODY.PEEK[") for _, items in fake.fetches for i in items)
    assert len(fake.fetches) <= 2                          # plus at most one structure look-up, skipped after a search
    assert fake.selected == [("INBOX", True)]
    assert r["notice"] == UNTRUSTED_NOTICE and "notice" not in r["messages"][0]       # one notice for the batch
    assert r["messages"][2]["flagged"] is True and r["messages"][0]["subject"] == "Message 3"


def test_bodies_are_capped_per_message_with_a_hint(mail):
    svc, _ = mail
    r = svc.get_messages("INBOX", [1, 2])
    long = r["messages"][1]
    assert long["text_truncated"] is True and len(long["text"]) == 4000 and "mail_get_message" in r["hint"]
    assert "text_truncated" not in r["messages"][0]                                   # false is left out, like every empty field
    short = svc.get_messages("INBOX", [2], body_chars=50)["messages"][0]["text"]
    assert len(short) == 200                                                          # never below a readable minimum
    whole = svc.get_messages("INBOX", [2], body_chars=10**9)["messages"][0]
    assert whole["text"].strip() == "x" * 9000 and "text_truncated" not in whole      # a huge body_chars is capped by MAX_BODY_CHARS
    svc2 = MailService(dataclasses.replace(svc.s, max_body_chars=1000))
    assert len(svc2.get_messages("INBOX", [2], body_chars=10**9)["messages"][0]["text"]) == 1000


def test_missing_uids_are_reported_not_fatal(mail):
    svc, _ = mail
    r = svc.get_messages("INBOX", [1, 99])
    assert [m["uid"] for m in r["messages"]] == [1] and r["missing_uids"] == [99]


def test_limits_are_enforced(mail):
    svc, fake = mail
    with pytest.raises(MailError, match="at least one"):
        svc.get_messages("INBOX", [])
    with pytest.raises(MailError, match=str(MAX_BULK_MESSAGES)):
        svc.get_messages("INBOX", list(range(1, MAX_BULK_MESSAGES + 2)))
    assert fake.fetches == []


def test_single_message_view_is_unchanged(mail):
    svc, _ = mail
    one = svc.get_message("INBOX", 1)
    r = svc.get_messages("INBOX", [1])
    batch = r["messages"][0]
    assert one["notice"] == UNTRUSTED_NOTICE and one["folder"] == r["folder"] == "INBOX"
    assert {k: v for k, v in one.items() if k not in ("notice", "folder", "uidvalidity")} == batch   # the batch names its folder once


def test_tool_is_registered_as_read_only(mail):
    svc, _ = mail
    mcp, _ = create_server(svc.s)
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    t = tools["mail_get_messages"]
    assert t.annotations.read_only_hint is True and "mail_search_messages" in t.description
