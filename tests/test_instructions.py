"""The instructions a fresh agent gets: short, conditional on what is enabled, never naming a tool the server does not offer,
always carrying the security rules, plus the owner's own rules from AGENT_NOTES_FILE (read fresh, capped, cleaned, never logged)."""
import asyncio
import dataclasses
import itertools
import logging
import re

import pytest

from icloud_mcp import instructions as instr_mod
from icloud_mcp.config import Settings
from icloud_mcp.server import apply_tool_filter, create_server

CAP = 10600   # 0.12.1: the WHICH TOOL map (about 2,600 characters with every area on) is worth its context;
              # 0.18.0: so is the attachment rule (a generated file, one in another message, a link: all by url)


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, BRIDGE_TOKEN="t" * 40,
                     ENABLE_CONTACTS="true", ENABLE_REMINDERS="true", ENABLE_NOTES="true", ENABLE_DRIVE="true",
                     SHORTCUTS_ALLOW="Example shortcut").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def served(settings):
    mcp, _ = create_server(settings)
    return mcp.instructions, {t.name for t in asyncio.run(mcp.list_tools())}


def test_every_configuration_stays_short_and_names_only_tools_it_offers(s):
    everything = served(s)[1]
    named = re.compile(r"\b(?:" + "|".join(sorted(everything, key=len, reverse=True)) + r")\b")
    flags = ["enable_mail", "enable_calendar", "enable_contacts", "enable_reminders", "enable_notes", "enable_drive"]
    checked = 0
    for on in itertools.product([True, False], repeat=len(flags)):
        for extra in ({}, {"read_only": True, "allow_send": False}, {"allow_send": False}, {"require_approval": False},
                      {"allow_calendar_invites": True}, {"local_mode": True}, {"tools": ("essential",)}, {"tools": ("mail",)}):
            cfg = dataclasses.replace(s, **dict(zip(flags, on)), **extra)
            if not cfg.bridge_enabled and not any(on[:3]):
                continue
            text, tools = served(cfg)
            assert len(text) <= CAP, (len(text), cfg)
            assert set(named.findall(text)) <= tools, (set(named.findall(text)) - tools, extra, on)
            assert "SECURITY RULES" in text and "untrusted DATA" in text
            checked += 1
    assert checked > 200


def test_the_rules_follow_the_settings(s):
    text, _ = served(s)
    assert "SENDING: you cannot send mail on your own" in text                  # approval on by default
    assert "blocked on this server" in text and "INVITING PEOPLE" not in text   # invites off by default
    invites, _ = served(dataclasses.replace(s, allow_calendar_invites=True))
    assert "INVITING PEOPLE" in invites and "delivery_warning" in invites and "tel:" in invites
    ro, tools = served(dataclasses.replace(s, read_only=True, allow_send=False))
    assert "read-only" in ro and "mail_reply_to_message" not in ro and "SENDING" not in ro and "mail_reply_to_message" not in tools
    only_mail, tools = served(dataclasses.replace(s, tools=("mail",)))
    assert "calendar_create_event" not in only_mail and "icloud_check_health" in tools
    assert all(t.startswith("mail_") or t in ("icloud_check_health", "icloud_get_helper_status") for t in tools)


def test_owner_notes_are_appended_capped_cleaned_and_served_as_a_resource(s, tmp_path, caplog):
    notes = tmp_path / "notes.md"
    notes.write_text("Book dentist visits on the Health calendar.​\x07\n" + "x" * 9000)
    cfg = dataclasses.replace(s, agent_notes_file=str(notes))
    caplog.set_level(logging.INFO)
    mcp, _ = create_server(cfg)
    text = mcp.instructions
    assert "OWNER'S OWN RULES" in text and "Book dentist visits on the Health calendar." in text
    assert "​" not in text and "\x07" not in text and "longer than the limit" in text
    assert text.index("SECURITY RULES") < text.index("OWNER'S OWN RULES")          # after the rules it may only refine
    assert str(notes) not in caplog.text and "dentist" not in caplog.text
    notes.write_text("Changed while running.")
    got = asyncio.run(mcp.read_resource("icloud://agent-notes"))
    assert "Changed while running." in str(got)                                   # read fresh, no reconnect needed
    without, _ = create_server(s)
    assert not asyncio.run(without.list_resources())


