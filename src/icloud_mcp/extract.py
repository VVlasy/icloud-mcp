"""Exact bookings and appointments out of an email: never guessed from the wording.

Two sources, both structured, both written by the sender's systems:
- schema.org JSON-LD in the HTML part (<script type="application/ld+json">), the markup airlines, hotels, ticket shops and
  restaurants add so Gmail can show trip cards: FlightReservation, LodgingReservation, EventReservation, TrainReservation,
  BusReservation, RentalCarReservation, FoodEstablishmentReservation, and plain Event.
- calendar attachments (text/calendar, .ics): every VEVENT, with its own start, end, place and organizer.

Every value is copied from those fields as they are; nothing is inferred from the text of the message. Each item carries a
'calendar_event' block shaped like calendar_create_event's arguments, for the agent to review and book.
"""
from __future__ import annotations

import email
import hashlib
import json
import re
from datetime import date, datetime
from email import policy
from typing import Any

import icalendar

from .mailparts import top_header

_LD = re.compile(r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_KINDS = {
    "FlightReservation": "flight", "LodgingReservation": "hotel", "EventReservation": "event", "TrainReservation": "train",
    "BusReservation": "bus", "RentalCarReservation": "rental car", "FoodEstablishmentReservation": "restaurant",
    "BoatReservation": "boat", "TaxiReservation": "taxi", "Event": "event",
}
MAX_ITEMS = 20
MAX_HTML = 2_000_000       # characters of HTML scanned for booking markup; real confirmations are far smaller


def _type(node: dict[str, Any]) -> str:
    t = node.get("@type")
    return (t[0] if isinstance(t, list) and t else t or "") if isinstance(t, (str, list)) else ""


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return _text(v.get("name") or v.get("@id") or v.get("text"))
    if isinstance(v, list):
        return ", ".join(t for t in (_text(x) for x in v) if t) or None
    s = " ".join(str(v).split())
    return s[:300] or None


def _place(v: Any) -> str | None:
    """A readable address from a Place / Airport / PostalAddress node, or its name."""
    if not isinstance(v, dict):
        return _text(v)
    addr = v.get("address")
    if isinstance(addr, dict):
        parts = [addr.get(k) for k in ("streetAddress", "postalCode", "addressLocality", "addressRegion", "addressCountry")]
        line = ", ".join(_text(p) for p in parts if _text(p))
        name = _text(v.get("name"))
        return f"{name}, {line}" if name and line and name not in line else (line or name)
    return _text(v.get("name")) or _text(addr) or _text(v.get("iataCode"))


def _walk(node: Any):
    """Every dict in a JSON-LD document, including @graph members and nested lists."""
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "subReservation"):
            if key in node:
                yield from _walk(node[key])


def _event(summary: str, start: Any, end: Any, location: str | None, notes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"summary": summary, "start": _text(start)}
    if end:
        out["end"] = _text(end)
    if location:
        out["location"] = location
    notes = [n for n in notes if n]
    if notes:
        out["description"] = "\n".join(notes)
    return out


