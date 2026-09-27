"""Bulk mail: telling newsletters from people, unsubscribing safely, and cleaning up many messages with a preview and an undo.

- bulk_view: a message is "bulk" when it carries List-Unsubscribe or List-Id, a bulk/list Precedence, or Auto-Submitted, or comes
  from a no-reply address. Only headers are read.
- senders: who fills a folder, grouped by address, with counts, unread counts and whether each sender can be unsubscribed from.
- unsubscribe: RFC 8058 one-click (an HTTPS POST to the address in the List-Unsubscribe header, only when List-Unsubscribe-Post
  says it is one-click), or a mailto unsubscribe sent through the normal send path (so approval, allowlist and ALLOW_SEND apply).
  Links in the message body are never followed, plain unsubscribe web pages are never opened (they are returned for the user),
  mail in Junk is refused (unsubscribing from spam confirms the address is alive), and the POST only goes to public addresses.
- bulk_action: move / archive / trash / mark read for everything matching a search, in two steps. A dry run returns the count, a
  sample and a confirm_token that stands for exactly those messages; running needs that token, so the agent must preview first
  and nothing that arrived in between is touched. Every run is logged by Message-ID and can be undone with bulk_undo; messages
  without a Message-ID are left alone (and counted), so nothing is changed that an undo could not find again.
"""
from __future__ import annotations

import email
import email.utils
import hashlib
import hmac
import secrets
import ipaddress
import json
import os
import re
import socket
import time
import uuid
from datetime import date, timedelta
from email import policy
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

from .safety import warnings_for

if TYPE_CHECKING:
    from .mail import MailService

_NOREPLY = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|notifications?|mailer-daemon|bounces?)([+@]|$)", re.I)
BULK_ACTIONS = ("move", "archive", "trash", "mark_read")
MAX_BULK, DEFAULT_BULK = 1000, 200
UNDO_DAYS = 30
_LOG = "bulk-actions.jsonl"
MAX_UIDS_PER_CHUNK = 250    # uids per COPY / STORE / EXPUNGE / FETCH; each must finish inside the 30 s socket timeout on iCloud, so
                            # this is not raised without measuring there
_MAX_SET_BYTES = 4000       # an encoded uid set per command, well inside IMAP server line limits
_OR_IDS = 25                # Message-IDs per undo SEARCH, as a balanced OR tree (nesting depth 5)
_SENDER_FIELDS = "FROM SUBJECT DATE LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST LIST-ID PRECEDENCE AUTO-SUBMITTED"


# ------------------------------------------------------------------ uid sets
def _sorted_uids(uids: Iterable[int]) -> list[int]:
    out = sorted({int(u) for u in uids})
    if out and out[0] < 1:
        raise ValueError(f"{out[0]} is not a message uid (uids start at 1).")
    return out


def uid_set(uids: Iterable[int]) -> bytes:
    """An IMAP uid set naming exactly these uids: sorted, deduplicated, and a run a:b only where every uid from a to b is in the
    list. A set never covers a gap, since a uid in a gap may be another message."""
    parts: list[str] = []
    start = prev = 0
    for u in _sorted_uids(uids):
        if prev and u == prev + 1:
            prev = u
            continue
        if prev:
            parts.append(str(start) if start == prev else f"{start}:{prev}")
        start = prev = u
    if prev:
        parts.append(str(start) if start == prev else f"{start}:{prev}")
    return ",".join(parts).encode("ascii")


def uid_chunks(uids: Iterable[int], *, max_uids: int = MAX_UIDS_PER_CHUNK, max_bytes: int = _MAX_SET_BYTES) -> Iterator[list[int]]:
    """The uids, sorted and deduplicated, in chunks of at most max_uids whose uid_set is at most max_bytes long."""
    chunk: list[int] = []
    size = start = 0                        # size: len(uid_set(chunk)); start: where the run that chunk[-1] ends began
    for u in _sorted_uids(uids):
        if chunk and u == chunk[-1] + 1:    # the run's tail (":prev", or nothing while it is one uid) becomes ":u"
            grow = 1 + len(str(u)) - (1 + len(str(chunk[-1])) if chunk[-1] > start else 0)
        else:
            grow = (1 if chunk else 0) + len(str(u))
        if chunk and (len(chunk) >= max_uids or size + grow > max_bytes):
            yield chunk
            chunk, size, grow = [], 0, len(str(u))
        if not chunk or u != chunk[-1] + 1:
            start = u
        chunk.append(u)
        size += grow
    if chunk:
        yield chunk


