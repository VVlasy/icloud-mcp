"""Sending iMessages: off unless IMESSAGE_ALLOW_SEND, never to IMESSAGE_NEVER_SEND (the owner's own assistant), only to people on
IMESSAGE_SEND_ALLOWLIST (empty = nobody), and by default only after the owner approves it on /outbox, whatever the mail setting.
Approval re-checks the lists; a message the Mac never got stays queued, one it tried is never sent twice."""
import asyncio
import contextlib
import dataclasses
import json
import re

import httpx
import pytest

from icloud_mcp.bridge import BridgeError
from icloud_mcp.config import Settings
from icloud_mcp.imessage import IMessageError, IMessageService
from icloud_mcp.outbox import Outbox
from icloud_mcp.server import create_server

CHATS = {"chats": [{"chat_id": "anna@example.org", "participants": ["anna@example.org"], "name": ""},
                   {"chat_id": "chat900", "participants": ["anna@example.org", "+31600000002"], "name": "Hiking crew", "group": True},
                   {"chat_id": "bot@example.org", "participants": ["bot@example.org"], "name": ""}]}


def read_one(chats, args):
    """What the Mac's imessage_read answers with limit 1: the chat record (as imessage_chats has it), or the helper's error."""
    c = next((x for x in chats["chats"] if x["chat_id"] == args["chat_id"]), None)
    if c is None:
        raise BridgeError(f"no conversation with chat_id '{args['chat_id']}' (take it from imessage_list_chats)")
    return {"chat": {**c, "participants": sorted(c["participants"])}, "messages": [], "complete": True}


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, BRIDGE_TOKEN="t" * 40,
                     ENABLE_IMESSAGE="true", IMESSAGE_ALLOW_SEND="true", IMESSAGE_SEND_ALLOWLIST="anna@example.org",
                     IMESSAGE_NEVER_SEND="bot@example.org", SEND_REQUIRES_APPROVAL="false").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


class Bridge:
    def __init__(self, send_answer=None, fail=None):
        self.calls, self.send_answer, self.fail = [], send_answer or {"status": "sent", "message_id": "m1"}, fail

    def call(self, op, args=None):
        self.calls.append((op, args))
        if op == "imessage_read":
            return read_one(CHATS, args)
        if self.fail:
            raise self.fail
        return self.send_answer


def svc(s, tmp_path, **kw):
    b = Bridge(**kw)
    return IMessageService(s, b, None), b, Outbox(str(tmp_path), 3600, 20, filename="imessage-outbox.json")


def test_approval_is_on_by_default_even_when_mail_sends_directly(s, tmp_path):
    assert s.require_approval is False and s.imessage_send_requires_approval is True
    m, b, box = svc(s, tmp_path)
    r = m.send("Hi Anna", handle="Anna@Example.org", outbox=box, public_url="https://mcp.example.com")
    assert r["status"] == "queued_for_owner_approval" and r["sent"] is False and len(box.pending()) == 1
    assert "imessage_send" not in [op for op, _ in b.calls]                                     # nothing reached the Mac


def test_the_owners_assistant_is_never_a_recipient(s, tmp_path):
    for allow in (("anna@example.org",), ("*",), ("bot@example.org",)):
        m, b, box = svc(dataclasses.replace(s, imessage_send_allowlist=allow, imessage_send_requires_approval=False), tmp_path)
        with pytest.raises(IMessageError, match="IMESSAGE_NEVER_SEND"):
            m.send("do this", handle="bot@example.org", outbox=box)
        with pytest.raises(IMessageError, match="IMESSAGE_NEVER_SEND"):
            m.send("do this", chat_id="bot@example.org", outbox=box)
        assert not [op for op, _ in b.calls if op == "imessage_send"] and not box.pending()


def test_the_allowlist_decides_who_may_receive(s, tmp_path):
    m, b, box = svc(dataclasses.replace(s, imessage_send_allowlist=()), tmp_path)
    with pytest.raises(IMessageError, match="empty, so nobody"):
        m.send("hi", handle="anna@example.org", outbox=box)
    m, b, box = svc(s, tmp_path)
    with pytest.raises(IMessageError, match=r"Not on IMESSAGE_SEND_ALLOWLIST: \+31600000002"):
        m.send("hi all", chat_id="chat900", outbox=box)                                        # a group needs everyone listed
    m, b, box = svc(dataclasses.replace(s, imessage_send_allowlist=("*",), imessage_send_requires_approval=False), tmp_path)
    assert m.send("hi all", chat_id="chat900", outbox=box)["status"] == "sent"
    assert b.calls[-1] == ("imessage_send", {"chat_id": "chat900", "text": "hi all"})


