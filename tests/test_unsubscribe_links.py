"""Unsubscribing through a web page: link detection, the two-step confirm, and the page visit."""
from __future__ import annotations

import gzip
import time
import tracemalloc
from contextlib import contextmanager
from email.message import EmailMessage
from types import SimpleNamespace

import httpx
import pytest

from icloud_mcp import mailbulk, unsublink
from icloud_mcp.mail import html_to_text

PLANEO_FOOTER = """<html><body>
<p><a href="https://cdn.example-esp.com/e/aaa/click">DO E-SHOPU</a></p>
<p>Mikrovlnná trouba ... <a href="https://cdn.example-esp.com/e/buy/click">KOUPIT</a></p>
<p>Odesílatelem tohoto e-mailu je firma FAST ČR, a.s.<br>
Pokud si již nepřejete dostávat naše výhodné nabidky, klikněte
<a href="https://cdn.example-esp.com/e/unsub/click">sem</a>.</p></body></html>"""


# ------------------------------------------------------------------ find_link
def test_finds_czech_footer_link_by_the_sentence_before_it():
    link = unsublink.find_link(PLANEO_FOOTER, None)
    assert link["url"] == "https://cdn.example-esp.com/e/unsub/click"
    assert link["link_text"] == "sem"


def test_link_text_beats_context_and_last_wins():
    html = ('<a href="https://x.test/1">Unsubscribe</a> <a href="https://x.test/2">shop</a> '
            'to stop these, <a href="https://x.test/3">click here</a> or <a href="https://x.test/4">unsubscribe here</a>')
    assert unsublink.find_link(html, None)["url"] == "https://x.test/4"


def test_ordinary_links_are_never_candidates():
    html = '<a href="https://x.test/buy">Buy now</a> <a href="https://x.test/news">Read more</a>'
    assert unsublink.find_link(html, None) is None


def test_unsubscribe_wording_does_not_leak_past_a_link():
    html = 'Do not want these? Unsubscribe in settings. <a href="https://x.test/a">Shop</a> <a href="https://x.test/b">Blog</a>'
    assert unsublink.find_link(html, None)["url"] == "https://x.test/a"   # only the link right after the sentence
    html2 = 'Unsubscribe in settings. <a href="https://x.test/a">Shop</a> new arrivals <a href="https://x.test/b">Blog</a>'
    assert unsublink.find_link(html2, None)["url"] == "https://x.test/a"


def test_mailto_and_javascript_links_are_ignored():
    html = '<a href="mailto:u@x.test">unsubscribe</a> <a href="javascript:void(0)">unsubscribe</a>'
    assert unsublink.find_link(html, None) is None


def test_plain_text_body():
    text = "Novinky...\r\nPokud si již nepřejete dostávat naše nabídky, klikněte sem [https://x.test/u?id=1]."
    assert unsublink.find_link(None, text)["url"] == "https://x.test/u?id=1"


def test_script_text_is_not_context():
    html = '<script>var s="unsubscribe";</script><a href="https://x.test/a">Shop</a>'
    assert unsublink.find_link(html, None) is None


# ------------------------------------------------------------------ open_link
def _ok(_url):
    return None


def _open(handler, url="https://esp.test/e/unsub/click", check=_ok):
    return unsublink.open_link(url, to_text=html_to_text, check=check, transport=httpx.MockTransport(handler))


def test_get_that_unsubscribes_directly():
    def h(req):
        if req.url.path == "/e/unsub/click":
            return httpx.Response(302, headers={"location": "https://www.shop.test/unsub?c=9"})
        return httpx.Response(200, html="<h1>Byli jste úspěšně odhlášeni z odběru novinek.</h1>")
    r = _open(h)
    assert r["opened"] and r["looks_unsubscribed"] and r["clicks"] == 0
    assert r["steps"][0]["opened"] == "www.shop.test"