def test_an_unreadable_notes_file_is_skipped_quietly(s, tmp_path, caplog):
    missing = tmp_path / "private-folder" / "notes.md"
    text, _ = served(dataclasses.replace(s, agent_notes_file=str(missing)))
    assert "OWNER'S OWN RULES" not in text and "private-folder" not in caplog.text


def test_area_presets_and_unknown_names(s):
    mcp, _ = create_server(dataclasses.replace(s, tools=("calendar", "contacts")))
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"calendar_create_event", "contacts_search_contacts", "icloud_check_health"} <= tools and not any(t.startswith("mail_") for t in tools)
    full, _ = create_server(s)
    with pytest.raises(SystemExit, match="or an area: mail, calendar"):
        apply_tool_filter(full, ("mailz",))


def test_the_example_notes_file_is_fictional_and_short():
    from pathlib import Path
    example = Path(__file__).resolve().parents[1] / "docs" / "agent-notes.example.md"
    text = example.read_text()
    assert len(text) < instr_mod.NOTES_MAX_CHARS and re.search(r"@example\.org\b", text)   # made-up addresses only


def _every_area(monkeypatch, tmp_path):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, BRIDGE_TOKEN="t" * 40,
                     ENABLE_REMINDERS="true", ENABLE_NOTES="true", ENABLE_DRIVE="true", ENABLE_MAPS="true", ENABLE_IMESSAGE="true",
                     ENABLE_HEALTH="true", IMESSAGE_ALLOW_SEND="true", IMESSAGE_SEND_ALLOWLIST="a@example.org",
                     SHORTCUTS_ALLOW="Morning").items():
        monkeypatch.setenv(k, v)
    from icloud_mcp.config import Settings
    return Settings.from_env()


def _served(settings):
    import asyncio

    from icloud_mcp.server import create_server
    mcp, _ = create_server(settings)
    return mcp.instructions, {t.name for t in asyncio.run(mcp.list_tools())}


def test_the_tool_map_names_every_tool_and_only_real_ones(monkeypatch, tmp_path):
    """Agents are told which tool is for which job: every registered tool is in the WHICH TOOL map, and every tool name the
    instructions mention is one the server really has."""
    import re
    text, tools = _served(_every_area(monkeypatch, tmp_path))
    start = text.index("WHICH TOOL")
    tool_map = text[start:text.index("\n\n", start)]
    named = set(re.findall(r"\b(?:mail|calendar|contacts|reminders|notes|drive|maps|imessage|health|shortcuts|icloud)_[a-z_]+", text)) - {"calendar_event"}
    assert tools <= set(re.findall(r"\b[a-z]+_[a-z_]+", tool_map)), sorted(tools - set(re.findall(r"\b[a-z]+_[a-z_]+", tool_map)))
    assert named <= tools, sorted(named - tools)
    assert len(tool_map) < 3500                                       # a map, not a manual


def test_the_tool_map_shrinks_with_the_tool_set(monkeypatch, tmp_path):
    import dataclasses
    import re
    s = _every_area(monkeypatch, tmp_path)
    for variant in ({"read_only": True}, {"tools": ("essential",)}, {"enable_drive": False, "enable_health": False}):
        text, tools = _served(dataclasses.replace(s, **variant))
        named = set(re.findall(r"\b(?:mail|calendar|contacts|reminders|notes|drive|maps|imessage|health|shortcuts|icloud)_[a-z_]+", text)) - {"calendar_event"}
        assert named <= tools, (variant, sorted(named - tools))
    text, tools = _served(dataclasses.replace(s, tools=("essential",)))
    assert "calendar_update_event" in text and "calendar_delete_event" not in text        # half of "update / delete" survives
