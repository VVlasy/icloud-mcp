"""A small journal of the email addresses and phone numbers an agent added to contact cards.

Why: an injected agent can put a lookalike address on a real contact so that a later, genuine "reply to Anna" goes to the
attacker, and the approval page would show an address the owner is unlikely to scrutinise. The journal lets contact results
and the approval page say "an agent added this address" for 90 days. It holds addresses only, never names or notes, in the
data directory (mode 600).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_FILE = "agent-contact-changes.json"
_KEEP_DAYS = 90
_lock = threading.Lock()


def _path(data_dir: str) -> Path:
    return Path(data_dir) / _FILE


def _load(data_dir: str) -> dict[str, dict[str, float]]:
    try:
        data = json.loads(_path(data_dir).read_text())
    except (OSError, ValueError):
        return {}
    cutoff = time.time() - _KEEP_DAYS * 86400
    return {uid: {a: t for a, t in items.items() if t >= cutoff} for uid, items in data.items() if isinstance(items, dict)}


def record(data_dir: str, uid: str, addresses: list[str]) -> None:
    """Remember that an agent added these addresses (emails or phone numbers) to the contact `uid`."""
    if not addresses:
        return
    with _lock:
        data = _load(data_dir)
        now = time.time()
        data.setdefault(uid, {}).update({a.strip().lower(): now for a in addresses if a and a.strip()})
        data = {u: items for u, items in data.items() if items}
        p = _path(data_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, p)


def agent_added(data_dir: str, uid: str) -> list[str]:
    """Addresses an agent added to this contact in the last 90 days, oldest first."""
    with _lock:
        items = _load(data_dir).get(uid, {})
    return [a for a, _ in sorted(items.items(), key=lambda kv: kv[1])]


def agent_added_addresses(data_dir: str) -> set[str]:
    """Every address an agent added to any contact in the last 90 days."""
    with _lock:
        data = _load(data_dir)
    return {a for items in data.values() for a in items}


def agent_added_map(data_dir: str) -> dict[str, list[str]]:
    """agent_added for every contact at once ({uid: addresses, oldest first}): one read of the journal for a whole result page."""
    with _lock:
        data = _load(data_dir)
    return {uid: [a for a, _ in sorted(items.items(), key=lambda kv: kv[1])] for uid, items in data.items() if items}
