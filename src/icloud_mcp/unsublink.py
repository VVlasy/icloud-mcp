"""Unsubscribe web pages: finding the unsubscribe link in a message body and opening it, for senders without RFC 8058 one-click.

Off unless ALLOW_UNSUBSCRIBE_LINKS=true, and always two steps (see mailbulk.unsubscribe): a preview names the link, the agent shows
it to the user, and only a call with the preview's confirm_token opens it. What this module adds on top:

- find_link: only a link whose own text, or the text just before it ("... nepřejete si dostávat ..., klikněte sem"), says
  unsubscribe in one of the supported languages. The last such link wins (unsubscribe links sit in the footer). Any other link in
  the body is never a candidate, so a message cannot get the server to open an arbitrary URL.
- open_link: GET with redirects followed by hand, every hop checked to be a public http(s) address, and the connection made to the
  address that was checked (so a name cannot resolve to a public address for the check and a private one for the visit). Then up
  to MAX_CLICKS forms are submitted, one per page, until the page says the address was removed. The first only on a page about
  unsubscribing; every one only when the page has exactly one form with exactly one matching button, the form posts to the same
  site as the first page that loaded (a redirect that would re-send it elsewhere is refused), it was not submitted before, and it
  asks for nothing the user would have to type (an empty email box, a required field). Optional fields (a "why are you leaving"
  survey) go empty; one "unsubscribe from all" box is ticked when its label clearly says so, and a ticked consent box ("Souhlas se
  zasíláním e-mailů", a consent category sent as "grant") is unticked, since sending it would keep the mail coming. Bodies are read
  up to a cap (counted after decompression), the whole visit has one time limit, and pages are only returned as short text, with
  the path taken.
- form_address: the one address a form that asks for an email may be given, the address this sender mailed: a Hide My Email
  alias as itself (never the address it forwards to, which would undo the alias), or one of the owner's addresses the message was
  sent to directly. The preview names it and the token covers it. At most two empty email fields ("email", "repeat email") are
  filled; a form with a CAPTCHA, or any other field to type, is left for the user.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import zlib
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

# "Unsubscribe" in the wording newsletters actually use (cs, en, de, fr, es, pl, sk). Matched on lowercased, space-collapsed text.
_UNSUB = re.compile(
    r"unsubscri|opt[- ]?out|odhl[aá][sš]|nep[řr][eé]jete.{0,60}?dost[aá]vat|nechcete.{0,60}?dost[aá]vat|"
    r"zru[sš]it\s+odb[eě]r|odb[eě]r\s+zru[sš]|abmeld|abbestell|d[ée]sinscri|d[ée]sabonn|darse\s+de\s+baja|cancelar\s+suscrip|"
    r"wypisz|rezygnuj|odhl[aá]si[tť]|nechcem.{0,40}?dost[aá]va[tť]")
# A submit control that confirms the action on a page that is already about unsubscribing.
_CONFIRM = re.compile(r"potvr[dď]|confirm|ano\b|yes\b|odeslat|submit|best[äa]tig|valider|confirmar|potwierd")
# A button that moves an unsubscribe flow on (a survey's "send", "continue"); only on pages after the first click.
_NEXT = re.compile(r"pokra[cč]ovat|continue|next|weiter|suivant|siguiente|dalej|odeslat|send|done|hotovo|dokon[cč]it|finish")
# The one "unsubscribe from all" box a preference centre may show.
_ALL = re.compile(r"(unsubscri|odhl[aá][sš]|abmeld|d[ée]sinscri|opt[- ]?out|zru[sš]).{0,40}?\b(all|v[sš]e|v[sš]ech|alle[nms]?|tous|toutes|todos)\b|"
                  r"\b(all|v[sš]ech)\b.{0,30}?(unsubscri|odhl[aá][sš])")
# Wording of a finished unsubscribe, to say whether it looks done. The agent still reads page_text. The Czech noun alone
# ("Odhlášení z newsletteru") is a heading, not a result; with a finished verb ("Odhlášení proběhlo úspěšně") it is one.
_DONE = re.compile(
    r"unsubscribed|been removed|no longer receive|odhl[aá][sš]en(?![ií]\b)(?!í)|odhl[aá][sš]eni\b|abgemeldet|d[ée]sinscrit|"
    r"dado de baja|wypisan|nebudete.{0,40}?dost[aá]vat|"
    r"odhl[aá][sš]en[ií]\s+(?:prob[eě]hl|byl[oa]?\s+(?:[uú]sp[eě][sš]n|dokon[cč]en|proveden))|\bodhl[aá]sil[aiy]?\b")
# A ticked box that keeps the mail coming: a consent ("Souhlas se zasíláním e-mailů", "I agree to receive newsletters") or a
# Bloomreach/Exponea consent category (value "grant"). Sent ticked with the unsubscribe button, it tells the sender to keep
# sending, so on an unsubscribe form it is unticked. A box whose own label says unsubscribe is never one of these.
_KEEP = re.compile(r"souhlas|consent|einwillig|zgod|\bopt[- ]?in\b|(?<!un)subscri|zas[ií]l[aá]n|dost[aá]vat|receive|newsletter")
# A text box for an email address, by its name, id, placeholder, autocomplete or label attribute.
_EMAIL_FIELD = re.compile(r"e-?mail|\bmail\b", re.I)
# A CAPTCHA in a form (reCAPTCHA, hCaptcha, Cloudflare Turnstile, Friendly Captcha): never solved, the form is left for the user.
_CAPTCHA = re.compile(r"g-recaptcha|h-captcha|cf-turnstile|frc-captcha", re.I)
MAX_EMAIL_FIELDS = 2      # "email" and "repeat email"

MAX_REDIRECTS = 5
MAX_CLICKS = 3
MAX_PAGE_BYTES = 512 * 1024
MAX_SECONDS = 45          # the whole visit, every page and form together: a server that sends a byte at a time cannot hold it
PAGE_TEXT_CHARS = 1500
_UA = "Mozilla/5.0 (compatible; icloud-mcp unsubscribe; owner-confirmed)"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


# ------------------------------------------------------------------ finding the link
class _Anchors(HTMLParser):
    """Every <a href> with its own text and the text between the previous link and it (the sentence that leads up to it)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._since_last: list[str] = []
        self._open: dict[str, Any] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag == "a" and self._open is None:
            href = dict(attrs).get("href") or ""
            self._open = {"href": href.strip(), "text": [], "context": " ".join(self._since_last)}

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._open is not None:
            o, self._open = self._open, None
            self.links.append({"href": o["href"], "text": " ".join(o["text"]), "context": o["context"]})
            self._since_last = []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        (self._open["text"] if self._open is not None else self._since_last).append(data)


