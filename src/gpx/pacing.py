"""
Activity-adaptive video pacing.

Video length scales with the activity (longer activities get proportionally
less screen time per mile), and frames are spaced by *real elapsed time* so
slow climbs genuinely look slow and fast sections look fast. Long stops are
capped so the video never sits frozen on a red light or a water break.
"""
import math
from datetime import datetime
from typing import List, Optional

import numpy as np

FPS           = 30
OUTRO_SECONDS = 6          # fixed pan-out tail appended after the route animation
MIN_VIDEO_S   = 30.0       # floor on the main animation length
MAX_VIDEO_S   = 150.0      # ceiling on the main animation length

# Real seconds any single contiguous stop may contribute before it is compressed.
STOP_CAP_S    = 4.0
STOP_SPEED_MS = 0.6        # below this instantaneous speed a point counts as "stopped"

# Video seconds per mile, chosen by real activity duration (minutes).
# Shorter activities get a higher rate; longer ones are compressed more.
_BUCKETS = [
    (30.0,           14.0),
    (60.0,           11.0),
    (120.0,           8.0),
    (240.0,           6.0),
    (480.0,           4.0),
    (float("inf"),    3.0),
]
_FALLBACK_RATE = 9.0       # used when the GPX has no timestamps (duration unknown)

_MILE_M = 1609.344


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlam   = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def activity_distance_miles(points) -> float:
    d = 0.0
    for a, b in zip(points, points[1:]):
        d += _haversine_m(a.lat, a.lon, b.lat, b.lon)
    return d / _MILE_M


def activity_duration_min(points) -> Optional[float]:
    """Real elapsed activity duration in minutes, or None if timestamps are missing."""
    ts = [p.timestamp for p in points]
    if not all(ts):
        return None
    return (_parse_ts(ts[-1]) - _parse_ts(ts[0])).total_seconds() / 60.0


def _rate_for_duration(duration_min: Optional[float]) -> float:
    if duration_min is None:
        return _FALLBACK_RATE
    for cap, rate in _BUCKETS:
        if duration_min <= cap:
            return rate
    return _BUCKETS[-1][1]


def video_main_seconds(distance_mi: float, duration_min: Optional[float]) -> float:
    """Length of the route animation (excluding the outro), clamped to a sane range."""
    seconds = _rate_for_duration(duration_min) * distance_mi
    return float(min(MAX_VIDEO_S, max(MIN_VIDEO_S, seconds)))


def elapsed_seconds(points) -> Optional[np.ndarray]:
    """Per-point elapsed seconds from the first timestamp, or None if unavailable."""
    ts = [p.timestamp for p in points]
    if not all(ts):
        return None
    t0 = _parse_ts(ts[0])
    return np.array([(_parse_ts(t) - t0).total_seconds() for t in ts], dtype=float)


def sample_fractions(
    coords,
    times: Optional[np.ndarray],
    n_samples: int,
    stop_cap_s: float = STOP_CAP_S,
    stop_speed: float = STOP_SPEED_MS,
) -> np.ndarray:
    """
    Fractional original-point indices for n_samples frames.

    With timestamps, samples are uniform in elapsed time (contiguous stops
    capped at stop_cap_s each). Without timestamps, falls back to uniform
    spacing by distance travelled — the pre-v1.3 behaviour.
    """
    pts = np.asarray(coords, dtype=float)
    n   = len(pts)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)   # length n-1

    if times is None:
        weight = seg
    else:
        t  = np.asarray(times, dtype=float)
        dt = np.diff(t)
        dt = np.where(dt > 0, dt, 1e-3)
        speed   = seg / dt
        display = dt.copy()

        # Compress each contiguous stopped run to at most stop_cap_s of display time.
        stopped = speed < stop_speed
        i = 0
        while i < len(stopped):
            if stopped[i]:
                j = i
                while j < len(stopped) and stopped[j]:
                    j += 1
                real = dt[i:j].sum()
                if real > stop_cap_s:
                    display[i:j] *= stop_cap_s / real
                i = j
            else:
                i += 1
        weight = display

    cum = np.concatenate([[0.0], np.cumsum(weight)])
    if cum[-1] <= 0:
        return np.linspace(0, n - 1, n_samples)

    samples = np.linspace(0.0, cum[-1], n_samples)
    return np.interp(samples, cum, np.arange(n))
