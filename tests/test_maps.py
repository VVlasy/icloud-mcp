"""Apple Maps through the Mac helper: tools only when ENABLE_MAPS is on, exactly the given arguments go to the Mac, repeat questions
are answered from a short cache, and the calendar's travel-time rule switches from 'measured or none' to 'measured, else a labelled
Apple Maps estimate' only when the tool is there."""
import asyncio
import dataclasses
import json

import pytest

from icloud_mcp.config import Settings
from icloud_mcp.server import create_server

TRIP = {"origin": {"name": "Utrecht Centraal"}, "destination": {"name": "Domtoren"}, "minutes": 4, "distance_km": 1.0,
        "travel_routing": "BICYCLE", "source": "Apple Maps estimate"}


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16, BRIDGE_TOKEN="t" * 40,
                     ENABLE_MAPS="true").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def tools(settings):
    async def go():
        mcp, _ = create_server(settings)
        return {t.name for t in await mcp.list_tools()}
    return asyncio.run(go())


def test_maps_tools_exist_only_when_enabled(s):
    assert {"maps_get_travel_time", "maps_search_places"} <= tools(s)
    assert not {"maps_get_travel_time", "maps_search_places"} & tools(dataclasses.replace(s, enable_maps=False))
    assert s.bridge_enabled and "icloud_get_helper_status" in tools(s)                 # Maps alone starts the Mac bridge


def test_exactly_the_given_arguments_go_to_the_mac_and_repeats_come_from_the_cache(s):
    seen = []

    async def go():
        mcp, _ = create_server(s)
        mcp._icloud_bridge.call = lambda op, a=None: seen.append((op, a)) or TRIP
        first = json.loads((await mcp.call_tool("maps_get_travel_time", {"origin": "Utrecht Centraal", "destination": "Domtoren",
                                                                         "arrive_at": "2026-09-26T10:00"})).content[0].text)
        again = json.loads((await mcp.call_tool("maps_get_travel_time", {"origin": "utrecht centraal ", "destination": "Domtoren",
                                                                         "arrive_at": "2026-09-26T10:00"})).content[0].text)
        await mcp.call_tool("maps_search_places", {"query": "bike repair", "near": "Utrecht", "limit": 50})
        return first, again
    first, again = asyncio.run(go())
    assert seen[0] == ("maps_travel_time", {"origin": "Utrecht Centraal", "destination": "Domtoren", "mode": "cycling",
                                            "arrive_at": "2026-09-26T10:00"})
    assert first["minutes"] == 4 and "Apple Maps" in first["notice"] and again.get("cached") is True
    assert [op for op, _ in seen] == ["maps_travel_time", "maps_search"]              # the repeat did not reach the Mac
    assert seen[1][1] == {"query": "bike repair", "near": "Utrecht", "limit": 20}      # limit capped at 20


def test_a_different_time_is_never_answered_from_the_cache(s):
    seen = []

    async def go():
        mcp, _ = create_server(s)
        mcp._icloud_bridge.call = lambda op, a=None: seen.append(a) or TRIP
        for t in ("2026-09-26T10:00", "2026-09-26T10:05"):
            await mcp.call_tool("maps_get_travel_time", {"origin": "A", "destination": "B", "arrive_at": t})
    asyncio.run(go())
    assert [a["arrive_at"] for a in seen] == ["2026-09-26T10:00", "2026-09-26T10:05"]


def test_depart_and_arrive_together_are_refused(s):
    from mcp.server.mcpserver.exceptions import ToolError

    async def go():
        mcp, _ = create_server(s)
        mcp._icloud_bridge.call = lambda op, a=None: TRIP
        await mcp.call_tool("maps_get_travel_time", {"origin": "A", "destination": "B", "depart_at": "2026-09-26T09:00",
                                                     "arrive_at": "2026-09-26T10:00"})
    with pytest.raises(ToolError, match="not both"):
        asyncio.run(go())


def test_the_travel_rule_switches_with_the_maps_tool(s):
    with_maps = create_server(s)[0].instructions
    without = create_server(dataclasses.replace(s, enable_maps=False))[0].instructions
    assert "maps_get_travel_time" in with_maps and "Apple Maps estimate" in with_maps and "Never invent one" in with_maps
    assert "measured one or none, never a guess" in without and "maps_get_travel_time" not in without
    assert "Apple Maps" in with_maps.split("\n\n")[1] or "Apple Maps" in with_maps[:400]            # listed among the areas


def test_the_helper_runs_the_maps_program_with_one_json_argument():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "mac-helper" / "icloud_mac_helper.py"
    spec = importlib.util.spec_from_file_location("helper_for_maps", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    cmd = helper.build_command("maps_search", {"query": "x"})
    assert cmd[0].endswith("bin/maps-cli") and cmd[1] == "maps_search" and json.loads(cmd[2]) == {"query": "x"}


@pytest.mark.parametrize("extra", [{}, {"read_only": True, "allow_send": False}, {"enable_mail": False, "enable_contacts": False},
                                   {"enable_reminders": True, "enable_notes": True, "enable_drive": True}, {"tools": ("essential",)}])
def test_instructions_with_maps_stay_short_and_name_only_offered_tools(s, extra):
    import re
    cfg = dataclasses.replace(s, **extra)
    mcp, _ = create_server(cfg)
    offered = {t.name for t in asyncio.run(mcp.list_tools())}
    every = tools(dataclasses.replace(s, enable_reminders=True, enable_notes=True, enable_drive=True))
    named = set(re.findall(r"\b(?:" + "|".join(sorted(every, key=len, reverse=True)) + r")\b", mcp.instructions))
    assert len(mcp.instructions) <= 10600 and named <= offered, named - offered     # the cap of tests/test_instructions.py