def _plain_links(text: str) -> list[dict[str, str]]:
    """Links in a plain-text body ("... klikněte sem [https://...]" or a bare URL) with the text since the previous link."""
    out, last = [], 0
    for m in re.finditer(r"https?://[^\s\]\)>\"']+", text):
        out.append({"href": m.group(0), "text": "", "context": text[last:m.start()]})
        last = m.end()
    return out


def find_link(html: str | None, plain: str | None) -> dict[str, str] | None:
    """The unsubscribe link of a message body, or None. HTML is preferred; the plain-text part is used when there is no HTML."""
    if html:
        p = _Anchors()
        try:
            p.feed(html)
            p.close()
        except Exception:  # noqa: BLE001 - malformed HTML: use what was parsed
            pass
        links = p.links
    else:
        links = _plain_links(plain or "")
    best: dict[str, str] | None = None
    best_score = 0
    for link in links:
        href = link["href"]
        if not href.lower().startswith(("https://", "http://")):
            continue
        text, context = _norm(link["text"]), _norm(link["context"])[-200:]
        score = 2 if _UNSUB.search(text) else 1 if _UNSUB.search(context) else 0
        if score and score >= best_score:        # ">=": the last of equally good links wins, which is the footer one
            best, best_score = {"url": href, "link_text": link["text"].strip()[:120], "context": context[-160:]}, score
    return best


