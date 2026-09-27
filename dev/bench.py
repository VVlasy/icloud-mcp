"""Measure icloud-mcp tool calls: time, network round trips and result size, per agent-like scenario.

    python dev/bench.py --local                       # against dev/start_local_stack.sh (Dovecot, SMTP sink, Radicale)
    python dev/bench.py --local --latency-ms 40       # the same, with 40 ms added to every connect and every request
    python dev/bench.py --live --env /path/to/.env    # a real account: read-only tools only
    python dev/bench.py --live --env ... --scratch-calendar NAME   # plus create/update/delete of one event in that calendar

Counts are the numbers to compare between versions: on localhost a round trip costs almost nothing, so time alone hides what
a real network charges for every login, command and request. --latency-ms puts that cost back, so timings become meaningful too.

'bytes' is the text the model reads (the result's text parts); 'wire bytes' is the whole JSON-RPC response body as the SDK
serializes it (text parts plus any structuredContent copy and the envelope), so what a connection carries compares honestly too.

Live mode never sends mail, never writes contacts, and writes to the calendar only inside --scratch-calendar. The report
contains tool names and numbers only, never message, event or contact content.
"""
from __future__ import annotations

import argparse
import asyncio
import imaplib
import json
import os
import smtplib
import socket
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOCAL_ENV = dict(
    ICLOUD_USERNAME="test", ICLOUD_APP_PASSWORD="testpass", ICLOUD_EMAIL_ADDRESS="test@icloud.test", ICLOUD_DISPLAY_NAME="Test User",
    IMAP_HOST="127.0.0.1", IMAP_PORT="1143", IMAP_SECURITY="none", SMTP_HOST="127.0.0.1", SMTP_PORT="1025", SMTP_SECURITY="none",
    CALDAV_URL="http://127.0.0.1:5232", CALDAV_REQUIRE_TLS="false", CARDDAV_URL="http://127.0.0.1:5232", ENABLE_CONTACTS="true",
    DEFAULT_TIMEZONE="Europe/Berlin", MCP_PUBLIC_URL="https://mcp.example.com", MCP_OWNER_PASSWORD="x" * 16,
    SEND_REQUIRES_APPROVAL="false", ALLOW_CALENDAR_INVITES="false",
)
CALENDARS = ["Personal", "Work", "Health", "Admin", "Calendar"]      # generic names, as a typical iCloud account has
NOTICE_KEYS = {"notice", "hint", "note", "delivery_note"}


# --------------------------------------------------------------------------- counting and latency
class Meter:
    """Thread-safe counters for everything that crosses the network, and an optional delay per round trip."""

    def __init__(self, latency_ms: float = 0.0):
        self.c: Counter[str] = Counter()
        self.lock = threading.Lock()
        self.delay = latency_ms / 1000.0

    def add(self, key: str, n: int) -> None:
        """A quantity (bytes), not a round trip: never delayed."""
        if threading.current_thread().name.startswith(("icloud-mcp-keepalive", "icloud-warmup")):
            key = "background_" + key
        with self.lock:
            self.c[key] += n

    def hit(self, key: str, round_trips: int = 1) -> None:
        if threading.current_thread().name.startswith(("icloud-mcp-keepalive", "icloud-warmup")):
            key = "background_" + key                 # keep-alive pings and warm-up: off the call path, counted apart
        with self.lock:
            self.c[key] += 1
        if self.delay and round_trips:
            time.sleep(self.delay * round_trips)

    def snapshot(self) -> Counter[str]:
        with self.lock:
            return Counter(self.c)