def test_hidden_chats_cannot_be_sent_to(s, tmp_path):
    m, b, box = svc(dataclasses.replace(s, imessage_hidden_chats=("anna@example.org",)), tmp_path)
    with pytest.raises(IMessageError, match="No conversation"):
        m.send("hi", handle="anna@example.org", outbox=box)


def test_local_mode_never_sends_without_the_owner(s, tmp_path):
    m, b, _ = svc(dataclasses.replace(s, local_mode=True), tmp_path)
    r = m.send("Hi Anna", handle="anna@example.org", outbox=None)
    assert r["status"] == "not_sent_needs_owner" and r["text"] == "Hi Anna" and not [op for op, _ in b.calls if op == "imessage_send"]


def test_release_rechecks_the_lists_and_only_requeues_what_the_mac_never_got(s, tmp_path):
    m, b, box = svc(s, tmp_path)
    q = m.send("Hi Anna", handle="anna@example.org", outbox=box)["outbox_id"]
    m.s = dataclasses.replace(s, imessage_send_allowlist=())                                    # the owner emptied the list meanwhile
    with pytest.raises(IMessageError, match="nobody"):
        m.release(box, q)
    assert not box.pending() and not [op for op, _ in b.calls if op == "imessage_send"]
    m.s = s
    q = m.send("Hi again", handle="anna@example.org", outbox=box)["outbox_id"]
    m.bridge = Bridge(fail=BridgeError("The Mac did not pick up the request within 60s"))
    with pytest.raises(IMessageError, match="still queued"):
        m.release(box, q)
    assert [x.id for x in box.pending()] == [q]                                                 # offline: kept for a retry
    m.bridge = Bridge(fail=BridgeError("The Mac picked up the request but did not finish within 60s"))
    with pytest.raises(BridgeError):
        m.release(box, q)
    assert not box.pending()                                                                    # it may have gone out: never twice
    m.bridge = Bridge()
    q = m.send("Third", handle="anna@example.org", outbox=box)["outbox_id"]
    assert m.release(box, q)["status"] == "sent" and m.bridge.calls[-1] == ("imessage_send", {"handle": "anna@example.org", "text": "Third"})


def test_the_tool_exists_only_when_sending_is_allowed_and_writable(s):
    def names(settings):
        async def go():
            mcp, _ = create_server(settings)
            return {t.name for t in await mcp.list_tools()}
        return asyncio.run(go())
    assert "imessage_send_message" in names(s)
    assert "imessage_send_message" not in names(dataclasses.replace(s, imessage_allow_send=False))
    assert "imessage_send_message" not in names(dataclasses.replace(s, read_only=True))


async def test_the_outbox_page_shows_and_sends_a_queued_imessage(s, monkeypatch):
    mcp, provider = create_server(s)
    sent = []
    mcp._icloud_bridge.call = lambda op, a=None: read_one(CHATS, a) if op == "imessage_read" else (sent.append((op, a)) or {"status": "sent"})
    r = json.loads((await mcp.call_tool("imessage_send_message", {"text": "Lunch <b>tomorrow</b>?", "handle": "anna@example.org"})).content[0].text)
    assert r["status"] == "queued_for_owner_approval" and not sent
    app = mcp.streamable_http_app(host="0.0.0.0")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com") as c:
            page = (await c.post("/outbox", data={"password": "x" * 16})).text
            assert "iMessage to anna@example.org" in page and "Lunch &lt;b&gt;tomorrow&lt;/b&gt;?" in page
            m = re.search(r'name="id" value="([^"]+)"><input type="hidden" name="kind" value="imessage"><input type="hidden" name="action" '
                          r'value="approve"><input type="hidden" name="exp" value="(\d+)"><input type="hidden" name="tok" value="([0-9a-f]+)"', page)
            forged = await c.post("/outbox/act", data={"id": m[1], "kind": "mail", "action": "approve", "exp": m[2], "tok": m[3]})
            assert forged.status_code == 400 and not sent                                         # a token for one queue fits no other
            done = await c.post("/outbox/act", data={"id": m[1], "kind": "imessage", "action": "approve", "exp": m[2], "tok": m[3]})
            assert "Sent" in done.text and sent == [("imessage_send", {"handle": "anna@example.org", "text": "Lunch <b>tomorrow</b>?"})]