def form_address(to_cc: list[str], hme: list[str], own: set[str]) -> tuple[str | None, str]:
    """The address an unsubscribe form may be given, the one this sender mailed: (address, how it was found) or (None, why not).

    to_cc are the message's To and Cc headers, hme its X-ICLOUD-HME headers ("p=<alias>; d=<site>; f=<forwarded to>; ..."). With
    Hide My Email the alias is used, and only when it is the message's recipient; the forwarding address (f=) never is, since
    handing it to the sender would undo the alias. Without it, one of the owner's addresses the message was sent to directly."""
    rcpts = {a.strip().lower() for _, a in getaddresses(to_cc) if "@" in a}
    aliases = set()
    for h in hme:
        params = dict(p.strip().split("=", 1) for p in h.split(";") if "=" in p)
        if alias := params.get("p", "").strip().lower():
            aliases.add(alias)
    if aliases:
        if len(aliases) == 1 and aliases <= rcpts:
            return next(iter(aliases)), "the Hide My Email alias this message was sent to"
        return None, "the message's Hide My Email header does not match its recipient"
    mine = rcpts & {a.lower() for a in own}
    if len(mine) == 1:
        return next(iter(mine)), "your address this message was sent to"
    if mine:
        return None, "the message was sent to more than one of your addresses"
    return None, "the message was not sent to one of your known addresses (aliases can be added to OWNER_ADDRESSES)"


# ------------------------------------------------------------------ opening it
def public_web(url: str) -> str | None:
    """Why a URL may not be opened, or None when it is a public http(s) address (every resolved address is global)."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return "only http and https addresses are opened"
    if parts.username or parts.password:
        return "the address carries credentials"
    return _public_address(parts.hostname, parts.port or (443 if parts.scheme.lower() == "https" else 80))[0]


def _public_address(host: str, port: int) -> tuple[str | None, str | None]:
    """(why not, None), or (None, the address to connect to) when every address the name resolves to is global."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return "the address does not resolve", None
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            return "the address points into a private or local network", None
    return None, infos[0][4][0]


class _Pinned(httpx.BaseTransport):
    """Connects to the address it has just checked, not to whatever the name resolves to a moment later (DNS rebinding). The Host
    header, the TLS server name and the certificate check keep the real name; connections are not reused across names."""

    def __init__(self, inner: httpx.BaseTransport | None = None,
                 resolve: Callable[[str, int], tuple[str | None, str | None]] | None = None) -> None:
        self._inner = inner or httpx.HTTPTransport(limits=httpx.Limits(max_keepalive_connections=0))
        self._resolve = resolve or _public_address

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        why, ip = self._resolve(url.host, url.port or (443 if url.scheme == "https" else 80))
        if why or not ip:
            raise PermissionError(f"{url.host}: {why}")
        request.url = url.copy_with(host=ip)
        request.extensions = {**request.extensions, "sni_hostname": url.host}
        try:
            return self._inner.handle_request(request)
        finally:
            request.url = url               # cookies are kept for the real name

    def close(self) -> None:
        self._inner.close()


