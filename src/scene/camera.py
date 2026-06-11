import math
import numpy as np
from typing import List, Tuple, Optional


def resample_track(
    coords: List[Tuple[float, float, float]],
    n_points: int = 900,
) -> List[Tuple[float, float, float]]:
    """Resample track to n_points uniformly spaced by cumulative arc length."""
    pts = np.array(coords, dtype=float)
    seg_len = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    t = np.linspace(0.0, cum[-1], n_points)
    x = np.interp(t, cum, pts[:, 0])
    y = np.interp(t, cum, pts[:, 1])
    z = np.interp(t, cum, pts[:, 2])
    return [tuple(row) for row in np.column_stack([x, y, z])]


def _smooth_directions(pts: np.ndarray, window: int) -> np.ndarray:
    """Forward direction at each point, smoothed over a window to remove GPS jitter."""
    n = len(pts)
    half = max(1, window // 2)
    dirs = np.zeros((n, 3))
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n - 1, i + half)
        d = pts[hi] - pts[lo]
        norm = np.linalg.norm(d)
        dirs[i] = d / norm if norm > 1e-6 else np.array([1.0, 0.0, 0.0])
    return dirs


def _smooth_camera_frames(
    frames: List[Tuple[Tuple, Tuple, Tuple]],
    window: int,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Moving-average smooth over camera positions and focal points.
    Edge-padded so start/end don't drift toward zero.
    """
    if window < 2 or len(frames) < window:
        return frames

    cams   = np.array([f[0] for f in frames], dtype=float)
    focals = np.array([f[1] for f in frames], dtype=float)
    ups    = [f[2] for f in frames]

    half   = window // 2
    kernel = np.ones(window) / window

    # Pad edges with replicated boundary values before convolving
    cams_pad   = np.pad(cams,   ((half, half), (0, 0)), mode="edge")
    focals_pad = np.pad(focals, ((half, half), (0, 0)), mode="edge")

    n = len(frames)
    cams_s   = np.zeros_like(cams)
    focals_s = np.zeros_like(focals)

    for ax in range(3):
        cams_s[:, ax]   = np.convolve(cams_pad[:, ax],   kernel, mode="valid")[:n]
        focals_s[:, ax] = np.convolve(focals_pad[:, ax], kernel, mode="valid")[:n]

    return [(tuple(cams_s[i]), tuple(focals_s[i]), ups[i]) for i in range(n)]


def chase_camera_frames(
    coords: List[Tuple[float, float, float]],
    back_offset: Optional[float] = None,
    up_offset: Optional[float] = None,
    orbit_sweep_deg: float = 180.0,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Elevated chase camera for cinematic mode.
    Sweeps orbit_sweep_deg around the head over the full route using a
    sine ease-in/out so the sweep accelerates through the middle and slows
    at both ends.  Camera positions are post-smoothed to eliminate turnaround
    jerk.  A brief zoom-out at the start pulls back then eases in.
    """
    pts = np.array(coords, dtype=float)
    xy_extent = float(np.max(pts[:, :2].max(axis=0) - pts[:, :2].min(axis=0)))

    if back_offset is None:
        back_offset = float(np.clip(xy_extent * 0.20, 100.0, 600.0))
    if up_offset is None:
        up_offset = float(np.clip(xy_extent * 0.22, 150.0, 600.0))
    look_ahead = back_offset * 0.30

    smooth_w   = max(10, len(pts) // 15)
    directions = _smooth_directions(pts, smooth_w)

    n          = len(pts)
    half_sweep = math.radians(orbit_sweep_deg / 2)

    # How many frames to use for the opening zoom-in
    zoom_frames = min(60, n // 8)

    frames = []
    for i, pt in enumerate(pts):
        fwd = directions[i]

        # Sine ease-in/out for the orbit sweep: slow at both ends, fastest in the middle
        t_linear = i / max(1, n - 1)
        t_eased  = 0.5 * (1.0 - math.cos(math.pi * t_linear))
        sweep    = -half_sweep + math.radians(orbit_sweep_deg) * t_eased

        cos_s, sin_s = math.cos(sweep), math.sin(sweep)
        back_x = -fwd[0] * cos_s + fwd[1] * sin_s
        back_y = -fwd[0] * sin_s - fwd[1] * cos_s
        swept_back = np.array([back_x, back_y, 0.0])

        # Opening zoom-in: camera starts pulled back 1.6× and eases to 1.0×
        if i < zoom_frames:
            ease_in = 3 * (i / zoom_frames) ** 2 - 2 * (i / zoom_frames) ** 3  # smoothstep
            zoom    = 1.6 - 0.6 * ease_in
        else:
            zoom = 1.0

        cam   = pt + swept_back * back_offset * zoom + np.array([0.0, 0.0, up_offset * zoom])
        focal = pt + fwd * look_ahead
        frames.append((tuple(cam), tuple(focal), (0, 0, 1)))

    # Smooth camera positions to eliminate jerk at direction reversals
    frames = _smooth_camera_frames(frames, window=41)
    return frames


def first_person_frames(
    coords: List[Tuple[float, float, float]],
    back_offset: Optional[float] = None,
    up_offset: Optional[float] = None,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Close chase camera for first-person mode.
    Slightly higher than v1.0 to reduce ground-skimming perspective.
    """
    pts = np.array(coords, dtype=float)
    xy_extent = float(np.max(pts[:, :2].max(axis=0) - pts[:, :2].min(axis=0)))

    if back_offset is None:
        back_offset = float(np.clip(xy_extent * 0.04, 20.0, 120.0))
    if up_offset is None:
        up_offset = float(np.clip(xy_extent * 0.04, 25.0, 120.0))
    look_ahead = back_offset * 0.5

    smooth_w   = max(10, len(pts) // 15)
    directions = _smooth_directions(pts, smooth_w)

    frames = []
    for i, pt in enumerate(pts):
        fwd   = directions[i]
        cam   = pt - fwd * back_offset + np.array([0.0, 0.0, up_offset])
        focal = pt + fwd * look_ahead
        frames.append((tuple(cam), tuple(focal), (0, 0, 1)))

    # Light smoothing to remove GPS jitter
    frames = _smooth_camera_frames(frames, window=21)
    return frames
