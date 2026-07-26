import math
import os
import time
import random
import requests
import numpy as np
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

MAPBOX_KEY  = os.getenv("MAPBOX_API_KEY")
TILE_SIZE   = 256
MAX_WORKERS = 8

# ── sources ───────────────────────────────────────────────────────────────────
# Terrain elevation: AWS Terrain Tiles (Terrarium) — keyless, no quota — by
# default; Mapbox Terrain-RGB kept as a fallback (needs MAPBOX_API_KEY).
# Satellite: ESRI World Imagery (keyless); Mapbox Satellite as fallback.
TERRAIN_SOURCE   = "aws"     # "aws" | "mapbox"
SATELLITE_SOURCE = "esri"    # "esri" | "mapbox"

# ── adaptive-zoom budgets ─────────────────────────────────────────────────────
# Zoom is chosen as the highest level whose stitched output stays within budget,
# so tiles-per-render is bounded regardless of route length (a longer route gets
# a lower zoom, not more tiles).
SAT_ZOOM_RANGE = (14, 19)
SAT_MAX_PX     = 15360        # under the 16384 GPU texture ceiling, with margin
SAT_MAX_TILES  = 3000         # second guard on request count per render

TERRAIN_ZOOM_RANGE   = (12, 15)      # Terrarium tops out at z15
TERRAIN_MAX_VERTICES = 5_000_000     # cap mesh vertices (px_w * px_h)
MAPBOX_TERRAIN_MAX_Z = 14            # Terrain-RGB v1 practical max here

# ── disk tile cache ───────────────────────────────────────────────────────────
# Sibling to output/ at the project root, gitignored. Re-renders and overlapping
# activities then hit no network. Delete .tilecache/ to clear.
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR  = os.path.join(_PROJ_ROOT, ".tilecache")


# ── tile math ─────────────────────────────────────────────────────────────────

def _lat_lon_to_tile(lat, lon, zoom):
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _tile_to_lat_lon(x, y, zoom):
    n = 2 ** zoom
    lon = x / n * 360 - 180
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad), lon


def _tile_grid(lat_min, lat_max, lon_min, lon_max, zoom):
    x_min, y_max = _lat_lon_to_tile(lat_min, lon_min, zoom)
    x_max, y_min = _lat_lon_to_tile(lat_max, lon_max, zoom)
    return x_min, x_max, y_min, y_max


def _best_zoom(lat_min, lat_max, lon_min, lon_max, zoom_range,
               max_px=None, max_tiles=None, max_vertices=None):
    """Highest zoom in range whose stitched output stays within every budget."""
    lo, hi = zoom_range
    for z in range(hi, lo - 1, -1):
        x_min, x_max, y_min, y_max = _tile_grid(lat_min, lat_max, lon_min, lon_max, z)
        cols, rows = x_max - x_min + 1, y_max - y_min + 1
        px_w, px_h = cols * TILE_SIZE, rows * TILE_SIZE
        if max_px       and max(px_w, px_h) > max_px:    continue
        if max_tiles    and cols * rows      > max_tiles: continue
        if max_vertices and px_w * px_h      > max_vertices: continue
        return z
    return lo


# ── fetching (retry + disk cache) ─────────────────────────────────────────────

def _get_with_retry(url, timeout=20, retries=4):
    """GET with exponential backoff on throttling / transient server errors."""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.content
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep((2 ** attempt) * 0.5 + random.random() * 0.3)
    raise RuntimeError("unreachable")


def _cached_tile(source, zoom, x, y, url, timeout=20):
    """Return a tile image, reading from / writing to the disk cache."""
    path = os.path.join(CACHE_DIR, source, str(zoom), str(x), f"{y}.tile")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            pass  # corrupt cache entry — refetch below
    content = _get_with_retry(url, timeout)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.part"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, path)   # atomic, so concurrent workers never see a half file
    return Image.open(BytesIO(content)).convert("RGB")


def _stitch(source, zoom, x_min, x_max, y_min, y_max, url_fn, timeout=20):
    """Fetch a tile grid in parallel (cached) and paste into one image."""
    cols, rows = x_max - x_min + 1, y_max - y_min + 1
    stitched = Image.new("RGB", (cols * TILE_SIZE, rows * TILE_SIZE))
    jobs = [(col, tx, row, ty)
            for row, ty in enumerate(range(y_min, y_max + 1))
            for col, tx in enumerate(range(x_min, x_max + 1))]

    def fetch(col, tx, row, ty):
        return col, row, _cached_tile(source, zoom, tx, ty, url_fn(zoom, tx, ty), timeout)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch, *job) for job in jobs]
        for future in as_completed(futures):
            col, row, tile = future.result()
            stitched.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))
    return stitched


# ── tile URLs + elevation decoders ────────────────────────────────────────────

def _terrarium_url(z, x, y):
    return f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def _mapbox_terrain_url(z, x, y):
    return f"https://api.mapbox.com/v4/mapbox.terrain-rgb/{z}/{x}/{y}.pngraw?access_token={MAPBOX_KEY}"


def _esri_url(z, x, y):
    # ESRI tile path is z/row/col (y before x), unlike the Mapbox z/x/y order.
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"


def _mapbox_sat_url(z, x, y):
    return f"https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.pngraw?access_token={MAPBOX_KEY}"


