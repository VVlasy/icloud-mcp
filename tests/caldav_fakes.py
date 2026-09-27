"""Discovery as the CalDAV service reads it, for test fakes: the calendar home from the principal, then one Depth:1 PROPFIND that
lists each calendar's type, name and component set."""
import re
from urllib.parse import unquote, urlsplit

from caldav.response import PropfindResult
from niquests.structures import CaseInsensitiveDict

CALDAV = "{urn:ietf:params:xml:ns:caldav}"


class Reply:
    def __init__(self, results, status=207):
        self.results, self.status = results, status


def home_set(principal_url, home_url):
    return Reply([PropfindResult(href=principal_url, properties={CALDAV + "calendar-home-set": home_url})])


def calendar_list(home_url, cals, components=lambda cal: ["VEVENT"]):
    """The home itself (a plain collection, never listed as a calendar) and one calendar entry per object in `cals`."""
    return Reply([PropfindResult(href=home_url, properties={"{DAV:}resourcetype": ["{DAV:}collection"]})] + [
        PropfindResult(href=str(c.url), properties={"{DAV:}resourcetype": ["{DAV:}collection", CALDAV + "calendar"],
                                                    "{DAV:}displayname": c.name,
                                                    CALDAV + "supported-calendar-component-set": components(c)})
        for c in cals])


class ListingClient:
    """The client of a fake principal: answers discovery for `cals` and hands the same objects back from calendar()."""
    def __init__(self, cals, home="https://caldav.example/1/calendars/", components=lambda cal: ["VEVENT"]):
        self.cals, self.home, self.components, self.requests = cals, home, components, []

    def propfind(self, url, props=None, depth=0):
        self.requests.append((url, depth))
        return calendar_list(self.home, self.cals, self.components) if depth == 1 else home_set(url, self.home)

    def calendar(self, url, name=None):
        return next(c for c in self.cals if unquote(str(c.url)) == unquote(url))


def principal_of(cals, **kw):
    """A principal whose calendar list is `cals` (the same objects, so tests can look at what was saved on them)."""
    return type("P", (), {"url": "https://caldav.example/1/principal/", "client": ListingClient(cals, **kw)})()


# ------------------------------------------------------------------ an in-memory CalDAV server behind a real caldav.DAVClient
class HTTPReply:
    def __init__(self, status, body=b"", headers=None, reason="OK"):
        self.status_code, self.content, self.reason = status, body, reason
        self.headers = CaseInsensitiveDict({"Content-Type": "text/xml; charset=utf-8", **(headers or {})})
        self.text = body.decode()


def _multistatus(*responses):
    return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' + "".join(responses)
            + "</d:multistatus>").encode()


def _response(href, props):
    return (f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
            "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")


class Server:
    """iCloud-shaped: the principal on caldav.icloud.com, the calendar home on a pNN host, one calendar that holds only
    reminders. Serves every request (install it as a DAVClient session's `request`) and logs (method, host, path)."""
    def __init__(self, password="pw", home_host="p42-caldav.icloud.com"):
        self.password, self.home_host, self.log, self.etags = password, home_host, [], 0
        self.cals = {"home": ("Calendar", "VEVENT"), "work": ("Work", "VEVENT"), "tasks": ("Reminders", "VTODO")}
        self.events: dict[str, dict[str, tuple[str, str]]] = {k: {} for k in self.cals}
        self.missing: set[tuple[str, str]] = set()                  # (host, path) that answer 404: a moved calendar home

    def requests(self, method=None):
        return [x for x in self.log if method is None or x[0] == method]

    def __call__(self, method, url, data=None, headers=None, auth=None, **kw):
        u = urlsplit(url)
        path, headers = unquote(u.path), headers or {}
        self.log.append((method, u.hostname, path))
        if getattr(auth, "password", None) not in (self.password, self.password.encode()):
            return HTTPReply(401, headers={"WWW-Authenticate": 'Basic realm="x"'}, reason="Unauthorized")
        body = data.decode() if isinstance(data, bytes) else (data or "")
        obj = re.fullmatch(r"/123/calendars/(\w+)/([^/]+)", path)
        if (u.hostname, path) in self.missing:
            return HTTPReply(404, reason="Not Found")
        if method == "PROPFIND":
            return self._propfind(path, headers.get("Depth", "0"))
        if method == "REPORT":
            cal = re.fullmatch(r"/123/calendars/(\w+)/", path).group(1)
            return HTTPReply(207, _multistatus(*(_response(f"/123/calendars/{cal}/{n}", f"<d:getetag>{e}</d:getetag>"
                                                           f"<c:calendar-data>{d}</c:calendar-data>")
                                                 for n, (d, e) in self.events[cal].items())))
        if obj and method == "GET":
            hit = self.events[obj.group(1)].get(obj.group(2))
            if hit is None:
                return HTTPReply(404, reason="Not Found")
            return HTTPReply(200, hit[0].encode(), {"Content-Type": "text/calendar", "ETag": hit[1]})
        if obj and method == "PUT":
            self.etags += 1
            self.events[obj.group(1)][obj.group(2)] = (body, f'"e{self.etags}"')
            return HTTPReply(201, headers={"ETag": f'"e{self.etags}"'}, reason="Created")
        if obj and method == "DELETE":
            self.events[obj.group(1)].pop(obj.group(2), None)
            return HTTPReply(204, reason="No Content")
        return HTTPReply(405, reason="Method Not Allowed")

    def _propfind(self, path, depth):
        if path in ("", "/"):
            return HTTPReply(207, _multistatus(_response("/", "<d:current-user-principal><d:href>/123/principal/</d:href>"
                                                              "</d:current-user-principal>")))
        if path == "/123/principal/":
            return HTTPReply(207, _multistatus(_response(path, f"<c:calendar-home-set><d:href>https://{self.home_host}:443/123/calendars/"
                                                               "</d:href></c:calendar-home-set>")))
        if path == "/123/calendars/":
            listed = [_response(f"/123/calendars/{k}/", self._calendar_props(k)) for k in self.cals] if depth == "1" else []
            return HTTPReply(207, _multistatus(_response(path, "<d:resourcetype><d:collection/></d:resourcetype>"), *listed))
        cal = re.fullmatch(r"/123/calendars/(\w+)/", path)
        if cal and cal.group(1) in self.cals:
            return HTTPReply(207, _multistatus(_response(path, self._calendar_props(cal.group(1)))))
        return HTTPReply(404, reason="Not Found")

    def _calendar_props(self, key):
        name, comp = self.cals[key]
        return ("<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                f'<d:displayname>{name}</d:displayname><c:supported-calendar-component-set><c:comp name="{comp}"/>'
                "</c:supported-calendar-component-set>")
