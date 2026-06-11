import os
import numpy as np
import pyvista as pv
import imageio
from typing import List, Tuple, Callable, Optional, Dict
from PIL import Image, ImageDraw, ImageFont


# ── font helpers ──────────────────────────────────────────────────────────────

def _load_font(size: int, italic: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1 if italic else 0),
        ("/System/Library/Fonts/Helvetica.ttc",     1 if italic else 0),
        ("/Library/Fonts/Arial.ttf",                0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
    ]
    for path, idx in candidates:
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            pass
    return ImageFont.load_default()


# ── unit conversions ──────────────────────────────────────────────────────────

_KM_TO_MI   = 0.621371
_M_TO_FT    = 3.28084
_MPS_TO_MPH = 2.23694


def _pace_str(speed_kmh: float) -> str:
    """Format speed as MM:SS /mi pace. Returns '--:--' if speed is near zero."""
    if speed_kmh < 0.5:
        return "--:--"
    speed_mph = speed_kmh * _KM_TO_MI
    pace_sec  = 3600.0 / speed_mph
    m, s      = divmod(int(pace_sec), 60)
    return f"{m}:{s:02d}"


def _fmt_elapsed(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# ── HUD drawing ───────────────────────────────────────────────────────────────

def _draw_hud(frame_np: np.ndarray, idx: int, hud: Dict) -> np.ndarray:
    """Composite the full stats panel onto the bottom-left of the frame."""
    img = Image.fromarray(frame_np)
    W, H = img.size
    sc   = W / 1920.0  # scale factor relative to 1080p

    def px(n): return int(n * sc)

    pad    = px(20)
    pw     = px(480)
    radius = px(10)

    # ── gather values ─────────────────────────────────────────────────────────
    n_main = hud["_n_main"]                 # number of main animation frames
    data_i = min(idx, n_main - 1)          # clamp to main frames for HUD data

    elapsed   = hud["elapsed_secs"][data_i]
    dist_mi   = hud["dist_covered_km"][data_i] * _KM_TO_MI
    total_mi  = hud["total_dist_km"] * _KM_TO_MI
    pct       = dist_mi / total_mi if total_mi > 0 else 1.0
    speed_mph = hud["speed_kmh"][data_i] * _KM_TO_MI
    elev_ft   = hud["elevation_m"][data_i] * _M_TO_FT
    gain_ft   = hud["elevation_gain_m"][data_i] * _M_TO_FT

    # ── panel height: header + elapsed + progress + 2 rows ───────────────────
    f_label  = _load_font(px(16))
    f_value  = _load_font(px(26))
    f_header = _load_font(px(15))
    f_big    = _load_font(px(38))

    row_h   = px(52)
    prog_h  = px(54)
    head_h  = px(36)
    elapsed_h = px(72)
    ph = head_h + elapsed_h + prog_h + row_h * 2 + px(16)
    py = H - ph - pad
    px0, px1 = pad, pad + pw

    # ── background panel ─────────────────────────────────────────────────────
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od      = ImageDraw.Draw(overlay)

    # Header strip (slightly darker)
    od.rounded_rectangle([px0, py, px1, py + head_h],
                         radius=radius, fill=(8, 12, 28, 210))
    # Body
    od.rounded_rectangle([px0, py + head_h, px1, py + ph],
                         radius=radius, fill=(12, 18, 42, 185))

    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")
    d   = ImageDraw.Draw(img)

    CYAN  = (0, 210, 255)
    WHITE = (255, 255, 255)
    DIM   = (140, 170, 200)
    MID   = (200, 220, 240)
    SEP   = (40, 60, 90)

    # ── header: badge + title ─────────────────────────────────────────────────
    hx = px0 + px(14)
    hy = py + px(9)
    # Small ◈ badge drawn as a rotated square
    bs = px(8)
    badge_pts = [(hx + bs, hy), (hx + bs * 2, hy + bs),
                 (hx + bs, hy + bs * 2), (hx, hy + bs)]
    d.polygon(badge_pts, fill=CYAN)
    d.text((hx + px(22), hy + px(1)), "GPS 3D VIZ", font=f_header, fill=WHITE)
    d.text((px1 - px(52), hy + px(1)), "v 1.2", font=f_header, fill=DIM)

    # ── elapsed time ──────────────────────────────────────────────────────────
    ey = py + head_h + px(6)
    d.text((hx, ey), _fmt_elapsed(elapsed), font=f_big, fill=WHITE)
    d.text((hx, ey + px(42)), "ELAPSED", font=f_label, fill=DIM)

    # ── route progress bar ────────────────────────────────────────────────────
    bar_y   = py + head_h + elapsed_h + px(8)
    bar_x0  = px0 + px(18)
    bar_x1  = px1 - px(18)
    bar_w   = bar_x1 - bar_x0
    bar_mid = bar_y + px(10)
    fill_x  = bar_x0 + int(bar_w * min(pct, 1.0))

    # Filled segment
    d.line([(bar_x0, bar_mid), (fill_x, bar_mid)], fill=CYAN, width=px(4))
    # Dotted remaining segment
    dot_gap = px(10)
    for dx in range(fill_x + dot_gap, bar_x1, dot_gap):
        d.ellipse([dx - px(2), bar_mid - px(2), dx + px(2), bar_mid + px(2)],
                  fill=(60, 80, 110))
    # Start dot, current dot, end dot
    r = px(5)
    d.ellipse([bar_x0 - r, bar_mid - r, bar_x0 + r, bar_mid + r], fill=CYAN)
    d.ellipse([fill_x - r, bar_mid - r, fill_x + r, bar_mid + r], fill=WHITE)
    d.ellipse([bar_x1 - r, bar_mid - r, bar_x1 + r, bar_mid + r], fill=(80, 100, 130))

    # Progress text
    prog_label = f"{dist_mi:.2f} mi  ·  {int(pct * 100)}% complete  ·  {total_mi:.2f} mi total"
    d.text((bar_x0, bar_y + px(20)), prog_label, font=f_label, fill=DIM)

    # ── two-column separator ──────────────────────────────────────────────────
    col_y0  = py + head_h + elapsed_h + prog_h
    col_x   = px0 + pw // 2
    col_y1  = py + ph - px(4)
    d.line([(px0, col_y0), (px1, col_y0)], fill=SEP, width=1)
    d.line([(col_x, col_y0), (col_x, col_y1)], fill=SEP, width=1)
    d.line([(px0, col_y0 + row_h), (px1, col_y0 + row_h)], fill=SEP, width=1)

    # ── speed / pace row ─────────────────────────────────────────────────────
    lx  = px0 + px(16)
    rx  = col_x + px(16)
    vy0 = col_y0 + px(6)
    ly0 = vy0 + px(28)

    d.text((lx, vy0), f"{speed_mph:.1f} mph", font=f_value, fill=WHITE)
    d.text((lx, ly0), "SPEED",                font=f_label, fill=DIM)

    d.text((rx, vy0), f"{_pace_str(hud['speed_kmh'][data_i])} /mi", font=f_value, fill=WHITE)
    d.text((rx, ly0), "PACE",                 font=f_label, fill=DIM)

    # ── elevation / gain row ─────────────────────────────────────────────────
    vy1 = col_y0 + row_h + px(6)
    ly1 = vy1 + px(28)

    d.text((lx, vy1), f"{elev_ft:,.0f} ft", font=f_value, fill=WHITE)
    d.text((lx, ly1), "ELEVATION",           font=f_label, fill=DIM)

    d.text((rx, vy1), f"+{gain_ft:,.0f} ft", font=f_value, fill=MID)
    d.text((rx, ly1), "TOTAL GAIN",          font=f_label, fill=DIM)

    return np.array(img)


def _draw_watermark(img: Image.Image) -> Image.Image:
    """Subtle italic 'BS ◈' signature in the bottom-right corner."""
    W, H = img.size
    sc   = W / 1920.0

    f_wm  = _load_font(int(22 * sc), italic=True)
    text  = "BS ◈"
    pad   = int(22 * sc)

    # Measure text width
    bbox  = img.crop((0, 0, 1, 1))  # dummy; use getbbox via draw
    tmp   = ImageDraw.Draw(img.copy())
    try:
        tw, th = tmp.textsize(text, font=f_wm)
    except AttributeError:
        bbox_r = f_wm.getbbox(text)
        tw     = bbox_r[2] - bbox_r[0]
        th     = bbox_r[3] - bbox_r[1]

    tx = W - tw - pad
    ty = H - th - pad

    d = ImageDraw.Draw(img)
    # Shadow for readability
    d.text((tx + 1, ty + 1), text, font=f_wm, fill=(0, 0, 0, 110))
    d.text((tx, ty),         text, font=f_wm, fill=(200, 220, 240, 140))
    return img


# ── main render ───────────────────────────────────────────────────────────────

def render_video(
    plotter: pv.Plotter,
    camera_frames: List[Tuple[Tuple, Tuple, Tuple]],
    resampled_coords: List[Tuple[float, float, float]],
    output_path: str,
    fps: int = 30,
    hud_data: Optional[Dict] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
):
    """
    Progressive render: trail grows, arrow marker advances, camera follows.
    Extra frames at the end are the pan-out outro (trail stays full, arrow
    at endpoint). PIL HUD + watermark composited on every frame via screenshot.
    """
    from src.scene.builder import build_trail_segment, build_arrow_marker

    plotter.off_screen = True
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    n_main  = len(resampled_coords)
    n_total = len(camera_frames)

    try:
        writer = imageio.get_writer(output_path, fps=fps, quality=8)
    except Exception:
        writer = imageio.get_writer(output_path, fps=fps)

    try:
        for i, (position, focal, view_up) in enumerate(camera_frames):
            # Clamp to last resampled point during outro
            track_idx  = min(i + 1, n_main - 1)
            marker_idx = min(i,     n_main - 1)

            trail = build_trail_segment(resampled_coords, track_idx)
            plotter.add_mesh(trail, name="trail", scalars="progress", cmap="plasma",
                             show_scalar_bar=False)

            # Direction from consecutive resampled points
            if marker_idx < n_main - 1:
                fwd = (np.array(resampled_coords[marker_idx + 1]) -
                       np.array(resampled_coords[marker_idx]))
            else:
                fwd = (np.array(resampled_coords[-1]) -
                       np.array(resampled_coords[-2]))

            head = build_arrow_marker(resampled_coords[marker_idx], fwd)
            plotter.add_mesh(head, name="head", color="cyan")

            plotter.camera.position    = position
            plotter.camera.focal_point = focal
            plotter.camera.up          = view_up
            plotter.render()

            frame = plotter.screenshot(return_img=True)
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]

            if hud_data is not None:
                frame = _draw_hud(frame, i, hud_data)

            # Watermark on every frame
            pil_frame = Image.fromarray(frame).convert("RGBA")
            pil_frame = _draw_watermark(pil_frame)
            frame     = np.array(pil_frame.convert("RGB"))

            writer.append_data(frame)

            if progress_callback:
                progress_callback(i + 1, n_total)
    finally:
        writer.close()
        plotter.close()
