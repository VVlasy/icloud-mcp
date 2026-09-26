"""The Reminders/Notes bridge: queue semantics, the private HTTPS app, certificate pinning, settings safety, tool gating."""
import asyncio
import json
import dataclasses
import hashlib
import logging
import socket
import stat
import threading
import time

import httpx
import pytest

import icloud_mcp.bridge as bridge_mod
from icloud_mcp.bridge import BridgeError, MacBridge, build_bridge_app, ensure_tls, start_bridge_listener, validate_args
from icloud_mcp.config import Settings
from icloud_mcp.server import build_app, build_instructions, create_server

TOKEN = "tok-" + "a1b2c3d4" * 6            # 52 chars, not a placeholder


@pytest.fixture
def s(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", MCP_PUBLIC_URL="https://mcp.example.net",
                     MCP_OWNER_PASSWORD="a-long-random-owner-password", DATA_DIR=str(tmp_path), ENABLE_REMINDERS="true", BRIDGE_TOKEN=TOKEN,
                     BRIDGE_JOB_TIMEOUT_SECONDS="5").items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


# ------------------------------------------------------------------ argument validation
@pytest.fixture
def rich_ops(monkeypatch):
    ops = {"demo": {"title": ("str", True, 10), "count": ("int", False, 50), "flag": ("bool", False, 0), "due": ("iso", False, 40)}}
    monkeypatch.setattr(bridge_mod, "OPS", ops)
    return ops


def test_arguments_are_validated_strictly(rich_ops):
    assert validate_args("demo", {"title": "hi", "count": 3, "flag": True, "due": "2026-09-21T15:00:00+02:00"}) == \
        {"title": "hi", "count": 3, "flag": True, "due": "2026-09-21T15:00:00+02:00"}
    assert validate_args("demo", {"title": "hi", "due": None})["due"] is None
    for bad, why in [({"title": "x" * 11}, "at most"), ({"title": 5}, "text"), ({"title": "a", "count": 51}, "between"), ({"title": "a", "count": True}, "between"),
                     ({"title": "a", "flag": "yes"}, "true or false"), ({"title": "a", "due": "tomorrow"}, "ISO"), ({"title": "a", "due": "2026-13-45"}, "ISO"),
                     ({"title": "a", "due": "2026-02-30"}, "ISO"), ({"title": "a", "due": "2026-09-21T25:00:00"}, "ISO"), ({"title": "a", "due": "2026-09-21T10:00:00+25:00"}, "ISO"), ({"title": "a", "extra": 1}, "does not take"),
                     ({}, "needs: title")]:
        with pytest.raises(BridgeError, match=why):
            validate_args("demo", bad)
    with pytest.raises(BridgeError, match="Unknown Mac operation"):
        validate_args("rm_rf", {})
    with pytest.raises(BridgeError, match="object"):
        validate_args("demo", ["title"])


def test_the_real_operation_table_is_exactly_the_reminders_notes_and_drive_operations():
    assert set(bridge_mod.OPS) == {"reminder_lists", "reminders_list", "reminder_create", "reminder_update", "reminder_complete", "reminder_delete", "reminder_move",
                                   "reminder_list_create", "reminder_list_update", "reminder_list_delete",
                                   "note_folders", "notes_list", "note_read", "note_create", "note_delete",
                                   "note_folder_create", "note_move", "note_update",
                                   "drive_list", "drive_search", "drive_search_content", "drive_info", "drive_read", "drive_get_file", "drive_write", "drive_mkdir", "drive_move", "drive_trash", "shortcut_run",
                                   "maps_travel_time", "maps_search", "imessage_chats", "imessage_read", "imessage_search", "imessage_send",
                                       "health_summary", "health_day", "health_status", "health_refresh"}
    assert validate_args("reminder_lists", None) == {} and validate_args("reminders_list", {"query": "x", "limit": 5})["limit"] == 5


