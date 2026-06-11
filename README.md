# 3D GPS Activity Visualization

Renders a cinematic 3D video of a Strava `.gpx` activity over real satellite terrain —
progressive route trail, animated human marker, and a stats HUD (distance, speed, pace, elevation).

## Requirements

- Python 3.9+
- A free [Mapbox API key](https://mapbox.com) (for terrain elevation tiles)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your Mapbox API key
```

## Run

Double-click `start.command` on macOS, or:

```bash
python main.py
```

## Usage

1. Select your `.gpx` file exported from Strava
2. Choose camera mode:
   - **Cinematic** — elevated chase camera that sweeps 180° around the route
   - **First-Person** — close follow camera from behind and above
3. Choose where to save the `.mp4`
4. The pipeline fetches terrain + satellite imagery, builds the 3D scene, and renders the video

## Data Sources

| Data | Source |
|------|--------|
| Terrain elevation | [Mapbox Terrain-RGB](https://docs.mapbox.com/data/tilesets/reference/mapbox-terrain-rgb-v1/) (zoom 14) |
| Satellite imagery | [ESRI World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9) (zoom 17, ~1.2 m/px) |

## Output

Videos are saved to the `output/` folder as `.mp4` at 1920×1080.
