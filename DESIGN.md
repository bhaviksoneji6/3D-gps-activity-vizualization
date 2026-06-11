# 3D GPS Activity Visualization — Design Document

## Overview

Converts a Strava `.gpx` activity file into a 3D rendered visualization over real satellite terrain. The user is walked through four native dialogs to configure the run, then all processing and progress happens in the terminal.

---

## User Flow

```
start.command (double-click)
  └── Terminal opens
  └── venv activated / dependencies installed on first run
  └── main.py runs

main.py
  ├── Dialog 1: File picker → select .gpx file
  ├── Dialog 2: Output mode → Interactive or Video
  ├── Dialog 3: Camera mode → Cinematic or First-Person
  ├── Dialog 4: Save location → Default folder or custom (video only)
  └── Pipeline starts → all progress printed to terminal
```

---

## Architecture

### Process Model

The pipeline runs in a **subprocess** (not a thread). This is a deliberate architectural decision for macOS: VTK (the rendering engine used by PyVista) requires that any window or rendering context be created on the OS main thread. Python threads share the main thread with the calling process, so running PyVista in a background thread causes an `NSInternalInconsistencyException` crash on macOS. A subprocess has its own OS-level main thread, which satisfies the Cocoa requirement.

```
main process (main.py)
  ├── Shows Tkinter dialogs (native macOS popups)
  ├── Spawns pipeline subprocess
  └── Polls queue → prints progress to terminal

pipeline subprocess (src/pipeline.py)
  ├── Owns its own OS main thread (safe for VTK/Cocoa)
  ├── Runs all 7 pipeline steps
  └── Sends progress messages back via multiprocessing.Queue
```

**Start method:** `spawn` (macOS default since Python 3.8). The subprocess starts a fresh Python interpreter — no shared memory with the parent. All arguments must be picklable (plain dicts and the Queue are).

---

## Pipeline Steps

### Step 1 — GPX Parsing (`src/gpx/parser.py`)

The `.gpx` file is XML. Each `<trkpt>` element contains `lat`, `lon`, `<ele>` (elevation in metres), and `<time>`. The parser:
- Drops trackpoints with missing elevation
- Removes consecutive duplicate points (GPS can stutter)
- Smooths elevation with a rolling average (window=5) to remove GPS noise

### Step 2 — Coordinate Conversion (`src/gpx/parser.py → to_cartesian`)

GPS coordinates (lat/lon/elevation) are converted to a local flat Cartesian space in metres so the terrain mesh and track path share the same unit system.

- **Origin**: centroid of all trackpoints → becomes `(0, 0, 0)`
- **x** (east-west): `R × Δlon_rad × cos(origin_lat)` — equirectangular approximation
- **y** (north-south): `R × Δlat_rad`
- **z** (vertical): `elevation_metres × elevation_scale` (default ×2 for visual drama)

### Step 3 — Terrain Tile Fetching (`src/terrain/fetcher.py`)