def _from_reservation(node: dict[str, Any]) -> dict[str, Any] | None:
    kind = _KINDS.get(_type(node))
    if not kind:
        return None
    number = _text(node.get("reservationNumber"))
    status = _text(node.get("reservationStatus"))
    status = status.rsplit("/", 1)[-1] if status else None
    by = _text(node.get("provider") or node.get("broker") or node.get("seller"))
    what = node.get("reservationFor") if kind != "event" or _type(node) != "Event" else node
    what = what if isinstance(what, dict) else {}
    item: dict[str, Any] = {"kind": kind, "source": "schema.org booking data", "reservation_number": number, "status": status,
                            "provider": by}
    if kind == "flight":
        airline = what.get("airline") if isinstance(what.get("airline"), dict) else {}
        code = f"{_text(airline.get('iataCode')) or ''}{_text(what.get('flightNumber')) or ''}".strip() or _text(what.get("flightNumber"))
        dep, arr = what.get("departureAirport"), what.get("arrivalAirport")
        dep_name = (_text(dep.get("iataCode")) if isinstance(dep, dict) else None) or _place(dep)   # an airport may be a plain string
        arr_name = (_text(arr.get("iataCode")) if isinstance(arr, dict) else None) or _place(arr)
        start, end = what.get("departureTime"), what.get("arrivalTime")
        item.update(flight=code, airline=_text(airline.get("name")), departure_airport=_place(dep), arrival_airport=_place(arr),
                    departure=_text(start), arrival=_text(end), seat=_text((node.get("reservedTicket") or {}).get("ticketedSeat"))
                    if isinstance(node.get("reservedTicket"), dict) else None)
        item["calendar_event"] = _event(f"Flight {code or ''} {dep_name or ''} to {arr_name or ''}".replace("  ", " ").strip(),
                                        start, end, _place(dep), [f"Booking {number}" if number else "", f"Airline: {item['airline']}"
                                                                  if item["airline"] else ""])
    elif kind == "hotel":
        start, end = node.get("checkinTime") or node.get("checkinDate"), node.get("checkoutTime") or node.get("checkoutDate")
        name = _text(what.get("name"))
        item.update(name=name, address=_place(what), check_in=_text(start), check_out=_text(end), telephone=_text(what.get("telephone")))
        item["calendar_event"] = _event(f"Stay at {name or 'hotel'}", start, end, _place(what),
                                        [f"Booking {number}" if number else ""])
    elif kind in ("train", "bus", "boat"):
        dep = what.get("departureStation") or what.get("departureBusStop") or what.get("departureBoatTerminal") or {}
        arr = what.get("arrivalStation") or what.get("arrivalBusStop") or what.get("arrivalBoatTerminal") or {}
        dep, arr = (dep if isinstance(dep, dict) else {"name": dep}), (arr if isinstance(arr, dict) else {"name": arr})
        start, end = what.get("departureTime"), what.get("arrivalTime")
        label = _text(what.get("trainNumber") or what.get("busNumber") or what.get("name"))
        item.update(number=label, departure_station=_place(dep), arrival_station=_place(arr), departure=_text(start), arrival=_text(end))
        item["calendar_event"] = _event(f"{kind.capitalize()} {_text(dep.get('name')) if isinstance(dep, dict) else ''} to "
                                        f"{_text(arr.get('name')) if isinstance(arr, dict) else ''}".replace("  ", " ").strip(),
                                        start, end, _place(dep), [f"Booking {number}" if number else "", label or ""])
    elif kind == "rental car":
        start, end = node.get("pickupTime"), node.get("dropoffTime")
        item.update(pickup=_place(node.get("pickupLocation")), dropoff=_place(node.get("dropoffLocation")), pickup_time=_text(start),
                    dropoff_time=_text(end))
        item["calendar_event"] = _event("Pick up rental car", start, None, item["pickup"], [f"Booking {number}" if number else "",
                                                                                             f"Return: {item['dropoff_time']}"])
    elif kind == "restaurant":
        start = node.get("startTime")
        name = _text(what.get("name"))
        item.update(name=name, address=_place(what), time=_text(start), party_size=_text(node.get("partySize")))
        item["calendar_event"] = _event(f"Dinner at {name}" if name else "Restaurant booking", start, node.get("endTime"), _place(what),
                                        [f"Booking {number}" if number else "", f"Party of {item['party_size']}" if item["party_size"] else ""])
    else:                                                                     # event tickets and plain events
        start, end = what.get("startDate"), what.get("endDate")
        name = _text(what.get("name"))
        item.update(name=name, venue=_place(what.get("location")), start=_text(start), end=_text(end),
                    ticket=_text((node.get("reservedTicket") or {}).get("ticketToken")) if isinstance(node.get("reservedTicket"), dict) else None)
        item["calendar_event"] = _event(name or "Event", start, end, item["venue"], [f"Booking {number}" if number else ""])
    item = {k: v for k, v in item.items() if v not in (None, "", [])}
    if not item.get("calendar_event", {}).get("start"):
        return None
    if (status or "").lower().endswith("cancelled"):                         # never hand over a cancelled booking as bookable
        item["kind"], item["cancelled_booking"] = "cancellation", kind
        item.pop("calendar_event")
    return item


def _key(item: dict[str, Any]) -> str:
    return json.dumps(item, sort_keys=True, default=str)