_SET = re.compile(r"\d{1,10}(?::\d{1,10})?(?:,\d{1,10}(?::\d{1,10})?)*")
_COPYUID = re.compile(r"\s*\[COPYUID ([1-9]\d{0,9}) ([\d:,]+) ([\d:,]+)\]")


def expand_uid_set(text: bytes | str, most: int) -> list[int] | None:
    """The uids a uid set such as 5:7,9 names, in the order written; None when it is malformed, contains 0 or names more than
    `most`, so a broken or hostile answer cannot make this build a huge list."""
    s = text.decode("ascii", "replace") if isinstance(text, bytes) else str(text)
    if not _SET.fullmatch(s):
        return None
    out: list[int] = []
    for part in s.split(","):
        a, _, b = part.partition(":")
        lo, hi = sorted((int(a), int(b or a)))         # a:b and b:a name the same uids
        if lo < 1 or len(out) + hi - lo + 1 > most:
            return None
        out.extend(range(lo, hi + 1))
    return out


def _copied_to(response: Any, chunk: list[int]) -> tuple[int, list[int]] | None:
    """(destination uidvalidity, destination uids) from a COPY answer's [COPYUID uv source destination] (RFC 4315), only when the
    source set is exactly the chunk and the destination names as many distinct uids; otherwise None, and an undo finds those
    messages by Message-ID instead."""
    text = response.decode("ascii", "replace") if isinstance(response, bytes) else str(response or "")
    m = _COPYUID.match(text)
    if not m:
        return None
    src, dst = expand_uid_set(m[2], len(chunk)), expand_uid_set(m[3], len(chunk))
    if src is None or dst is None or sorted(src) != sorted(chunk) or len(dst) != len(chunk) or len(set(dst)) != len(dst):
        return None
    return int(m[1]), sorted(dst)