class _Forms(HTMLParser):
    """The forms on a page: action, method, fields, submit controls, and which fields the user would have to fill in."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._form: dict[str, Any] | None = None
        self._button: dict[str, Any] | None = None
        self._select: dict[str, Any] | None = None
        self._label: dict[str, Any] | None = None
        self._label_for: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": (a.get("method") or "get").lower(), "fields": [],
                          "boxes": [], "submits": [], "needs_input": None, "email_fields": 0, "captcha": False}
            self.forms.append(self._form)
            return
        if tag == "label":
            self._label = {"for": a.get("for", ""), "text": "", "boxes": []}
            return
        f = self._form
        if f is None:
            return
        if "data-sitekey" in a or _CAPTCHA.search(a.get("class", "")) or _CAPTCHA.search(a.get("name", "")):
            f["captcha"] = True
        required = "required" in a
        if tag == "input":
            kind = (a.get("type") or "text").lower()
            if kind in ("submit", "image"):
                f["submits"].append({"name": a.get("name", ""), "value": a.get("value", ""), "text": a.get("value", "")})
            elif kind in ("password", "file"):
                f["needs_input"] = f["needs_input"] or f"a {kind} field"
            elif kind in ("checkbox", "radio"):
                box = {"kind": kind, "name": a.get("name", ""), "value": a.get("value") or "on", "checked": "checked" in a,
                       "id": a.get("id", ""), "label": ""}
                f["boxes"].append(box)
                if self._label is not None:
                    self._label["boxes"].append(box)
                if required and kind == "radio":
                    f.setdefault("required_radios", set()).add(box["name"])
            elif kind not in ("button", "reset"):
                empty = not a.get("value")
                hint = " ".join(a.get(k, "") for k in ("name", "id", "placeholder", "autocomplete", "aria-label"))
                if kind != "hidden" and empty and (kind == "email" or kind == "text" and _EMAIL_FIELD.search(hint)):
                    if a.get("name"):
                        f["fields"].append((a["name"], None))     # the address for this sender goes here (see _pick_form)
                        f["email_fields"] += 1
                    else:
                        f["needs_input"] = f["needs_input"] or "an email address"
                else:
                    if kind != "hidden" and empty and required:
                        f["needs_input"] = f["needs_input"] or "a required field"
                    if a.get("name"):
                        f["fields"].append((a["name"], a.get("value", "")))
        elif tag == "button":
            kind = (a.get("type") or "submit").lower()
            self._button = {"name": a.get("name", ""), "value": a.get("value", ""), "text": "", "submit": kind == "submit"}
        elif tag == "select" and a.get("name"):
            self._select = {"name": a["name"], "first": None, "selected": None, "required": required}
        elif tag == "option" and self._select is not None:
            val = a.get("value", "")
            if self._select["first"] is None:
                self._select["first"] = val
            if "selected" in a:
                self._select["selected"] = val
        elif tag == "textarea":
            if required:
                f["needs_input"] = f["needs_input"] or "a required field"
            elif a.get("name"):
                f["fields"].append((a["name"], ""))     # an optional comment box goes empty

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = None
        elif tag == "label" and self._label is not None:
            lab, self._label = self._label, None
            for box in lab["boxes"]:
                box["label"] = lab["text"]
            if lab["for"]:
                self._label_for[lab["for"]] = lab["text"]
        elif tag == "button" and self._button is not None:
            if self._form is not None and self._button["submit"]:
                self._form["submits"].append(self._button)
            self._button = None
        elif tag == "select" and self._select is not None:
            sel, self._select = self._select, None
            if self._form is not None:
                chosen = sel["selected"] if sel["selected"] is not None else sel["first"]
                if sel["required"] and not chosen:
                    self._form["needs_input"] = self._form["needs_input"] or "a required choice"
                elif chosen is not None:
                    self._form["fields"].append((sel["name"], chosen))

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button["text"] += data
        if self._label is not None:
            self._label["text"] += data

    def close(self) -> None:
        super().close()
        for form in self.forms:
            for box in form["boxes"]:
                if not box["label"] and box["id"] in self._label_for:
                    box["label"] = self._label_for[box["id"]]


def _site(host: str | None) -> str:
    """The registrable part of a host, near enough without a public-suffix list: shop.planeo.cz -> planeo.cz, a.b.co.uk -> b.co.uk."""
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _pick_form(html: str, page_text: str, *, first: bool, email: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """The one form to submit on this page, or (None, why not). The first click needs a page about unsubscribing; later clicks
    (a confirm, a survey) also accept a continue/send button. Empty email fields get `email` (see form_address), or stop it."""
    if first and not _UNSUB.search(_norm(page_text)):
        return None, "the page does not say anything about unsubscribing"
    p = _Forms()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001
        pass
    buttons = (_UNSUB, _CONFIRM) if first else (_UNSUB, _CONFIRM, _NEXT)
    matches = []
    for form in p.forms:
        hits = [b for b in form["submits"] if any(rx.search(_norm(b["text"])) for rx in buttons)]
        if len(hits) == 1:
            matches.append((form, hits[0]))
    if not matches:
        return None, ("no form with an unsubscribe, confirm or continue button" if p.forms else None)
    if len(matches) > 1:
        return None, "more than one form could be the next step"
    form, button = matches[0]
    if form["captcha"]:
        return None, "the form has a CAPTCHA, which the user has to solve"
    if form["needs_input"]:
        return None, f"the form asks for {form['needs_input']}, which the user has to fill in"
    if form["email_fields"] > MAX_EMAIL_FIELDS:
        return None, f"the form asks for more than {MAX_EMAIL_FIELDS} email addresses"
    if form["email_fields"] and not email:
        return None, "the form asks for an email address, which the user has to fill in"
    data = [(k, email if v is None else v) for k, v in form["fields"]]
    boxes = form["boxes"]
    ticked_boxes = [b for b in boxes if b["checked"] and b["name"]]
    unticked = [b for b in ticked_boxes if b["kind"] == "checkbox" and not _UNSUB.search(_norm(b["label"]))
                and (b["value"].lower() == "grant" or _KEEP.search(_norm(b["label"])))]
    data += [(b["name"], b["value"]) for b in ticked_boxes if b not in unticked]
    every = [b for b in boxes if not b["checked"] and b["name"] and _ALL.search(_norm(b["label"]))]
    ticked = None
    if len(every) == 1:
        ticked = every[0]
        if ticked["kind"] == "radio":           # a radio replaces whatever was chosen in its group
            data = [(k, v) for k, v in data if k != ticked["name"]]
        data.append((ticked["name"], ticked["value"]))
    chosen = {k for k, _ in data}
    missing = [n for n in form.get("required_radios", ()) if n not in chosen]
    if missing:
        return None, "the form asks for a required choice, which the user has to make"
    data += [(button["name"], button["value"])] if button["name"] else []
    return {"action": form["action"], "method": form["method"], "data": data, "button": button["text"].strip()[:60],
            **({"ticked": ticked["label"].strip()[:80]} if ticked else {}),
            **({"unticked": [_norm(b["label"])[:80] or b["name"] for b in unticked]} if unticked else {}),
            **({"entered": email} if form["email_fields"] else {})}, None


def _read(response: Any, deadline: float) -> tuple[str, bool]:
    """The body as text, at most MAX_PAGE_BYTES of it, and whether it was HTML. The cap counts unpacked bytes, so a small compressed
    body cannot unpack into a large one in memory; compression other than gzip or deflate is not read."""
    enc = response.headers.get("content-encoding", "").strip().lower()
    if response.is_stream_consumed:                          # built in memory (a test transport): httpx has unpacked it already
        chunks, unpack = iter([response.content]), None
    elif enc in ("gzip", "x-gzip", "deflate"):
        chunks, unpack = response.iter_raw(), zlib.decompressobj(zlib.MAX_WBITS | 32)   # a gzip or a zlib header, whichever
    elif enc in ("", "identity"):
        chunks, unpack = response.iter_raw(), None
    else:
        raise httpx.DecodingError(f"the page was sent compressed as {enc}", request=response.request)
    buf = bytearray()
    try:
        for chunk in chunks:
            buf += unpack.decompress(chunk, MAX_PAGE_BYTES - len(buf)) if unpack else chunk
            if len(buf) >= MAX_PAGE_BYTES:
                break
            if time.monotonic() > deadline:
                raise TimeoutError
    except zlib.error as e:
        raise httpx.DecodingError("the compressed page is damaged", request=response.request) from e
    ctype = response.headers.get("content-type", "")
    charset = response.charset_encoding or "utf-8"
    try:
        text = bytes(buf[:MAX_PAGE_BYTES]).decode(charset, errors="replace")
    except LookupError:
        text = bytes(buf[:MAX_PAGE_BYTES]).decode("utf-8", errors="replace")
    return text, "html" in ctype.lower() or text.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


def _request(client: Any, method: str, url: str, check: Callable[[str], str | None], deadline: float,
             data: list[tuple[str, str]] | None = None) -> tuple[str, int, str, bool]:
    """One request with redirects followed by hand, each hop checked. Returns (final_url, status, body, is_html). A redirect that
    keeps a form's POST (307, 308) has to stay on the form's site; any other redirect drops the form's fields."""
    for _ in range(MAX_REDIRECTS + 1):
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        if why := check(url):
            raise PermissionError(f"{urlsplit(url).hostname or url}: {why}")
        kwargs: dict[str, Any] = {"timeout": min(15.0, left)}
        if data is not None:
            if method == "POST":            # encoded here: a form can repeat a name (several ticked boxes), kept in page order
                kwargs["content"] = urlencode(data).encode()
                kwargs["headers"] = {"Content-Type": "application/x-www-form-urlencoded"}
            else:
                kwargs["params"] = data
        with client.stream(method, url, **kwargs) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                nxt = urljoin(url, r.headers["location"])
                if r.status_code in (301, 302, 303) or method != "POST":
                    method, data = "GET", None
                elif _site(urlsplit(nxt).hostname) != _site(urlsplit(url).hostname):
                    raise PermissionError(f"{urlsplit(nxt).hostname}: a redirect would send the form on to another site")
                url = nxt
                continue
            body, is_html = _read(r, deadline)
            return url, r.status_code, body, is_html
    raise PermissionError(f"more than {MAX_REDIRECTS} redirects")