def instrument(m: Meter) -> None:
    """Wrap the transports once, for the whole process. Each wrapped call is one network round trip."""
    import httpx
    from imapclient import IMAPClient

    def wrap(owner, name, key, trips=1):
        orig = getattr(owner, name)

        def w(*a, **k):
            m.hit(key, trips)
            return orig(*a, **k)
        setattr(owner, name, w)

    real_connect = socket.socket.connect

    def connect(self, address):
        if isinstance(address, tuple):               # TCP only; not unix sockets
            m.hit("tcp_connects")
        return real_connect(self, address)
    socket.socket.connect = connect
    wrap(imaplib.IMAP4, "_command", "imap_commands")
    for name in ("read", "readline"):                   # bytes the IMAP server sent: what partial fetches save
        orig = getattr(imaplib.IMAP4, name)

        def counted(self, *a, _orig=orig, **k):
            data = _orig(self, *a, **k)
            m.add("imap_kb_in", len(data))
            return data
        setattr(imaplib.IMAP4, name, counted)
    wrap(IMAPClient, "login", "imap_logins", 0)
    wrap(smtplib.SMTP, "login", "smtp_logins", 0)
    wrap(smtplib.SMTP, "putcmd", "smtp_commands")
    wrap(httpx.Client, "send", "carddav_requests")
    for mod in ("niquests", "requests"):             # caldav 3.x uses niquests; older installs use requests
        try:
            wrap(__import__(mod).Session, "request", "caldav_requests")
        except (ImportError, AttributeError):
            pass
    try:
        from icloud_mcp import bridge
        wrap(bridge.MacBridge, "call", "bridge_jobs", 0)
    except (ImportError, AttributeError):
        pass


# --------------------------------------------------------------------------- local seed data (fictional)
def seed_local(env: dict[str, str]) -> None:
    """Fill the local stack with a realistic, fictional account: people and newsletters, attachments, five calendars
    with a month of events, and an address book."""
    from email import policy
    from email.message import EmailMessage

    import caldav
    import httpx
    from imapclient import IMAPClient

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    people = [("Anna Weber", "anna@example.org"), ("Ben Okafor", "ben@example.net"), ("Carla Ruiz", "carla@example.com"),
              ("Dev Patel", "dev@example.org"), ("Eva Lind", "eva@example.net")]

    def msg(i, sender, subject, body, bulk=False, pdf_kb=0):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = f"{sender[0]} <{sender[1]}>", "Test User <test@icloud.test>", subject
        m["Date"] = (now - timedelta(hours=3 * i)).strftime("%a, %d %b %Y %H:%M:%S +0200")
        m["Message-ID"] = f"<bench-{i}@example.org>"
        if bulk:
            m["List-Unsubscribe"] = "<https://news.example.com/u>"
            m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            m.set_content(f"<html><body>{'<p>Weekly digest paragraph.</p>' * 400}</body></html>", subtype="html")
        else:
            m.set_content(body)
        if pdf_kb:
            m.add_attachment(b"%PDF-1.4\n" + os.urandom(pdf_kb * 1024), maintype="application", subtype="pdf", filename=f"doc{i}.pdf")
        return m

    with IMAPClient("127.0.0.1", port=1143, ssl=False) as c:
        c.login(env["ICLOUD_USERNAME"], env["ICLOUD_APP_PASSWORD"])
        for name in ("INBOX", "Sent Messages", "Archive"):
            c.select_folder(name)
            uids = c.search(["ALL"])
            if uids:
                c.delete_messages(uids)
                c.expunge()
        for i in range(60):
            sender = people[i % len(people)] if i % 3 else ("News Example", "news@news.example.com")
            m = msg(i, sender, f"Subject {i}", "Hello,\n\nA short note.\n\nRegards", bulk=not i % 3, pdf_kb=300 if i % 10 == 1 else 0)
            c.append("INBOX", m.as_bytes(policy=policy.SMTP), flags=() if i % 4 else ("\\Seen",))
        for i in range(60, 80):
            c.append("Archive", msg(i, people[i % 5], f"Old {i}", "Archived note.").as_bytes(policy=policy.SMTP), flags=("\\Seen",))
        for i in range(80, 90):
            m = msg(i, ("Test User", "test@icloud.test"), f"Re: Subject {i}", "Thanks, will do.")
            m.replace_header("To", f"{people[i % 5][0]} <{people[i % 5][1]}>")
            c.append("Sent Messages", m.as_bytes(policy=policy.SMTP), flags=("\\Seen",))

    with caldav.DAVClient(url=env["CALDAV_URL"], username=env["ICLOUD_USERNAME"], password=env["ICLOUD_APP_PASSWORD"],
                          require_tls=False) as client:
        p = client.principal()
        have = {c.get_display_name(): c for c in p.calendars()}
        for name in CALENDARS:
            cal = have.get(name) or p.make_calendar(name=name)
            for ev in cal.events():
                ev.delete()
            for d in range(0, 30, 2):
                start = now + timedelta(days=d, hours=CALENDARS.index(name))
                cal.save_event(dtstart=start, dtend=start + timedelta(hours=1), summary=f"{name} item {d}",
                               uid=f"bench-{name}-{d}@example.org")
            cal.save_event(dtstart=now + timedelta(hours=1), dtend=now + timedelta(hours=2), summary=f"{name} weekly",
                           uid=f"bench-{name}-weekly@example.org", rrule={"FREQ": "WEEKLY", "COUNT": 10})

    base = f"{env['CARDDAV_URL']}/{env['ICLOUD_USERNAME']}/contacts/"
    with httpx.Client(auth=(env["ICLOUD_USERNAME"], env["ICLOUD_APP_PASSWORD"]), timeout=30) as h:
        h.request("DELETE", base)
        h.request("MKCOL", base, content='<?xml version="1.0"?><d:mkcol xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
                  '<d:set><d:prop><d:resourcetype><d:collection/><c:addressbook/></d:resourcetype></d:prop></d:set></d:mkcol>')
        for i in range(200):
            first, last = ["Anna", "Ben", "Carla", "Dev", "Eva", "Finn", "Gita", "Hugo"][i % 8], f"Example{i}"
            card = (f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:bench-{i}\r\nFN:{first} {last}\r\nN:{last};{first};;;\r\n"
                    f"EMAIL;TYPE=INTERNET:{first.lower()}.{i}@example.org\r\nTEL;TYPE=CELL:+4930{1000000 + i}\r\nEND:VCARD\r\n")
            h.put(f"{base}bench-{i}.vcf", content=card, headers={"Content-Type": "text/vcard"})