# ------------------------------------------------------------------ the queue
def poll_in_thread(bridge, results, wait=5):
    def run():
        job = bridge.next_job({"host": "test-mac", "version": "9"}, wait)
        results.append(job)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_an_offline_helper_gives_a_clear_error_immediately():
    b = MacBridge(timeout=2)
    with pytest.raises(BridgeError, match="has not connected since the server started"):
        b.call("reminder_lists")
    b.next_job({"host": "m"}, 0)                                   # one poll makes it online...
    b.last_seen = time.time() - 300                                # ...and five minutes of silence makes it offline again
    with pytest.raises(BridgeError, match="last seen 5 minute"):
        b.call("reminder_lists")


def test_a_call_travels_to_the_helper_and_the_answer_comes_back():
    b = MacBridge(timeout=5)
    b.next_job({"host": "test-mac", "version": "9", "os": "macOS 15"}, 0)     # helper is online
    got = []
    t = poll_in_thread(b, got)
    out = []
    caller = threading.Thread(target=lambda: out.append(b.call("reminder_lists")))
    caller.start()
    t.join(3)
    job = got[0]
    assert job.op == "reminder_lists" and job.args == {}
    assert b.complete(job.id, True, [{"id": "1", "name": "Home"}], "") is True
    caller.join(3)
    assert out == [[{"id": "1", "name": "Home"}]]
    assert "host" not in b.status()["helper"] and "test-mac" not in str(b.status()) and b.status()["online"] is True


def test_a_failure_reported_by_the_mac_becomes_a_tool_error():
    b = MacBridge(timeout=5)
    b.next_job({}, 0)
    got = []
    t = poll_in_thread(b, got)
    errors = []
    def caller():
        try:
            b.call("reminder_lists")
        except BridgeError as e:
            errors.append(str(e))
    c = threading.Thread(target=caller)
    c.start(); t.join(3)
    b.complete(got[0].id, False, None, "macOS has not allowed this helper to control the app")
    c.join(3)
    assert errors == ["macOS has not allowed this helper to control the app"]


def test_a_helper_that_never_answers_times_out_and_the_job_is_cancelled():
    b = MacBridge(timeout=1)
    b.next_job({}, 0)
    started = time.monotonic()
    with pytest.raises(BridgeError, match="did not pick up the request within 1s"):
        b.call("reminder_lists")
    assert time.monotonic() - started < 3
    assert b.next_job({}, 0) is None                               # the cancelled job is never handed out late


def test_a_job_the_mac_picked_up_but_did_not_finish_says_the_mac_was_slow():
    b = MacBridge(timeout=1)
    b.next_job({}, 0)
    got = []
    t = poll_in_thread(b, got)                                     # the helper takes the job and then never answers
    with pytest.raises(BridgeError, match="picked up the request but did not finish within 1s"):
        b.call("reminder_lists")
    t.join(5)
    assert got


def test_status_reports_queue_and_typical_job_time():
    b = MacBridge(timeout=5)
    b.next_job({}, 0)
    for _ in range(3):
        got = []
        t = poll_in_thread(b, got)
        c = threading.Thread(target=lambda: b.call("reminder_lists"))
        c.start()
        t.join(5)
        b.complete(got[0].id, True, [], "")
        c.join(5)
    st = b.status()
    assert st["queue_length"] == 0 and st["median_job_seconds"] is not None and st["median_job_seconds"] < 5


def test_results_for_unknown_or_finished_jobs_are_ignored():
    b = MacBridge(timeout=5)
    assert b.complete("nope", True, [], "") is False
    b.next_job({}, 0)
    got = []
    t = poll_in_thread(b, got)
    c = threading.Thread(target=lambda: b.call("reminder_lists"))
    c.start(); t.join(3)
    assert b.complete(got[0].id, True, [], "") is True
    assert b.complete(got[0].id, True, ["again"], "") is False     # answered once only
    c.join(3)


