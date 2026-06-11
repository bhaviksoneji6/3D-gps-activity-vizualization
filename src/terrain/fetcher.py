import math
import os
import requests
import numpy as np
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

MAPBOX_KEY = os.getenv("MAPBOX_API_KEY")
TILE_SIZE = 256
TERRAIN_ZOOM = 14    # elevation mesh — zoom 14 gives 4× more mesh vertices than 13
SATELLITE_ZOOM = 15  # Mapbox satellite fallback zoom

# Switch between "esri" and "mapbox" without touching anything else
SATELLITE_SOURCE = "esri"
ESRI_SATELLITE_ZOOM = 17   # 1.2 m/pixel — raw tile pyramid, same quality as web maps


# ── shared tile helpers ───────────────────────────────────────────────────────

def _lat_lon_to_tile(lat, lon, zoom):
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _tile_to_lat_lon(x, y, zoom):
    n = 2 ** zoom
    lon = x / n * 360 - 180
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lon


def _fetch_tile(x, y, zoom, layer="mapbox.terrain-rgb"):
    url = f"https://api.mapbox.com/v4/{layer}/{zoom}/{x}/{y}.pngraw?access_token={MAPBOX_KEY}"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")


def _decode_elevation(img: Image.Image) -> np.ndarray:
    r, g, b = np.array(img).transpose(2, 0, 1).astype(np.float64)
    return -10000 + (r * 256 * 256 + g * 256 + b) * 0.1


# ── terrain elevation (always Mapbox Terrain-RGB) ─────────────────────────────

def fetch_terrain(lat_min, lat_max, lon_min, lon_max):
    """
    Fetch and stitch Mapbox Terrain-RGB tiles covering the bounding box.
    Returns (elevation_grid, lat_grid, lon_grid).
    """
    zoom = TERRAIN_ZOOM
    x_min, y_max = _lat_lon_to_tile(lat_min, lon_min, zoom)
    x_max, y_min = _lat_lon_to_tile(lat_max, lon_max, zoom)

    tile_cols = x_max - x_min + 1
    tile_rows = y_max - y_min + 1

    stitched = Image.new("RGB", (tile_cols * TILE_SIZE, tile_rows * TILE_SIZE))
    for row, ty in enumerate(range(y_min, y_max + 1)):
        for col, tx in enumerate(range(x_min, x_max + 1)):
            tile = _fetch_tile(tx, ty, zoom)
            stitched.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))

    elevation = _decode_elevation(stitched)

    lat_top, lon_left = _tile_to_lat_lon(x_min, y_min, zoom)
    lat_bot, lon_right = _tile_to_lat_lon(x_max + 1, y_max + 1, zoom)

    lats = np.linspace(lat_top, lat_bot, elevation.shape[0])
    lons = np.linspace(lon_left, lon_right, elevation.shape[1])
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    return elevation, lat_grid, lon_grid


# ── satellite imagery ─────────────────────────────────────────────────────────

def _fetch_esri_tile(x, y, zoom):
    """
    ESRI World Imagery tile pyramid. Note: URL order is z/row/col (y before x),
    unlike Mapbox which uses z/x/y.
    """
    url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")


def _fetch_satellite_esri(lat_min, lat_max, lon_min, lon_max):
    """
    Fetch and stitch ESRI World Imagery tiles at ESRI_SATELLITE_ZOOM.
    Returns (image_array, (lat_top, lat_bot, lon_left, lon_right)) — the actual
    tile bounds, which differ from the requested bbox due to tile-grid snapping.
    """
    zoom = ESRI_SATELLITE_ZOOM
    x_min, y_max = _lat_lon_to_tile(lat_min, lon_min, zoom)
    x_max, y_min = _lat_lon_to_tile(lat_max, lon_max, zoom)

    tile_cols = x_max - x_min + 1
    tile_rows = y_max - y_min + 1

    all_coords = [
        (col, tx, row, ty)
        for row, ty in enumerate(range(y_min, y_max + 1))
        for col, tx in enumerate(range(x_min, x_max + 1))
    ]

    tile_map = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_esri_tile, tx, ty, zoom): (col, row)
                   for col, tx, row, ty in all_coords}
        for future in as_completed(futures):
            col, row = futures[future]
            tile_map[(col, row)] = future.result()

    stitched = Image.new("RGB", (tile_cols * TILE_SIZE, tile_rows * TILE_SIZE))
    for (col, row), tile in tile_map.items():
        stitched.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))

    lat_top, lon_left = _tile_to_lat_lon(x_min, y_min, zoom)
    lat_bot, lon_right = _tile_to_lat_lon(x_max + 1, y_max + 1, zoom)

    return np.array(stitched), (lat_top, lat_bot, lon_left, lon_right)


def _fetch_satellite_mapbox(lat_min, lat_max, lon_min, lon_max):
    """Mapbox satellite tiles (classic /v4/ API). Kept as fallback."""
    zoom = SATELLITE_ZOOM
    x_min, y_max = _lat_lon_to_tile(lat_min, lon_min, zoom)
    x_max, y_min = _lat_lon_to_tile(lat_max, lon_max, zoom)

    tile_cols = x_max - x_min + 1
    tile_rows = y_max - y_min + 1

    stitched = Image.new("RGB", (tile_cols * TILE_SIZE, tile_rows * TILE_SIZE))
    for row, ty in enumerate(range(y_min, y_max + 1)):
        for col, tx in enumerate(range(x_min, x_max + 1)):
            tile = _fetch_tile(tx, ty, zoom, layer="mapbox.satellite")
            stitched.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))

    lat_top, lon_left = _tile_to_lat_lon(x_min, y_min, zoom)
    lat_bot, lon_right = _tile_to_lat_lon(x_max + 1, y_max + 1, zoom)

    return np.array(stitched), (lat_top, lat_bot, lon_left, lon_right)


def _fetch_satellite_esri_export(lat_min, lat_max, lon_min, lon_max, max_px=4096):
    """
    ESRI Export API — single request, aspect-ratio-correct.
    Kept for reference; tile service above gives better quality.
    """
    mid_lat = (lat_min + lat_max) / 2
    lat_m = (lat_max - lat_min) * 111_320
    lon_m = (lon_max - lon_min) * 111_320 * math.cos(math.radians(mid_lat))

    if lon_m >= lat_m:
        w, h = max_px, max(1, int(max_px * lat_m / lon_m))
    else:
        h, w = max_px, max(1, int(max_px * lon_m / lat_m))

    url = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export"
    params = {
        "bbox":    f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "bboxSR":  "4326",
        "imageSR": "4326",
        "size":    f"{w},{h}",
        "format":  "jpg",
        "f":       "image",
    }
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    img = Image.open(BytesIO(response.content)).convert("RGB")
    return np.array(img)


def fetch_satellite_texture(lat_min, lat_max, lon_min, lon_max):
    """Dispatch to the configured satellite source."""
    if SATELLITE_SOURCE == "esri":
        return _fetch_satellite_esri(lat_min, lat_max, lon_min, lon_max)
    return _fetch_satellite_mapbox(lat_min, lat_max, lon_min, lon_max)
