"""
brief/geo.py
Resolve a client's city from their request IP, for the daily brief's Local
Pulse segment (harness.users.location_name).

Called once per user at prefs-save — not per request — so a hosted lookup is
cheaper than shipping and monthly-refreshing a GeoLite2 database.

Everything here fails soft: no city just means the brief generates without a
Local Pulse segment (see user_brief_runner), never an error to the caller.

Proxy chain
-----------
In prod the request path is viewer -> CloudFront -> ALB -> app, so
request.client.host is the ALB and would geolocate our own infrastructure.
The viewer IP is in X-Forwarded-For, which each hop APPENDS to:

    "<viewer>, <cloudfront-edge>"        # ALB appended CloudFront's IP

so the viewer is the 2nd entry from the right. Counting from the right is also
what makes this spoof-resistant: a client that sends its own X-Forwarded-For
only prepends junk (CloudFront appends the real viewer IP after it), so the
offset from the right still lands on the real one.

BRIEF_GEO_XFF_INDEX is that 1-based offset from the right: 1 = rightmost
(single proxy), 2 = CloudFront + ALB (prod). Unset/0 means no proxy at all
(local dev) — use the socket peer.
"""

from __future__ import annotations

import ipaddress
import os

import httpx
from loguru import logger

GEO_API_URL = os.getenv("BRIEF_GEO_API_URL", "https://ipapi.co/{ip}/json/")
XFF_INDEX = int(os.getenv("BRIEF_GEO_XFF_INDEX", "0"))
TIMEOUT_S = 4

# Local dev: Docker Desktop's port-forwarding presents an arbitrary PUBLIC IP as
# the peer (observed: 172.217.113.4, a Google address -> "Mountain View"), so the
# private-range guard below doesn't catch it and the lookup returns junk. Set this
# to pin a city instead. Also usable as a prod escape hatch.
OVERRIDE_CITY = os.getenv("BRIEF_GEO_OVERRIDE_CITY", "")


def client_ip(request) -> str:
    """The viewer's IP, accounting for trusted proxy hops. "" if unknown."""
    if XFF_INDEX > 0:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= XFF_INDEX:
            return parts[-XFF_INDEX]
        # Fewer hops than configured — the request didn't come through the
        # expected chain (health check, direct ALB hit). Don't guess.
        return ""
    return getattr(request.client, "host", "") or ""


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_reserved
                or addr.is_link_local or addr.is_multicast)


async def city_from_request(request) -> str:
    """Best-effort city name (e.g. "Bengaluru"). "" when it can't be resolved —
    local dev (private IPs never geolocate), VPNs, lookup failure."""
    if OVERRIDE_CITY:
        return OVERRIDE_CITY
    ip = client_ip(request)
    if not ip or not _is_public(ip):
        logger.debug(f"[brief.geo] no geolocatable client IP (got {ip!r})")
        return ""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.get(GEO_API_URL.format(ip=ip))
            if resp.status_code != 200:
                logger.warning(f"[brief.geo] lookup returned {resp.status_code} for {ip}")
                return ""
            city = (resp.json() or {}).get("city") or ""
            logger.info(f"[brief.geo] {ip} -> {city or '(no city)'}")
            return str(city).strip()
    except Exception as e:
        logger.warning(f"[brief.geo] lookup failed for {ip}: {e}")
        return ""
