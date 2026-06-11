"""
Full pipeline worker. Runs inside a subprocess so it has its own OS main thread,
satisfying macOS Cocoa's requirement that NSWindow only be created on the main thread.
"""
import os
import sys
import math
import time
import warnings
warnings.filterwarnings("ignore")  # suppress urllib3/LibreSSL noise in subprocess


def run(config: dict, queue):
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if proj_root not in sys.path:
        sys.path.insert(0, proj_root)

    try:
        import numpy as np
        from src.gpx.parser import parse, to_cartesian
        from src.terrain.fetcher import fetch_terrain, fetch_satellite_texture
        from src.terrain.mesh import build_terrain_mesh
        from src.scene.builder import (build_ghost_track, build_end_marker,
                                        assemble_scene_video)
        from src.scene.camera import resample_track, chase_camera_frames, first_person_frames
        from src.renderer.renderer import render_video

        gpx_path    = config["gpx_path"]
        camera_mode = config["camera_mode"]
        video_path  = config["video_path"]

        ELEVATION_SCALE = 1.2  # must match build_terrain_mesh default

        # ── step 0: parse GPX ──────────────────────────────────────────────────
        queue.put(("step", 0))
        points = parse(gpx_path)
        coords, (origin_lat, origin_lon) = to_cartesian(points, elevation_scale=ELEVATION_SCALE)

        lats = [p.lat for p in points]
        lons = [p.lon for p in points]
        padding = 0.005

        # ── step 1: terrain ────────────────────────────────────────────────────
        queue.put(("step", 1))
        elevation, lat_grid, lon_grid = fetch_terrain(
            min(lats) - padding, max(lats) + padding,
            min(lons) - padding, max(lons) + padding,
        )

        # ── step 2: satellite ──────────────────────────────────────────────────
        queue.put(("step", 2))
        texture_img, texture_bounds = fetch_satellite_texture(
            lat_grid.min(), lat_grid.max(),
            lon_grid.min(), lon_grid.max(),
        )

        # ── step 3: camera path ────────────────────────────────────────────────
        queue.put(("step", 3))
        N_POINTS = 900
        resampled = resample_track(coords, n_points=N_POINTS)
        if camera_mode == "cinematic":
            frames = chase_camera_frames(resampled)
        else:
            frames = first_person_frames(resampled)

        # ── HUD metadata (interpolated to resampled points) ───────────────────
        # Cumulative distance (metres) along the original GPS track
        seg_dists = [0.0]
        for i in range(1, len(coords)):
            d = math.sqrt(sum((coords[i][k] - coords[i - 1][k]) ** 2 for k in range(3)))
            seg_dists.append(d)
        cum_dist_orig = np.cumsum(seg_dists)
        total_dist_m  = float(cum_dist_orig[-1])

        # Speed (m/s) at each original point — derived from timestamps when available
        def _parse_ts(ts_str):
            from datetime import datetime, timezone
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

        timestamps = [p.timestamp for p in points]
        if all(timestamps):
            ts_objs  = [_parse_ts(t) for t in timestamps]
            time_secs_orig = np.array(
                [(t - ts_objs[0]).total_seconds() for t in ts_objs], dtype=float
            )
            dt = np.diff(time_secs_orig)
            dt = np.where(dt > 0, dt, 0.1)
            speeds_mps = np.concatenate([[0.0], np.array(seg_dists[1:]) / dt])
            # Smooth speed over a 7-point window to reduce GPS noise
            k = np.ones(7) / 7
            speeds_mps = np.convolve(speeds_mps, k, mode="same")
        else:
            time_secs_orig = np.linspace(0, total_dist_m / 3.0, len(coords))
            speeds_mps     = np.full(len(coords), 3.0)

        # Elevation (metres, unscaled) and cumulative elevation gain
        elevations_m = np.array([p.elevation for p in points], dtype=float)
        ele_gain     = np.zeros(len(points))
        for i in range(1, len(points)):
            diff      = points[i].elevation - points[i - 1].elevation
            ele_gain[i] = ele_gain[i - 1] + max(0.0, diff)

        # Interpolate everything onto the N_POINTS resampled arc-length grid
        t_rs = np.linspace(0.0, total_dist_m, N_POINTS)

        hud_data = {
            "total_dist_km":     total_dist_m / 1000.0,
            "dist_covered_km":   t_rs / 1000.0,
            "speed_kmh":         np.interp(t_rs, cum_dist_orig, speeds_mps) * 3.6,
            "elevation_m":       np.interp(t_rs, cum_dist_orig, elevations_m),
            "elevation_gain_m":  np.interp(t_rs, cum_dist_orig, ele_gain),
            "elapsed_secs":      np.interp(t_rs, cum_dist_orig, time_secs_orig),
        }

        # ── step 4: terrain mesh ───────────────────────────────────────────────
        queue.put(("step", 4))
        terrain_mesh, satellite_texture = build_terrain_mesh(
            elevation, lat_grid, lon_grid, origin_lat, origin_lon,
            texture_image=texture_img,
            texture_bounds=texture_bounds,
        )

        # ── step 5: assemble scene ─────────────────────────────────────────────
        queue.put(("step", 5))
        ghost_track = build_ghost_track(resampled)
        end_marker  = build_end_marker(resampled)
        plotter     = assemble_scene_video(terrain_mesh, satellite_texture,
                                           ghost_track, end_marker)

        # ── step 6: render ─────────────────────────────────────────────────────
        queue.put(("step", 6))
        render_start = time.time()

        def progress_cb(current, total_frames):
            elapsed  = time.time() - render_start
            rate     = current / elapsed if elapsed > 0 else 0
            remaining = int((total_frames - current) / rate) if rate > 0 else 0
            queue.put(("progress", (current, total_frames, remaining)))

        render_video(plotter, frames, resampled, video_path,
                     hud_data=hud_data, progress_callback=progress_cb)
        queue.put(("done", video_path))

    except Exception:
        import traceback
        queue.put(("error", traceback.format_exc()))
