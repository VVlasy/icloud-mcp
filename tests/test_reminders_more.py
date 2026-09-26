"""Repeating reminders, extra alerts, completed reminders and list management: what the server sends to the Mac, what it refuses
before the Mac is asked, and the preview-and-token rule for deleting a list (Reminders has no trash)."""
import asyncio
import dataclasses
import json

import pytest

import icloud_mcp.bridge as bridge_mod
from icloud_mcp.bridge import BridgeError, MacBridge
from icloud_mcp.config import Settings
from icloud_mcp.server import create_server


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, BRIDGE_TOKEN="t" * 40,
                     ENABLE_REMINDERS="true").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def run(s, tool, args, answer=None):
    seen = []

    async def go():
        mcp, _ = create_server(s)
        mcp._icloud_bridge.call = lambda op, a=None: seen.append((op, a)) or (answer(op, a) if callable(answer) else answer)
        r = await mcp.call_tool(tool, args)
        return json.loads(r.content[0].text)
    return asyncio.run(go()), seen


def test_repeat_and_alerts_cross_the_bridge_as_text(s):
    _, seen = run(s, "reminders_create_reminder", {"title": "Deworm the cats", "due": "2026-10-01", "repeat": "FREQ=MONTHLY;INTERVAL=3",
                                          "alerts_minutes_before": [1440, 30], "alerts_at": ["2026-09-30T18:00"]}, {"id": "r1"})
    assert seen == [("reminder_create", {"title": "Deworm the cats", "due": "2026-10-01", "repeat": "FREQ=MONTHLY;INTERVAL=3",
                                         "alerts_before": "1440,30", "alerts_at": "2026-09-30T18:00"})]
    _, seen = run(s, "reminders_update_reminder", {"id": "r1", "clear_repeat": True, "alerts_minutes_before": [], "alerts_at": []}, {"id": "r1"})
    assert seen == [("reminder_update", {"id": "r1", "clear_repeat": True, "alerts_before": "", "alerts_at": ""})]   # [] and [] clear
    _, seen = run(s, "reminders_update_reminder", {"id": "r1", "title": "x"}, {"id": "r1"})
    assert seen == [("reminder_update", {"id": "r1", "title": "x"})]                # alerts untouched unless given


HOURS = ",".join(str(h) for h in range(24))


@pytest.mark.parametrize("rule", ["FREQ=HOURLY", "FREQ=MINUTELY", "FREQ=SECONDLY", "INTERVAL=2", f"FREQ=DAILY;BYHOUR={HOURS};BYMINUTE=0,20,40"])
def test_rules_finer_than_daily_are_refused_before_the_mac_is_asked(s, rule, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError
    seen = []
    monkeypatch.setattr(MacBridge, "call", lambda self, op, a=None: seen.append(op))
    with pytest.raises(ToolError, match="repeats at most daily"):
        run(s, "reminders_create_reminder", {"title": "x", "due": "2026-10-01", "repeat": rule}, {"id": "r1"})


def test_completed_reminders_are_asked_for_only_when_wanted(s):
    _, seen = run(s, "reminders_list_reminders", {}, {"reminders": []})
    assert seen == [("reminders_list", {"limit": 50})]
    _, seen = run(s, "reminders_list_reminders", {"completed": "only", "completed_since": "2026-09-01"}, {"reminders": []})
    assert seen == [("reminders_list", {"limit": 50, "completed": "only", "completed_since": "2026-09-01"})]


def test_deleting_a_list_with_reminders_needs_the_preview_token(s):
    items = {"reminders": [{"id": "a", "title": "Milk"}, {"id": "b", "title": "Eggs"}]}
    answer = lambda op, a: items if op == "reminders_list" else {"deleted": True}              # noqa: E731
    out, seen = run(s, "reminders_delete_list", {"list_id": "L1", "name": "Groceries"}, answer)
    assert out["deleted"] is False and out["reminders"] == 2 and out["sample"] == ["Milk", "Eggs"] and out["confirm_token"]
    assert [op for op, _ in seen] == ["reminders_list"]                                          # nothing deleted yet
    done, seen = run(s, "reminders_delete_list", {"list_id": "L1", "name": "Groceries", "confirm_token": out["confirm_token"]}, answer)
    assert seen[-1] == ("reminder_list_delete", {"list_id": "L1", "name": "Groceries", "delete_reminders": True})
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="confirm_token"):                                        # a bad token deletes nothing
        run(s, "reminders_delete_list", {"list_id": "L1", "name": "Groceries", "confirm_token": "1.forged"}, answer)
    empty = lambda op, a: {"reminders": []} if op == "reminders_list_reminders" else {"deleted": True}     # noqa: E731
    _, seen = run(s, "reminders_delete_list", {"list_id": "L2", "name": "Empty"}, empty)
    assert seen[-1] == ("reminder_list_delete", {"list_id": "L2", "name": "Empty", "delete_reminders": False})


