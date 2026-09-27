"""The tool surface every client loads: slim, still valid JSON Schema, still accepting an explicit null, and still carrying the
sentences that keep an agent from doing damage."""
import asyncio
import json

import jsonschema
import pytest

from icloud_mcp.config import Settings
from icloud_mcp.server import create_server, slim_schema

SCHEMA_BUDGET = 52000       # raised for 0.9.0 and 0.10.0 (about 21 new tools), see docs/PERFORMANCE.md
LIST_BUDGET = 180000        # the whole tools/list result: 0.12.2 gave every tool a full description (use, parameters, behavior,
                            # returns, errors) for agents and registry scoring, 80,000 before; see docs/PERFORMANCE.md


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, BRIDGE_TOKEN="t" * 40,
                     ENABLE_CONTACTS="true", ENABLE_REMINDERS="true", ENABLE_NOTES="true", ENABLE_DRIVE="true",
                     ALLOW_CALENDAR_INVITES="true", SHORTCUTS_ALLOW="Example shortcut").items():
        monkeypatch.setenv(k, v)
    server, _ = create_server(Settings.from_env())
    return server


def listed(mcp):
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def leftovers(node):
    """Derived titles, 'default: null' and 'anyOf [X, null]' that slimming should have removed."""
    if isinstance(node, list):
        for x in node:
            yield from leftovers(x)
    elif isinstance(node, dict):
        for k, v in node.items():
            if (k == "title" and isinstance(v, str)) or (k == "default" and v is None) or (k == "anyOf" and {"type": "null"} in v):
                yield k
            yield from leftovers(v)


def test_every_schema_is_valid_and_the_total_stays_in_budget(mcp):
    total = 0
    for tool in listed(mcp).values():
        jsonschema.Draft202012Validator.check_schema(tool.input_schema)
        text = json.dumps(tool.input_schema, separators=(",", ":"), ensure_ascii=False)
        assert not list(leftovers(tool.input_schema)), (tool.name, list(leftovers(tool.input_schema)))
        total += len(text)
    assert total <= SCHEMA_BUDGET, total


def test_no_output_schema_and_no_docstring_indent(mcp):
    """Results are one compact JSON text, so an outputSchema would only describe a structuredContent copy that is never sent."""
    from mcp.types import ListToolsResult
    tools = asyncio.run(mcp.list_tools())
    for tool in tools:
        assert tool.output_schema is None, tool.name
        assert not [line for line in (tool.description or "").splitlines()[1:] if line[:1].isspace()], tool.name
    assert len(ListToolsResult(tools=tools).model_dump_json(by_alias=True, exclude_none=True).encode()) <= LIST_BUDGET


def test_slimming_keeps_meaning():
    node = {"title": "Args", "type": "object", "required": ["a"], "properties": {
        "a": {"title": "A", "type": "integer", "description": "An a."},
        "b": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "A b.", "title": "B"},
        "c": {"anyOf": [{"type": "string"}, {"type": "integer"}], "default": 3},
        "title": {"type": "string", "title": "Title"}}}
    assert slim_schema(node) == {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "integer", "description": "An a."},
        "b": {"type": "string", "description": "A b."},
        "c": {"anyOf": [{"type": "string"}, {"type": "integer"}], "default": 3},
        "title": {"type": "string"}}}                                          # a parameter called 'title' survives


def test_an_explicit_null_is_still_accepted(mcp):
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    model = tools["calendar_list_events"].fn_metadata.arg_model
    got = model.model_validate({"start": "2026-09-01", "end": "2026-09-02", "calendar": None})
    assert got.calendar is None
    model = tools["mail_reply_to_message"].fn_metadata.arg_model
    assert model.model_validate({"folder": "INBOX", "uid": 1, "body": "x", "uidvalidity": None}).uidvalidity is None


def test_destructive_mail_tools_require_uidvalidity(mcp):
    import pydantic
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    base = {"folder": "INBOX", "uids": [1]}
    for name, extra in (("mail_delete_messages", {}), ("mail_move_messages", {"destination": "Archive"}), ("mail_mark_messages", {"read": True})):
        model = tools[name].fn_metadata.arg_model
        for bad in ({}, {"uidvalidity": None}):
            with pytest.raises(pydantic.ValidationError):
                model.model_validate({**base, **extra, **bad})
        assert model.model_validate({**base, **extra, "uidvalidity": 7}).uidvalidity == 7
        assert "uidvalidity" in listed(mcp)[name].input_schema["required"]