def open_link(url: str, *, to_text: Callable[[str], str], check: Callable[[str], str | None] | None = None,
              transport: Any = None, email: str | None = None) -> dict[str, Any]:
    """Open an unsubscribe page and click through its confirmation steps (at most MAX_CLICKS). to_text turns HTML into text;
    email (from form_address, confirmed by the owner) goes into a form's empty email fields, without it such a form is not sent."""
    check = check or public_web
    deadline = time.monotonic() + MAX_SECONDS
    # trust_env=False: a proxy from the environment would make its own connection and skip the pinned, checked address.
    with httpx.Client(timeout=15, follow_redirects=False, headers={"User-Agent": _UA, "Accept-Encoding": "identity"},
                      transport=transport if transport is not None else _Pinned(), trust_env=False) as client:
        try:
            final, status, body, is_html = _request(client, "GET", url, check, deadline)
        except PermissionError as e:
            return {"opened": False, "reason": f"Not opened: {e}."}
        except TimeoutError:
            return {"opened": False, "reason": f"The page took longer than {MAX_SECONDS} seconds to load."}
        except httpx.HTTPError as e:
            return {"opened": False, "reason": f"The page could not be loaded ({type(e).__name__})."}
        site = _site(urlsplit(final).hostname)
        steps: list[dict[str, Any]] = [{"opened": urlsplit(final).hostname, "status": status}]
        out: dict[str, Any] = {"opened": True}
        sent: set[str] = set()
        text = to_text(body) if is_html else body
        for click in range(MAX_CLICKS):
            if not (is_html and 200 <= status < 300) or _DONE.search(_norm(text)):
                break
            form, why = _pick_form(body, text, first=click == 0, email=email)
            if not form:
                if why:
                    out["stopped"] = f"No form was submitted: {why}." if click == 0 else f"Stopped after {click} click(s): {why}."
                break
            action = urljoin(final, form["action"] or final)
            if _site(urlsplit(action).hostname) != site:
                out["stopped"] = f"The next form posts to another site ({urlsplit(action).hostname}), so it was not sent."
                break
            method = "POST" if form["method"] == "post" else "GET"
            key = f"{method} {action.split('?')[0]} {sorted(k for k, _ in form['data'])}"
            if key in sent:
                out["stopped"] = "The page showed the same form again, so it was not sent twice."
                break
            sent.add(key)
            try:
                final, status, body, is_html = _request(client, method, action, check, deadline, form["data"])
            except PermissionError as e:
                out["stopped"] = f"Stopped at the form: {e}."
                break
            except TimeoutError:
                out["stopped"] = f"Stopped: the pages took longer than {MAX_SECONDS} seconds."
                break
            except httpx.HTTPError as e:
                out["stopped"] = f"The form could not be sent ({type(e).__name__})."
                break
            text = to_text(body) if is_html else body
            steps.append({"clicked": form["button"], **({"ticked": form["ticked"]} if "ticked" in form else {}),
                          **({"unticked": form["unticked"]} if "unticked" in form else {}),
                          **({"entered_email": form["entered"]} if "entered" in form else {}),
                          "then": urlsplit(final).hostname, "status": status})
        else:
            if not _DONE.search(_norm(text)):
                out["stopped"] = f"Stopped after {MAX_CLICKS} clicks without the page saying the address was removed."
        done = 200 <= status < 300 and bool(_DONE.search(_norm(text)))
        return {**out, "clicks": len(steps) - 1, "steps": steps, "looks_unsubscribed": done, "page_text": text[:PAGE_TEXT_CHARS]}
