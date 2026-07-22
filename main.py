import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import filedialog
import multiprocessing as mp
from datetime import datetime

from src.gpx.parser import parse as parse_gpx
from src.gpx.pacing import (FPS, OUTRO_SECONDS, video_main_seconds,
                            activity_distance_miles, activity_duration_min)

STEPS = [
    "Parsing GPX",
    "Fetching terrain tiles",
    "Fetching satellite imagery",
    "Computing camera path",
    "Building terrain mesh",
    "Assembling scene",
    "Rendering",
]

ASPECTS = [
    ("16:9",  "Landscape — YouTube, desktop"),
    ("9:16",  "Vertical — Reels, Shorts, TikTok"),
    ("1:1",   "Square — feed posts"),
]

# (tier label, short side, 16:9 long side)
RESOLUTIONS = [
    ("1080p", 1080, 1920),
    ("720p",  720,  1280),
    ("480p",  480,  854),
    ("360p",  360,  640),
    ("240p",  240,  426),
]

# Measured from CRF 23 test renders: (pixels per frame, bits per pixel).
# H.264 gets more efficient per pixel at higher resolutions, so bpp is
# interpolated on log(pixels) between these calibration points.
_CALIB = [(414_720, 0.442),      # 480p render
          (2_073_600, 0.276)]    # 1080p render


def _bits_per_pixel(pixels: int) -> float:
    import math
    (p0, b0), (p1, b1) = _CALIB
    t = (math.log(pixels) - math.log(p0)) / (math.log(p1) - math.log(p0))
    return b0 + (b1 - b0) * t


def video_dimensions(aspect: str, short: int, long: int):
    """Pixel dimensions for an aspect ratio and resolution tier (all values even)."""
    if aspect == "16:9":
        return long, short
    if aspect == "9:16":
        return short, long
    return short, short


def estimate_mb(w: int, h: int, n_frames: int):
    """Estimated output size range in MB for a video of n_frames frames."""
    mb = w * h * n_frames * _bits_per_pixel(w * h) / 8 / 1e6
    return mb * 0.7, mb * 1.4


def ask_choice(root, title, question, options, default_idx=0):
    """Blocking radio-list dialog. options is a list of (label, sublabel).
    Returns the selected index, or None if closed."""
    choice = [None]
    d = tk.Toplevel(root)
    d.title(title)
    d.resizable(False, False)
    d.grab_set()

    tk.Label(d, text=question,
             font=("Helvetica Neue", 13),
             wraplength=360, justify="center",
             padx=28, pady=18).pack()

    var = tk.IntVar(value=default_idx)
    box = tk.Frame(d)
    box.pack(padx=32, anchor="w")

    for i, (label, sublabel) in enumerate(options):
        text = f"{label}   —   {sublabel}" if sublabel else label
        tk.Radiobutton(box, text=text, variable=var, value=i,
                       font=("Helvetica Neue", 12),
                       anchor="w", justify="left").pack(fill="x", pady=2, anchor="w")

    def ok():
        choice[0] = var.get()
        d.destroy()

    tk.Button(d, text="Continue", width=18, command=ok).pack(pady=(16, 20))
    root.wait_window(d)
    return choice[0]


def ask_two(root, title, question, btn_a, btn_b):
    """Blocking two-button modal dialog. Returns 'a' or 'b'."""
    choice = [None]
    d = tk.Toplevel(root)
    d.title(title)
    d.resizable(False, False)
    d.grab_set()

    tk.Label(d, text=question,
             font=("Helvetica Neue", 13),
             wraplength=340, justify="center",
             padx=28, pady=22).pack()

    row = tk.Frame(d)
    row.pack(pady=(0, 22), padx=28)

    def pick(val):
        choice[0] = val
        d.destroy()

    tk.Button(row, text=btn_a, width=22,
              command=lambda: pick("a")).pack(side="left", padx=8)
    tk.Button(row, text=btn_b, width=22,
              command=lambda: pick("b")).pack(side="left", padx=8)

    root.wait_window(d)
    return choice[0]


