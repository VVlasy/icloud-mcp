"""Upload URLs: a file an agent generated reaches a message through the server's own store, never as base64 through the model.
mail_create_upload_url mints the URL, PUT /upload/<token> takes the bytes, attachment_urls attaches them. Nothing here touches the
network: the endpoint is called through Starlette's test client on the app exactly as served."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import hashlib
import json
import time
import types
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest
from starlette.testclient import TestClient

import icloud_mcp.mail as mail_mod
from icloud_mcp import uploads
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailService
from icloud_mcp.server import build_app, create_server
from icloud_mcp.uploads import MAX_PENDING, UploadError, UploadStore

PUBLIC = "https://mcp.example.com"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX = b"PK\x03\x04" + b"\x14\x00" * 4 + b"[Content_Types].xml" + b"\x00" * 300     # a zip container, as Word writes one
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TYPES = ("application/pdf", DOCX_TYPE, "image/png")
SHA = hashlib.sha256(DOCX).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL=PUBLIC, MCP_OWNER_PASSWORD="x" * 16, SEND_REQUIRES_APPROVAL="false",
                     ATTACHMENT_URL_CONTENT_TYPES=",".join(TYPES)).items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def store(tmp_path, **kw) -> UploadStore:
    return UploadStore(str(tmp_path), PUBLIC, **{"ttl": 600, "max_bytes": 4096, "content_types": TYPES, **kw})


# ------------------------------------------------------------------ the store
def test_a_url_is_minted_under_the_public_address_and_recognised(tmp_path):
    st = store(tmp_path)
    token, up = st.create("Report Q3.docx")
    url = st.url(token)
    assert url == f"{PUBLIC}/upload/{token}" and len(token) == 43
    assert (up.filename, up.content_type, up.expected_sha256, up.ready) == ("Report Q3.docx", DOCX_TYPE, None, False)
    assert st.match(url) == token
    assert st.match(url.replace("mcp.example.com", "MCP.EXAMPLE.COM")) == token          # host names are not case-sensitive
    for other in (url + "?x=1", url + "#f", url + "/", url[:-1], "http" + url[5:], f"https://user:pw@mcp.example.com/upload/{token}",
                  url.replace("mcp.example.com", "mcp.example.com:8443"), url.replace("mcp.example.com", "evil.test"),
                  f"https://evil.test/{url}", "not a url", ""):
        assert st.match(other) is None, other
    told = st.describe(token, up)
    assert told == {"url": url, "status": "awaiting_upload", "filename": "Report Q3.docx", "content_type": DOCX_TYPE, "max_bytes": 4096,
                    "expires_at": told["expires_at"]}
    assert told["expires_at"].endswith("+00:00")


def test_the_type_comes_from_the_extension_or_is_given_and_must_be_allowed(tmp_path):
    st = store(tmp_path)
    assert st.create("a.pdf")[1].content_type == "application/pdf"
    assert st.create("photo.PNG")[1].content_type == "image/png"
    assert st.create("x.bin", content_type="image/PNG; charset=x")[1].content_type == "image/png"
    assert st.create("../../etc/report.docx")[1].filename == "report.docx"
    with pytest.raises(UploadError, match="not on this server's ATTACHMENT_URL_CONTENT_TYPES"):
        st.create("sheet.xlsx")
    with pytest.raises(UploadError, match="not known from its extension; pass content_type"):
        st.create("x.nosuchext")
    for bad in ("", "report", ".hidden", "\x00\x07"):
        with pytest.raises(UploadError, match="plain file name with an extension"):
            st.create(bad)
    with pytest.raises(UploadError, match="sha256 is 64 hexadecimal"):
        st.create("a.pdf", sha256="abc")
    assert st.create("a.pdf", sha256="SHA256:" + "A" * 64)[1].expected_sha256 == "a" * 64


def test_put_checks_the_url_the_size_the_signature_and_the_hash(tmp_path):
    st = store(tmp_path)
    token, _ = st.create("report.docx", sha256=SHA)
    with pytest.raises(UploadError, match="unknown or has expired") as e:
        st.put("x" * 43, DOCX)
    assert e.value.status == 404
    with pytest.raises(UploadError, match="unknown or has expired"):
        st.put("short", DOCX)
    with pytest.raises(UploadError, match="body is empty") as e:
        st.put(token, b"")
    assert e.value.status == 400
    with pytest.raises(UploadError, match="larger than 4096") as e:
        st.put(token, b"PK\x03\x04" + b"0" * 5000)
    assert e.value.status == 413
    with pytest.raises(UploadError, match=r"not a real application/vnd\.openxmlformats.*does not start with PK.*created for report\.docx") as e:
        st.put(token, b"<!doctype html><html>sign in</html>")
    assert e.value.status == 415
    with pytest.raises(UploadError, match=r"sha256 is [0-9a-f]{64}, not the " + SHA + " given when the URL was created") as e:
        st.put(token, DOCX + b"!")
    assert e.value.status == 422
    assert st.pending()[0].ready is False                     # nothing was kept from the refused tries
    up = st.put(token, DOCX)
    assert (up.size, up.sha256, up.ready) == (len(DOCX), SHA, True)
    with pytest.raises(UploadError, match="has its file already") as e:
        st.put(token, DOCX)
    assert e.value.status == 409
    assert st.get(token) == {"filename": "report.docx", "content_type": DOCX_TYPE, "content": DOCX, "source": "upload"}


def test_get_needs_a_live_url_with_a_file_that_is_still_intact(tmp_path):
    st = store(tmp_path)
    token, _ = st.create("a.png")
    with pytest.raises(UploadError, match="no file has been sent to this upload URL yet"):
        st.get(token)
    st.put(token, PNG)
    assert st.get(token)["content"] == PNG
    (st.dir / f"{uploads._h(token)}.bin").write_bytes(PNG + b"x")          # changed on disk after the upload
    with pytest.raises(UploadError, match="integrity check"):
        st.get(token)
    with pytest.raises(UploadError, match="unknown or has expired"):        # and it is gone
        st.get(token)
    with pytest.raises(UploadError, match="unknown or has expired"):
        st.get("")


def test_urls_and_files_expire_and_are_removed(tmp_path, monkeypatch):
    st = store(tmp_path, ttl=600)
    token, _ = st.create("a.png")
    st.put(token, PNG)
    orphan = st.dir / ("f" * 64 + ".bin")                                   # a file whose record is gone
    orphan.write_bytes(PNG)
    assert len(list(st.dir.iterdir())) == 3
    assert st.pending()[0].ready and not orphan.exists()
    now = time.time()
    monkeypatch.setattr(uploads, "time", types.SimpleNamespace(time=lambda: now + 601))
    with pytest.raises(UploadError, match="unknown or has expired"):
        st.get(token)
    assert list(st.dir.iterdir()) == [] and st.pending() == []


def test_files_are_private_and_the_token_is_not_on_disk(tmp_path):
    st = store(tmp_path)
    token, _ = st.create("a.png")
    st.put(token, PNG)
    assert oct(st.dir.stat().st_mode)[-3:] == "700"
    for p in st.dir.iterdir():
        assert oct(p.stat().st_mode)[-3:] == "600" and token not in p.name and token not in p.read_bytes().decode("latin-1")


def test_put_ready_holds_a_file_at_once_under_the_size_limits(tmp_path):
    st = store(tmp_path)
    token, up = st.put_ready("../x/Invoice.pdf", "application/PDF; name=x", b"%PDF-1.4 abc")
    assert (up.filename, up.content_type, up.ready, up.source) == ("Invoice.pdf", "application/pdf", True, "mail")
    assert st.get(token) == {"filename": "Invoice.pdf", "content_type": "application/pdf", "content": b"%PDF-1.4 abc", "source": "mail"}
    told = st.describe(token, up)
    assert (told["status"], told["size"], told["sha256"]) == ("ready", 12, hashlib.sha256(b"%PDF-1.4 abc").hexdigest())
    with pytest.raises(UploadError, match="empty"):
        st.put_ready("a.pdf", "application/pdf", b"")
    with pytest.raises(UploadError, match="larger than 4096"):
        st.put_ready("a.pdf", "application/pdf", b"x" * 5000)
    with pytest.raises(UploadError, match="not the 0{64} given"):
        st.put_ready("a.pdf", "application/pdf", b"abc", sha256="0" * 64)
    token2, _ = st.put_ready("noext", "", b"abc")
    assert (st.get(token2)["content_type"], st.get(token2)["filename"]) == ("application/octet-stream", "noext")
    with pytest.raises(UploadError, match="has its file already"):                   # a ready URL takes no upload
        st.put(token, b"%PDF-1.4 other")


def test_at_most_twenty_urls_are_live_and_the_store_is_capped(tmp_path, monkeypatch):
    st = store(tmp_path)
    tokens = [st.create("a.png")[0] for _ in range(MAX_PENDING)]
    with pytest.raises(UploadError, match="already live") as e:
        st.create("a.png")
    assert e.value.status == 429
    monkeypatch.setattr(uploads, "MAX_STORE_BYTES", 2 * len(PNG) + 10)
    st.put(tokens[0], PNG)
    st.put(tokens[1], PNG)
    with pytest.raises(UploadError, match="together") as e:
        st.put(tokens[2], PNG)
    assert e.value.status == 413 and st.get(tokens[1])["content"] == PNG
    with pytest.raises(UploadError, match="no file has been sent"):
        st.get(tokens[2])


# ------------------------------------------------------------------ the endpoint
@pytest.fixture
def served(env):
    s = Settings.from_env()
    mcp, _ = create_server(s)
    with TestClient(build_app(s, mcp), base_url=PUBLIC) as client:
        yield s, mcp, mcp._icloud_uploads, client


def test_put_stores_the_file_and_answers_what_was_kept(served):
    s, mcp, st, client = served
    token, _ = st.create("report.docx")
    url = st.url(token)
    r = client.put(url, content=DOCX)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["size"], body["sha256"], body["url"], body["filename"]) == ("uploaded", len(DOCX), SHA, url, "report.docx")
    assert "attachment_urls" in body["next"] and body["expires_at"].endswith("+00:00")
    assert r.headers["cache-control"] == "no-store"
    assert st.get(token)["content"] == DOCX
    assert client.put(url, content=DOCX).status_code == 409
    assert client.get(url).status_code == 405                         # nothing is ever served back
    assert client.put(f"{PUBLIC}/upload/{'x' * 43}", content=DOCX).status_code == 404
    assert client.put(f"{PUBLIC}/upload/short", content=DOCX).status_code == 404


def test_post_works_too_and_a_form_upload_is_told_what_to_send(served):
    s, mcp, st, client = served
    token, _ = st.create("a.png")
    r = client.post(st.url(token), files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 415 and "curl -T" in r.json()["error"]
    r = client.post(st.url(token), content=PNG, headers={"Content-Type": "application/x-www-form-urlencoded"})   # curl --data-binary
    assert r.status_code == 200 and st.get(token)["content"] == PNG


def test_the_endpoint_refuses_large_wrong_and_empty_bodies(served, monkeypatch):
    s, mcp, st, client = served
    token, _ = st.create("a.png")
    monkeypatch.setattr(st, "max_bytes", 16)
    r = client.put(st.url(token), content=PNG)
    assert r.status_code == 413 and f"{len(PNG)} bytes, larger than 16" in r.json()["error"]
    r = client.put(st.url(token), content=iter([PNG[:10], PNG[10:]]))              # no Content-Length: cut off while streaming
    assert r.status_code == 413 and "larger than 16 bytes" in r.json()["error"]
    monkeypatch.setattr(st, "max_bytes", 4096)
    r = client.put(st.url(token), content=b"<html>")
    assert r.status_code == 415 and "PNG signature" in r.json()["error"]
    r = client.put(st.url(token), content=b"")
    assert r.status_code == 400 and "empty" in r.json()["error"]
    assert client.put(st.url(token), content=PNG).status_code == 200


# ------------------------------------------------------------------ the tool
def call(mcp, name, **args):
    out = asyncio.run(mcp.call_tool(name, args))
    return json.loads(out.content[0].text)


def test_the_tool_mints_a_url_and_says_how_to_use_it(served):
    s, mcp, st, client = served
    got = call(mcp, "mail_create_upload_url", filename="Report Q3.docx", sha256=SHA)
    assert got["url"].startswith(f"{PUBLIC}/upload/") and st.match(got["url"])
    assert (got["content_type"], got["max_bytes"], got["sha256"], got["filename"]) == (DOCX_TYPE, s.attachment_url_max_bytes, SHA, "Report Q3.docx")
    assert got["upload_with"] == f"curl -sS -T 'Report Q3.docx' {got['url']}" and "attachment_urls" in got["then"]
    assert client.put(got["url"], content=DOCX).status_code == 200
    with pytest.raises(Exception, match="not on this server's ATTACHMENT_URL_CONTENT_TYPES"):
        call(mcp, "mail_create_upload_url", filename="sheet.xlsx")
    with pytest.raises(Exception, match="sha256 is 64 hexadecimal"):
        call(mcp, "mail_create_upload_url", filename="a.pdf", sha256="nope")
    assert call(mcp, "mail_create_upload_url", filename="x.data", content_type="image/png")["content_type"] == "image/png"


def test_the_tool_and_the_route_exist_only_where_they_can_work(env, monkeypatch):
    s = Settings.from_env()

    def offered(settings):
        mcp, _ = create_server(settings)
        tools = {t.name for t in asyncio.run(mcp.list_tools())}
        route = any(getattr(r, "path", "") == "/upload/{token}" for r in mcp._custom_starlette_routes)
        return "mail_create_upload_url" in tools, route, "mail_create_upload_url" in (mcp.instructions or "")

    assert offered(s) == (True, True, True)
    assert offered(dataclasses.replace(s, allow_send=False)) == (True, True, True)       # drafts still take files
    for extra in ({"local_mode": True}, {"read_only": True}, {"allow_attachment_uploads": False}, {"enable_mail": False}):
        assert offered(dataclasses.replace(s, **extra)) == (False, False, False), extra
    monkeypatch.setenv("ALLOW_ATTACHMENT_UPLOADS", "false")
    assert Settings.from_env().allow_attachment_uploads is False
    monkeypatch.setenv("ALLOW_ATTACHMENT_UPLOADS", "true")
    monkeypatch.setenv("READ_ONLY", "true")
    assert Settings.from_env().allow_attachment_uploads is False
    monkeypatch.setenv("READ_ONLY", "false")
    assert Settings.from_env().attachment_upload_ttl_seconds == 3600
    monkeypatch.setenv("ATTACHMENT_UPLOAD_TTL_SECONDS", "0")
    with pytest.raises(SystemExit, match="ATTACHMENT_UPLOAD_TTL_SECONDS"):
        Settings.from_env().validate_for_server()


# ------------------------------------------------------------------ through the mail tools
@pytest.fixture
def svc(env, monkeypatch):
    monkeypatch.setattr(mail_mod, "TICKER", type("T", (), {"add": lambda self, fn: None})())
    sent, appended = [], []

    class FakeIMAP:
        def append(self, folder, raw, flags=(), msg_time=None):
            appended.append((folder, raw))
            return b"[APPENDUID 7 101] APPEND completed"

        def select_folder(self, folder, readonly=False):
            return {b"UIDVALIDITY": 7}

        def add_flags(self, uids, flags, silent=False):
            pass

    @contextlib.contextmanager
    def fake_imap(self, **kw):
        yield FakeIMAP()

    monkeypatch.setattr(MailService, "imap", fake_imap)
    monkeypatch.setattr(MailService, "resolve_folder",
                        lambda self, c, name: {"sent": "Sent Messages", "drafts": "Drafts", "trash": "Deleted Messages"}.get(name.lower(), name))
    monkeypatch.setattr(MailService, "_smtp_send", lambda self, msg, rcpts: sent.append((msg, list(rcpts))) or {})
    s = Settings.from_env()
    mcp, _ = create_server(s)
    with TestClient(build_app(s, mcp), base_url=PUBLIC) as client:
        yield s, mcp, mcp._icloud_uploads, client, sent, appended


def files(msg) -> list[tuple[str, str, bytes]]:
    return [(p.get_filename(), p.get_content_type(), p.get_payload(decode=True)) for p in msg.iter_attachments()]


def send(mcp, **extra):
    return call(mcp, "mail_send_message", to=["anna@example.org"], subject="Report", body="Attached.", **extra)


def test_an_uploaded_file_is_attached_without_any_allowlist(svc):
    s, mcp, st, client, sent, appended = svc
    assert s.attachment_url_allowlist == ()                       # downloads are off; the server's own uploads still work
    got = call(mcp, "mail_create_upload_url", filename="report.docx")
    assert client.put(got["url"], content=DOCX).status_code == 200
    r = send(mcp, attachment_urls=[{"url": got["url"], "sha256": SHA}])
    assert r["status"] == "sent"
    (msg, _), = sent
    assert files(msg) == [("report.docx", DOCX_TYPE, DOCX)]
    r = send(mcp, draft=True, attachment_urls=[{"url": got["url"], "filename": "Report (final).docx"}])    # usable until it expires
    assert r["status"] == "draft_saved"
    folder, raw = appended[-1]                                    # after the Sent copy of the message above
    assert folder == "Drafts" and files(message_from_bytes(raw, policy=policy.default)) == [("Report (final).docx", DOCX_TYPE, DOCX)]


def test_a_missing_file_a_wrong_hash_or_an_expired_url_sends_nothing(svc, monkeypatch):
    s, mcp, st, client, sent, appended = svc
    minted = call(mcp, "mail_create_upload_url", filename="a.png")
    token = st.match(minted["url"])
    with pytest.raises(Exception, match=r"attachment_urls\[0\] \(an upload URL of this server\) could not be attached: no file has been sent") as e:
        send(mcp, attachment_urls=[{"url": minted["url"]}])
    assert token not in str(e.value) and "Nothing was sent or saved" in str(e.value)
    assert client.put(minted["url"], content=PNG).status_code == 200
    with pytest.raises(Exception, match=r"its sha256 is [0-9a-f]{64}, not the 0{64} given") as e:
        send(mcp, attachment_urls=[{"url": minted["url"], "sha256": "0" * 64}])
    assert token not in str(e.value)
    with pytest.raises(Exception, match=r"attachment_urls\[0\]: sha256: a sha256 is 64 hexadecimal"):
        send(mcp, attachment_urls=[{"url": minted["url"], "sha256": "zz"}])
    with pytest.raises(Exception, match="downloads nothing from other sites.*ATTACHMENT_URL_ALLOWLIST is empty.*mail_create_upload_url"):
        send(mcp, attachment_urls=[{"url": minted["url"]}, {"url": "https://evil.test/x.pdf"}])
    now = time.time()
    monkeypatch.setattr(uploads, "time", types.SimpleNamespace(time=lambda: now + s.attachment_upload_ttl_seconds + 1))
    with pytest.raises(Exception, match=r"attachment_urls\[0\] \(an upload URL of this server\).*unknown or has expired") as e:
        send(mcp, attachment_urls=[{"url": minted["url"]}])
    assert token not in str(e.value)
    assert sent == [] and appended == []


def test_update_draft_adds_the_uploaded_files_to_the_drafts_own(svc, monkeypatch):
    s, mcp, st, client, sent, appended = svc
    old = EmailMessage()
    old["From"], old["To"], old["Subject"], old["Message-ID"] = "Me <me@icloud.com>", "anna@example.org", "Report", "<d1@x>"
    old.set_content("Attached.")
    old.add_attachment(b"%PDF-1.4 kept", maintype="application", subtype="pdf", filename="old.pdf")
    monkeypatch.setattr(MailService, "_load_draft", lambda self, c, folder, uid, uv: ("Drafts", old, 7))
    monkeypatch.setattr(MailService, "_select", lambda self, c, folder, readonly=True, expect=None: 7)
    monkeypatch.setattr(MailService, "_move_messages", lambda self, c, uids, dest: None)
    one, two = (call(mcp, "mail_create_upload_url", filename=n) for n in ("one.docx", "two.docx"))
    assert client.put(one["url"], content=DOCX).status_code == 200 and client.put(two["url"], content=DOCX + b"2").status_code == 200
    r = call(mcp, "mail_update_draft", uid=5, uidvalidity=7,
             attachment_urls=[{"url": one["url"]}, {"url": two["url"], "filename": "Two (final).docx"}])
    assert r["status"] == "draft_updated"
    (folder, raw), = appended
    assert folder == "Drafts"
    assert [f[:2] for f in files(message_from_bytes(raw, policy=policy.default))] == [
        ("old.pdf", "application/pdf"), ("one.docx", DOCX_TYPE), ("Two (final).docx", DOCX_TYPE)]


# ------------------------------------------------------------------ base64 with a hash
def test_inline_base64_is_checked_against_its_sha256(svc):
    s, mcp, st, client, sent, appended = svc
    b64 = base64.b64encode(DOCX).decode()
    mangled = b64[:40] + ("B" if b64[40] != "B" else "C") + b64[41:]       # one character changed, as a model retyping it might
    assert base64.b64decode(mangled) != DOCX
    with pytest.raises(Exception, match=r"Attachment 'report.docx': its bytes have sha256 [0-9a-f]{64}, not the " + SHA + " given") as e:
        send(mcp, attachments=[{"filename": "report.docx", "content_base64": mangled, "sha256": SHA}])
    assert "Nothing was sent or saved" in str(e.value)
    with pytest.raises(Exception, match="Attachment 'report.docx': sha256: a sha256 is 64 hexadecimal"):
        send(mcp, draft=True, attachments=[{"filename": "report.docx", "content_base64": b64, "sha256": "short"}])
    assert sent == [] and appended == []
    r = send(mcp, attachments=[{"filename": "report.docx", "content_base64": b64, "sha256": "sha256:" + SHA.upper()}])
    assert r["status"] == "sent" and files(sent[0][0]) == [("report.docx", DOCX_TYPE, DOCX)]
    r = send(mcp, attachments=[{"filename": "report.docx", "content_base64": b64}])          # without a hash, as before
    assert r["status"] == "sent" and len(sent) == 2


# ------------------------------------------------------------------ a file copied out of a message
def _message_with_files() -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Anna <anna@example.org>", "me@icloud.com", "Invoice", "<m1@x>"
    m.set_content("See attached.")
    m.add_attachment(b"PK\x03\x04" + b"z" * 100, maintype="application", subtype="zip", filename="archive.zip")
    m.add_attachment(DOCX, maintype="application", subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                     filename="Report.docx")
    return m.as_bytes(policy=policy.SMTP)


def test_a_file_in_another_message_is_copied_into_the_store_and_attached(svc, monkeypatch):
    s, mcp, st, client, sent, appended = svc
    raw = _message_with_files()
    monkeypatch.setattr(MailService, "_select", lambda self, c, folder, readonly=True, expect=None: 7)
    monkeypatch.setattr(MailService, "_attachment_part", lambda self, c, folder, uv, uid, index: None)   # the whole-message path
    monkeypatch.setattr(MailService, "_fetch_raw", lambda self, c, folder, uid, readonly=True, uidvalidity=None: (raw, None, None, 7))
    got = call(mcp, "mail_create_upload_url", from_folder="INBOX", from_uid=5, from_index=1, from_uidvalidity=7)
    assert (got["status"], got["filename"], got["content_type"], got["size"], got["sha256"]) == ("ready", "Report.docx", DOCX_TYPE, len(DOCX), SHA)
    assert "upload_with" not in got and st.match(got["url"])
    # the zip is not on ATTACHMENT_URL_CONTENT_TYPES, but a file from the owner's own mailbox keeps its type
    zipped = call(mcp, "mail_create_upload_url", from_folder="INBOX", from_uid=5, from_index=0, filename="Renamed.zip")
    assert (zipped["status"], zipped["content_type"], zipped["filename"]) == ("ready", "application/zip", "Renamed.zip")
    r = send(mcp, attachment_urls=[{"url": got["url"]}, {"url": zipped["url"], "sha256": zipped["sha256"]}])
    assert r["status"] == "sent"
    (msg, _), = sent
    assert [f[:2] for f in files(msg)] == [("Report.docx", DOCX_TYPE), ("Renamed.zip", "application/zip")]
    assert files(msg)[0][2] == DOCX
    assert client.put(got["url"], content=DOCX).status_code == 409                    # it holds its file already
    with pytest.raises(Exception, match="out of range"):
        call(mcp, "mail_create_upload_url", from_folder="INBOX", from_uid=5, from_index=7)
    with pytest.raises(Exception, match="from_folder, from_uid and from_index together"):
        call(mcp, "mail_create_upload_url", from_folder="INBOX", from_uid=5)
    with pytest.raises(Exception, match="filename is required"):
        call(mcp, "mail_create_upload_url")
    with pytest.raises(Exception, match=r"sha256 is [0-9a-f]{64}, not the 0{64} given"):
        call(mcp, "mail_create_upload_url", from_folder="INBOX", from_uid=5, from_index=1, sha256="0" * 64)
    assert len(sent) == 1