def _message_ids(c: Any, uids: list[int]) -> dict[int, str]:
    """uid -> Message-ID for the selected folder's uids (those without one left out): one header FETCH per chunk, parsed as
    mail._summaries parses it, so the values are the ones undo logs have always held."""
    from .mail import _Headers

    out: dict[int, str] = {}
    for chunk in uid_chunks(uids):
        want = set(chunk)
        for uid, d in c.fetch(uid_set(chunk), ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"]).items():
            raw = next((v for k, v in d.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")), None)
            if uid in want and (mid := _Headers(raw).text("Message-ID")):
                out[uid] = mid
    return out


def _search_ids(c: Any, message_ids: Iterable[str]) -> list[int]:
    """Uids in the selected folder whose Message-ID header contains one of these, _OR_IDS ids per SEARCH instead of one each."""
    def any_of(ids: list[str]) -> list[Any]:
        if len(ids) == 1:
            return ["HEADER", "Message-ID", ids[0]]
        return ["OR", any_of(ids[:len(ids) // 2]), any_of(ids[len(ids) // 2:])]

    ids = sorted(set(message_ids))
    found: set[int] = set()
    for i in range(0, len(ids), _OR_IDS):
        found.update(c.search(any_of(ids[i:i + _OR_IDS])))
    return sorted(found)


# ------------------------------------------------------------------ headers
def parse_unsubscribe(value: str | None, post: str | None = None) -> dict[str, Any] | None:
    """The unsubscribe options in a List-Unsubscribe header: <https://...> and <mailto:...> entries, and whether the HTTPS one is
    RFC 8058 one-click (List-Unsubscribe-Post: List-Unsubscribe=One-Click)."""
    if not value:
        return None
    entries = re.findall(r"<\s*([^>\s]+)\s*>", str(value))
    https = [e for e in entries if e.lower().startswith("https://")]
    mailto = [e for e in entries if e.lower().startswith("mailto:")]
    if not https and not mailto:
        return None
    one_click = bool(https) and "list-unsubscribe=one-click" in str(post or "").replace(" ", "").lower()
    return {"https": https, "mailto": mailto, "one_click": one_click}


def bulk_view(hdr: Any, sender: str | None = None) -> dict[str, Any]:
    """Extra summary fields for a message that looks like bulk mail; {} for mail that looks written by a person. hdr is a parsed
    header block (anything with .get); sender is the From address when the caller already parsed it."""
    unsub = parse_unsubscribe(hdr.get("List-Unsubscribe"), hdr.get("List-Unsubscribe-Post"))
    precedence = str(hdr.get("Precedence") or "").strip().lower()
    auto = str(hdr.get("Auto-Submitted") or "").strip().lower()
    if sender is None:
        sender = email.utils.parseaddr(str(hdr.get("From") or ""))[1]
    bulk = bool(unsub or hdr.get("List-Id") or precedence in ("bulk", "list", "junk")
                or (auto and auto != "no") or _NOREPLY.match(sender or ""))
    if not bulk:
        return {}
    out: dict[str, Any] = {"bulk": True}
    if unsub:
        out["unsubscribe"] = {"one_click": unsub["one_click"], "by_mail": bool(unsub["mailto"]), "web_page": bool(unsub["https"])}
    return out


# ------------------------------------------------------------------ who fills the folder
def senders(mail: MailService, folder: str = "INBOX", *, days: int = 30, limit: int = 20, scan: int = 1000) -> dict[str, Any]:
    from .mail import _flag_view, _Headers, _iso_date

    days, limit, scan = max(1, min(int(days), 365)), max(1, min(int(limit), 100)), max(50, min(int(scan), 3000))
    since = date.today() - timedelta(days=days)
    groups: dict[str, dict[str, Any]] = {}
    with mail.imap() as c:
        folder = mail.resolve_folder(c, folder)
        mail._select(c, folder)
        uids = sorted(c.search(["SINCE", since]), reverse=True)[:scan]
        # Only the headers grouping needs, parsed once (no structure, size or per-message safety checks); newest first, as
        # the first message seen gives a sender's name and wins a tie for 'latest'.
        for chunk in reversed(list(uid_chunks(uids))):
            data = c.fetch(uid_set(chunk), ["FLAGS", "INTERNALDATE", f"BODY.PEEK[HEADER.FIELDS ({_SENDER_FIELDS})]"])
            for uid in reversed(chunk):
                if not (d := data.get(uid)):
                    continue
                hdr = _Headers(next((v for k, v in d.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")), None))
                frm = hdr.addrs("From")
                if not frm:
                    continue
                addr, view, when = frm[0][1].lower(), bulk_view(hdr, sender=frm[0][1]), _iso_date(hdr, d.get(b"INTERNALDATE"))
                g = groups.setdefault(addr, {"email": addr, "name": frm[0][0], "messages": 0, "unread": 0, "bulk": False,
                                             "unsubscribe": None, "latest": None, "latest_subject": None})
                g["messages"] += 1
                g["unread"] += 1 if _flag_view(d.get(b"FLAGS", ()))["unread"] else 0
                g["bulk"] = g["bulk"] or bool(view.get("bulk"))
                g["unsubscribe"] = g["unsubscribe"] or view.get("unsubscribe")
                if not g["latest"] or (when or "") > g["latest"]:
                    g["latest"], g["latest_subject"], g["latest_uid"] = when, hdr.text("Subject") or "(no subject)", uid
    ranked = sorted(groups.values(), key=lambda g: (-g["messages"], g["email"]))[:limit]
    found = warnings_for(*(g["name"] for g in ranked), *(g["latest_subject"] for g in ranked))    # a stranger's text
    return {"folder": folder, "days": days, "scanned": len(uids), "senders_found": len(groups),
            "bulk_messages": sum(g["messages"] for g in groups.values() if g["bulk"]),
            "senders": ranked, **({"safety_warnings": found} if found else {}),
            "hint": "bulk=true marks newsletters and automated mail. Use mail_run_bulk_action (dry run first) to clean up a sender, "
                    "or mail_unsubscribe_from_list with a message's uid to stop one."}


# ------------------------------------------------------------------ unsubscribing
def _public_https(url: str) -> str | None:
    """Why a URL may not be POSTed to, or None when it is a public HTTPS address (every resolved address is global)."""
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        return "only https addresses are used"
    if parts.username or parts.password:
        return "the address carries credentials"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return "the address does not resolve"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return "the address points into a private or local network"
    return None


def _post_one_click(url: str) -> dict[str, Any]:
    import httpx

    # Only the status line matters, so the body is never read: a hostile endpoint cannot make the server download anything.
    with httpx.Client(timeout=10, follow_redirects=False, headers={"User-Agent": "icloud-mcp one-click unsubscribe (RFC 8058)"}) as client:
        with client.stream("POST", url, data={"List-Unsubscribe": "One-Click"}) as r:
            return {"status": r.status_code}


def unsubscribe(mail: MailService, folder: str, uid: int, *, uidvalidity: int | None = None,
                post: Callable[[str], dict[str, Any]] | None = None, check_url: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """Unsubscribe from the list that sent one message. The sender's name, its addresses and whatever its server answered are a
    stranger's text, so the result carries safety_warnings when any of it looks like instructions."""
    out = _unsubscribe(mail, folder, uid, uidvalidity=uidvalidity, post=post, check_url=check_url)
    found = warnings_for(json.dumps(out, ensure_ascii=False))
    return {**out, "safety_warnings": found} if found else out


def _unsubscribe(mail: MailService, folder: str, uid: int, *, uidvalidity: int | None = None,
                 post: Callable[[str], dict[str, Any]] | None = None, check_url: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    post, check_url = post or _post_one_click, check_url or _public_https
    with mail.imap() as c:
        folder = mail.resolve_folder(c, folder)
        try:
            junk = mail.resolve_folder(c, "junk")
        except Exception:  # noqa: BLE001 - no Junk folder: nothing to compare against
            junk = None
        if folder == junk:
            return {"unsubscribed": False, "reason": "This message is in Junk. Unsubscribing from spam only confirms the address is "
                                                     "read. Leave it in Junk instead."}
        mail._select(c, folder, expect=uidvalidity)
        data = c.fetch([uid], ["BODY.PEEK[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST)]"]).get(uid)
    if not data:
        return {"unsubscribed": False, "reason": f"No message with uid {uid} in {folder}."}
    raw = next((v for k, v in data.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")), b"")
    hdr = email.message_from_bytes(raw, policy=policy.default)
    sender = str(hdr.get("From") or "")
    opts = parse_unsubscribe(hdr.get("List-Unsubscribe"), hdr.get("List-Unsubscribe-Post"))
    if not opts:
        return {"unsubscribed": False, "sender": sender,
                "reason": "This message has no List-Unsubscribe header. Links inside the message body are never followed; the user "
                          "can unsubscribe from the message itself, or you can filter it with mail_run_bulk_action."}
    if opts["one_click"]:
        url = opts["https"][0]
        why = check_url(url)
        if why is None:
            try:
                res = post(url)
            except Exception as e:  # noqa: BLE001
                res = {"error": f"{type(e).__name__}: {e}"[:200]}
            ok = 200 <= int(res.get("status") or 0) < 300
            if ok:
                return {"unsubscribed": True, "method": "one-click (RFC 8058)", "sender": sender,
                        "note": "The sender was asked to stop. A few more messages may still arrive while it processes this."}
            if not opts["mailto"]:
                return {"unsubscribed": False, "sender": sender, "reason": f"The one-click request did not succeed ({res})."}
        elif not opts["mailto"]:
            return {"unsubscribed": False, "sender": sender, "reason": f"The unsubscribe address was not used: {why}."}
    if opts["mailto"]:
        target = opts["mailto"][0]
        parts = urlsplit(target)
        address = unquote(parts.path)
        q = {k.lower(): v[0] for k, v in parse_qs(parts.query).items()}
        if not mail.s.allow_send:
            return {"unsubscribed": False, "sender": sender, "reason": f"Unsubscribing means emailing {address}, and sending is "
                                                                       "disabled on this server. The user can send it themselves."}
        # The mailto is the sender's: its subject is kept (list software often keys on it) but capped and stripped of control
        # characters, and the body is always the one word, never the sender's text going out under the owner's name.
        subject = re.sub(r"[\x00-\x1f\x7f]", " ", q.get("subject") or "unsubscribe").strip()[:120] or "unsubscribe"
        sent = mail.send(to=[address], subject=subject, body="unsubscribe")
        return {"unsubscribed": sent.get("status") == "sent", "method": "email to the list's unsubscribe address", "sender": sender,
                **({"waiting": "The unsubscribe email is waiting for the owner's approval or in Drafts; it takes effect once sent."}
                   if sent.get("status") != "sent" else {}), "result": sent}
    return {"unsubscribed": False, "sender": sender, "web_page": opts["https"][0],
            "reason": "This sender only offers an unsubscribe web page, which is never opened automatically (a visit can confirm "
                      "the address or do more than unsubscribe). Give the user the link to open themselves."}


# ------------------------------------------------------------------ bulk actions with preview and undo
_TOKEN_KEY = secrets.token_bytes(32)      # per process: a token cannot be computed from a search result, only obtained from a dry run
TOKEN_TTL = 900                          # seconds a dry run's confirm_token stays valid


def _token(folder: str, uv: Any, action: str, destination: str, uids: list[int], issued: int | None = None) -> str:
    issued = int(time.time()) if issued is None else issued
    raw = f"{issued}|{folder}|{uv}|{action}|{destination}|{','.join(map(str, sorted(uids)))}"
    return f"{issued}.{hmac.new(_TOKEN_KEY, raw.encode(), hashlib.sha256).hexdigest()[:16]}"


def _token_ok(token: str | None, folder: str, uv: Any, action: str, destination: str, uids: list[int]) -> str | None:
    """Why a confirm_token is not accepted, or None when it stands for exactly these messages and is not too old."""
    issued_s, _, _ = (token or "").partition(".")
    if not issued_s.isdigit():
        return "confirm_token is not one this server issued. Run the dry run again and use its token."
    issued = int(issued_s)
    if not hmac.compare_digest(token or "", _token(folder, uv, action, destination, uids, issued)):
        return ("confirm_token does not match the messages that match now (new mail arrived, or the filters changed). "
                "Run the dry run again and use its new token.")
    if time.time() - issued > TOKEN_TTL:
        return f"confirm_token is older than {TOKEN_TTL // 60} minutes. Run the dry run again and use its new token."
    return None


def _log_path(mail: MailService) -> str:
    return os.path.join(mail.s.data_dir, _LOG)


def _write_log(mail: MailService, entry: dict[str, Any]) -> None:
    path = _log_path(mail)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_log(mail: MailService) -> list[dict[str, Any]]:
    try:
        with open(_log_path(mail)) as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


def _MailError(message: str) -> Exception:
    """A MailError (imported late: mail imports this module), so the message reaches the agent scrubbed, as a normal tool error."""
    from .mail import MailError

    return MailError(message + ("" if message.endswith(".") else "."))


def bulk_action(mail: MailService, folder: str, action: str, *, destination: str | None = None, dry_run: bool = True,
                confirm_token: str | None = None, max_messages: int = DEFAULT_BULK, **filters: Any) -> dict[str, Any]:
    if action not in BULK_ACTIONS:
        raise _MailError(f"action must be one of {', '.join(BULK_ACTIONS)}.")
    if action == "move" and not destination:
        raise _MailError("action 'move' needs a destination folder (a name from mail_list_folders).")
    if not any(v not in (None, "") for v in filters.values()):
        raise _MailError("give at least one filter (from_address, subject, text, since, before, unread, flagged): "
                         "a bulk action on a whole folder is refused.")
    limit = max(1, min(int(max_messages), MAX_BULK))
    filters["since"] = mail._floor_since(filters.get("since"))
    crit, charset = mail.criteria(**filters)
    with mail.imap() as c:
        src = mail.resolve_folder(c, folder)
        dst = {"move": destination, "archive": "archive", "trash": "trash"}.get(action)
        dst = mail.resolve_folder(c, dst) if dst else None
        if action == "trash" and src == mail.resolve_folder(c, "trash"):
            raise _MailError("these messages are already in the Trash; bulk actions never delete permanently.")
        if dst and dst == src:
            raise _MailError(f"the messages are already in {src}")
        uv = mail._select(c, src, readonly=dry_run)
        matched = sorted(c.search(crit, charset=charset), reverse=True)
        picked = matched[:limit]
        # Only messages with a Message-ID are handled: that is how an undo finds them again, so everything done can be undone.
        ids = _message_ids(c, picked)
        uids = [u for u in picked if u in ids]
        token = _token(src, uv, action, dst or "", uids)
        preview = {"folder": src, "action": action, **({"destination": dst} if dst else {}), "total_matches": len(matched),
                   "would_handle": len(uids),
                   **({"left_alone_without_message_id": len(picked) - len(uids)} if len(picked) > len(uids) else {})}
        if dry_run:
            sample = mail._summaries(c, src, uids[:10])
            found = warnings_for(*(m.get("subject") for m in sample), *((m.get("from") or [{}])[0].get("name") for m in sample))
            return {**preview, "dry_run": True, "confirm_token": token if uids else None,
                    **({"safety_warnings": found} if found else {}),
                    "sample": [{"from": (m.get("from") or [{}])[0].get("email"), "subject": m.get("subject"), "date": m.get("date")}
                               for m in sample],
                    **({"note": f"Only the newest {limit} of {len(matched)} would be handled; narrow the search or run again."}
                       if len(matched) > limit else {}),
                    "next": "Show the user the count and sample. To go ahead, call again with dry_run=false and this confirm_token."}
        if not uids:
            return {**preview, "done": 0}
        if why := _token_ok(confirm_token, src, uv, action, dst or "", uids):
            raise _MailError(why)
        entry = {"action_id": uuid.uuid4().hex[:12], "time": time.time(), "folder": src, "action": action, "destination": dst,
                 "message_ids": [ids[u] for u in uids]}
        _write_log(mail, entry)                     # logged before anything changes, so an interrupted run can still be undone
        for chunk in uid_chunks(uids):
            if action == "mark_read":
                c.add_flags(uid_set(chunk), ["\\Seen"], silent=True)
            elif copied := _copied_to(mail._move_messages(c, chunk, dst), chunk):
                # Where the chunk landed, so an undo fetches those uids instead of searching for each Message-ID. A line of its
                # own that never carries action_id: the entry above stays the only record an undo looks up by it.
                _write_log(mail, {"progress_of": entry["action_id"], "dst_uidvalidity": copied[0], "dst_uids": copied[1]})
    return {**preview, "done": len(uids), "action_id": entry["action_id"],
            "undo": f"mail_undo_bulk_action with action_id {entry['action_id']} reverses this for {UNDO_DAYS} days"}


def bulk_undo(mail: MailService, action_id: str) -> dict[str, Any]:
    entries = _read_log(mail)
    entry = next((e for e in entries if e.get("action_id") == action_id and not e.get("undo_of")), None)
    if not entry:
        return {"undone": False, "reason": f"No bulk action {action_id} in the log."}
    if any(e.get("undo_of") == action_id for e in entries):
        return {"undone": False, "reason": "That action was already undone."}
    if time.time() - entry["time"] > UNDO_DAYS * 86400:
        return {"undone": False, "reason": f"That action is older than {UNDO_DAYS} days."}
    where = entry["destination"] if entry["action"] != "mark_read" else entry["folder"]
    wanted = set(entry["message_ids"])
    with mail.imap() as c:
        uv = mail._select(c, where, readonly=False)
        # Uids recorded from COPYUID count only under the same uidvalidity, and only when the message there still carries a
        # logged Message-ID; every other id (moved since, a run cut short, a MOVE server, an older log) is searched for.
        recorded = [u for e in entries if e.get("progress_of") == action_id and uv is not None and e.get("dst_uidvalidity") == uv
                    for u in e.get("dst_uids") or [] if type(u) is int and u > 0]
        verified = {u: mid for u, mid in _message_ids(c, recorded).items() if mid in wanted} if recorded else {}
        found = sorted(set(verified) | set(_search_ids(c, wanted - set(verified.values()))))
        for chunk in uid_chunks(found):
            if entry["action"] == "mark_read":
                c.remove_flags(uid_set(chunk), ["\\Seen"], silent=True)
            else:
                mail._move_messages(c, chunk, entry["folder"])
    _write_log(mail, {"action_id": uuid.uuid4().hex[:12], "time": time.time(), "undo_of": action_id, "restored": len(found)})
    return {"undone": True, "restored": len(found), "of": len(entry["message_ids"]),
            **({"note": "Some messages were no longer where the action left them (moved or deleted since) and were skipped."}
               if len(found) < len(entry["message_ids"]) else {})}