def test_confirm_form_is_submitted_with_its_hidden_fields():
    seen = {}

    def h(req):
        if req.method == "GET":
            return httpx.Response(200, html="""<p>Opravdu se chcete odhlásit z odběru?</p>
                <form method="post" action="/confirm"><input type="hidden" name="t" value="abc">
                <input type="checkbox" name="all" value="1" checked><button type="submit" name="go" value="y">Odhlásit</button></form>
                <form action="/search"><input name="q"><button>Hledat</button></form>""")
        seen["body"] = req.content.decode()
        seen["path"] = req.url.path
        return httpx.Response(200, html="<p>Hotovo, byli jste odhlášeni.</p>")
    r = _open(h)
    assert r["clicks"] == 1 and r["looks_unsubscribed"] and r["steps"][1]["clicked"] == "Odhlásit"
    assert seen["path"] == "/confirm" and "t=abc" in seen["body"] and "all=1" in seen["body"] and "go=y" in seen["body"]


def test_form_needing_an_email_is_not_sent():
    def h(req):
        return httpx.Response(200, html="""<p>Unsubscribe from our newsletter</p>
            <form method="post"><input type="email" name="email"><button>Unsubscribe</button></form>""")
    r = _open(h)
    assert r["clicks"] == 0 and not r["looks_unsubscribed"] and "email address" in r["stopped"]


def test_no_form_on_unrelated_page():
    def h(req):
        return httpx.Response(200, html='<p>Welcome to our shop</p><form><button>Subscribe</button></form>')
    r = _open(h)
    assert r["clicks"] == 0 and "not say anything about unsubscribing" in r["stopped"]


def test_three_step_flow_confirm_then_optional_survey():
    calls = []

    def h(req):
        calls.append((req.method, req.url.path))
        if req.url.path == "/e/unsub/click":
            return httpx.Response(302, headers={"location": "https://news.shop.test/u"})
        if req.url.path == "/u":
            return httpx.Response(200, html="""<h1>Odhlášení z newsletteru</h1>
                <form method="post" action="https://news.shop.test/u2"><input type="hidden" name="id" value="5">
                <button>Potvrdit odhlášení</button></form>""")
        if req.url.path == "/u2":
            return httpx.Response(200, html="""<h1>Mrzí nás, že odcházíte. Proč?</h1>
                <form method="post" action="/u3"><input type="radio" name="why" value="many"> Příliš mnoho e-mailů
                <textarea name="note"></textarea><button>Odeslat</button></form>""")
        assert dict(httpx.QueryParams(req.content.decode())) == {"note": ""}
        return httpx.Response(200, html="<p>Byli jste odhlášeni. Děkujeme za zpětnou vazbu.</p>")
    r = _open(h)
    assert r["looks_unsubscribed"] and r["clicks"] == 2
    assert [s.get("clicked") for s in r["steps"][1:]] == ["Potvrdit odhlášení", "Odeslat"]
    assert calls[-1] == ("POST", "/u3")


def test_unsubscribe_from_all_box_is_ticked_only_when_clear():
    seen = {}

    def h(req):
        if req.method == "GET":
            return httpx.Response(200, html="""<h2>Manage your subscription</h2><p>Choose what to unsubscribe from.</p>
                <form method="post" action="/prefs">
                <label><input type="checkbox" name="topic" value="deals" checked> Weekly deals</label>
                <input type="checkbox" id="u_all" name="unsub_all" value="1"><label for="u_all">Unsubscribe from all emails</label>
                <button>Save</button><button>Unsubscribe</button></form>""")
        seen["body"] = req.content.decode()
        return httpx.Response(200, html="<p>You have been unsubscribed.</p>")
    r = _open(h)
    assert r["looks_unsubscribed"] and r["steps"][1]["ticked"] == "Unsubscribe from all emails"
    assert "unsub_all=1" in seen["body"]


