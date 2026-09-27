"""What the tool call running on this thread is doing, and the one retry policy.

stage(): services name the step in progress ("IMAP SEARCH in Archive"), so a call that times out can say which step was slow.
The tool wrapper hands each call a fresh holder; the event loop reads it when the timeout fires.

retry_once_if_safe(): retry a call ONCE, and only when all of these hold:
  * the failure is the connection, not the request (a dead socket, a dropped TLS session, a timeout);
  * the connection was a reused one from a pool (a brand-new connection that fails is a real failure);
  * nothing was written yet on this call (after a STORE, APPEND, EXPUNGE, PUT, DELETE or SMTP send the write may have landed,
    and repeating it could duplicate or double-delete).
The retry always uses a brand-new connection. Services record "reused" and "mutated" on the per-call state below.
"""
from __future__ import annotations

import functools
import threading
from typing import Any, Callable

_call = threading.local()


def begin(holder: dict[str, Any]) -> None:
    """Start a tool call on this thread (called by the tool wrapper in the worker thread)."""
    _call.holder = holder


def current() -> dict[str, Any] | None:
    """The holder of the call running on this thread, to hand to helper threads it starts (begin() there)."""
    return getattr(_call, "holder", None)


def stage(text: str) -> None:
    holder = getattr(_call, "holder", None)
    if holder is not None:
        holder["stage"] = text


class CallState(threading.local):
    """Per service, per thread: the connection state of the call in progress."""

    def __init__(self) -> None:
        self.reused = False       # the connection came from a pool
        self.mutated = False      # a write went out on this call
        self.fresh = False        # the next connection must be a brand-new one (set before a retry)
        self.depth = 0            # nesting of decorated methods within one call


def retry_once_if_safe(is_transport: Callable[[BaseException], bool], state_attr: str = "_tl") -> Callable:
    def decorate(method: Callable) -> Callable:
        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            st = getattr(self, state_attr)
            outer = st.depth == 0
            if outer:
                st.mutated = st.reused = False
            st.depth += 1
            try:
                return method(self, *args, **kwargs)
            except Exception as e:  # noqa: BLE001 - re-raised unless it is a retryable dead reused connection
                if not (outer and st.reused and not st.mutated and is_transport(e)):
                    raise
                st.fresh, st.mutated, st.reused = True, False, False
                stage("retrying on a new connection")
                return method(self, *args, **kwargs)
            finally:
                st.depth -= 1
        return wrapper
    return decorate
