# 3D GPS Activity Visualization

Renders a cinematic 3D video of a GPS activity (`.gpx`, e.g. exported from Strava)
over real satellite terrain — progressive route trail, animated direction arrow,
a live stats HUD, and a pan-out outro shot.

**v1.2** — adaptive chase camera, redesigned HUD overlay, Google Maps–style arrow
marker, and an automatic pan-out ending.

## Requirements

- Python 3.9+ (macOS — the GUI dialogs and fonts are macOS-specific)
- A free [Mapbox API key](https://mapbox.com) (for terrain elevation tiles)

ffmpeg is bundled via `imageio-ffmpeg`; no separate install needed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your MAPBOX_API_KEY
```

## Run

Double-click `start.command` on macOS, or:

```bash
python main.py
```

You'll be prompted (native dialogs) for:

1. **GPX file** — your exported activity
2. **Camera mode**
   - **Cinematic** — elevated chase camera that adapts to terrain and sweeps around the route
   - **First-Person** — close follow camera from behind and above
3. **Aspect ratio** — 16:9 (YouTube/desktop), 9:16 (Reels/Shorts/TikTok), or 1:1 (feed posts)
4. **Resolution** — 1080p / 720p / 480p / 360p / 240p, each shown with an
   estimated output file size before you choose
5. **Save location** — default `output/` folder, or pick your own

The camera automatically pulls back further in 9:16 and 1:1 to keep the route
framed, and the stats HUD scales to every aspect/resolution combination.

### Pacing

Video length adapts to the activity — longer activities get proportionally
less screen time per mile (roughly 30 s for a short run up to ~2.5 min for a
marathon), plus a 6 s pan-out outro. Frames are spaced by **real elapsed time**,
so slow climbs genuinely look slow and fast descents look fast; the HUD clock
shows true elapsed time. Long stops (red lights, water breaks) are compressed
so the video never sits frozen. GPX files without timestamps fall back to
constant-speed, distance-based pacing.

The pipeline then runs in the terminal with step-by-step progress and a render ETA:

```
[1/7] Parsing GPX
[2/7] Fetching terrain tiles
[3/7] Fetching satellite imagery
[4/7] Computing camera path
[5/7] Building terrain mesh
[6/7] Assembling scene
[7/7] Rendering   [████████░░░░░░░░░░░░] 40%  ETA 2m 10s
```

## What's in the video

- Satellite-textured 3D terrain (1.2× vertical exaggeration)
- Progressive trail that grows along the route (plasma color ramp), with a faint
  "ghost" of the full track and an end marker
- Animated arrow marker pointing in the direction of travel
- Stats HUD (bottom-left): elapsed time, route progress bar, speed (mph),
  pace (/mi), elevation (ft), total gain (ft) — computed from GPX timestamps,
  smoothed; falls back to estimates if the file has no timestamps
- Pan-out outro once the route completes

## Output

H.264 MP4 (CRF 23, `yuv420p`, faststart — plays everywhere, streams well),
30 fps, saved to `output/` as `visualization_<timestamp>.mp4`. Length depends
on the activity (see [Pacing](#pacing)); the dialog shows a size estimate for
your specific activity before you render.

Typical file sizes per 60 s of video (varies with route scenery):

| Resolution | 16:9 / 9:16 | 1:1 |
|-----------|-------------|-----|
| 1080p | ~90–180 MB | ~60–120 MB |
| 720p | ~50–105 MB | ~35–70 MB |
| 480p | ~28–58 MB | ~18–36 MB |
| 360p | ~18–36 MB | ~12–22 MB |
| 240p | ~9–18 MB | ~5–11 MB |

## Data Sources

| Data | Source |
|------|--------|
| Terrain elevation | [Mapbox Terrain-RGB](https://docs.mapbox.com/data/tilesets/reference/mapbox-terrain-rgb-v1/) (zoom 14) |
| Satellite imagery | [ESRI World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9) (zoom 17, ~1.2 m/px); Mapbox Satellite (zoom 15) available as a fallback via `SATELLITE_SOURCE` in `src/terrain/fetcher.py` |

## Project Structure

```
main.py               Entry point — dialogs, then runs the pipeline in a subprocess
src/pipeline.py       7-step pipeline worker (subprocess for macOS GUI safety)
src/gpx/              GPX parsing and coordinate conversion
src/terrain/          Tile fetching (elevation + satellite) and terrain mesh
src/scene/            Scene assembly, trail/marker builders, camera paths
src/renderer/         Frame-by-frame render loop, HUD and watermark compositing
```

## Roadmap

- Higher-resolution terrain and satellite imagery
- Web service version
- Seasonal / time-of-day lighting