# --------------------------------------------------------------------------- scenarios
def parts_of(res) -> list[str]:
    parts = getattr(res, "content", None)
    if parts is None and isinstance(res, tuple):
        parts = res[0]
    return [getattr(p, "text", "") for p in (parts or [])]


def parsed(res):
    """The result as JSON (older versions sent a list result as one text part per item)."""
    items = []
    for t in parts_of(res):
        try:
            items.append(json.loads(t))
        except ValueError:
            items.append(t)
    return items[0] if len(items) == 1 else items


def wire_bytes(res) -> int:
    """The JSON-RPC response body for this result, serialized as the SDK sends it (result dumped without None fields)."""
    import pydantic_core
    result = res.model_dump(by_alias=True, mode="json", exclude_none=True) if hasattr(res, "model_dump") else {}
    return len(pydantic_core.to_json({"jsonrpc": "2.0", "id": 1, "result": result}))


def payload_of(res) -> tuple[int, int]:
    """(bytes of the result text the model reads, characters inside notice/hint/note fields)."""
    size = sum(len(t.encode()) for t in parts_of(res))
    notice, stack = 0, [parsed(res)]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            for k, x in v.items():
                if k in NOTICE_KEYS and isinstance(x, str):
                    notice += len(x)
                else:
                    stack.append(x)
        elif isinstance(v, list):
            stack.extend(v)
    return size, notice


