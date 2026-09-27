"""How much text the server hands an agent before it does anything: per-tool description and parameter-schema sizes, the
whole tools/list result as sent, plus the instructions, for the configuration with every area on.

    python dev/tool_surface.py            # totals and the 15 largest tools
    python dev/tool_surface.py --all      # every tool
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

EVERYTHING_ON = dict(
    ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", MCP_PUBLIC_URL="https://mcp.example.com",
    MCP_OWNER_PASSWORD="x" * 16, ENABLE_CONTACTS="true", ENABLE_REMINDERS="true", ENABLE_NOTES="true", ENABLE_DRIVE="true", ENABLE_MAPS="true", ENABLE_IMESSAGE="true", IMESSAGE_ALLOW_SEND="true",
    BRIDGE_TOKEN="t" * 40, SHORTCUTS_ALLOW="Example shortcut",
)


def measure() -> tuple[list[dict], int, int]:
    os.environ.update(EVERYTHING_ON, DATA_DIR=tempfile.mkdtemp(prefix="icmcp-surface-"))
    from icloud_mcp.config import Settings
    from icloud_mcp.server import build_instructions, create_server
    from mcp.types import ListToolsResult

    s = Settings.from_env()
    tools = asyncio.run(create_server(s)[0].list_tools())
    rows = [{"tool": t.name, "description": len(t.description or ""),
             "schema": len(json.dumps(t.input_schema, separators=(",", ":"), ensure_ascii=False)),
             "output_schema": len(json.dumps(t.output_schema, separators=(",", ":"))) if t.output_schema else 0} for t in tools]
    wire = len(ListToolsResult(tools=tools).model_dump_json(by_alias=True, exclude_none=True).encode())
    return rows, len(build_instructions(s)), wire


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    rows, instructions, wire = measure()
    rows.sort(key=lambda r: r["description"] + r["schema"], reverse=True)
    print("| tool | description chars | schema chars |\n|---|---|---|")
    for r in rows if args.all else rows[:15]:
        print(f"| {r['tool']} | {r['description']} | {r['schema']} |")
    print(f"\n**{len(rows)} tools**: {sum(r['description'] for r in rows):,} description chars, "
          f"{sum(r['schema'] for r in rows):,} schema chars, {sum(r['output_schema'] for r in rows):,} output-schema chars; "
          f"tools/list result {wire:,} bytes; instructions {instructions:,} chars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