def test_never_send_and_hidden_match_the_data_however_it_is_written(s, tmp_path):
    odd = {"chats": [{"chat_id": "Bot@Example.ORG", "participants": ["Bot@Example.ORG"], "name": ""},
                     {"chat_id": "chat901", "participants": ["anna@example.org", "+31 6 1234 5678"], "name": "", "group": True}]}
    for never in (("bot@example.org",), ("0612345678",)):
        m, b, box = svc(dataclasses.replace(s, imessage_never_send=never, imessage_send_allowlist=("*",)), tmp_path)
        b.call = lambda op, a=None, b=b: b.calls.append((op, a)) or read_one(odd, a)
        chat = "Bot@Example.ORG" if "@" in never[0] else "chat901"
        with pytest.raises(IMessageError, match="IMESSAGE_NEVER_SEND"):
            m.send("x", chat_id=chat, outbox=box)
    m, _, _ = svc(dataclasses.replace(s, imessage_hidden_chats=("0612345678",)), tmp_path)
    assert not m._shown("+31612345678") and not m._shown("+31 6 12345678") and m._shown("+31612345679")


def test_release_checks_who_is_in_the_group_now(s, tmp_path):
    m, b, box = svc(dataclasses.replace(s, imessage_send_allowlist=("anna@example.org", "+31600000002")), tmp_path)
    q = m.send("Hi all", chat_id="chat900", outbox=box)["outbox_id"]
    CHATS["chats"][1]["participants"].append("bot@example.org")                 # the assistant was added to the group meanwhile
    try:
        with pytest.raises(IMessageError, match="IMESSAGE_NEVER_SEND"):
            m.release(box, q)
    finally:
        CHATS["chats"][1]["participants"].remove("bot@example.org")
    assert not box.pending() and not [op for op, _ in b.calls if op == "imessage_send"]


def test_the_target_is_read_as_one_chat_and_a_missing_chat_sends_nothing(s, tmp_path):
    m, b, box = svc(dataclasses.replace(s, imessage_send_allowlist=("anna@example.org", "+31600000002"),
                                        imessage_hidden_chats=("+31699999999",)), tmp_path)
    q = m.send("Hi all", chat_id="chat900", outbox=box)["outbox_id"]
    assert b.calls[0] == ("imessage_read", {"chat_id": "chat900", "limit": 1, "exclude": "+31699999999"})   # hidden chats stay hidden
    assert json.loads(box.pending()[0].raw)["to"]["participants"] == [{"handle": "+31600000002"}, {"handle": "anna@example.org"}]
    with pytest.raises(BridgeError, match="no conversation"):
        m.send("Hi", chat_id="chat404", outbox=box)                                       # unknown to the Mac: nothing queued
    assert len(box.pending()) == 1
    CHATS["chats"][1]["chat_id"] = "chat901"                                              # the chat is gone by the time of approval
    try:
        with pytest.raises(IMessageError, match="dropped"):
            m.release(box, q)
    finally:
        CHATS["chats"][1]["chat_id"] = "chat900"
    assert not box.pending() and not [op for op, _ in b.calls if op == "imessage_send"]  # dropped, not put back


def test_the_target_gets_the_same_participants_as_the_chat_list(s, tmp_path):
    """imessage_read's chat record and imessage_chats' entry come from the same chat_groups() on the Mac: _check_chat and
    _permit see the same chat_id, name, group and participants either way, at queue time and at release."""
    m, b, box = svc(dataclasses.replace(s, imessage_send_allowlist=("*",)), tmp_path)
    for c in CHATS["chats"]:
        t = m._target(c["chat_id"], None)
        assert (t["chat_id"], t["name"], t["group"], t["participants"]) == (c["chat_id"], c["name"], c.get("group", False),
                                                                           sorted(c["participants"]))