def test_an_older_helper_is_told_to_update_for_new_arguments():
    b = MacBridge(timeout=1)
    b.next_job({"version": "0.4.2"}, 0)
    with pytest.raises(BridgeError, match="repeat on reminder_create needs 0.5.0 or newer"):
        b.call("reminder_create", {"title": "x", "due": "2026-10-01", "repeat": "FREQ=WEEKLY"})
    with pytest.raises(BridgeError, match="reminder_list_create needs 0.5.0"):
        b.call("reminder_list_create", {"name": "x"})
    with pytest.raises(BridgeError, match="did not pick up"):                                    # old arguments still work
        b.call("reminder_create", {"title": "x"})


def test_list_tools_are_write_tools(s):
    async def names(settings):
        mcp, _ = create_server(settings)
        return {t.name: t for t in await mcp.list_tools()}
    on = asyncio.run(names(s))
    assert {"reminders_create_list", "reminders_update_list", "reminders_delete_list"} <= set(on)
    assert on["reminders_delete_list"].annotations.destructive_hint is True
    ro = asyncio.run(names(dataclasses.replace(s, read_only=True)))
    assert not {"reminders_create_list", "reminders_update_list", "reminders_delete_list"} & set(ro)


def test_reminder_lists_leave_empty_fields_out_and_name_a_shared_list_once(s):
    def item(i, list_id="L1", **kw):
        return {"id": f"r{i}", "title": f"T{i}", "notes": "", "completed": False, "due": None, "priority": 0, "list": "Home",
                "list_id": list_id, "account": "iCloud", **kw}
    out, _ = run(s, "reminders_list_reminders", {}, {"reminders": [item(1), item(2, priority=5, notes="n")]})
    assert (out["list"], out["list_id"], out["account"]) == ("Home", "L1", "iCloud")                # one list: said once, at the top
    assert out["reminders"] == [{"id": "r1", "title": "T1"}, {"id": "r2", "title": "T2", "notes": "n", "priority": 5}]
    out, _ = run(s, "reminders_list_reminders", {"completed": "all"},
                 {"reminders": [item(1), item(2, list_id="L2", completed=True, completed_at="2026-09-20T10:00:00Z")]})
    assert "list_id" not in out and [r["list_id"] for r in out["reminders"]] == ["L1", "L2"]         # mixed lists: kept per item
    assert out["reminders"][1]["completed"] is True and out["reminders"][1]["completed_at"] and "completed" not in out["reminders"][0]
    out, _ = run(s, "reminders_list_reminders", {}, {"reminders": [item(1, title="")]})
    assert out["reminders"] == [{"id": "r1", "title": ""}] and out["count"] == 1                      # id and title always kept


def test_limits_above_what_the_mac_takes_are_lowered_and_said(s):
    out, seen = run(dataclasses.replace(s, enable_notes=True, enable_drive=True), "reminders_list_reminders", {"limit": 500},
                    {"reminders": [{"id": str(i), "title": "x"} for i in range(200)]})
    assert seen == [("reminders_list", {"limit": 200})] and out["limit_capped"] == 200               # full at the cap: there may be more
    out, seen = run(s, "reminders_list_reminders", {"limit": 500}, {"reminders": [{"id": "1", "title": "x"}]})
    assert seen == [("reminders_list", {"limit": 200})] and "limit_capped" not in out
    out, seen = run(s, "reminders_list_reminders", {"limit": 0}, {"reminders": []})
    assert seen == [("reminders_list", {"limit": 1})]
    on = dataclasses.replace(s, enable_notes=True, enable_drive=True)
    out, seen = run(on, "notes_read_note", {"id": "n1", "max_chars": 10**6}, {"text": "x" * 10, "truncated": True})
    assert seen == [("note_read", {"id": "n1", "max_chars": 100000})] and out["limit_capped"] == 100000
    out, seen = run(on, "notes_read_note", {"id": "n1"}, {"text": "x"})
    assert seen == [("note_read", {"id": "n1"})] and "limit_capped" not in out                      # not given: the helper's default
    for tool, args, op, arg, cap in (("notes_list_notes", {"limit": 101}, "notes_list", "limit", 100),
                                     ("drive_list_folder", {"limit": 5000}, "drive_list", "limit", 1000),
                                     ("drive_search_files", {"query": "a", "limit": 201}, "drive_search", "limit", 200),
                                     ("drive_read_file", {"path": "a.txt", "max_chars": 300000}, "drive_read", "max_chars", 200000)):
        _, seen = run(on, tool, args, [] if tool == "notes_list_notes" else {"items": []})
        assert seen[-1][1][arg] == cap, tool