class Bench:
    def __init__(self, settings, meter: Meter, runs: int):
        self.s, self.m, self.runs = settings, meter, runs
        self.rows: list[dict] = []

    def server(self):
        from icloud_mcp.server import create_server
        return create_server(self.s)[0]

    async def call(self, mcp, tool, args):
        res = await mcp.call_tool(tool, args)
        out = parsed(res)
        return res, out if isinstance(out, dict) else {"items": out}

    async def measure(self, name, steps, fresh=False, mcp=None):
        """Run a chain of (tool, args-or-callable) steps `runs` times; one row with medians over the runs."""
        times, sizes, wires, notices, deltas = [], [], [], [], []
        for _ in range(self.runs):
            srv = self.server() if fresh or mcp is None else mcp
            before = self.m.snapshot()
            t0, size, wire, notice, prev = time.perf_counter(), 0, 0, 0, None
            for tool, args in steps:
                a = args(prev) if callable(args) else args
                if a is None:
                    continue
                res, prev = await self.call(srv, tool, a)
                b, n = payload_of(res)
                size, wire, notice = size + b, wire + wire_bytes(res), notice + n
            times.append(time.perf_counter() - t0)
            sizes.append(size)
            wires.append(wire)
            notices.append(notice)
            deltas.append(self.m.snapshot() - before)
        keys = sorted({k for d in deltas for k in d})
        row = {"scenario": name, "median_s": statistics.median(times),
               "p90_s": sorted(times)[max(0, int(len(times) * 0.9 + 0.5) - 1)],
               "bytes": int(statistics.median(sizes)), "wire_bytes": int(statistics.median(wires)),
               "notice_chars": int(statistics.median(notices))}
        row.update({k: statistics.median([d.get(k, 0) for d in deltas]) for k in keys})
        self.rows.append(row)
        print(f"  {name}: {row['median_s']:.3f} s", file=sys.stderr)

    async def idle_loop(self, calls: int, gap: float):
        """A mail search and a week of calendar, repeated with long pauses in between, as an agent that checks in now and
        then does. Without keep-alive every call after a pause reconnects; background_* columns show the pings instead."""
        mcp = self.server()
        today = date.today()
        steps = [("mail_search_messages", {"folder": "INBOX", "limit": 5}),
                 ("calendar_list_events", {"start": str(today), "end": str(today + timedelta(days=7))})]
        times: dict[str, list[float]] = {t: [] for t, _ in steps}
        deltas: dict[str, Counter] = {t: Counter() for t, _ in steps}
        for i in range(calls):
            for tool, a in steps:
                before = self.m.snapshot()
                t0 = time.perf_counter()
                await self.call(mcp, tool, a)
                times[tool].append(time.perf_counter() - t0)
                d = self.m.snapshot() - before
                deltas[tool] += Counter({k: v for k, v in d.items() if not k.startswith("background_")})
            if i < calls - 1:
                before = self.m.snapshot()
                await asyncio.sleep(gap)
                bg = self.m.snapshot() - before
                deltas[steps[1][0]] += Counter({k: v for k, v in bg.items() if k.startswith("background_")})
        for tool, _ in steps:
            self.rows.append({"scenario": f"{tool} x{calls}, {gap:g} s apart (totals)", "median_s": statistics.median(times[tool]),
                              "p90_s": max(times[tool]), "bytes": 0, "wire_bytes": 0, "notice_chars": 0,
                              **deltas[tool]})


async def run(args, settings, meter: Meter) -> list[dict]:
    b = Bench(settings, meter, args.runs)
    today = date.today()
    d7, d14, d30 = (today + timedelta(days=n) for n in (7, 14, 30))
    warm = b.server()
    for job in getattr(warm, "_icloud_warmups", {}).values():               # what WARMUP_ON_START does at server start
        job()

    def first_uids(prev):
        uids = [m["uid"] for m in (prev or {}).get("messages", [])][:10]
        return {"folder": "INBOX", "uids": uids} if uids else None

    def attachment(prev):
        hit = next((m for m in (prev or {}).get("messages", []) if m.get("has_attachments")), None)
        return {"folder": "INBOX", "uid": hit["uid"], "index": 0} if hit else None

    await b.measure("mail_search_messages 20", [("mail_search_messages", {"folder": "INBOX", "limit": 20})], mcp=warm)
    await b.measure("mail_search_messages 20 + get_messages 10", [("mail_search_messages", {"folder": "INBOX", "limit": 20}),
                                                         ("mail_get_messages", first_uids)], mcp=warm)
    await b.measure("mail_search_messages all_folders", [("mail_search_messages", {"all_folders": True, "limit": 20})], mcp=warm)
    await b.measure("mail_get_attachment (300 KB pdf)", [("mail_search_messages", {"folder": "INBOX", "limit": 20}),
                                                         ("mail_get_attachment", attachment)], mcp=warm)
    await b.measure("calendar_list_calendars (cold)", [("calendar_list_calendars", {})], fresh=True)
    await b.measure("calendar_list_calendars (warm)", [("calendar_list_calendars", {})], mcp=warm)
    await b.measure("calendar_list_events 7 days", [("calendar_list_events", {"start": str(today), "end": str(d7)})], mcp=warm)
    await b.measure("calendar_list_events 30 days", [("calendar_list_events", {"start": str(today), "end": str(d30)})], mcp=warm)
    await b.measure("calendar_list_events 30 days, fields=summary", [("calendar_list_events", {"start": str(today), "end": str(d30),
                                                                                              "fields": "summary"})], mcp=warm)
    await b.measure("calendar_find_free_time 14 days", [("calendar_find_free_time", {"start": str(today), "end": str(d14),
                                                                                     "duration_minutes": 60})], mcp=warm)
    if settings.enable_contacts:
        await b.measure("contacts_search_contacts (cold)", [("contacts_search_contacts", {"query": "Anna"})], fresh=True)
        await b.measure("contacts_search_contacts (warm)", [("contacts_search_contacts", {"query": "Anna"})], mcp=warm)
    scratch = args.scratch_calendar if args.live else "Personal"
    if scratch:
        start = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=40)
        await b.measure("calendar create + update + delete", [
            ("calendar_create_event", {"summary": "Benchmark scratch event", "start": start.isoformat(),
                                       "end": (start + timedelta(hours=1)).isoformat(), "calendar": scratch}),
            ("calendar_update_event", lambda p: {"uid": p["uid"], "calendar": scratch, "summary": "Benchmark scratch event (edited)"}),
            ("calendar_delete_event", lambda p: {"uid": p["uid"], "calendar": scratch}),
        ], mcp=warm)
    if args.idle_calls:
        await b.idle_loop(args.idle_calls, args.gap)
    return b.rows


