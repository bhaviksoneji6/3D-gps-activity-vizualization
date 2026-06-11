import os
import numpy as np
import pyvista as pv
import imageio
from typing import List, Tuple, Callable, Optional, Dict
from PIL import Image, ImageDraw, ImageFont


# ── HUD helpers ───────────────────────────────────────────────────────────────

def _get_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _draw_hud(frame_np: np.ndarray, idx: int, hud: Dict) -> np.ndarray:
    """Composite a semi-transparent stats panel onto the bottom-left of the frame."""
    img = Image.fromarray(frame_np)
    w, h = img.size

    # Panel geometry — scales with frame size
    scale   = w / 1920
    pad     = int(20 * scale)
    pw      = int(310 * scale)
    ph      = int(178 * scale)
    px, py  = pad, h - ph - pad       # bottom-left anchor
    radius  = int(10 * scale)
    fsize   = max(14, int(22 * scale))
    lh      = int(fsize * 1.55)

    font = _get_font(fsize)

    # Draw semi-transparent background
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_o  = ImageDraw.Draw(overlay)
    draw_o.rounded_rectangle(
        [px, py, px + pw, py + ph],
        radius=radius,
        fill=(0, 0, 0, 165),
    )
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")

    draw = ImageDraw.Draw(img)

    elapsed = hud["elapsed_secs"][idx]
    dist    = hud["dist_covered_km"][idx]
    total   = hud["total_dist_km"]
    speed   = hud["speed_kmh"][idx]
    elev    = hud["elevation_m"][idx]
    gain    = hud["elevation_gain_m"][idx]

    lines = [
        ("ELAPSED", _fmt_elapsed(elapsed)),
        ("DIST",    f"{dist:.2f} / {total:.2f} km"),
        ("SPEED",   f"{speed:.1f} km/h"),
        ("ELEV",    f"{elev:.0f} m  +{gain:.0f} m"),
    ]

    tx  = px + int(14 * scale)
    ty  = py + int(14 * scale)
    dim = (160, 160, 160)
    brt = (255, 255, 255)

    for label, value in lines:
        draw.text((tx, ty),      label, font=font, fill=dim)
        draw.text((tx + int(76 * scale), ty), value, font=font, fill=brt)
        ty += lh

    return np.array(img)


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
    Progressive render: each frame grows the trail, advances the human marker
    and camera.  Frames are captured as screenshots so the HUD overlay can be
    composited via PIL before writing to the output mp4.
    """
    from src.scene.builder import build_trail_segment, build_human_marker

    plotter.off_screen = True
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    total = len(camera_frames)

    try:
        writer = imageio.get_writer(output_path, fps=fps, quality=8)
    except Exception:
        writer = imageio.get_writer(output_path, fps=fps)

    try:
        for i, (position, focal, view_up) in enumerate(camera_frames):
            trail = build_trail_segment(resampled_coords, i + 1)
            plotter.add_mesh(trail, name="trail", scalars="progress", cmap="plasma",
                             show_scalar_bar=False)

            head = build_human_marker(resampled_coords[i])
            plotter.add_mesh(head, name="head", color="cyan")

            plotter.camera.position  = position
            plotter.camera.focal_point = focal
            plotter.camera.up        = view_up
            plotter.render()

            frame = plotter.screenshot(return_img=True)
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]  # drop alpha if present

            if hud_data is not None:
                frame = _draw_hud(frame, i, hud_data)

            writer.append_data(frame)

            if progress_callback:
                progress_callback(i + 1, total)
    finally:
        writer.close()
        plotter.close()
