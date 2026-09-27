"""A restart or a stuck call must not leave Claude with a broken connector (the 'Missing session ID' / hang failure)."""
import asyncio
import dataclasses
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import test_oauth_flow as T
import icloud_mcp.server as server_mod
from icloud_mcp.config import Settings
from icloud_mcp.server import build_app, create_server

HDRS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", MCP_PUBLIC_URL=T.BASE,
                     MCP_OWNER_PASSWORD=T.PASSWORD, DATA_DIR=str(tmp_path)).items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def make_app(settings):
    mcp, _ = create_server(settings)
    return build_app(settings, mcp)


async def token_for(app):
    async with T.client_for(app) as c:
        cid = (await T.register(c)).json()["client_id"]
        verifier, challenge = T.pkce()
        ok, _ = await T.authorize(c, cid, challenge)
        code = parse_qs(urlsplit(ok.headers["location"]).query)["code"][0]
        r = await c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": T.REDIRECT, "client_id": cid, "code_verifier": verifier})
        return r.json()["access_token"]


async def rpc(app, token, payload, session=None):
    headers = {**HDRS, "Authorization": f"Bearer {token}", **({"mcp-session-id": session} if session else {})}
    async with T.client_for(app) as c:
        r = await c.post("/mcp", json=payload, headers=headers)
    body = r.text
    data = json.loads(body.split("data:", 1)[1].strip()) if "data:" in body else (r.json() if body.strip().startswith("{") else {})
    return r, data


async def test_stateful_mode_breaks_across_a_restart_which_is_the_original_failure(s):
    stateful = dataclasses.replace(s, stateless_http=False)
    token = await token_for(make_app(stateful))
    r, _ = await rpc(make_app(stateful), token, INIT)                          # session lives only inside this app instance
    session = r.headers.get("mcp-session-id")
    assert session, "stateful mode should hand out a session id"
    r2, _ = await rpc(make_app(stateful), token, LIST, session=session)        # 'restart': a fresh app instance, same stored tokens
    assert r2.status_code in (400, 404)                                        # what Claude saw as 'Missing session ID'


async def test_default_stateless_mode_survives_a_restart_without_any_session(s):
    assert s.stateless_http is True                                            # the shipped default
    token = await token_for(make_app(s))
    r, data = await rpc(make_app(s), token, INIT)
    assert r.status_code == 200 and not r.headers.get("mcp-session-id") and data["result"]["serverInfo"]["name"] == "iCloud"
    r2, data2 = await rpc(make_app(s), token, LIST)                            # a brand-new instance, no initialize, no session id
    assert r2.status_code == 200 and len(data2["result"]["tools"]) >= 18
    r3, data3 = await rpc(make_app(s), token, LIST, session="a-session-id-from-before-the-restart")   # stale id from an older connection
    assert r3.status_code == 200 and len(data3["result"]["tools"]) >= 18


async def test_no_token_is_still_rejected_in_stateless_mode(s):
    r, _ = await rpc(make_app(s), "not-a-real-token", LIST)
    assert r.status_code == 401


async def test_a_tool_call_is_answered_with_one_plain_json_body(s):
    assert s.json_response is True                                             # the shipped default
    token = await token_for(make_app(s))
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "icloud_get_time", "arguments": {}}}
    r, data = await rpc(make_app(s), token, call)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    content = data["result"]["content"]
    assert len(content) == 1 and "weekday" in json.loads(content[0]["text"]) and "structuredContent" not in data["result"]
    r, data = await rpc(make_app(s), token, {**call, "params": {"name": "icloud_get_time", "arguments": {"timezone": "Mars/Base"}}})
    assert r.status_code == 200 and data["result"]["isError"] is True                 # errors stay is_error text results
    r, _ = await rpc(make_app(s), "not-a-real-token", call)
    assert r.status_code == 401


def test_json_responses_cap_the_tool_timeout_below_cloudflares_limit(s, monkeypatch):
    monkeypatch.setenv("TOOL_TIMEOUT_SECONDS", "150")
    create_server(Settings.from_env())
    assert server_mod._tool_timeout == 90.0
    monkeypatch.setenv("MCP_JSON_RESPONSE", "false")                          # event stream: headers go out at once, no cap
    create_server(Settings.from_env())
    assert server_mod._tool_timeout == 150.0


async def test_a_hung_tool_call_becomes_an_error_not_a_hang(s, monkeypatch):
    monkeypatch.setattr(server_mod, "_tool_timeout", 0.3)
    def slow():
        time.sleep(3)
    wrapped = server_mod._guard(slow)
    started = time.monotonic()
    with pytest.raises(Exception, match="took longer than"):
        await wrapped()
    assert time.monotonic() - started < 2


def test_tool_timeout_is_configurable_and_applied(s, monkeypatch):
    monkeypatch.setenv("TOOL_TIMEOUT_SECONDS", "42")
    create_server(Settings.from_env())
    assert server_mod._tool_timeout == 42.0