def main():
    root = tk.Tk()
    root.withdraw()

    print("\n=== 3D GPS Visualizer ===\n")

    # ── 1. GPX file ──────────────────────────────────────────────────────────
    print("Select your .gpx file in the dialog...")
    gpx = filedialog.askopenfilename(
        parent=root,
        title="Select your Strava GPX file",
        filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")],
    )
    if not gpx:
        print("No file selected. Exiting.")
        return
    print(f"  ✓ {gpx}\n")

    # Parse once up front to size the video to the activity.
    pts        = parse_gpx(gpx)
    dist_mi    = activity_distance_miles(pts)
    dur_min    = activity_duration_min(pts)
    main_secs  = video_main_seconds(dist_mi, dur_min)
    total_secs = main_secs + OUTRO_SECONDS
    n_frames   = round(total_secs * FPS)
    dur_txt    = f"{dur_min:.0f} min" if dur_min is not None else "unknown duration"
    print(f"  ✓ {dist_mi:.2f} mi · {dur_txt} → ~{total_secs:.0f} s video")

    # ── 2. Camera mode ───────────────────────────────────────────────────────
    cam = ask_two(root, "Camera Mode",
                  "Which camera style?",
                  "Cinematic  (orbit 180° around route)",
                  "First-Person  (follow path forward)")
    if cam is None:
        print("Cancelled. Exiting.")
        return
    camera_mode = "cinematic" if cam == "a" else "first_person"
    print(f"  ✓ Camera mode: {camera_mode}")

    # ── 3. Aspect ratio ──────────────────────────────────────────────────────
    a_idx = ask_choice(root, "Aspect Ratio", "Which aspect ratio?", ASPECTS)
    if a_idx is None:
        print("Cancelled. Exiting.")
        return
    aspect = ASPECTS[a_idx][0]
    print(f"  ✓ Aspect ratio: {aspect}")

    # ── 4. Resolution (with size estimates) ──────────────────────────────────
    res_options = []
    for tier, short, long in RESOLUTIONS:
        w, h = video_dimensions(aspect, short, long)
        lo, hi = estimate_mb(w, h, n_frames)
        est = f"est. {lo:.0f}–{hi:.0f} MB" if lo >= 1 else "est. <1 MB"
        res_options.append((f"{tier}  ({w}×{h})", est))

    r_idx = ask_choice(root, "Resolution",
                       f"Which resolution?\n(estimated file size for a ~{total_secs:.0f} s video)",
                       res_options)
    if r_idx is None:
        print("Cancelled. Exiting.")
        return
    tier, short, long = RESOLUTIONS[r_idx]
    width, height = video_dimensions(aspect, short, long)
    print(f"  ✓ Resolution: {tier} → {width}×{height}")

    # ── 5. Save location ─────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    default = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "output", f"visualization_{ts}.mp4",
    )

    loc = ask_two(root, "Save Location",
                  f"Save video to default output folder?\n\n{default}",
                  "Yes, use default",
                  "No, choose folder")

    if loc == "b":
        folder = filedialog.askdirectory(parent=root, title="Choose output folder")
        video_path = os.path.join(folder, f"visualization_{ts}.mp4") if folder else default
    else:
        video_path = default

    print(f"  ✓ Will save to: {video_path}")

    root.destroy()

    # ── 6. Run pipeline ──────────────────────────────────────────────────────
    from src.pipeline import run as pipeline_run

    print("\n--- Starting pipeline ---\n")

    queue = mp.Queue()
    process = mp.Process(
        target=pipeline_run,
        args=({"gpx_path": gpx, "camera_mode": camera_mode, "video_path": video_path,
               "width": width, "height": height, "video_seconds": main_secs}, queue),
        daemon=True,
    )
    process.start()

    in_render = False

    while True:
        try:
            msg_type, data = queue.get(timeout=0.5)

            if msg_type == "step":
                if in_render:
                    print()
                    in_render = False
                label = STEPS[data] if data < len(STEPS) else ""
                print(f"[{data + 1}/7] {label}...")
                if data == 6:
                    in_render = True

            elif msg_type == "progress":
                current, total, remaining = data
                pct = int(current / total * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                eta = (f"ETA {remaining // 60}m {remaining % 60}s"
                       if remaining > 5 else "almost done")
                print(f"\r  [{bar}] {pct}%  {eta}   ", end="", flush=True)

            elif msg_type == "done":
                if in_render:
                    print()
                print(f"\n✓ Done!  Saved to: {data}")
                break

            elif msg_type == "error":
                if in_render:
                    print()
                print(f"\n✗ Pipeline error:\n\n{data}")
                break

        except Exception:
            if not process.is_alive():
                code = process.exitcode
                print(f"\n✗ Pipeline process exited unexpectedly (code {code})")
                break

    process.join()


if __name__ == "__main__":
    mp.freeze_support()
    main()
