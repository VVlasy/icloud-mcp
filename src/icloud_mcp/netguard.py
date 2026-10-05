"""Outbound web requests made on an agent's behalf (unsubscribe pages, attachment downloads) reach only the public internet.

public_address resolves a name and accepts it only when every address it resolves to is public; PinnedTransport then connects
to the address that was checked, so a name cannot resolve to a public address for the check and a private one for the visit
(DNS rebinding). The Host header, the TLS server name and the certificate check keep the real name.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable

import httpx

_NAT64 = ipaddress.ip_network("64:ff9b::/96")             # RFC 6052: the last 32 bits are an IPv4 address
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")     # RFC 8215: local-use NAT64, whatever it maps to is the site's own


def blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything but a public unicast address: private, loopback, link-local, CGNAT (100.64.0.0/10), IPv6 unique-local,
    reserved and unspecified (all outside is_global), multicast (which is_global lets through), and an IPv6 address that only
    wraps one of those (IPv4-mapped, NAT64)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip in _NAT64_LOCAL:
            return True
    return not ip.is_global or ip.is_multicast


def public_address(host: str, port: int) -> tuple[str | None, str | None]:
    """(why not, None), or (None, the address to connect to) when every address the name resolves to is public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return "the address does not resolve", None
    if not infos:
        return "the address does not resolve", None
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            return "the address does not resolve to an IP address", None
        if blocked(ip):
            return "the address points into a private or local network", None
    return None, str(infos[0][4][0])


class PinnedTransport(httpx.BaseTransport):
    """Connects to the address it has just checked, not to whatever the name resolves to a moment later (DNS rebinding). The Host
    header, the TLS server name and the certificate check keep the real name; connections are not reused across names."""

    def __init__(self, inner: httpx.BaseTransport | None = None,
                 resolve: Callable[[str, int], tuple[str | None, str | None]] | None = None) -> None:
        self._inner = inner or httpx.HTTPTransport(limits=httpx.Limits(max_keepalive_connections=0))
        self._resolve = resolve or public_address

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
