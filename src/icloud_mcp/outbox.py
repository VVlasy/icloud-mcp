"""Outbox: outgoing messages wait here until the owner releases them on /outbox.

The MCP token cannot tell the owner from an injected instruction, so sending is split in two: an agent can only
*queue* a message; releasing it needs the owner password, typed in a browser. Queued items are stored as the exact
raw message that will be sent, expire on their own, and can be released at most once.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OutboxFull(Exception):
    pass


@dataclass
class QueuedMessage:
    id: str
    created_at: float
    expires_at: float
    raw: bytes
    recipients: list[str]
    followup: dict[str, Any] | None   # e.g. {"folder": "INBOX", "uid": 7, "flag": "\\Answered"} applied after a successful send
    sha256: str
    key: str | None = None            # what the message was made from (e.g. one saved draft), so asking again finds this entry

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "created_at": self.created_at, "expires_at": self.expires_at,
                "raw": base64.b64encode(self.raw).decode(), "recipients": self.recipients,
                "followup": self.followup, "sha256": self.sha256, **({"key": self.key} if self.key else {})}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "QueuedMessage":
        raw = base64.b64decode(d["raw"])
        if hashlib.sha256(raw).hexdigest() != d["sha256"]:
            raise ValueError("outbox entry failed its integrity check")
        return cls(d["id"], d["created_at"], d["expires_at"], raw, list(d["recipients"]), d.get("followup"), d["sha256"], d.get("key"))


class Outbox:
    def __init__(self, data_dir: str, ttl: int, max_items: int, filename: str = "outbox.json"):
        self.path = Path(data_dir) / filename
        self.ttl, self.max_items = ttl, max_items
        self._lock = threading.RLock()
        self._items: dict[str, QueuedMessage] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001  -- unreadable state must never turn into a send
            return
        for d in data.get("items", []):
            try:
                q = QueuedMessage.from_json(d)
            except Exception:  # noqa: BLE001  -- skip tampered/corrupt entries
                continue
            self._items[q.id] = q
        self._prune()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".outbox-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"items": [q.to_json() for q in self._items.values()]}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _prune(self) -> None:
        now = time.time()
        self._items = {k: v for k, v in self._items.items() if v.expires_at > now}

    # -- operations ------------------------------------------------------------
    def add(self, raw: bytes, recipients: list[str], followup: dict[str, Any] | None = None, key: str | None = None) -> QueuedMessage:
        """Queue a message, or return the entry already waiting for the same bytes or the same key. A key names what the message
        was built from (one saved draft), for when rebuilding it gives different bytes (a fresh Date or Message-ID)."""
        with self._lock:
            self._prune()
            sha = hashlib.sha256(raw).hexdigest()
            same = next((q for q in self._items.values() if q.sha256 == sha or (key is not None and q.key == key)), None)
            if same is not None:                 # the same message is already waiting: one entry, not two
                return same
            if len(self._items) >= self.max_items:
                raise OutboxFull(f"{len(self._items)} messages are already waiting for owner approval (OUTBOX_MAX={self.max_items}). "
                                 "Nothing was queued. Do not retry: tell the owner to review the queue on the /outbox page, "
                                 "where they can send or discard what is waiting.")
            now = time.time()
            q = QueuedMessage(secrets.token_urlsafe(12), now, now + self.ttl, raw, recipients, followup, sha, key)
            self._items[q.id] = q
            self._save()
            return q

    def pending(self) -> list[QueuedMessage]:
        with self._lock:
            self._prune()
            return sorted(self._items.values(), key=lambda q: q.created_at)

    def claim(self, item_id: str) -> QueuedMessage | None:
        """Atomically remove and return an item, so it can be released at most once."""
        with self._lock:
            self._prune()
            q = self._items.pop(item_id, None)
            if q:
                self._save()
            return q

    def restore(self, q: QueuedMessage) -> None:
        """Put a claimed item back (used when the send itself failed, so the owner can retry)."""
        with self._lock:
            self._items[q.id] = q
            self._save()