def table(rows: list[dict], title: str) -> str:
    counts = ["tcp_connects", "imap_logins", "imap_commands", "imap_kb_in", "smtp_logins", "caldav_requests", "carddav_requests",
              "bridge_jobs"]
    counts += ["background_" + c for c in counts]
    shown = [c for c in counts if any(r.get(c) for r in rows)]
    head = ["scenario", "median s", "p90 s", "bytes", "wire bytes", "notice chars"] + [c.replace("_", " ") for c in shown]
    out = [f"### {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        cells = [r["scenario"], f"{r['median_s']:.3f}", f"{r['p90_s']:.3f}", str(r["bytes"]), str(r.get("wire_bytes", 0)),
                 str(r["notice_chars"])]
        cells += [f"{r.get(c, 0) / 1024:.0f}" if c.endswith("kb_in") else f"{r.get(c, 0):g}" for c in shown]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def load_env(path: str) -> dict[str, str]:
    env = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", action="store_true")
    mode.add_argument("--live", action="store_true")
    ap.add_argument("--env", help="live mode: the server's .env file")
    ap.add_argument("--scratch-calendar", help="live mode: a calendar the bench may create, edit and delete one event in")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--latency-ms", type=float, default=0.0, help="added to every connect and every request/command")
    ap.add_argument("--idle-calls", type=int, default=10, help="mail_search_messages calls in the spaced-out loop (0 = skip)")
    ap.add_argument("--gap", type=float, default=20.0, help="seconds between those calls")
    ap.add_argument("--no-seed", action="store_true", help="local mode: reuse the data already in the stack")
    ap.add_argument("--out", help="also write the markdown table to this file")
    ap.add_argument("--json", help="also write the raw rows as JSON to this file")
    args = ap.parse_args()

    if args.live:
        if not args.env:
            ap.error("--live needs --env")
        env = load_env(args.env)
        env.update(SEND_REQUIRES_APPROVAL="true", ALLOW_SEND="false")        # belt and braces: the bench never sends
    else:
        env = dict(LOCAL_ENV)
    env.setdefault("DATA_DIR", str(Path(os.environ.get("TMPDIR", "/tmp")) / "icmcp-bench-data"))
    os.environ.update(env)
    from icloud_mcp.config import Settings
    settings = Settings.from_env()

    if args.local and not args.no_seed:
        print("seeding the local stack...", file=sys.stderr)
        seed_local(env)
    meter = Meter(args.latency_ms)
    instrument(meter)
    rows = asyncio.run(run(args, settings, meter))
    title = f"{'live' if args.live else 'local'} stack, {args.runs} runs" + (f", +{args.latency_ms:g} ms per round trip" if args.latency_ms else "")
    md = table(rows, title)
    print(md)
    if args.out:
        Path(args.out).write_text(md)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