def test_two_all_boxes_are_left_alone():
    html = """<p>Unsubscribe</p><form method="post" action="/p">
        <label><input type="checkbox" name="a" value="1"> Unsubscribe from all newsletters</label>
        <label><input type="checkbox" name="b" value="1"> Unsubscribe from all offers</label><button>Unsubscribe</button></form>"""
    form, _ = unsublink._pick_form(html, "unsubscribe", first=True)
    assert "ticked" not in form and ("a", "1") not in form["data"] and ("b", "1") not in form["data"]


def test_required_choice_stops():
    def h(req):
        return httpx.Response(200, html="""<p>Unsubscribe?</p><form method="post">
            <input type="radio" name="why" value="x" required><button>Unsubscribe</button></form>""")
    r = _open(h)
    assert r["clicks"] == 0 and "required choice" in r["stopped"]


def test_form_posting_to_another_site_is_not_sent():
    def h(req):
        if req.method == "GET":
            return httpx.Response(200, html='<p>Unsubscribe</p><form method="post" action="https://evil.test/x"><button>Unsubscribe</button></form>')
        raise AssertionError("must not post")
    r = _open(h)
    assert r["clicks"] == 0 and "another site" in r["stopped"]


def test_same_form_twice_is_not_resent():
    posts = []

    def h(req):
        if req.method == "POST":
            posts.append(1)
        return httpx.Response(200, html='<p>Unsubscribe</p><form method="post" action="/x"><button>Unsubscribe</button></form>')
    r = _open(h)
    assert len(posts) == 1 and "same form again" in r["stopped"] and not r["looks_unsubscribed"]


def test_click_limit():
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(200, html=f'<p>Unsubscribe step</p><form method="post" action="/s{n["i"]}"><button>Confirm</button>'
                                        f'<button style="display:none">x</button><input type="hidden" name="k{n["i"]}" value="1"></form>'
                                        '<p>Potvrďte odhlášení</p>')
    r = _open(h)
    assert r["clicks"] == unsublink.MAX_CLICKS and "Stopped after" in r["stopped"]


def test_czech_noun_is_not_success():
    for page in ("Odhlášení z newsletteru", "Potvrďte odhlášení odběru"):
        assert not unsublink._DONE.search(unsublink._norm(page)), page
    for page in ("Byli jste odhlášeni.", "E-mail byl odhlášen z odběru", "Jste odhlášena.", "Úspěšně odhlášeno"):
        assert unsublink._DONE.search(unsublink._norm(page)), page


def test_site_helper():
    assert unsublink._site("news.shop.planeo.cz") == "planeo.cz"
    assert unsublink._site("a.b.co.uk") == "b.co.uk"


def test_redirect_into_private_network_is_refused():
    def h(req):
        return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})

    def check(url):
        return "the address points into a private or local network" if "10.0.0.5" in url else None
    r = _open(h, check=check)
    assert not r["opened"] and "private" in r["reason"]


def test_redirect_loop_is_cut():
    def h(req):
        return httpx.Response(302, headers={"location": str(req.url)})
    r = _open(h)
    assert not r["opened"] and "redirects" in r["reason"]


def test_public_web_rejects_private_and_odd_schemes():
    assert unsublink.public_web("ftp://x.test/") is not None
    assert unsublink.public_web("http://127.0.0.1/") is not None
    assert unsublink.public_web("https://user:pw@x.test/") is not None


def test_page_is_read_up_to_the_cap():
    def h(req):
        return httpx.Response(200, html="<p>" + "x" * (unsublink.MAX_PAGE_BYTES * 3) + "</p>")
    r = _open(h)
    assert r["opened"] and len(r["page_text"]) <= unsublink.PAGE_TEXT_CHARS


