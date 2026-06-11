import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
import math

NS = "{http://www.topografix.com/GPX/1/1}"


@dataclass
class Trackpoint:
    lat: float
    lon: float
    elevation: float
    timestamp: Optional[str]


def parse(gpx_path: str) -> List[Trackpoint]:
    tree = ET.parse(gpx_path)
    root = tree.getroot()

    points = []
    for trkpt in root.iter(f"{NS}trkpt"):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])

        ele_el = trkpt.find(f"{NS}ele")
        ele = float(ele_el.text) if ele_el is not None else None

        time_el = trkpt.find(f"{NS}time")
        timestamp = time_el.text if time_el is not None else None

        if ele is not None:
            points.append(Trackpoint(lat, lon, ele, timestamp))

    points = _drop_duplicates(points)
    points = _smooth_elevation(points, window=5)
    return points


def _drop_duplicates(points: List[Trackpoint]) -> List[Trackpoint]:
    out = [points[0]]
    for p in points[1:]:
        if p.lat != out[-1].lat or p.lon != out[-1].lon:
            out.append(p)
    return out


def _smooth_elevation(points: List[Trackpoint], window: int) -> List[Trackpoint]:
    elevations = [p.elevation for p in points]
    smoothed = []
    half = window // 2
    for i in range(len(elevations)):
        lo = max(0, i - half)
        hi = min(len(elevations), i + half + 1)
        smoothed.append(sum(elevations[lo:hi]) / (hi - lo))
    for p, e in zip(points, smoothed):
        p.elevation = e
    return points


def to_cartesian(points: List[Trackpoint], elevation_scale: float = 2.0):
    """Convert trackpoints to local (x, y, z) in meters. Returns (coords, origin)."""
    origin_lat = sum(p.lat for p in points) / len(points)
    origin_lon = sum(p.lon for p in points) / len(points)

    R = 6_371_000  # Earth radius in meters
    coords = []
    for p in points:
        x = R * math.radians(p.lon - origin_lon) * math.cos(math.radians(origin_lat))
        y = R * math.radians(p.lat - origin_lat)
        z = p.elevation * elevation_scale
        coords.append((x, y, z))

    return coords, (origin_lat, origin_lon)
