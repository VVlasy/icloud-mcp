"""Partial message fetches: read a message's structure (IMAP BODYSTRUCTURE), then fetch only the headers of every part and the
bodies that are actually needed, and rebuild a "skeleton" message from them.

The skeleton has exactly the original MIME tree: every part keeps its own headers (so filenames, content types, dispositions and
charsets are the real ones), text bodies are real, and the parts that were not needed have an empty body plus two internal
headers, X-Icloud-Mcp-Section (the IMAP section number) and X-Icloud-Mcp-Size (the decoded size, computed from BODYSTRUCTURE).
Any such header already in the message is removed first, so a sender cannot plant them. Because the rest of the mail code parses
the skeleton with the same functions it uses for full messages, attachment numbering stays exactly the same.

Every skeleton is checked against the structure it came from (same leaves, same content types, same section numbers, in the
same order). When anything does not line up the caller falls back to fetching the whole message, as before.
"""
from __future__ import annotations

import email
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from typing import Any, Callable, Iterator

SECTION = "X-Icloud-Mcp-Section"
SIZE = "X-Icloud-Mcp-Size"
_OURS = re.compile(rb"^x-icloud-mcp-[^:]*:.*(?:\r?\n[ \t].*)*\r?\n?", re.I | re.M)
BODY_TEXT = ("text/plain", "text/html")


def _s(v: Any) -> str:
    return v.decode("ascii", "replace") if isinstance(v, bytes) else str(v or "")


@dataclass
class Node:
    section: str                  # IMAP section number; "" for the top level
    multipart: bool
    ctype: str                    # type/subtype, lower case
    size: int = 0                 # encoded size in octets (leaves)
    encoding: str = ""
    children: list["Node"] = field(default_factory=list)
    filename: str = ""            # leaves: the file name parameters as sent (RFC 2231 pieces joined, not decoded)

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def leaves(self) -> list["Node"]:
        return [n for n in self.walk() if not n.multipart]

    def decoded_size(self) -> int:
        if self.encoding == "base64":
            return self.size * 57 // 78            # base64 lines: 76 characters plus CRLF carry 57 bytes
        return self.size


def tree(bs: Any, section: str = "") -> Node:
    """imapclient's BODYSTRUCTURE as a Node tree. message/rfc822 parts are leaves, as the rest of the mail code treats them."""
    if getattr(bs, "is_multipart", False):
        kids = [tree(ch, f"{section}.{i}" if section else str(i)) for i, ch in enumerate(bs[0], 1)]
        return Node(section, True, "multipart/" + _s(bs[1]).lower(), children=kids)
    size = bs[6] if len(bs) > 6 and isinstance(bs[6], int) else 0
    return Node(section or "1", False, f"{_s(bs[0])}/{_s(bs[1])}".lower(), size, _s(bs[5]).lower(), filename=_filename(bs))


def _params(ps: Any, prefix: str) -> str:
    if not isinstance(ps, (list, tuple)):
        return ""
    return "".join(_s(v) for k, v in zip(ps[::2], ps[1::2]) if _s(k).lower().startswith(prefix))


def _filename(bs: Any) -> str:
    """Content-Disposition's filename, else Content-Type's name. The disposition's place after the size depends on the type
    (text and message/rfc822 carry extra fields), so it is the first (type, parameters) pair there."""
    for x in list(bs)[7:]:
        if isinstance(x, (list, tuple)) and len(x) == 2 and isinstance(x[0], bytes) and isinstance(x[1], (list, tuple)):
            if name := _params(x[1], "filename"):
                return name
            break
    return _params(bs[2] if len(bs) > 2 else None, "name")


def skeleton_items(root: Node, want_body: Callable[[Node], bool]) -> list[str]:
    """FETCH items for a skeleton: the top header, every part's MIME header, and the bodies want_body() asks for."""
    items = ["BODY.PEEK[HEADER]"]
    for n in root.walk():
        if n is root:
            continue
        items.append(f"BODY.PEEK[{n.section}.MIME]")
        if not n.multipart and want_body(n):
            items.append(f"BODY.PEEK[{n.section}]")
    return items


def _header_block(raw: bytes) -> bytes:
    head = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
    return head.rstrip(b"\r\n") + b"\r\n"


def top_header(raw: bytes) -> bytes:
    """A message's own header block, byte for byte the same whether raw is the whole message or a skeleton of it."""
    return _OURS.sub(b"", _header_block(raw))


def _get(data: dict, key: str) -> bytes | None:
    v = data.get(key.encode())
    return v if isinstance(v, bytes) else (b"" if v is None and key.encode() in data else v)


def assemble(root: Node, data: dict, want_body: Callable[[Node], bool]) -> bytes | None:
    """The skeleton message as bytes, or None when a needed piece is missing."""
    top = _get(data, "BODY[HEADER]")
    if top is None or not root.multipart:
        return None

    def build(n: Node, header: bytes) -> bytes | None:
        head = _OURS.sub(b"", _header_block(header))
        if n.multipart:
            boundary = email.message_from_bytes(head + b"\r\n", policy=policy.compat32).get_boundary()
            if not boundary:
                return None
            parts = []
            for c in n.children:
                h = _get(data, f"BODY[{c.section}.MIME]")
                p = build(c, h) if h is not None else None
                if p is None:
                    return None
                parts.append(p)
            b = boundary.encode("ascii", "replace")
            return head + b"\r\n" + b"".join(b"--" + b + b"\r\n" + p + b"\r\n" for p in parts) + b"--" + b + b"--\r\n"
        body = b""
        if want_body(n):
            got = _get(data, f"BODY[{n.section}]")
            if got is None:
                return None
            body = got
        head += f"{SECTION}: {n.section}\r\n{SIZE}: {n.decoded_size()}\r\n".encode()
        return head + b"\r\n" + body

    return build(root, top)


def _leaf_parts(part: Message) -> Iterator[Message]:
    if part.get_content_type() == "message/rfc822":
        yield part
    elif part.is_multipart():
        for child in part.get_payload():
            yield from _leaf_parts(child)
    else:
        yield part


def parse(root: Node, raw: bytes) -> EmailMessage | None:
    """Parse a skeleton and prove it matches the structure: same leaves, same content types, same sections, same order."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    got = list(_leaf_parts(msg))
    want = root.leaves()
    if len(got) != len(want):
        return None
    for p, n in zip(got, want):
        if p.get_content_type() != n.ctype or (p.get(SECTION) or "").strip() != n.section:
            return None
    msg._icloud_skeleton = True           # sizes come from SIZE headers written above, never from the sender's
    return msg


def section_of(part: Message) -> str | None:
    v = part.get(SECTION)
    return str(v).strip() if v else None


def size_of(part: Message) -> int | None:
    try:
        return int(str(part.get(SIZE)).strip())
    except (TypeError, ValueError):
        return None


def leaf_message(mime_header: bytes, body: bytes) -> Message:
    """One part on its own (its MIME header plus its raw body), for decoding a fetched attachment."""
    return email.message_from_bytes(_OURS.sub(b"", _header_block(mime_header)) + b"\r\n" + body, policy=policy.default)