**Source:** [Mapbox Terrain-RGB](https://docs.mapbox.com/data/tilesets/reference/mapbox-terrain-rgb-v1/)

Terrain-RGB tiles encode elevation in the RGB channels of a PNG:
```
elevation (m) = -10000 + (R×256² + G×256 + B) × 0.1
```

The bounding box of the track (plus padding) is converted to tile coordinates at zoom level 13. All tiles in the bounding box are fetched and stitched into one elevation grid.

A matching set of **satellite imagery tiles** is fetched from the same tile coordinates and used as a texture draped over the terrain mesh.

Requires a Mapbox API key stored in `.env`.

### Step 4 — Terrain Mesh Construction (`src/terrain/mesh.py`)

The 2D elevation grid becomes a 3D `pv.StructuredGrid`:
- Each grid cell → two triangles
- x, y from the same Cartesian conversion applied to the lat/lon grid
- z from the decoded elevation × elevation scale
- **Important:** `StructuredGrid` requires 3D arrays — a `np.newaxis` dimension is added for the surface
- Satellite texture coordinates are assigned in Fortran order to match PyVista's internal point ordering

### Step 5 — Scene Assembly (`src/scene/builder.py`)

Three objects are combined into a single PyVista plotter:

| Object | Detail |
|---|---|
| Terrain mesh | Satellite texture draped over it; falls back to elevation colormap if no texture |
| Track tube | `polyline.tube(radius=8m)` coloured by progress along route (plasma colormap) |
| Start / end markers | Small spheres lifted 10m above terrain — green for start, red for end |

The track is lifted +10m in z to prevent z-fighting with the terrain surface.

### Step 6 — Camera Path (`src/scene/camera.py`)

#### Cinematic Orbit
- Computes bounding center and max radius of the track
- Camera sits at `radius × 1.8` distance, elevated 35° above horizontal
- Always points at the track center (fixed look-at)
- 600 frames = 20 seconds at 30fps, rotating 360° in azimuth

#### First-Person
- At each trackpoint `i`, camera is offset backward and upward from the current point
- Forward vector = direction from point `i` to point `i+1`
- A rolling average (window=15) smooths the camera path to remove GPS jitter

### Step 7 — Rendering (`src/renderer/renderer.py`)

**Interactive mode:** The plotter opens a live window. PyVista's built-in orbit controls (mouse drag, scroll) are enabled.

**Video mode:** Off-screen rendering. Each camera frame is rendered to a buffer, written via `plotter.open_movie()` + `plotter.write_frame()`, then encoded to `.mp4` using imageio/ffmpeg. A progress callback fires after each frame so the terminal progress bar updates.

---

## File Structure

```
3D-gps-activity-visualization/
│
├── main.py                  # Entry point: dialogs → subprocess → console progress
├── start.command            # macOS double-click launcher (bash)
├── requirements.txt
├── .env                     # MAPBOX_API_KEY (not committed)
├── .env.example
├── DESIGN.md                # This document
│
├── src/
│   ├── pipeline.py          # Full pipeline worker — runs inside subprocess
│   │
│   ├── gpx/
│   │   └── parser.py        # GPX XML parsing, cleaning, Cartesian conversion
│   │
│   ├── terrain/
│   │   ├── fetcher.py       # Mapbox tile fetching, elevation decoding, stitching
│   │   └── mesh.py          # Elevation grid → PyVista StructuredGrid
│   │
│   ├── scene/
│   │   ├── builder.py       # Assembles terrain + track + markers into plotter
│   │   └── camera.py        # Cinematic orbit and first-person camera frame lists
│   │
│   └── renderer/
│       └── renderer.py      # Interactive window or off-screen video encode
│
├── output/                  # Rendered .mp4 files (gitignored)
└── assets/                  # Drop sample .gpx files here for testing
```

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `pyvista` | 3D scene assembly, mesh rendering, video encoding |
| `numpy` | Grid computation, coordinate conversion |
| `requests` | Mapbox tile HTTP fetching |
| `Pillow` | PNG tile decoding |
| `python-dotenv` | `.env` file loading for API key |
| `imageio-ffmpeg` | `.mp4` encoding backend used by PyVista |
| `scipy` | (Available for future smoothing / interpolation) |

---

## Configuration

| Setting | Location | Default |
|---|---|---|
| Mapbox API key | `.env` → `MAPBOX_API_KEY` | — |
| Tile zoom level | `src/terrain/fetcher.py` → `ZOOM` | `13` |
| Elevation scale | `src/gpx/parser.py` → `to_cartesian(elevation_scale=)` | `2.0` |
| Track tube radius | `src/scene/builder.py` → `build_track(tube_radius=)` | `8.0 m` |
| Orbit frames (video) | `src/scene/camera.py` → `cinematic_orbit_frames(n_frames=)` | `600` |
| Orbit elevation angle | same | `35°` |
| Camera smoothing window | `src/scene/camera.py` → `smooth_frames(window=)` | `15` |
| Video FPS | `src/renderer/renderer.py` → `render_video(fps=)` | `30` |

---

## Design Decisions

**Why subprocess instead of threading?**
macOS Cocoa requires NSWindow (and any VTK rendering context) to be created on the process's main thread. Python threads share the calling thread, so any PyVista call from a background thread crashes with `NSInternalInconsistencyException`. A subprocess has its own OS main thread, solving this cleanly without any workarounds.

**Why Mapbox Terrain-RGB over SRTM?**
Terrain-RGB tiles are tile-based (fetch only what you need), globally available, at ~5m resolution, and the same tile server also provides satellite imagery — meaning both elevation and texture come from one API with one key.

**Why elevation scale ×2?**
Real terrain elevation differences are often small relative to the horizontal extent of a run. A ×2 vertical exaggeration makes hills and valleys visually dramatic without distorting the perceived path.

**Why `pv.StructuredGrid` instead of `pv.PolyData`?**
StructuredGrid preserves the regular grid topology of the terrain, which is necessary for correct texture coordinate mapping. PolyData loses the grid structure after triangulation, making UV mapping unreliable.

**Why no persistent UI window?**
Three attempts at a custom Tkinter window on macOS all failed to render correctly: custom `bg`/`fg` colors are ignored by the Aqua theme in dark mode, leaving labels and entry fields invisible. Native Tkinter dialogs (`filedialog`, `Toplevel`) render correctly because they use system-native widgets. All non-dialog feedback is better suited to the terminal anyway.
