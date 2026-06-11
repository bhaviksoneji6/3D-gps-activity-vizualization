import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import multiprocessing as mp
import os
from datetime import datetime

STEPS = [
    "Parsing GPX…",
    "Fetching terrain tiles…",
    "Fetching satellite imagery…",
    "Computing camera path…",
    "Building terrain mesh…",
    "Assembling scene…",
    "Rendering…",
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("3D GPS Visualizer")
        self.resizable(False, False)
        self._process = None
        self._queue   = None
        self._build_ui()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = ttk.Frame(self, padding="24 20 24 20")
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="3D GPS Visualizer",
                  font=("Helvetica Neue", 18, "bold")).pack(anchor="w")
        ttk.Label(outer,
                  text="Turn a Strava .gpx activity into a 3D rendered video",
                  foreground="gray").pack(anchor="w", pady=(2, 14))

        ttk.Separator(outer).pack(fill="x")

        # ── GPX file ──
        ttk.Label(outer, text="GPX File",
                  font=("Helvetica Neue", 12, "bold")).pack(anchor="w", pady=(12, 0))
        ttk.Label(outer,
                  text="Your activity export from Strava  (open a run → ··· → Export File → GPX)",
                  foreground="gray").pack(anchor="w", pady=(2, 6))

        file_row = ttk.Frame(outer)
        file_row.pack(fill="x", pady=(0, 2))

        self.gpx_path = tk.StringVar(value="No file selected")
        ttk.Entry(file_row, textvariable=self.gpx_path,
                  state="readonly", width=46).pack(side="left", padx=(0, 8), ipady=3)
        ttk.Button(file_row, text="Browse…", command=self._pick_file).pack(side="left")

        ttk.Separator(outer).pack(fill="x", pady=12)

        # ── Camera mode ──
        cam_lf = ttk.LabelFrame(outer, text="Camera Mode", padding="12 8 12 12")
        cam_lf.pack(fill="x")

        self.camera_mode = tk.StringVar(value="cinematic")

        ttk.Radiobutton(cam_lf, text="Cinematic",
                        variable=self.camera_mode, value="cinematic").pack(anchor="w")
        ttk.Label(cam_lf, text="Orbiting elevated chase camera — sweeps 180° around the route",
                  foreground="gray", font=("Helvetica Neue", 11)
                  ).pack(anchor="w", padx=(20, 0))

        ttk.Separator(cam_lf).pack(fill="x", pady=8)

        ttk.Radiobutton(cam_lf, text="First-Person",
                        variable=self.camera_mode, value="first_person").pack(anchor="w")
        ttk.Label(cam_lf, text="Camera follows the GPS path from close behind and above",
                  foreground="gray", font=("Helvetica Neue", 11)
                  ).pack(anchor="w", padx=(20, 0))

        ttk.Separator(outer).pack(fill="x", pady=12)

        # ── Save video as ──
        ttk.Label(outer, text="Save Video As",
                  font=("Helvetica Neue", 12, "bold")).pack(anchor="w")
        ttk.Label(outer,
                  text="Where the rendered .mp4 will be saved on your Mac",
                  foreground="gray").pack(anchor="w", pady=(2, 6))

        save_row = ttk.Frame(outer)
        save_row.pack(fill="x", pady=(0, 2))

        self.video_path = tk.StringVar(value=self._default_video_path())
        ttk.Entry(save_row, textvariable=self.video_path,
                  state="readonly", width=46).pack(side="left", padx=(0, 8), ipady=3)
        ttk.Button(save_row, text="Change…",
                   command=self._pick_video_path).pack(side="left")

        ttk.Separator(outer).pack(fill="x", pady=12)

        # ── Progress ──
        prog_row = ttk.Frame(outer)
        prog_row.pack(fill="x", pady=(0, 6))

        self.step_var = tk.StringVar(value="Ready")
        ttk.Label(prog_row, textvariable=self.step_var).pack(side="left")

        self.eta_var = tk.StringVar(value="")
        ttk.Label(prog_row, textvariable=self.eta_var,
                  foreground="gray").pack(side="right")

        self.progress = ttk.Progressbar(outer, orient="horizontal",
                                        length=500, mode="determinate")
        self.progress.pack(pady=(0, 14))

        # ── Run ──
        self.run_btn = ttk.Button(outer, text="Run", command=self._run)
        self.run_btn.pack(fill="x", ipady=8)

    # ── interactions ─────────────────────────────────────────────────────────

    def _default_video_path(self):
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "output", f"visualization_{ts}.mp4")

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Select your Strava GPX file",
            filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")],
        )
        if path:
            self.gpx_path.set(path)

    def _pick_video_path(self):
        path = filedialog.asksaveasfilename(
            title="Choose where to save the video",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4")],
        )
        if path:
            self.video_path.set(path)

    def _validate(self):
        if self.gpx_path.get() in ("", "No file selected"):
            messagebox.showerror(
                "No File Selected",
                "Click Browse… to select a .gpx file exported from Strava.",
            )
            return False
        if not self.gpx_path.get().lower().endswith(".gpx"):
            messagebox.showerror("Wrong File Type", "Please select a .gpx file.")
            return False
        if not self.video_path.get():
            messagebox.showerror(
                "No Output Path",
                "Click Change… to choose where to save the video.",
            )
            return False
        return True

    # ── pipeline ─────────────────────────────────────────────────────────────

    def _run(self):
        if not self._validate():
            return

        self.run_btn.config(state="disabled")
        self.progress.config(value=0)
        self.step_var.set("Starting…")
        self.eta_var.set("")
        self.update_idletasks()

        from src.pipeline import run as pipeline_run

        self._queue   = mp.Queue()
        self._process = mp.Process(
            target=pipeline_run,
            args=({
                "gpx_path":    self.gpx_path.get(),
                "camera_mode": self.camera_mode.get(),
                "video_path":  self.video_path.get(),
            }, self._queue),
            daemon=True,
        )
        self._process.start()
        self.after(200, self._poll_queue)

    def _poll_queue(self):
        alive = self._process and self._process.is_alive()

        try:
            while True:
                msg_type, data = self._queue.get_nowait()

                if msg_type == "step":
                    label = STEPS[data] if data < len(STEPS) else ""
                    self.step_var.set(f"Step {data + 1}/{len(STEPS)}  —  {label}")
                    self.progress.config(value=int(data / len(STEPS) * 100))

                elif msg_type == "progress":
                    current, total, remaining = data
                    self.progress.config(value=int(current / total * 100))
                    self.eta_var.set(
                        f"ETA: {remaining // 60}m {remaining % 60}s"
                        if remaining > 5 else "Almost done…"
                    )

                elif msg_type == "done":
                    name = os.path.basename(data)
                    self.step_var.set(f"Done  —  {name}")
                    self.progress.config(value=100)
                    self.eta_var.set("")
                    self.run_btn.config(state="normal")
                    return

                elif msg_type == "error":
                    messagebox.showerror("Pipeline Error", data)
                    self.step_var.set("Failed — see error dialog above")
                    self.progress.config(value=0)
                    self.run_btn.config(state="normal")
                    return

        except Exception:
            pass  # queue.Empty

        if alive:
            self.after(200, self._poll_queue)
        else:
            code = getattr(self._process, "exitcode", -1)
            if code and code != 0:
                messagebox.showerror(
                    "Process Error",
                    f"Pipeline crashed (exit code {code}).\n"
                    "Check the terminal window for the full traceback.",
                )
                self.step_var.set("Failed")
                self.run_btn.config(state="normal")