def test_jobs_are_served_first_in_first_out_and_status_counts_waiting_jobs():
    b = MacBridge(timeout=5)
    b.next_job({}, 0)
    def submit():
        try:
            b.call("reminder_lists")
        except BridgeError:
            pass
    threads = [threading.Thread(target=submit) for _ in range(3)]
    for th in threads:
        th.start(); time.sleep(0.05)
    assert b.status()["jobs_waiting"] == 3
    first = b.next_job({}, 1)
    second = b.next_job({}, 1)
    assert first.deadline <= second.deadline                        # created in order, served in order
    for j in (first, second):
        b.complete(j.id, True, [], "")
    third = b.next_job({}, 1)
    b.complete(third.id, True, [], "")
    for th in threads:
        th.join(3)


def test_a_helper_busy_with_a_long_job_still_counts_as_online():
    b = MacBridge(timeout=60)
    b.next_job({}, 0)
    got = []
    t = poll_in_thread(b, got)
    c = threading.Thread(target=lambda: b.call("reminder_lists"), daemon=True)
    c.start(); t.join(3)
    b.last_seen = time.time() - 120                                 # no polls for two minutes: it is busy running the job
    assert b.status()["online"] is True


# ------------------------------------------------------------------ the private HTTPS app (over plain ASGI here; TLS is tested below)
async def client(s, bridge):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=build_bridge_app(bridge, s)), base_url="http://bridge")


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def test_every_bridge_route_needs_the_token(s):
    async with await client(s, MacBridge()) as c:
        for method, path in (("GET", "/bridge/ping"), ("POST", "/bridge/poll"), ("POST", "/bridge/result")):
            assert (await c.request(method, path)).status_code == 401
            assert (await c.request(method, path, headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/bridge/ping", headers=AUTH)).status_code == 200


async def test_repeated_wrong_tokens_lock_that_address_out_but_never_the_right_token(s):
    async with await client(s, MacBridge()) as c:
        for _ in range(20):
            assert (await c.get("/bridge/ping", headers={"Authorization": "Bearer nope"})).status_code == 401
        assert (await c.get("/bridge/ping", headers={"Authorization": "Bearer nope"})).status_code == 429
        assert (await c.get("/bridge/ping", headers=AUTH)).status_code == 200      # the real helper is never locked out


async def test_poll_and_result_round_trip_over_http(s):
    b = MacBridge(timeout=5)
    async with await client(s, b) as c:
        r = await c.post("/bridge/poll", headers=AUTH, json={"wait": 0, "host": "m"})
        assert r.status_code == 204                                 # idle, and now the helper counts as online
        caller = threading.Thread(target=lambda: b.call("reminder_lists"))
        caller.start()
        job = None
        for _ in range(50):
            r = await c.post("/bridge/poll", headers=AUTH, json={"wait": 1, "host": "m"})
            if r.status_code == 200:
                job = r.json(); break
        assert job and job["op"] == "reminder_lists" and job["args"] == {} and 1 <= job["seconds"] <= 5 - bridge_mod.JOB_MARGIN
        # the helper stops its work JOB_MARGIN seconds before the server stops waiting, so a "too slow" report still arrives
        done = await c.post("/bridge/result", headers=AUTH, json={"job": job["id"], "ok": True, "result": []})
        assert done.json() == {"accepted": True}
        caller.join(3)


async def test_oversized_and_malformed_requests_are_rejected_not_crashed(s):
    async with await client(s, MacBridge()) as c:
        assert (await c.post("/bridge/poll", headers=AUTH, content=b"x" * 2_000_000)).status_code == 400
        assert (await c.post("/bridge/poll", headers=AUTH, content=b"not json")).status_code == 400
        assert (await c.post("/bridge/result", headers=AUTH, json={"nope": 1})).status_code == 400
        assert (await c.post("/bridge/result", headers=AUTH, content=b"x" * 13_000_000)).status_code == 400


async def test_a_result_may_carry_a_file_but_a_poll_may_not_and_neither_without_the_token(s):
    b = MacBridge(timeout=5)
    big = {"job": "no-such-job", "ok": True, "result": {"data_base64": "A" * 7_000_000}}      # a ~5 MB file, base64
    async with await client(s, b) as c:
        assert (await c.post("/bridge/result", headers=AUTH, json=big)).json() == {"accepted": False}   # read, parsed, unknown job
        assert (await c.post("/bridge/poll", headers=AUTH, json=big)).status_code == 400
        assert (await c.post("/bridge/result", json=big)).status_code == 401


async def test_the_token_never_reaches_the_log(s, caplog):
    caplog.set_level(logging.DEBUG)
    async with await client(s, MacBridge()) as c:
        await c.get("/bridge/ping", headers={"Authorization": "Bearer super-secret-wrong-token"})
        await c.get("/bridge/ping", headers=AUTH)
    assert "super-secret-wrong-token" not in caplog.text and TOKEN not in caplog.text
    assert any("refused" in r.getMessage() for r in caplog.records)


async def test_the_public_app_serves_no_bridge_routes(s):
    mcp, _ = create_server(s)
    app = build_app(s, mcp)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.net") as c:
            for path in ("/bridge/ping", "/bridge/poll", "/bridge/result"):
                assert (await c.get(path, headers=AUTH)).status_code in (404, 405)
                assert (await c.post(path, headers=AUTH, json={})).status_code in (404, 405)


# ------------------------------------------------------------------ TLS certificate
def test_certificate_is_created_once_private_and_stable(tmp_path):
    cert, key, fp = ensure_tls(str(tmp_path))
    assert stat.S_IMODE((tmp_path / "bridge-key.pem").stat().st_mode) == 0o600
    cert2, key2, fp2 = ensure_tls(str(tmp_path))                     # a restart must not change the pinned fingerprint
    assert (cert, key, fp) == (cert2, key2, fp2) and len(fp) == 64
    assert (tmp_path / "bridge_fingerprint.txt").read_text().strip() == fp


# ------------------------------------------------------------------ settings safety
@pytest.mark.parametrize("token,why", [("", "BRIDGE_TOKEN"), ("short", "BRIDGE_TOKEN"), ("change-me-" + "x" * 40, "BRIDGE_TOKEN")])
def test_bridge_refuses_to_start_with_a_missing_weak_or_placeholder_token(s, token, why):
    with pytest.raises(SystemExit, match=why):
        dataclasses.replace(s, bridge_token=token).validate_for_server()


def test_bridge_token_must_differ_from_the_owner_password(s):
    with pytest.raises(SystemExit, match="differ"):
        dataclasses.replace(s, bridge_token=s.owner_password + "x" * 20, owner_password=s.owner_password + "x" * 20).validate_for_server()


def test_the_bridge_is_off_by_default_and_needs_no_token_then(s, monkeypatch):
    monkeypatch.delenv("ENABLE_REMINDERS"); monkeypatch.delenv("BRIDGE_TOKEN")
    off = Settings.from_env()
    assert off.bridge_enabled is False and off.enable_notes is False
    off.validate_for_server()


# ------------------------------------------------------------------ tools and instructions
def names(settings):
    async def go():
        mcp, _ = create_server(settings)
        return {t.name: t for t in await mcp.list_tools()}
    return asyncio.run(go())


def test_bridge_tools_exist_only_when_enabled(s):
    on = names(s)
    assert {"icloud_get_helper_status", "reminders_list_lists"} <= set(on)
    off = names(dataclasses.replace(s, enable_reminders=False))
    assert "reminders_list_lists" not in off and "icloud_get_helper_status" not in off
    assert all(v.get("description") for t in (on["reminders_list_lists"], on["icloud_get_helper_status"]) for v in (getattr(t, "input_schema", None) or t.inputSchema).get("properties", {}).values())


def test_a_tool_call_with_no_helper_says_so_plainly(s):
    async def go():
        mcp, _ = create_server(s)
        with pytest.raises(Exception, match="has not connected since the server started"):
            await mcp.call_tool("reminders_list_lists", {})
        status = await mcp.call_tool("icloud_get_helper_status", {})
        return status.content[0].text
    assert '"online": false' in asyncio.run(go())


def test_reminders_list_passes_the_new_arguments_and_shapes_the_answer(s):
    seen = []

    async def go(answer):
        mcp, _ = create_server(s)
        mcp._icloud_bridge.call = lambda op, args=None: seen.append((op, args)) or answer
        result = await mcp.call_tool("reminders_list_reminders", {"list_id": "L1", "query": "milk", "refresh": True, "limit": 5})
        return json.loads(result.content[0].text) if result.content else result
    cached = {"reminders": [{"id": "a"}], "cached": [{"list": "Big", "list_id": "L1", "age_seconds": 90, "refreshing": False}]}
    out = asyncio.run(go(cached))
    assert seen[-1] == ("reminders_list", {"list_id": "L1", "query": "milk", "refresh": True, "limit": 5})
    assert out["count"] == 1 and out["cached"][0]["age_seconds"] == 90
    assert asyncio.run(go([{"id": "a"}, {"id": "b"}]))["count"] == 2                            # an older helper still answers with a bare list
    assert "include_completed" not in {k for t_ in [names(s)["reminders_list_reminders"]] for k in (getattr(t_, "input_schema", None) or t_.inputSchema)["properties"]}


def test_instructions_mention_the_mac_only_when_enabled(s):
    assert "REMINDERS / NOTES" in build_instructions(s) and "if it is offline, say so" in build_instructions(s)
    assert "REMINDERS / NOTES" not in build_instructions(dataclasses.replace(s, enable_reminders=False))


DRIVE_READ = {"drive_list_folder", "drive_search_files", "drive_search_content", "drive_get_info", "drive_read_file", "drive_get_file"}
DRIVE_WRITE = {"drive_write_file", "drive_create_folder", "drive_move_item", "drive_trash_item"}


def test_drive_tools_exist_only_when_enabled_and_writes_only_when_writable(s):
    assert not (DRIVE_READ | DRIVE_WRITE) & set(names(s))
    on = dataclasses.replace(s, enable_drive=True)
    assert (DRIVE_READ | DRIVE_WRITE) <= set(names(on))
    ro = names(dataclasses.replace(on, read_only=True))
    assert DRIVE_READ <= set(ro) and not DRIVE_WRITE & set(ro)
    alone = names(dataclasses.replace(on, enable_reminders=False))                           # Drive alone still starts the bridge
    assert "icloud_get_helper_status" in alone and DRIVE_READ <= set(alone) and "reminders_list_lists" not in alone
    assert names(on)["drive_trash_item"].annotations.destructive_hint is True


def test_drive_tools_pass_exactly_the_given_arguments_to_the_mac(s):
    seen = []

    async def go(tool, args, answer):
        mcp, _ = create_server(dataclasses.replace(s, enable_drive=True))
        mcp._icloud_bridge.call = lambda op, a=None: seen.append((op, a)) or answer
        r = await mcp.call_tool(tool, args)
        return json.loads(r.content[0].text)
    out = asyncio.run(go("drive_read_file", {"path": "Documents/a.pdf", "max_chars": 500}, {"path": "Documents/a.pdf", "text": "hi"}))
    assert seen[-1] == ("drive_read", {"path": "Documents/a.pdf", "max_chars": 500}) and out["text"] == "hi" and "data" in out["notice"]
    out = asyncio.run(go("drive_get_file", {"path": "CV.pdf"}, {"name": "CV.pdf", "data_base64": "JVBERi0="}))
    assert seen[-1] == ("drive_get_file", {"path": "CV.pdf", "max_bytes": 5 * 1024 * 1024}) and out["data_base64"] == "JVBERi0="
    asyncio.run(go("drive_create_folder", {"path": "A/B"}, {"path": "A/B"}))
    assert seen[-1] == ("drive_mkdir", {"path": "A/B"})
    asyncio.run(go("drive_move_item", {"path": "a.txt", "to": "A"}, {"moved": True}))
    assert seen[-1] == ("drive_move", {"path": "a.txt", "to": "A"})
    asyncio.run(go("drive_write_file", {"path": "n.md", "content": "x"}, {}))
    assert seen[-1] == ("drive_write", {"path": "n.md", "content": "x"})                           # overwrite not sent unless true


def test_drive_listings_leave_out_what_the_folder_already_says(s):
    seen = []

    async def go(tool, args, answer):
        mcp, _ = create_server(dataclasses.replace(s, enable_drive=True))
        mcp._icloud_bridge.call = lambda op, a=None: seen.append((op, a)) or answer
        return json.loads((await mcp.call_tool(tool, args)).content[0].text)
    items = [{"path": "Docs/Tax", "name": "Tax", "type": "folder", "modified": "2026-09-20T10:00:00.123456Z"},
             {"path": "Docs/a.pdf", "name": "a.pdf", "type": "file", "modified": "2026-09-20T10:00:00Z", "bytes": 5, "offloaded": False},
             {"path": "Docs/b.pdf", "name": "b.pdf", "type": "file", "modified": "2026-09-20T10:00:00.5+00:00", "bytes": 7, "offloaded": True}]
    out = asyncio.run(go("drive_list_folder", {"path": "Docs"}, {"path": "Docs", "count": 3, "truncated": False, "items": items}))
    assert out["items"] == [{"name": "Tax", "type": "folder", "modified": "2026-09-20T10:00:00Z"},
                            {"name": "a.pdf", "type": "file", "modified": "2026-09-20T10:00:00Z", "bytes": 5},
                            {"name": "b.pdf", "type": "file", "modified": "2026-09-20T10:00:00+00:00", "bytes": 7, "offloaded": True}]
    root = [{"path": "a.txt", "name": "a.txt", "type": "file", "bytes": 1}, {"path": "Other/x.txt", "name": "x.txt", "type": "file"}]
    out = asyncio.run(go("drive_list_folder", {}, {"path": "", "count": 2, "truncated": False, "items": root}))
    assert "path" not in out["items"][0] and out["items"][1]["path"] == "Other/x.txt"               # kept when it says more
    out = asyncio.run(go("drive_search_files", {"query": "a"}, {"query": "a", "count": 1, "items": [dict(items[1])]}))
    assert out["items"] == [{"path": "Docs/a.pdf", "type": "file", "modified": "2026-09-20T10:00:00Z", "bytes": 5, "offloaded": False}]


def test_instructions_mention_drive_only_when_enabled(s):
    assert "ICLOUD DRIVE" not in build_instructions(s)
    drive_only = dataclasses.replace(s, enable_drive=True, enable_reminders=False)
    assert "ICLOUD DRIVE" in build_instructions(drive_only) and "REMINDERS / NOTES" not in build_instructions(drive_only)


# ------------------------------------------------------------------ end to end: real TLS listener + the real helper code + a fake osascript
def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def live(tmp_path, s):
    b = MacBridge(timeout=5)
    cert, key, fp = ensure_tls(str(tmp_path))
    port = free_port()
    server = start_bridge_listener(build_bridge_app(b, s), port, cert, key, host="127.0.0.1")
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield b, {"server": f"https://127.0.0.1:{port}", "token": TOKEN, "fingerprint": fp}
    server.should_exit = True


def test_an_operation_newer_than_the_helper_says_to_update_it(monkeypatch):
    monkeypatch.setitem(bridge_mod.OP_MIN_HELPER, "reminder_lists", "0.10.0")
    b = MacBridge(timeout=1)
    b.next_job({"version": "0.9.3"}, 0)
    with pytest.raises(BridgeError, match=r"helper is 0\.9\.3 but reminder_lists needs 0\.10\.0 or newer: update the helper"):
        b.call("reminder_lists")                                   # compared as numbers: "0.10.0" > "0.9.3"
    b.next_job({"version": "0.10.0"}, 0)
    with pytest.raises(BridgeError, match="did not pick up"):      # new enough: it is queued (nobody polls in this test)
        b.call("reminder_lists")
    b.agent = {}                                                   # version unknown: queued too, the helper decides
    b.last_seen = time.time()
    with pytest.raises(BridgeError, match="did not pick up"):
        b.call("reminder_lists")
