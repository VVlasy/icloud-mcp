"""Local mode: a desktop client on the same computer starts the server over stdio, with no OAuth, public URL or outbox page."""
import asyncio
import contextlib
import dataclasses
import os
import sys

import pytest

from icloud_mcp.config import Settings
from icloud_mcp.mail import MailService
from icloud_mcp.server import build_instructions, create_server, load_env_file


class FakeIMAP:
    def __init__(self):
        self.appended = []

    def append(self, folder, raw, flags=(), msg_time=None):
        self.appended.append((folder, raw, list(flags)))


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    for k in ("MCP_PUBLIC_URL", "MCP_OWNER_PASSWORD", "BRIDGE_HOST", "SEND_REQUIRES_APPROVAL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     ICLOUD_DISPLAY_NAME="Me", ALLOW_SEND="true").items():
        monkeypatch.setenv(k, v)
    return dataclasses.replace(Settings.from_env(), local_mode=True)


@pytest.fixture
def fake_mail(monkeypatch):
    fake, sent = FakeIMAP(), []

    @contextlib.contextmanager
    def fake_imap(self):
        yield fake

    monkeypatch.setattr(MailService, "imap", fake_imap)
    monkeypatch.setattr(MailService, "resolve_folder", lambda self, c, name: {"sent": "Sent Messages", "drafts": "Drafts"}.get(name.lower(), name))
    monkeypatch.setattr(MailService, "_smtp_send", lambda self, msg, rcpts: sent.append((msg, list(rcpts))) or {})
    return fake, sent


# ------------------------------------------------------------------ settings
def test_local_needs_no_public_url_or_owner_password(local_env):
    local_env.validate_for_local()                      # must not raise
    with pytest.raises(SystemExit, match="MCP_OWNER_PASSWORD|MCP_PUBLIC_URL"):
        local_env.validate_for_server()


def test_local_still_refuses_placeholders_and_weak_bridge_tokens(local_env):
    with pytest.raises(SystemExit, match="placeholder"):
        dataclasses.replace(local_env, app_password="xxxx-xxxx-xxxx-xxxx").validate_for_local()
    with pytest.raises(SystemExit, match="BRIDGE_TOKEN"):
        dataclasses.replace(local_env, enable_notes=True, bridge_token="short").validate_for_local()


def test_bind_addresses_default_to_loopback(monkeypatch):
    monkeypatch.delenv("BRIDGE_HOST", raising=False)
    monkeypatch.delenv("MCP_HOST", raising=False)
    assert Settings.from_env().bridge_host == "127.0.0.1" and Settings.from_env().host == "127.0.0.1"
    monkeypatch.setenv("BRIDGE_HOST", "0.0.0.0")            # what the Docker image sets, so compose can publish the port
    assert Settings.from_env().bridge_host == "0.0.0.0"


# ------------------------------------------------------------------ sending
def test_approval_in_local_mode_saves_to_drafts_and_sends_nothing(local_env, fake_mail):
    fake, sent = fake_mail
    r = MailService(local_env).send(to=["bob@example.org"], subject="Hello", body="Hi Bob")
    assert r["status"] == "saved_to_drafts_for_owner_approval" and r["sent"] is False and "NOT SENT" in r["notice"]
    assert sent == []
    (folder, raw, flags), = fake.appended
    assert folder == "Drafts" and b"Subject: Hello" in raw and any("Draft" in str(f) for f in flags)
    assert "approve_at" not in r                         # there is no outbox page to point at


def test_local_mode_without_approval_sends_directly(local_env, fake_mail):
    _, sent = fake_mail
    r = MailService(dataclasses.replace(local_env, require_approval=False)).send(to=["bob@example.org"], subject="Hi", body="x")
    assert r["status"] == "sent" and len(sent) == 1


def test_instructions_tell_the_agent_mail_waits_in_drafts(local_env):
    text = build_instructions(local_env)
    assert "Drafts" in text and "saved_to_drafts_for_owner_approval" in text and "approve_at" not in text


# ------------------------------------------------------------------ server
def test_local_server_has_no_oauth_and_the_same_tools(local_env, tmp_path, monkeypatch):
    mcp, provider = create_server(local_env)
    assert provider is None
    local_tools = {t.name for t in asyncio.run(mcp.list_tools())}
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.setenv("MCP_OWNER_PASSWORD", "x" * 16)
    remote, _ = create_server(Settings.from_env())
    # the one tool apart: an upload URL needs the HTTP server a hosted deployment has and a desktop client over stdio does not
    assert local_tools == {t.name for t in asyncio.run(remote.list_tools())} - {"mail_create_upload_url"}
    assert {"mail_send_message", "calendar_create_event", "contacts_search_contacts"} <= local_tools


def test_env_file_fills_gaps_but_never_overrides(tmp_path, monkeypatch):
    f = tmp_path / "icloud.env"
    f.write_text("# comment\nexport ICLOUD_TEST_A=from-file\nICLOUD_TEST_B = 'quoted value'\nICLOUD_TEST_C=from-file\n\nnot a line\n")
    monkeypatch.delenv("ICLOUD_TEST_A", raising=False)
    monkeypatch.delenv("ICLOUD_TEST_B", raising=False)
    monkeypatch.setenv("ICLOUD_TEST_C", "from-client")
    load_env_file(str(f))
    assert os.environ["ICLOUD_TEST_A"] == "from-file"
    assert os.environ["ICLOUD_TEST_B"] == "quoted value"
    assert os.environ["ICLOUD_TEST_C"] == "from-client"
    f.write_text("ICLOUD_TEST_A=true  # keep drafts\nICLOUD_TEST_B='pa ss # word' # comment\nICLOUD_TEST_D=abc#def\n")
    for k in ("ICLOUD_TEST_A", "ICLOUD_TEST_B", "ICLOUD_TEST_D"):
        monkeypatch.delenv(k, raising=False)
    load_env_file(str(f))
    assert os.environ["ICLOUD_TEST_A"] == "true"         # an inline comment must not silently turn a switch off
    assert os.environ["ICLOUD_TEST_B"] == "pa ss # word"
    assert os.environ["ICLOUD_TEST_D"] == "abc#def"      # '#' inside a password stays
    with pytest.raises(SystemExit, match="Cannot read"):
        load_env_file(str(tmp_path / "missing.env"))


def test_stdio_end_to_end_lists_tools_over_a_clean_stdout(tmp_path):
    """Starts `python -m icloud_mcp --local` the way Claude Desktop does and talks MCP to it. Any stray print on stdout would
    corrupt the protocol and fail this test."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env_file = tmp_path / "icloud.env"
    env_file.write_text("ICLOUD_USERNAME=me@icloud.com\nICLOUD_APP_PASSWORD=aaaa-bbbb-cccc-dddd\nICLOUD_DISPLAY_NAME=Me\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ICLOUD_", "MCP_", "BRIDGE_", "ENABLE_"))}
    env["DATA_DIR"] = str(tmp_path / "data")
    params = StdioServerParameters(command=sys.executable, args=["-m", "icloud_mcp", "--local", "--env-file", str(env_file)], env=env)

    async def run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            return init, {t.name for t in tools.tools}

    init, names = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert init.server_info.name == "iCloud"
    assert "Drafts" in (init.instructions or "")
    assert {"mail_search_messages", "mail_send_message", "calendar_list_events", "contacts_search_contacts"} <= names
    assert os.path.isdir(tmp_path / "data")


def test_data_dir_probe_leaves_nothing_behind_and_tolerates_parallel_starts(local_env, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from icloud_mcp.server import _ensure_data_dir

    s = dataclasses.replace(local_env, data_dir=str(tmp_path / "d"))
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: _ensure_data_dir(s), range(40)))
    assert os.listdir(tmp_path / "d") == []