def test_repeated_field_names_are_all_sent_in_page_order():
    seen = {}

    def h(req):
        if req.method == "GET":
            return httpx.Response(200, html="""<p>Unsubscribe from these lists:</p><form method="post" action="/u">
                <input type="hidden" name="id" value="7">
                <input type="checkbox" name="list" value="news" checked><input type="checkbox" name="list" value="deals" checked>
                <button>Unsubscribe</button></form>""")
        seen["body"], seen["type"] = req.content.decode(), req.headers["content-type"]
        return httpx.Response(200, html="<p>You have been unsubscribed.</p>")
    r = _open(h)
    assert r["looks_unsubscribed"] and seen["type"] == "application/x-www-form-urlencoded"
    assert httpx.QueryParams(seen["body"]).multi_items() == [("id", "7"), ("list", "news"), ("list", "deals")]


def test_connection_goes_to_the_checked_address_under_the_real_name():
    seen = {"hops": []}

    def h(req):
        seen["hops"].append((req.url.host, req.headers["host"], req.extensions.get("sni_hostname")))
        if req.method == "GET":
            return httpx.Response(200, headers={"set-cookie": "csrf=1; Path=/"},
                                  html='<p>Unsubscribe?</p><form method="post" action="/u2"><button>Unsubscribe</button></form>')
        seen["cookie"] = req.headers.get("cookie")
        return httpx.Response(200, html="<p>You have been unsubscribed.</p>")
    pinned = unsublink._Pinned(inner=httpx.MockTransport(h), resolve=lambda host, port: (None, "93.184.216.34"))
    r = unsublink.open_link("https://news.shop.test/u", to_text=html_to_text, check=_ok, transport=pinned)
    assert r["looks_unsubscribed"] and r["steps"][0]["opened"] == "news.shop.test"
    assert seen["hops"] == [("93.184.216.34", "news.shop.test", "news.shop.test")] * 2
    assert seen["cookie"] == "csrf=1"                  # cookies stay with the name, not the address


def test_name_that_resolves_private_at_connect_time_is_refused():
    """DNS rebinding: the check saw a public address, the connection would get a private one."""
    def h(req):
        raise AssertionError("must not connect")
    pinned = unsublink._Pinned(inner=httpx.MockTransport(h), resolve=lambda host, port: unsublink._public_address("127.0.0.1", port))
    r = unsublink.open_link("https://rebind.test/u", to_text=html_to_text, check=_ok, transport=pinned)
    assert not r["opened"] and "private" in r["reason"]


def test_slow_page_is_cut_off_by_the_time_limit(monkeypatch):
    monkeypatch.setattr(unsublink, "MAX_SECONDS", 0.3)

    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                time.sleep(0.05)
                yield b"<p>x</p>"

    def h(req):
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=Drip())
    start = time.monotonic()
    r = _open(h)
    assert not r["opened"] and "longer than" in r["reason"] and time.monotonic() - start < 2


def test_compressed_page_is_capped_after_unpacking():
    bomb = gzip.compress(b"<html><body><p>Unsubscribe</p>" + b"x" * (64 * 1024 * 1024))

    def h(req):
        assert req.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"},
                              stream=httpx.ByteStream(bomb))   # streamed, as from the network (content= is unpacked up front)
    tracemalloc.start()
    try:
        r = _open(h)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert r["opened"] and peak < 16 * 1024 * 1024


def test_unknown_compression_is_not_read():
    def h(req):
        return httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "br"},
                              stream=httpx.ByteStream(b"\x8b\x00\x01"))
    r = _open(h)
    assert not r["opened"] and "DecodingError" in r["reason"]


def test_form_redirected_to_another_site_is_not_resent():
    calls = []

    def h(req):
        calls.append((req.method, req.url.host))
        if req.method == "GET":
            return httpx.Response(200, html='<p>Unsubscribe</p><form method="post" action="/x"><input type="hidden" name="t" '
                                            'value="1"><button>Unsubscribe</button></form>')
        if req.url.host == "esp.test":
            return httpx.Response(307, headers={"location": "https://collector.evil.test/c"})
        raise AssertionError("must not re-send the form")
    r = _open(h)
    assert calls == [("GET", "esp.test"), ("POST", "esp.test")] and "another site" in r["stopped"]


