import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import filedialog
import multiprocessing as mp
from datetime import datetime

STEPS = [
    "Parsing GPX",
    "Fetching terrain tiles",
    "Fetching satellite imagery",
    "Computing camera path",
    "Building terrain mesh",
    "Assembling scene",
    "Rendering",
]


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

    # ── 3. Save location ─────────────────────────────────────────────────────
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

    # ── 4. Run pipeline ──────────────────────────────────────────────────────
    from src.pipeline import run as pipeline_run

    print("\n--- Starting pipeline ---\n")

    queue = mp.Queue()
    process = mp.Process(
        target=pipeline_run,
        args=({"gpx_path": gpx, "camera_mode": camera_mode, "video_path": video_path}, queue),
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
