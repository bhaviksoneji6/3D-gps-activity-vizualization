"""
Full pipeline worker. Runs inside a subprocess so it has its own OS main thread,
satisfying macOS Cocoa's requirement that NSWindow only be created on the main thread.
"""
import os
import sys
import math
import time
import warnings
warnings.filterwarnings("ignore")


def run(config: dict, queue):
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if proj_root not in sys.path:
        sys.path.insert(0, proj_root)

    try:
        import numpy as np
        from scipy.interpolate import RegularGridInterpolator
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
        width       = config.get("width", 1920)
        height      = config.get("height", 1080)

        frame_aspect    = width / height
        ELEVATION_SCALE = 1.2

        # ── step 0: parse GPX ──────────────────────────────────────────────────
        queue.put(("step", 0))
        points = parse(gpx_path)
        coords, (origin_lat, origin_lon) = to_cartesian(points, elevation_scale=ELEVATION_SCALE)

        lats    = [p.lat for p in points]
        lons    = [p.lon for p in points]
        padding = 0.005

        # ── step 1: terrain ────────────────────────────────────────────────────
        queue.put(("step", 1))
        elevation, lat_grid, lon_grid = fetch_terrain(
            min(lats) - padding, max(lats) + padding,
            min(lons) - padding, max(lons) + padding,
        )

        # Build terrain sampler: (lat, lon) → elevation in metres
        lats_1d = lat_grid[:, 0]
        lons_1d = lon_grid[0, :]

        # RegularGridInterpolator requires strictly increasing axes
        if lats_1d[0] > lats_1d[-1]:
            lats_1d = lats_1d[::-1]
            elev_for_interp = elevation[::-1, :]
        else:
            elev_for_interp = elevation

        _interp = RegularGridInterpolator(
            (lats_1d, lons_1d),
            elev_for_interp,
            method="linear",
            bounds_error=False,
            fill_value=float(elevation.min()),
        )

        def terrain_sampler(lat, lon):
            lat_c = float(np.clip(lat, lats_1d.min(), lats_1d.max()))
            lon_c = float(np.clip(lon, lons_1d.min(), lons_1d.max()))
            return float(_interp([[lat_c, lon_c]])[0])

        # ── step 2: satellite ──────────────────────────────────────────────────
        queue.put(("step", 2))
        texture_img, texture_bounds = fetch_satellite_texture(
            lat_grid.min(), lat_grid.max(),
            lon_grid.min(), lon_grid.max(),
        )

        # ── step 3: camera path ────────────────────────────────────────────────
        queue.put(("step", 3))
        N_POINTS  = 900
        resampled = resample_track(coords, n_points=N_POINTS)

        if camera_mode == "cinematic":
            frames = chase_camera_frames(
                resampled,
                terrain_sampler=terrain_sampler,
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                elevation_scale=ELEVATION_SCALE,
                frame_aspect=frame_aspect,
            )
        else:
            frames = first_person_frames(resampled, frame_aspect=frame_aspect)

        # ── HUD metadata ───────────────────────────────────────────────────────
        seg_dists = [0.0]
        for i in range(1, len(coords)):
            d = math.sqrt(sum((coords[i][k] - coords[i - 1][k]) ** 2 for k in range(3)))
            seg_dists.append(d)
        cum_dist_orig = np.cumsum(seg_dists)
        total_dist_m  = float(cum_dist_orig[-1])

        def _parse_ts(ts_str):
            from datetime import datetime
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

        timestamps = [p.timestamp for p in points]
        if all(timestamps):
            ts_objs        = [_parse_ts(t) for t in timestamps]
            time_secs_orig = np.array(
                [(t - ts_objs[0]).total_seconds() for t in ts_objs], dtype=float
            )
            dt         = np.diff(time_secs_orig)
            dt         = np.where(dt > 0, dt, 0.1)
            speeds_mps = np.concatenate([[0.0], np.array(seg_dists[1:]) / dt])
            speeds_mps = np.convolve(speeds_mps, np.ones(7) / 7, mode="same")
        else:
            time_secs_orig = np.linspace(0.0, total_dist_m / 3.0, len(coords))
            speeds_mps     = np.full(len(coords), 3.0)

        elevations_m = np.array([p.elevation for p in points], dtype=float)
        ele_gain     = np.zeros(len(points))
        for i in range(1, len(points)):
            ele_gain[i] = ele_gain[i - 1] + max(0.0, points[i].elevation - points[i - 1].elevation)

        t_rs = np.linspace(0.0, total_dist_m, N_POINTS)

        # n_total frames = N_POINTS main + outro extras
        n_outro = len(frames) - N_POINTS
        # Extend HUD arrays: hold last value over the outro frames
        def _extend(arr):
            return np.concatenate([arr, np.full(n_outro, arr[-1])])

        hud_data = {
            "_n_main":           N_POINTS,
            "total_dist_km":     total_dist_m / 1000.0,
            "dist_covered_km":   _extend(t_rs / 1000.0),
            "speed_kmh":         _extend(np.interp(t_rs, cum_dist_orig, speeds_mps) * 3.6),
            "elevation_m":       _extend(np.interp(t_rs, cum_dist_orig, elevations_m)),
            "elevation_gain_m":  _extend(np.interp(t_rs, cum_dist_orig, ele_gain)),
            "elapsed_secs":      _extend(np.interp(t_rs, cum_dist_orig, time_secs_orig)),
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
                                           ghost_track, end_marker,
                                           window_size=(width, height))

        # ── step 6: render ─────────────────────────────────────────────────────
        queue.put(("step", 6))
        render_start = time.time()

        def progress_cb(current, total_frames):
            elapsed   = time.time() - render_start
            rate      = current / elapsed if elapsed > 0 else 0
            remaining = int((total_frames - current) / rate) if rate > 0 else 0
            queue.put(("progress", (current, total_frames, remaining)))

        render_video(plotter, frames, resampled, video_path,
                     hud_data=hud_data, progress_callback=progress_cb)
        queue.put(("done", video_path))

    except Exception:
        import traceback
        queue.put(("error", traceback.format_exc()))