def _decode_terrarium(img: Image.Image) -> np.ndarray:
    a = np.array(img).astype(np.float64)
    return (a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256) - 32768


# Terrarium includes (often noisy) ocean bathymetry; unlike Mapbox Terrain-RGB
# it does not clamp water to sea level. For a route-over-land visualization we
# floor elevation at sea level so water renders flat instead of as a noisy
# canyon. Tradeoff: genuinely below-sea-level terrain (Death Valley, Dead Sea)
# also flattens to 0 — acceptable and matches the previous Mapbox behavior.
WATER_FLOOR_M = 0.0


def _clean_elevation(elev: np.ndarray, water_floor: float = WATER_FLOOR_M) -> np.ndarray:
    """
    Replace physically-impossible values (Terrarium emits nodata strips at some
    tile edges that decode to ~-23000 m) with the nearest valid value in the
    same column, then floor at sea level so water is flat.
    """
    valid = (elev > -12000) & (elev < 9000)   # Earth's real elevation range
    out   = elev.copy()
    if not valid.all():
        rows = np.arange(elev.shape[0])
        fill = float(np.median(elev[valid])) if valid.any() else 0.0
        for c in range(elev.shape[1]):
            col_valid = valid[:, c]
            if col_valid.all():
                continue
            idx = np.where(col_valid)[0]
            if len(idx) == 0:
                out[:, c] = fill
                continue
            nearest = idx[np.abs(idx[None, :] - rows[:, None]).argmin(axis=1)]
            out[~col_valid, c] = out[nearest, c][~col_valid]
    return np.maximum(out, water_floor)


def _decode_mapbox(img: Image.Image) -> np.ndarray:
    a = np.array(img).astype(np.float64)
    return -10000 + (a[:, :, 0] * 65536 + a[:, :, 1] * 256 + a[:, :, 2]) * 0.1


# ── terrain elevation ─────────────────────────────────────────────────────────

def fetch_terrain(lat_min, lat_max, lon_min, lon_max):
    """
    Fetch and stitch terrain-elevation tiles covering the bounding box, at the
    highest zoom that keeps the mesh under the vertex budget.
    Returns (elevation_grid, lat_grid, lon_grid).
    """
    if TERRAIN_SOURCE == "aws":
        zoom_range, url_fn, decode, src = TERRAIN_ZOOM_RANGE, _terrarium_url, _decode_terrarium, "terrarium"
    else:
        zoom_range = (TERRAIN_ZOOM_RANGE[0], min(TERRAIN_ZOOM_RANGE[1], MAPBOX_TERRAIN_MAX_Z))
        url_fn, decode, src = _mapbox_terrain_url, _decode_mapbox, "mapbox-terrain"

    zoom = _best_zoom(lat_min, lat_max, lon_min, lon_max, zoom_range,
                      max_vertices=TERRAIN_MAX_VERTICES)
    x_min, x_max, y_min, y_max = _tile_grid(lat_min, lat_max, lon_min, lon_max, zoom)
    print(f"  terrain: {src} z{zoom}  ({x_max - x_min + 1}×{y_max - y_min + 1} tiles)")

    stitched  = _stitch(src, zoom, x_min, x_max, y_min, y_max, url_fn)
    elevation = _clean_elevation(decode(stitched))

    lat_top, lon_left  = _tile_to_lat_lon(x_min, y_min, zoom)
    lat_bot, lon_right = _tile_to_lat_lon(x_max + 1, y_max + 1, zoom)

    lats = np.linspace(lat_top, lat_bot, elevation.shape[0])
    lons = np.linspace(lon_left, lon_right, elevation.shape[1])
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    return elevation, lat_grid, lon_grid


# ── satellite imagery ─────────────────────────────────────────────────────────

def _fetch_satellite(source, url_fn, lat_min, lat_max, lon_min, lon_max):
    zoom = _best_zoom(lat_min, lat_max, lon_min, lon_max, SAT_ZOOM_RANGE,
                      max_px=SAT_MAX_PX, max_tiles=SAT_MAX_TILES)
    x_min, x_max, y_min, y_max = _tile_grid(lat_min, lat_max, lon_min, lon_max, zoom)
    cols, rows = x_max - x_min + 1, y_max - y_min + 1
    print(f"  satellite: {source} z{zoom}  ({cols}×{rows} tiles, "
          f"{cols * TILE_SIZE}×{rows * TILE_SIZE}px)")

    stitched = _stitch(source, zoom, x_min, x_max, y_min, y_max, url_fn)

    lat_top, lon_left  = _tile_to_lat_lon(x_min, y_min, zoom)
    lat_bot, lon_right = _tile_to_lat_lon(x_max + 1, y_max + 1, zoom)
    return np.array(stitched), (lat_top, lat_bot, lon_left, lon_right)


def fetch_satellite_texture(lat_min, lat_max, lon_min, lon_max):
    """Dispatch to the configured satellite source (adaptive zoom, cached)."""
    if SATELLITE_SOURCE == "esri":
        return _fetch_satellite("esri", _esri_url, lat_min, lat_max, lon_min, lon_max)
    return _fetch_satellite("mapbox-sat", _mapbox_sat_url, lat_min, lat_max, lon_min, lon_max)
