"""attachment_urls: files the server downloads itself, only from allowlisted https prefixes, only from public addresses, within
size, type and time limits, and all or nothing. Every request goes to a mock transport; nothing here touches the network."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import gzip
import ipaddress
import json
import logging
import socket
import time
from email import message_from_bytes, policy
from email.message import EmailMessage

import httpx
import pytest

import icloud_mcp.mail as mail_mod
from icloud_mcp import netguard, urlattach
from icloud_mcp.config import Settings
from icloud_mcp.mail import MailError, MailService

ALZA = "https://www.alza.cz/Apps/pdfdoc.asp"
ALLOW = ("https://www.alza.cz/Apps/pdfdoc.asp", "https://pdf.alza.cz/Apps/pdfdoc.asp")
KEY = "31415926535897Ab123CDeFg"                       # the access key in Alza's x=, never to be logged or echoed
INVOICE = f"{ALZA}?d=1000000001&x={KEY}"
PDF = b"%PDF-1.7\n" + b"0" * 2000 + b"\n%%EOF\n"


def prefixes(*entries: str):
    return urlattach.parse_allowlist(entries or ALLOW)


def pdf_response(body: bytes = PDF, **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/pdf",
                                        "content-disposition": 'attachment; filename="1000000001.pdf"', **headers}, content=body)


def fetch(handler, items, *, max_bytes=10 * 1024 * 1024, types=("application/pdf",), allow=ALLOW, resolve=None):
    """fetch_all through the real pinned transport, with DNS answered by `resolve` (a public address by default)."""
    transport = netguard.PinnedTransport(inner=httpx.MockTransport(handler),
                                         resolve=resolve or (lambda host, port: (None, "93.184.216.34")))
    return urlattach.fetch_all(items, allowlist=urlattach.parse_allowlist(allow), max_bytes=max_bytes, content_types=types,
                               transport=transport)


# ------------------------------------------------------------------ the allowlist
@pytest.mark.parametrize("url", [
    INVOICE,
    f"{ALZA}?d=1",
    ALZA,
    "https://WWW.ALZA.CZ/Apps/pdfdoc.asp?d=1",                     # host names are not case-sensitive
    "https://www.alza.cz:443/Apps/pdfdoc.asp?d=1",                 # the default port written out
    "https://pdf.alza.cz/Apps/pdfdoc.asp?d=1",
    "https://www.alza.cz/Apps/./pdfdoc.asp?d=1",                   # resolves to the listed path
])
def test_allowlisted_urls_match(url):
    assert urlattach.allowed(httpx.URL(url), prefixes())


@pytest.mark.parametrize("url", [
    "http://www.alza.cz/Apps/pdfdoc.asp?d=1",                      # https only
    "https://alza.cz/Apps/pdfdoc.asp?d=1",                         # another host
    "https://www.alza.cz.evil.test/Apps/pdfdoc.asp",               # a prefix of the host is not the host
    "https://evil.test/https://www.alza.cz/Apps/pdfdoc.asp",       # the allowed URL as a substring
    "https://evil.test/?u=https://www.alza.cz/Apps/pdfdoc.asp",
    "https://www.alza.cz:8443/Apps/pdfdoc.asp",                    # another port
    "https://www.alza.cz/Apps/pdfdoc.aspx",                        # the path continues the last segment
    "https://www.alza.cz/apps/pdfdoc.asp",                         # paths are case-sensitive
    "https://www.alza.cz/Apps/pdfdoc.asp/../../Admin/x",           # sent as /Admin/x
    "https://www.alza.cz/Apps/pdfdoc.asp/%2e%2e/%2e%2e/Admin",     # encoded dot segments
    "https://www.alza.cz/Apps/pdfdoc.asp%2f..%2fAdmin",            # encoded slash
    "https://www.alza.cz/Apps\\pdfdoc.asp",                        # backslash
    "https://user:pw@www.alza.cz/Apps/pdfdoc.asp",                 # credentials
    "https://www.alza.cz./Apps/pdfdoc.asp",                        # trailing dot: not the listed name
])
def test_other_urls_do_not_match(url):
    assert not urlattach.allowed(httpx.URL(url), prefixes())


def test_a_prefix_ending_in_a_slash_covers_the_folder():
    p = prefixes("https://files.example.com/invoices/")
    assert urlattach.allowed(httpx.URL("https://files.example.com/invoices/2026/a.pdf"), p)
    assert not urlattach.allowed(httpx.URL("https://files.example.com/invoices-old/a.pdf"), p)
    assert not urlattach.allowed(httpx.URL("https://files.example.com/invoices"), p)


@pytest.mark.parametrize("entry", ["http://www.alza.cz/Apps/", "www.alza.cz/Apps/", "https://www.alza.cz/x?d=1",
                                   "https://u:p@www.alza.cz/", "https:///nohost", "https://www.alza.cz/a%2f..", ""])
def test_bad_allowlist_entries_are_refused(entry):
    with pytest.raises(ValueError):
        urlattach.parse_allowlist([entry])


@pytest.fixture
def server_env(tmp_path, monkeypatch):
    for k, v in dict(ICLOUD_USERNAME="me@icloud.com", ICLOUD_APP_PASSWORD="aaaa-bbbb-cccc-dddd", DATA_DIR=str(tmp_path),
                     MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16).items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def test_config_reads_and_checks_the_settings(server_env):
    s = Settings.from_env()
    assert s.attachment_url_allowlist == () and s.attachment_url_max_bytes == 10 * 1024 * 1024
    assert s.attachment_url_content_types == ("application/pdf",)
    server_env.setenv("ATTACHMENT_URL_ALLOWLIST", ",".join(ALLOW))
    server_env.setenv("ATTACHMENT_URL_CONTENT_TYPES", "application/PDF, image/png")
    s = Settings.from_env()
    s.validate_for_server()
    assert s.attachment_url_allowlist == ALLOW and s.attachment_url_content_types == ("application/pdf", "image/png")
    for name, value in (("ATTACHMENT_URL_ALLOWLIST", "http://www.alza.cz/Apps/pdfdoc.asp"), ("ATTACHMENT_URL_MAX_BYTES", "0"),
                        ("ATTACHMENT_URL_CONTENT_TYPES", "pdf")):
        with server_env.context() as m:
            m.setenv(name, value)
            with pytest.raises(SystemExit, match=name):
                Settings.from_env().validate_for_server()


# ------------------------------------------------------------------ fetching
def test_fetches_the_invoice_under_the_servers_name():
    seen = []

    def h(req):
        seen.append((req.url.host, req.headers["host"], req.extensions.get("sni_hostname"), req.url.params["x"],
                     req.headers["accept-encoding"], req.headers["accept"]))
        return pdf_response()
    got = fetch(h, [{"url": INVOICE}])
    assert got == [{"filename": "1000000001.pdf", "content_type": "application/pdf", "content": PDF}]
    # connected to the checked address, under the real name (Host header, TLS server name), query string intact
    assert seen == [("93.184.216.34", "www.alza.cz", "www.alza.cz", KEY, "identity", "application/pdf")]


def test_disabled_without_an_allowlist():
    with pytest.raises(urlattach.AttachmentUrlError, match="ATTACHMENT_URL_ALLOWLIST is empty"):
        urlattach.fetch_all([{"url": INVOICE}], allowlist=(), max_bytes=1000, content_types=("application/pdf",))


def test_a_url_off_the_allowlist_is_refused_before_anything_is_downloaded():
    def h(req):
        raise AssertionError("nothing may be downloaded")
    with pytest.raises(urlattach.AttachmentUrlError) as e:
        fetch(h, [{"url": INVOICE}, {"url": "https://evil.test/Apps/pdfdoc.asp?x=secret"}])
    msg = str(e.value)
    assert "attachment_urls[1] (https://evil.test/Apps/pdfdoc.asp)" in msg and "not on ATTACHMENT_URL_ALLOWLIST" in msg
    assert "https://www.alza.cz/Apps/pdfdoc.asp" in msg and "secret" not in msg and "Nothing was sent" in msg


def test_redirect_within_the_allowlist_is_followed():
    def h(req):
        if req.headers["host"] == "www.alza.cz":
            return httpx.Response(302, headers={"location": f"https://pdf.alza.cz/Apps/pdfdoc.asp?d=1&x={KEY}"})
        return pdf_response()
    assert fetch(h, [{"url": INVOICE}])[0]["content"] == PDF


@pytest.mark.parametrize("location", ["https://evil.test/a.pdf", "http://www.alza.cz/Apps/pdfdoc.asp?d=1",
                                      "/Apps/other.asp?d=1", "https://www.alza.cz/Apps/pdfdoc.asp/../../Admin"])
def test_redirect_off_the_allowlist_is_refused(location):
    hops = []

    def h(req):
        hops.append(req.headers["host"])
        if len(hops) > 1:
            raise AssertionError("the off-list hop must not be requested")
        return httpx.Response(302, headers={"location": location})
    with pytest.raises(urlattach.AttachmentUrlError, match=r"attachment_urls\[0\].*redirected to .*not on ATTACHMENT_URL_ALLOWLIST"):
        fetch(h, [{"url": INVOICE}])
    assert hops == ["www.alza.cz"]


def test_more_than_three_redirects_are_refused():
    n = []

    def h(req):
        n.append(1)
        return httpx.Response(307, headers={"location": f"{ALZA}?d={len(n)}"})
    with pytest.raises(urlattach.AttachmentUrlError, match="more than 3 redirects"):
        fetch(h, [{"url": INVOICE}])
    assert len(n) == 4                      # the first request and three redirects


def test_a_malformed_redirect_is_refused_without_quoting_it():
    def h(req):
        return httpx.Response(302, headers={"location": f"https://www.alza.cz:abc/Apps/pdfdoc.asp?x={KEY}"})
    with pytest.raises(urlattach.AttachmentUrlError, match=r"could not be downloaded \(RemoteProtocolError\)") as e:
        fetch(h, [{"url": INVOICE}])
    assert KEY not in str(e.value)


def test_http_errors_are_named():
    with pytest.raises(urlattach.AttachmentUrlError, match="HTTP 404"):
        fetch(lambda req: httpx.Response(404, text="gone"), [{"url": INVOICE}])


# ------------------------------------------------------------------ the network guard
@pytest.mark.parametrize("address", ["10.0.0.5", "172.16.3.4", "192.168.1.20", "127.0.0.1", "169.254.169.254", "100.64.0.1",   # privacy-ok: test values
                                     "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fd12:3456::1", "fe80::1", "::ffff:10.0.0.5",   # privacy-ok: test values
                                     "64:ff9b::a00:5", "64:ff9b:1::1"])
def test_private_and_local_addresses_are_blocked(address):
    assert netguard.blocked(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["93.184.216.34", "2a00:1450:4001::1", "::ffff:93.184.216.34", "64:ff9b::5db8:d822"])
def test_public_addresses_pass(address):
    assert not netguard.blocked(ipaddress.ip_address(address))


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    def h(req):
        raise AssertionError("must not connect")
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))])   # privacy-ok: test values
    with pytest.raises(urlattach.AttachmentUrlError, match=r"attachment_urls\[0\].*private or local network"):
        fetch(h, [{"url": INVOICE}], resolve=netguard.public_address)


def test_one_private_address_among_public_ones_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::1", port, 0, 0))])
    assert netguard.public_address("www.alza.cz", 443)[0] == "the address points into a private or local network"


def test_each_redirect_hop_is_resolved_and_checked_again(monkeypatch):
    """The second name resolves privately: the redirect is on the allowlist, the address is not."""
    answers = {"www.alza.cz": "93.184.216.34", "pdf.alza.cz": "192.168.1.20"}   # privacy-ok: test values
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], port))])
    seen = []

    def h(req):
        seen.append(req.url.host)
        return httpx.Response(302, headers={"location": "https://pdf.alza.cz/Apps/pdfdoc.asp?d=1"})
    with pytest.raises(urlattach.AttachmentUrlError, match="pdf.alza.cz: the address points into a private"):
        fetch(h, [{"url": INVOICE}], resolve=netguard.public_address)
    assert seen == ["93.184.216.34"]


# ------------------------------------------------------------------ size, type, content
class Drip(httpx.SyncByteStream):
    """A body sent in chunks, counting how many were pulled."""

    def __init__(self, chunk: bytes, count: int | None = None, delay: float = 0.0):
        self.chunk, self.count, self.delay, self.pulled = chunk, count, delay, 0

    def __iter__(self):
        while self.count is None or self.pulled < self.count:
            if self.delay:
                time.sleep(self.delay)
            self.pulled += 1
            yield self.chunk


def test_declared_size_over_the_limit_is_refused_without_reading():
    stream = Drip(b"%PDF-" + b"0" * 1000, count=5)

    def h(req):
        return httpx.Response(200, headers={"content-type": "application/pdf", "content-length": "5025"}, stream=stream)
    with pytest.raises(urlattach.AttachmentUrlError, match=r"5025 bytes, larger than 4096 bytes \(ATTACHMENT_URL_MAX_BYTES\)"):
        fetch(h, [{"url": INVOICE}], max_bytes=4096)
    assert stream.pulled == 0


def test_undeclared_size_is_cut_off_while_streaming():
    stream = Drip(b"%PDF-" + b"0" * 1019)                  # endless 1 KiB chunks, no Content-Length

    def h(req):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, stream=stream)
    with pytest.raises(urlattach.AttachmentUrlError, match="larger than 4096 bytes"):
        fetch(h, [{"url": INVOICE}], max_bytes=4096)
    assert stream.pulled == 5                              # stopped at the first chunk past the limit


def test_a_compressed_body_is_capped_after_unpacking():
    bomb = gzip.compress(b"%PDF-" + b"0" * (32 * 1024 * 1024))

    def h(req):
        return httpx.Response(200, headers={"content-type": "application/pdf", "content-encoding": "gzip"},
                              stream=Drip(bomb, count=1))
    with pytest.raises(urlattach.AttachmentUrlError, match="larger than"):
        fetch(h, [{"url": INVOICE}], max_bytes=1024 * 1024)


def test_files_together_may_not_pass_the_message_limit(monkeypatch):
    monkeypatch.setattr(urlattach, "MAX_TOTAL_BYTES", 5000)
    with pytest.raises(urlattach.AttachmentUrlError, match=r"attachment_urls\[2\].*left of the 5000-byte limit"):
        fetch(lambda req: pdf_response(), [{"url": f"{ALZA}?d={i}"} for i in range(3)])


@pytest.mark.parametrize("ctype", ["text/html; charset=utf-8", "application/octet-stream", ""])
def test_wrong_content_type_is_refused(ctype):
    def h(req):
        return httpx.Response(200, headers={"content-type": ctype} if ctype else {}, content=PDF)
    with pytest.raises(urlattach.AttachmentUrlError, match=r"attachment_urls\[0\] \(https://www.alza.cz/Apps/pdfdoc.asp\).*"
                                                            r"not application/pdf"):
        fetch(h, [{"url": INVOICE}])


def test_a_pdf_that_is_not_a_pdf_is_refused():
    login_page = b"<!doctype html><html><body>Please sign in</body></html>"
    with pytest.raises(urlattach.AttachmentUrlError, match="does not start with %PDF-"):
        fetch(lambda req: pdf_response(login_page), [{"url": INVOICE}])


def test_other_allowed_types_need_no_magic():
    def h(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG....")
    got = fetch(h, [{"url": "https://www.alza.cz/Apps/pdfdoc.asp?d=1"}], types=("application/pdf", "image/png"))
    assert got[0]["content_type"] == "image/png" and got[0]["filename"] == "pdfdoc.asp.png"


def test_a_slow_server_is_cut_off(monkeypatch):
    monkeypatch.setattr(urlattach, "TIMEOUT_SECONDS", 0.3)

    def h(req):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, stream=Drip(b"%PDF-", delay=0.05))
    start = time.monotonic()
    with pytest.raises(urlattach.AttachmentUrlError, match="longer than 0.3 seconds"):
        fetch(h, [{"url": INVOICE}], max_bytes=10 ** 9)
    assert time.monotonic() - start < 2


# ------------------------------------------------------------------ filenames
@pytest.mark.parametrize("given, expected", [
    ("Faktura 1000000001.pdf", "Faktura 1000000001.pdf"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\x\\invoice.pdf", "invoice.pdf"),
    ("in\x00voi\x07ce\r\n.pdf", "invoice.pdf"),
    ("invoice\u202efdp.exe", "invoicefdp.exe"),             # a right-to-left override would show it as ...exe.pdf
    ("  .hidden.pdf. ", "hidden.pdf"),
    ("a\u2028b   c.pdf", "a b c.pdf"),
    ("Žluťoučký kůň.pdf", "Žluťoučký kůň.pdf"),
])
def test_filenames_are_cleaned(given, expected):
    assert urlattach.safe_filename(given) == expected


def test_long_filenames_keep_their_extension():
    name = urlattach.safe_filename("x" * 300 + ".pdf")
    assert len(name) == 120 and name.endswith("x.pdf")
    assert len(urlattach.safe_filename("y" * 300)) == 120


def test_filename_order_given_then_server_then_path():
    assert fetch(lambda req: pdf_response(), [{"url": INVOICE, "filename": "Alza/faktura.pdf"}])[0]["filename"] == "faktura.pdf"
    assert fetch(lambda req: pdf_response(), [{"url": INVOICE, "filename": "/\x07"}])[0]["filename"] == "1000000001.pdf"
    starred = pdf_response(**{"content-disposition": "attachment; filename*=UTF-8''Faktura%20%C4%8D.%201.pdf"})
    assert fetch(lambda req: starred, [{"url": INVOICE}])[0]["filename"] == "Faktura č. 1.pdf"
    bare = httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF)
    assert fetch(lambda req: bare, [{"url": INVOICE}])[0]["filename"] == "pdfdoc.asp.pdf"
    sneaky = pdf_response(**{"content-disposition": 'attachment; filename="../../.ssh/authorized_keys"'})
    assert fetch(lambda req: sneaky, [{"url": INVOICE}])[0]["filename"] == "authorized_keys"


# ------------------------------------------------------------------ all or nothing, logs
def test_one_failure_fails_them_all():
    def h(req):
        return pdf_response() if req.url.params["d"] != "2" else httpx.Response(500)
    with pytest.raises(urlattach.AttachmentUrlError, match=r"attachment_urls\[1\] .*HTTP 500.*Nothing was sent or saved"):
        fetch(h, [{"url": f"{ALZA}?d=1&x={KEY}"}, {"url": f"{ALZA}?d=2&x={KEY}"}, {"url": f"{ALZA}?d=3&x={KEY}"}])


def test_at_most_twenty_urls():
    with pytest.raises(urlattach.AttachmentUrlError, match="at most 20"):
        fetch(lambda req: pdf_response(), [{"url": INVOICE}] * 21)


def test_logs_and_errors_never_carry_the_query_string(caplog):
    caplog.set_level(logging.DEBUG)
    fetch(lambda req: pdf_response(), [{"url": INVOICE}])
    with pytest.raises(urlattach.AttachmentUrlError) as e:
        fetch(lambda req: httpx.Response(403), [{"url": INVOICE}])
    assert "www.alza.cz/Apps/pdfdoc.asp" in caplog.text and "fetched" in caplog.text and "failed" in caplog.text
    assert KEY not in caplog.text and KEY not in str(e.value) and "d=1000000001" not in caplog.text


# ------------------------------------------------------------------ through the mail service
class FakeIMAP:
    def __init__(self):
        self.appended, self.drafts, self.trash, self.uv, self.cur = [], {}, [], 7, None

    def append(self, folder, raw, flags=(), msg_time=None):
        self.appended.append((folder, raw))
        if folder == "Drafts":
            self.drafts[100 + len(self.appended)] = raw
            return f"[APPENDUID 7 {100 + len(self.appended)}] APPEND completed".encode()
        return b"APPEND completed"

    def select_folder(self, folder, readonly=False):
        self.cur = folder
        return {b"UIDVALIDITY": self.uv}

    def add_flags(self, uids, flags, silent=False):
        pass


def _draft(**extra) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Me <me@icloud.com>", "anna@example.org", "Invoices", "<d1@x>"
    m.set_content("Attached.")
    m.add_attachment(b"%PDF-1.4 " + b"k" * extra.get("kept_size", 10), maintype="application", subtype="pdf", filename="old.pdf")
    return m.as_bytes(policy=policy.SMTP)


@pytest.fixture
def svc(server_env, monkeypatch):
    server_env.setenv("ATTACHMENT_URL_ALLOWLIST", ",".join(ALLOW))
    server_env.setenv("SEND_REQUIRES_APPROVAL", "false")
    monkeypatch.setattr(mail_mod, "TICKER", type("T", (), {"add": lambda self, fn: None})())
    s = Settings.from_env()
    imap, sent, opened = FakeIMAP(), [], []

    @contextlib.contextmanager
    def fake_imap(self, **kw):
        opened.append(1)
        yield imap

    monkeypatch.setattr(MailService, "imap", fake_imap)
    monkeypatch.setattr(MailService, "resolve_folder",
                        lambda self, c, name: {"sent": "Sent Messages", "drafts": "Drafts", "trash": "Deleted Messages"}.get(name.lower(), name))
    monkeypatch.setattr(MailService, "_smtp_send", lambda self, msg, rcpts: sent.append((msg, list(rcpts))) or {})
    handler = {"h": lambda req: pdf_response()}
    monkeypatch.setattr(urlattach, "PinnedTransport", lambda: netguard.PinnedTransport(
        inner=httpx.MockTransport(lambda req: handler["h"](req)), resolve=lambda host, port: (None, "93.184.216.34")))
    return s, imap, sent, opened, handler


def _files(msg) -> list[tuple[str, str, bytes]]:
    return [(p.get_filename(), p.get_content_type(), p.get_payload(decode=True)) for p in msg.iter_attachments()]


def test_send_attaches_downloaded_files_next_to_base64_ones(svc):
    s, imap, sent, _, _ = svc
    import base64
    r = MailService(s).send(to=["anna@example.org"], subject="Invoice", body="Attached.",
                            attachments=[{"filename": "note.txt", "content_base64": base64.b64encode(b"hi").decode()}],
                            attachment_urls=[{"url": INVOICE}])
    assert r["status"] == "sent"
    (msg, _), = sent
    assert _files(msg) == [("note.txt", "text/plain", b"hi"), ("1000000001.pdf", "application/pdf", PDF)]


def test_a_failed_url_sends_nothing_and_touches_no_mailbox(svc):
    s, imap, sent, opened, handler = svc
    handler["h"] = lambda req: pdf_response() if req.url.params["d"] == "1" else pdf_response(b"<html>login</html>")
    mail = MailService(s)
    for call in (lambda: mail.send(to=["anna@example.org"], subject="x", body="x", attachment_urls=[
                     {"url": f"{ALZA}?d=1&x={KEY}"}, {"url": f"{ALZA}?d=2&x={KEY}"}]),
                 lambda: mail.send(to=["anna@example.org"], subject="x", body="x", draft=True,
                                   attachment_urls=[{"url": f"{ALZA}?d=2&x={KEY}"}]),
                 lambda: mail.reply("INBOX", 5, "x", attachment_urls=[{"url": f"{ALZA}?d=2&x={KEY}"}]),
                 lambda: mail.update_draft(101, uidvalidity=7, attachment_urls=[{"url": f"{ALZA}?d=2&x={KEY}"}])):
        with pytest.raises(MailError, match=r"attachment_urls\[\d\] \(https://www.alza.cz/Apps/pdfdoc.asp\).*%PDF-") as e:
            call()
        assert KEY not in str(e.value)
    assert sent == [] and imap.appended == [] and opened == [] and len(mail.outbox.pending()) == 0


def test_owner_approval_holds_downloaded_files_like_any_other(svc):
    s, imap, sent, _, _ = svc
    mail = MailService(dataclasses.replace(s, require_approval=True))
    r = mail.send(to=["anna@example.org"], subject="Invoice", body="Attached.", attachment_urls=[{"url": INVOICE}])
    assert r["status"] == "queued_for_owner_approval" and sent == []
    (q,) = mail.outbox.pending()
    assert _files(message_from_bytes(q.raw, policy=policy.default)) == [("1000000001.pdf", "application/pdf", PDF)]
    assert [a["filename"] for a in mail.describe_queued(q)["attachments"]] == ["1000000001.pdf"]   # what the approval page lists
    mail.release(q.id)
    (msg, _), = sent
    assert _files(msg) == [("1000000001.pdf", "application/pdf", PDF)]


def test_draft_paths_carry_the_files(svc):
    s, imap, sent, _, _ = svc
    r = MailService(s).send(to=["anna@example.org"], subject="Invoice", body="x", draft=True, attachment_urls=[{"url": INVOICE}])
    assert r["status"] == "draft_saved" and sent == []
    (folder, raw), = imap.appended
    assert folder == "Drafts" and _files(message_from_bytes(raw, policy=policy.default))[0][0] == "1000000001.pdf"


def test_update_draft_adds_to_the_kept_files_or_to_new_ones(svc, monkeypatch):
    s, imap, sent, _, _ = svc
    big = 6 * 1024 * 1024                                    # over MAX_ATTACHMENT_BYTES (5 MiB): kept files are not re-checked
    monkeypatch.setattr(MailService, "_load_draft", lambda self, c, folder, uid, uv: (
        "Drafts", message_from_bytes(_draft(kept_size=big), policy=policy.default), 7))
    monkeypatch.setattr(MailService, "_select", lambda self, c, folder, readonly=True, expect=None: 7)
    monkeypatch.setattr(MailService, "_move_messages", lambda self, c, uids, dest: None)
    mail = MailService(s)
    r = mail.update_draft(5, uidvalidity=7, attachment_urls=[{"url": INVOICE, "filename": "Faktura.pdf"}])
    new = message_from_bytes(imap.drafts[r["uid"]], policy=policy.default)
    assert [f[0] for f in _files(new)] == ["old.pdf", "Faktura.pdf"] and len(_files(new)[0][2]) == big + 9
    import base64
    r = mail.update_draft(5, uidvalidity=7, attachments=[{"filename": "a.txt", "content_base64": base64.b64encode(b"a").decode()}],
                          attachment_urls=[{"url": INVOICE}])
    new = message_from_bytes(imap.drafts[r["uid"]], policy=policy.default)
    assert [f[0] for f in _files(new)] == ["a.txt", "1000000001.pdf"]


def test_the_tool_passes_attachment_urls_through(svc):
    from icloud_mcp.server import create_server
    s, imap, sent, _, _ = svc
    server, _ = create_server(s)
    out = asyncio.run(server.call_tool("mail_send_message", {"to": ["anna@example.org"], "subject": "Invoice", "body": "x",
                                                             "attachment_urls": [{"url": INVOICE, "filename": "Alza.pdf"}]}))
    assert json.loads(out.content[0].text)["status"] == "sent"
    (msg, _), = sent
    assert _files(msg) == [("Alza.pdf", "application/pdf", PDF)]
    with pytest.raises(Exception, match="20"):
        asyncio.run(server.call_tool("mail_send_message", {"to": ["anna@example.org"], "subject": "x", "body": "x",
                                                           "attachment_urls": [{"url": INVOICE}] * 21}))
