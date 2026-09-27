"""End-to-end check of a RUNNING server over real HTTP: OAuth (DCR + PKCE + owner login) then MCP tool calls.

    MCP_PUBLIC_URL=http://localhost:8000 MCP_OWNER_PASSWORD=... python dev/e2e_http.py
"""
import asyncio, base64, hashlib, os, re, secrets, sys
from urllib.parse import parse_qs, urlsplit

import httpx

BASE = os.environ.get("MCP_PUBLIC_URL", "http://localhost:8000").rstrip("/")
PASSWORD = os.environ["MCP_OWNER_PASSWORD"]
REDIRECT = "http://localhost:6274/callback"


async def get_token() -> str:
    async with httpx.AsyncClient(base_url=BASE, follow_redirects=False) as c:
        md = (await c.get("/.well-known/oauth-authorization-server")).json()
        reg = (await c.post("/register", json={"client_name": "e2e", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                               "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})).json()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
                                              "code_challenge": challenge, "code_challenge_method": "S256", "state": "s", "scope": "icloud"})
        login = r.headers["location"]
        assert (await c.get(login)).status_code == 200
        pid = parse_qs(urlsplit(login).query)["p"][0]
        r = await c.post("/login", data={"p": pid, "password": PASSWORD, "action": "approve"})
        code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
        t = await c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                         "client_id": reg["client_id"], "code_verifier": verifier})
        t.raise_for_status()
        print("OAuth OK; token endpoint:", md["token_endpoint"])
        return t.json()["access_token"]


async def main() -> None:
    token = await get_token()
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    async with streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write, *_):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            print(f"{len(tools.tools)} tools:", ", ".join(t.name for t in tools.tools))
            r = await client.call_tool("mail_list_folders", {})
            assert len(r.content) == 1 and r.structured_content is None, "a result is one compact JSON text block"
            print("mail_list_folders ->", r.content[0].text[:300])
            r = await client.call_tool("mail_search_messages", {"folder": "INBOX", "limit": 2})
            print("mail_search_messages ->", r.content[0].text[:300])
            r = await client.call_tool("calendar_list_calendars", {})
            print("calendar_list_calendars ->", r.content[0].text[:200])
            r = await client.call_tool("mail_get_message", {"folder": "INBOX", "uid": 424242})
            print("error path -> isError =", r.is_error, "|", str(r.content)[:160])
    await http.aclose()


asyncio.run(main())