@pytest.mark.parametrize("tool, param, phrase", [
    ("mail_move_messages", "uidvalidity", "a renumbered folder is refused"),
    ("mail_delete_messages", "uidvalidity", "a renumbered folder is refused"),
    ("mail_reply_to_message", "uidvalidity", "renumbered folder is then refused"),
    ("calendar_create_event", "attendees", "iCloud emails each one an invitation"),
    ("calendar_create_event", "request_id", "never a second copy"),
    ("contacts_create_contact", "request_id", "never a second copy"),
    ("calendar_delete_event", "occurrence_start", "'recurrence_id' if set, else its 'start'. Omit to delete the whole series"),
    ("calendar_update_event", "occurrence_start", "'recurrence_id' if set, else its 'start'"),
    ("calendar_respond_to_event", "occurrence_start", "'recurrence_id' if set, else its 'start'"),
    ("drive_search_content", "download", "kept on the Mac"),
])
def test_the_safety_sentences_survive(mcp, tool, param, phrase):
    assert phrase in listed(mcp)[tool].input_schema["properties"][param]["description"]


# ------------------------------------------------------------------------------------------------ names
PREFIXES = ("mail", "calendar", "contacts", "reminders", "notes", "drive", "shortcuts", "icloud", "maps", "imessage", "health")
VERBS = {"append", "check", "complete", "create", "delete", "extract", "find", "forward", "get", "list", "mark", "move", "read",
         "refresh", "reply", "respond", "run", "search", "send", "trash", "undo", "unsubscribe", "update", "write"}


def test_every_tool_name_is_prefix_verb_noun(mcp):
    """<area>_<verb>_<noun> for every tool (0.12.0): a verb alone (mail_send, contacts_get) is not a name."""
    for name in listed(mcp):
        prefix, _, rest = name.partition("_")
        verb, _, noun = rest.partition("_")
        assert prefix in PREFIXES and verb in VERBS and noun, name


def served_text(server):
    """Everything an agent reads: instructions, tool and parameter descriptions, and every prompt as rendered."""
    parts = [server.instructions or ""]
    for tool in asyncio.run(server.list_tools()):
        parts += [tool.name, tool.description or "", json.dumps(tool.input_schema)]
    for prompt in asyncio.run(server.list_prompts()):
        args = {a.name: "x" for a in (prompt.arguments or []) if a.required}
        parts.append(str(asyncio.run(server.get_prompt(prompt.name, args))))
    return "\n".join(parts)


def test_no_old_tool_name_is_served_anywhere(mcp, monkeypatch):
    import dataclasses
    import re

    from icloud_mcp import bridge as bridge_mod
    from icloud_mcp.server import RENAMED
    old = re.compile(r"\b(?:" + "|".join(RENAMED) + r")\b")
    s = Settings.from_env()
    for extra in ({}, {"read_only": True, "allow_send": False}, {"require_approval": False}, {"local_mode": True},
                  {"enable_reminders": False, "enable_notes": False, "enable_drive": False}):
        server, _ = create_server(dataclasses.replace(s, **extra))
        found = set(old.findall(served_text(server)))
        assert not found - {"drive_info"}, (found, extra)
    bridge_source = open(bridge_mod.__file__).read()
    # Mac helper operations keep their own names (drive_info, drive_list, notes_list, ...); only prose may not use old tool names
    assert not set(old.findall(bridge_source)) - set(bridge_mod.OPS) - {"drive_info"}


def test_tools_setting_still_accepts_the_old_names(mcp, caplog):
    import logging

    from icloud_mcp.server import apply_tool_filter
    server, _ = create_server(Settings.from_env())
    with caplog.at_level(logging.WARNING):
        apply_tool_filter(server, ("mail_changes", "icloud_now", "mail_search_messages"))
    assert set(listed(server)) == {"mail_list_changes", "icloud_get_time", "mail_search_messages"}
    assert "renamed to mail_list_changes" in caplog.text


def test_the_stale_name_check_would_see_a_leftover(mcp):
    assert "mail_list_senders" in served_text(mcp) and "replies" in served_text(mcp).lower()   # tools and prompts are really read
