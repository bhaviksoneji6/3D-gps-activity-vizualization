import math
import numpy as np
from typing import List, Tuple, Optional, Callable


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
    """Moving-average smooth over camera positions and focal points (edge-padded)."""
    if window < 2 or len(frames) < window:
        return frames

    cams   = np.array([f[0] for f in frames], dtype=float)
    focals = np.array([f[1] for f in frames], dtype=float)
    ups    = [f[2] for f in frames]

    half   = window // 2
    kernel = np.ones(window) / window

    cams_pad   = np.pad(cams,   ((half, half), (0, 0)), mode="edge")
    focals_pad = np.pad(focals, ((half, half), (0, 0)), mode="edge")

    n = len(frames)
    cams_s   = np.zeros_like(cams)
    focals_s = np.zeros_like(focals)

    for ax in range(3):
        cams_s[:, ax]   = np.convolve(cams_pad[:, ax],   kernel, mode="valid")[:n]
        focals_s[:, ax] = np.convolve(focals_pad[:, ax], kernel, mode="valid")[:n]

    return [(tuple(cams_s[i]), tuple(focals_s[i]), ups[i]) for i in range(n)]


def _apply_terrain_floor(
    frames: List[Tuple[Tuple, Tuple, Tuple]],
    terrain_sampler: Callable,
    origin_lat: float,
    origin_lon: float,
    elevation_scale: float,
    clearance: float,
    look_ahead: int = 30,
    n_ridge_samples: int = 8,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Lift camera positions so the camera is always above terrain and maintains
    line-of-sight to the focal point.

    For each frame, samples terrain height along the sight line to the focal
    point and also for the next look_ahead frames (predictive lift so ridges
    are cleared smoothly before they enter the line of sight).
    """
    R = 6_371_000
    cos_lat = math.cos(math.radians(origin_lat))

    def xy_to_elev(x, y):
        lat = origin_lat + math.degrees(y / R)
        lon = origin_lon + math.degrees(x / (R * cos_lat))
        return terrain_sampler(lat, lon) * elevation_scale

    n = len(frames)

    # Pass 1: compute max terrain+clearance along each frame's sight line
    required = np.zeros(n)
    ts = np.linspace(0.0, 1.0, n_ridge_samples)

    for i, (cam_t, foc_t, _) in enumerate(frames):
        cam = np.array(cam_t)
        foc = np.array(foc_t)
        max_t = 0.0
        for t in ts:
            x = cam[0] * (1 - t) + foc[0] * t
            y = cam[1] * (1 - t) + foc[1] * t
            max_t = max(max_t, xy_to_elev(x, y))
        required[i] = max_t + clearance

    # Pass 2: look-ahead max — camera must satisfy the requirement of upcoming frames
    required_la = np.array([
        required[i:min(i + look_ahead, n)].max()
        for i in range(n)
    ])

    # Pass 3: apply floor — only lift, never lower
    result = list(frames)
    for i, (cam_t, foc_t, up) in enumerate(frames):
        cam = np.array(cam_t)
        if cam[2] < required_la[i]:
            cam[2] = required_la[i]
            result[i] = (tuple(cam), foc_t, up)

    return result


def _build_outro_frames(
    coords: List[Tuple[float, float, float]],
    last_frame: Tuple[Tuple, Tuple, Tuple],
    n_flyout: int = 120,
    n_hold: int = 60,
    fit_scale: float = 1.0,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Pan-out sequence appended after the main animation.
    Camera eases from its final chase position to a bird's-eye overview
    showing the entire route, then holds.
    """
    pts = np.array(coords, dtype=float)

    x_min, y_min = pts[:, :2].min(axis=0)
    x_max, y_max = pts[:, :2].max(axis=0)
    z_max = float(pts[:, 2].max())

    cx = (x_min + x_max) / 2.0
    cy = (y_min + y_max) / 2.0
    extent = max(x_max - x_min, y_max - y_min)

    # Height to fit full route in 45° FOV with some margin.
    # fit_scale > 1 for narrow frames (9:16, 1:1) whose horizontal FOV is smaller.
    overview_z = z_max + extent * 1.5 * fit_scale
    overview_cam   = np.array([cx, cy, overview_z])
    overview_focal = np.array([cx, cy, z_max])

    last_cam   = np.array(last_frame[0])
    last_focal = np.array(last_frame[1])

    # The overview looks straight down, where up=(0,0,1) is parallel to the view
    # direction — a degenerate camera basis that renders black. Blend to a
    # north-up vector as the camera tilts vertical.
    up_start = np.array([0.0, 0.0, 1.0])
    up_end   = np.array([0.0, 1.0, 0.0])

    frames = []
    for i in range(n_flyout):
        t = i / max(1, n_flyout - 1)
        ease = 3 * t ** 2 - 2 * t ** 3  # smoothstep
        cam   = last_cam   + (overview_cam   - last_cam)   * ease
        focal = last_focal + (overview_focal - last_focal) * ease
        up    = up_start + (up_end - up_start) * ease
        up    = up / np.linalg.norm(up)
        frames.append((tuple(cam), tuple(focal), tuple(up)))

    frames += [(tuple(overview_cam), tuple(overview_focal), (0.0, 1.0, 0.0))] * n_hold
    return frames


def _aspect_factors(frame_aspect: float) -> Tuple[float, float]:
    """
    Camera distance compensation for frames narrower than 16:9, whose
    horizontal FOV is proportionally smaller.

    chase_scale — sqrt compromise: pulls the chase camera back enough that the
    route isn't constantly cropped, without making it feel distant.
    fit_scale   — full geometric factor: the outro must fit the whole route
    horizontally, so no compromise there.
    """
    wide = 16.0 / 9.0
    ratio = wide / frame_aspect
    chase_scale = max(1.0, math.sqrt(ratio))
    fit_scale   = max(1.0, ratio)
    return chase_scale, fit_scale


def chase_camera_frames(
    coords: List[Tuple[float, float, float]],
    back_offset: Optional[float] = None,
    up_offset: Optional[float] = None,
    orbit_sweep_deg: float = 180.0,
    terrain_sampler: Optional[Callable] = None,
    origin_lat: float = 0.0,
    origin_lon: float = 0.0,
    elevation_scale: float = 1.2,
    frame_aspect: float = 16.0 / 9.0,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Elevated cinematic chase camera.

    Sweeps orbit_sweep_deg with a sine ease-in/out. Opening zoom-in over
    first 60 frames. Post-smoothed with a 41-frame moving average.

    When terrain_sampler is provided, camera is lifted adaptively to stay
    above terrain and maintain line-of-sight to the subject (ridge check
    with 30-frame look-ahead). Outro pan-out appended at the end.
    """
    pts = np.array(coords, dtype=float)
    xy_extent = float(np.max(pts[:, :2].max(axis=0) - pts[:, :2].min(axis=0)))

    chase_scale, fit_scale = _aspect_factors(frame_aspect)

    if back_offset is None:
        back_offset = float(np.clip(xy_extent * 0.40, 200.0, 1200.0)) * chase_scale
    if up_offset is None:
        up_offset = float(np.clip(xy_extent * 0.44, 300.0, 1200.0)) * chase_scale
    look_ahead = back_offset * 0.30

    smooth_w   = max(10, len(pts) // 15)
    directions = _smooth_directions(pts, smooth_w)

    n          = len(pts)
    half_sweep = math.radians(orbit_sweep_deg / 2)
    zoom_frames = min(60, n // 8)

    frames = []
    for i, pt in enumerate(pts):
        fwd = directions[i]

        t_linear = i / max(1, n - 1)
        t_eased  = 0.5 * (1.0 - math.cos(math.pi * t_linear))
        sweep    = -half_sweep + math.radians(orbit_sweep_deg) * t_eased

        cos_s, sin_s = math.cos(sweep), math.sin(sweep)
        back_x = -fwd[0] * cos_s + fwd[1] * sin_s
        back_y = -fwd[0] * sin_s - fwd[1] * cos_s
        swept_back = np.array([back_x, back_y, 0.0])

        if i < zoom_frames:
            ease_in = 3 * (i / zoom_frames) ** 2 - 2 * (i / zoom_frames) ** 3
            zoom    = 1.6 - 0.6 * ease_in
        else:
            zoom = 1.0

        cam   = pt + swept_back * back_offset * zoom + np.array([0.0, 0.0, up_offset * zoom])
        focal = pt + fwd * look_ahead
        frames.append((tuple(cam), tuple(focal), (0, 0, 1)))

    # Adaptive terrain floor + ridge check
    if terrain_sampler is not None:
        elev_range = float(pts[:, 2].max() - pts[:, 2].min()) / elevation_scale
        clearance  = max(80.0, elev_range * 0.15) * elevation_scale
        frames = _apply_terrain_floor(
            frames, terrain_sampler,
            origin_lat, origin_lon, elevation_scale,
            clearance=clearance,
        )

    frames = _smooth_camera_frames(frames, window=41)

    # Outro pan-out
    frames += _build_outro_frames(coords, frames[-1], fit_scale=fit_scale)
    return frames


def first_person_frames(
    coords: List[Tuple[float, float, float]],
    back_offset: Optional[float] = None,
    up_offset: Optional[float] = None,
    frame_aspect: float = 16.0 / 9.0,
) -> List[Tuple[Tuple, Tuple, Tuple]]:
    """
    Close chase camera for first-person mode.
    3× higher than v1.1 for a less ground-skimming perspective.
    Outro pan-out appended at the end.
    """
    pts = np.array(coords, dtype=float)
    xy_extent = float(np.max(pts[:, :2].max(axis=0) - pts[:, :2].min(axis=0)))

    chase_scale, fit_scale = _aspect_factors(frame_aspect)

    if back_offset is None:
        back_offset = float(np.clip(xy_extent * 0.06, 30.0, 180.0)) * chase_scale
    if up_offset is None:
        up_offset = float(np.clip(xy_extent * 0.12, 75.0, 360.0)) * chase_scale
    look_ahead = back_offset * 0.5

    smooth_w   = max(10, len(pts) // 15)
    directions = _smooth_directions(pts, smooth_w)

    frames = []
    for i, pt in enumerate(pts):
        fwd   = directions[i]
        cam   = pt - fwd * back_offset + np.array([0.0, 0.0, up_offset])
        focal = pt + fwd * look_ahead
        frames.append((tuple(cam), tuple(focal), (0, 0, 1)))

    frames = _smooth_camera_frames(frames, window=21)

    # Outro pan-out
    frames += _build_outro_frames(coords, frames[-1], fit_scale=fit_scale)
    return frames