def test_form_redirected_on_the_same_site_is_followed():
    def h(req):
        if req.method == "GET":
            return httpx.Response(200, html='<p>Unsubscribe</p><form method="post" action="/x"><button>Unsubscribe</button></form>')
        if req.url.path == "/x":
            return httpx.Response(307, headers={"location": "https://www.esp.test/y"})
        assert req.method == "POST" and req.url.host == "www.esp.test"
        return httpx.Response(200, html="<p>You have been unsubscribed.</p>")
    r = _open(h)
    assert r["looks_unsubscribed"] and r["clicks"] == 1


# ------------------------------------------------------------------ mailbulk two-step flow
def _message(html: str, headers: dict[str, str] | None = None) -> bytes:
    m = EmailMessage()
    m["From"] = "PLANEO <info@planeo.test>"
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content("plain")
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


class _Conn:
    def __init__(self, raw: bytes):
        self.raw = raw

    def fetch(self, uids, items):
        head, _, _ = self.raw.partition(b"\n\n")
        return {uids[0]: {b"BODY[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST)]": head + b"\r\n\r\n"}}


class _Mail:
    def __init__(self, raw: bytes, allow: bool = True):
        self.raw, self.s = raw, SimpleNamespace(allow_unsubscribe_links=allow, allow_send=False)

    @contextmanager
    def imap(self):
        yield _Conn(self.raw)

    def resolve_folder(self, c, name):
        return {"junk": "Junk"}.get(name.lower(), name)

    def _select(self, c, folder, readonly=True, expect=None):
        return 7

    def _fetch_raw(self, c, folder, uid, uidvalidity=None):
        return self.raw, (), None, 7


def test_off_by_default_says_so():
    r = mailbulk.unsubscribe(_Mail(_message(PLANEO_FOOTER), allow=False), "INBOX", 1)
    assert not r["unsubscribed"] and "ALLOW_UNSUBSCRIBE_LINKS" in r["reason"]


def test_preview_then_confirm_opens_only_that_page():
    mail, opened = _Mail(_message(PLANEO_FOOTER)), []
    pub = unsublink.public_web
    unsublink.public_web = lambda url: None          # no DNS in tests
    try:
        prev = mailbulk.unsubscribe(mail, "INBOX", 1)
        assert prev["needs_confirmation"] and prev["page"]["link_text"] == "sem"
        assert prev["page"]["source"] == "message body" and not opened
        done = mailbulk.unsubscribe(mail, "INBOX", 1, confirm_token=prev["confirm_token"],
                                    open_page=lambda u: opened.append(u) or {"opened": True, "looks_unsubscribed": True})
        assert done["unsubscribed"] and opened == ["https://cdn.example-esp.com/e/unsub/click"]
        with pytest.raises(Exception, match="confirm_token"):
            mailbulk.unsubscribe(mail, "INBOX", 2, confirm_token=prev["confirm_token"], open_page=lambda u: {})
    finally:
        unsublink.public_web = pub


def test_header_web_page_goes_through_the_same_confirm():
    raw = _message("<p>hi</p>", {"List-Unsubscribe": "<https://lists.test/u/1>"})
    pub = unsublink.public_web
    unsublink.public_web = lambda url: None
    try:
        prev = mailbulk.unsubscribe(_Mail(raw), "INBOX", 1)
        assert prev["needs_confirmation"] and prev["page"]["source"] == "List-Unsubscribe header"
    finally:
        unsublink.public_web = pub


def test_junk_is_still_refused():
    r = mailbulk.unsubscribe(_Mail(_message(PLANEO_FOOTER)), "Junk", 1)
    assert not r["unsubscribed"] and "Junk" in r["reason"]


def test_no_unsubscribe_link_in_body():
    r = mailbulk.unsubscribe(_Mail(_message('<a href="https://x.test/">Shop</a>')), "INBOX", 1)
    assert not r["unsubscribed"] and "no link" in r["reason"]