def from_json_ld(html: str) -> list[dict[str, Any]]:
    """Reservations from the JSON-LD blocks in an HTML part. Bounded: at most MAX_HTML characters are scanned and the walk
    stops at MAX_ITEMS, so a message built to be slow (thousands of unclosed script tags, tens of thousands of tiny
    reservations) costs a bounded amount of work."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _LD.findall((html or "")[:MAX_HTML]):
        try:
            doc = json.loads(block.strip())
        except ValueError:
            continue
        for node in _walk(doc):
            if len(items) >= MAX_ITEMS:
                return items
            got = _from_reservation(node)
            if got and (k := _key(got)) not in seen:
                seen.add(k)
                items.append(got)
    return items


def _ics_value(v: Any) -> str | None:
    if v is None:
        return None
    try:
        dt = getattr(v, "dt", v)
    except Exception:  # noqa: BLE001 - a malformed date is kept by icalendar as a placeholder that raises on access
        return None
    if isinstance(dt, (datetime, date)):
        return dt.isoformat()
    return _text(str(v))


def from_ics(data: bytes) -> list[dict[str, Any]]:
    try:
        cal = icalendar.Calendar.from_ical(data)
    except Exception:  # noqa: BLE001 - a broken attachment is simply skipped
        return []
    method = _text(cal.get("method"))
    items = []
    for ev in cal.walk("VEVENT"):
        try:
            items.append(_ics_item(ev, method))
        except Exception:  # noqa: BLE001 - one broken event must not hide the others
            continue
    return [i for i in items if i]


def _ics_item(ev: Any, method: str | None) -> dict[str, Any] | None:
    if True:
        start, end = _ics_value(ev.get("dtstart")), _ics_value(ev.get("dtend"))
        if not start:
            return None
        summary, location = _text(str(ev.get("summary") or "")) or "Appointment", _text(str(ev.get("location") or ""))
        organizer = str(ev.get("organizer") or "").removeprefix("mailto:").removeprefix("MAILTO:") or None
        cancelled = method == "CANCEL" or str(ev.get("status") or "").upper() == "CANCELLED"
        item = {"kind": "cancellation" if cancelled else "invitation" if method == "REQUEST" else "calendar entry",
                "source": "calendar attachment (.ics)",
                "method": method, "uid": _text(str(ev.get("uid") or "")), "organizer": organizer, "summary": summary,
                "start": start, "end": end, "location": location,
                **({} if cancelled else {"calendar_event": _event(summary, start, end, location,
                                                                  [f"Organizer: {organizer}" if organizer else ""])})}
        return {k: v for k, v in item.items() if v not in (None, "")}


def extract(raw: bytes) -> dict[str, Any]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    items: list[dict[str, Any]] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        name = (part.get_filename() or "").lower()
        try:
            if ctype == "text/html":
                items += from_json_ld(part.get_content())
            elif ctype in ("text/calendar", "application/ics") or name.endswith(".ics"):
                payload = part.get_payload(decode=True) or b""
                items += from_ics(payload)
        except Exception:  # noqa: BLE001 - one unreadable part must not hide the others
            continue
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        if (k := _key(it)) not in seen:
            seen.add(k)
            unique.append(it)
    # A ready retry key per booking, so search -> extract -> create can never book the same thing twice. The Message-ID is
    # the sender's text, so only a hash of it goes into the key. Without one, the header block: raw may be a skeleton (mailparts)
    # or the whole message depending on what this process remembers, and the key must not change with that.
    source = str(msg.get("Message-ID") or "").strip() or hashlib.sha256(top_header(raw)).hexdigest()
    tag = hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()[:16]
    for n, it in enumerate(unique[:MAX_ITEMS]):
        if isinstance(it.get("calendar_event"), dict):
            it["calendar_event"]["request_id"] = f"booking:{tag}:{n}"
    return {"subject": _text(str(msg.get("Subject") or "")), "from": _text(str(msg.get("From") or "")), "items": unique[:MAX_ITEMS],
            "found": len(unique),
            "note": ("Copied from the booking data and calendar attachments the sender embedded, never guessed from the text. Check "
                     "the calendar before booking, and put people in attendees, not in the title. Items of kind 'cancellation' "
                     "carry no calendar_event: find the existing event and cancel or remove it instead." if unique else
                     "This message carries no structured booking data or calendar attachment. Read it with mail_get_message; "
                     "only book what it states plainly, and confirm the details with the user.")}
