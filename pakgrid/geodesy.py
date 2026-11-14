"""Measurements on the WGS 84 ellipsoid.

All angles passed in and returned are in degrees unless a name says otherwise.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
import math

from . import spec

A = spec.WGS84_A
E2 = spec.WGS84_F * (2 - spec.WGS84_F)
E = math.sqrt(E2)

FEET_PER_METRE = 1 / 0.3048


def _gauss_legendre(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Nodes and weights for n-point Gauss-Legendre quadrature on [-1, 1]."""
    nodes: list[float] = []
    weights: list[float] = []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1)
            dx = p1 / dp
            x -= dx
            if abs(dx) < 1e-16:
                break
        nodes.append(x)
        weights.append(2 / ((1 - x * x) * dp * dp))
    return tuple(nodes), tuple(weights)


_GL_NODES, _GL_WEIGHTS = _gauss_legendre(16)


def _integrate(fn: Callable[[float], float], a: float, b: float) -> float:
    mid, half = (a + b) / 2, (b - a) / 2
    return half * sum(w * fn(mid + half * x) for x, w in zip(_GL_NODES, _GL_WEIGHTS))


def meridian_radius(lat_rad: float) -> float:
    """Radius of curvature along a meridian (north-south), in metres."""
    s = math.sin(lat_rad)
    return A * (1 - E2) / (1 - E2 * s * s) ** 1.5


def prime_vertical_radius(lat_rad: float) -> float:
    """Radius of curvature at right angles to the meridian, in metres."""
    s = math.sin(lat_rad)
    return A / math.sqrt(1 - E2 * s * s)


def meridian_arc(lat1: float, lat2: float) -> float:
    """Distance in metres along a meridian between two latitudes."""
    return _integrate(meridian_radius, math.radians(lat1), math.radians(lat2))


def parallel_length(lat: float, lon1: float, lon2: float) -> float:
    """Distance in metres along the parallel at `lat` between two longitudes."""
    phi = math.radians(lat)
    return prime_vertical_radius(phi) * math.cos(phi) * math.radians(lon2 - lon1)


def metres_per_degree(lat: float) -> tuple[float, float]:
    """Return (metres per degree of latitude, metres per degree of longitude) at `lat`."""
    phi = math.radians(lat)
    per_rad_lat = meridian_radius(phi)
    per_rad_lon = prime_vertical_radius(phi) * math.cos(phi)
    return per_rad_lat * math.pi / 180, per_rad_lon * math.pi / 180


def _area_term(phi: float) -> float:
    s = math.sin(phi)
    return s / (2 * (1 - E2 * s * s)) + math.log((1 + E * s) / (1 - E * s)) / (4 * E)


def rect_area(south: float, north: float, west: float, east: float) -> float:
    """Area in square metres of the latitude/longitude rectangle on the ellipsoid."""
    return (A * A * (1 - E2) * math.radians(east - west)
            * (_area_term(math.radians(north)) - _area_term(math.radians(south))))


def _area_weight(phi: float) -> float:
    s = math.sin(phi)
    return math.cos(phi) / (1 - E2 * s * s) ** 2


@functools.lru_cache(maxsize=1 << 16)
def band_centroid_lat(south: float, north: float) -> float:
    """Area-weighted mean latitude of the band between two latitudes.

    Slightly south of the plain midpoint, because the southern part of the
    band is wider.
    """
    a, b = math.radians(south), math.radians(north)
    num = _integrate(lambda p: p * _area_weight(p), a, b)
    den = _integrate(_area_weight, a, b)
    return math.degrees(num / den)


def local_offset(ref_lat: float, ref_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """Metres (east, north) from a reference point to a point.

    Uses the ellipsoid's scale at the reference latitude, so it is exactly
    inverted by `apply_offset`. Accurate to millimetres within ~1 km.
    """
    m_lat, m_lon = metres_per_degree(ref_lat)
    return (lon - ref_lon) * m_lon, (lat - ref_lat) * m_lat


def apply_offset(ref_lat: float, ref_lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """Point (lat, lon) that lies `east_m` east and `north_m` north of a reference point."""
    m_lat, m_lon = metres_per_degree(ref_lat)
    return ref_lat + north_m / m_lat, ref_lon + east_m / m_lon


def bearing(east_m: float, north_m: float) -> float:
    """Compass bearing in degrees (0-360, clockwise from north) of an east/north vector."""
    return math.degrees(math.atan2(east_m, north_m)) % 360.0
