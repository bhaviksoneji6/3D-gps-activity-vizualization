import numpy as np
import pyvista as pv
from typing import Optional, Tuple


def build_terrain_mesh(
    elevation: np.ndarray,
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    origin_lat: float,
    origin_lon: float,
    elevation_scale: float = 1.2,
    texture_image: Optional[np.ndarray] = None,
    texture_bounds: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[pv.StructuredGrid, Optional[pv.Texture]]:
    R = 6_371_000

    x = R * np.radians(lon_grid - origin_lon) * np.cos(np.radians(origin_lat))
    y = R * np.radians(lat_grid - origin_lat)
    z = elevation * elevation_scale

    # StructuredGrid requires 3D arrays — add a depth dimension for a surface
    grid = pv.StructuredGrid(
        x[:, :, np.newaxis],
        y[:, :, np.newaxis],
        z[:, :, np.newaxis],
    )

    texture = None
    if texture_image is not None:
        if texture_bounds is not None:
            # Compute UV per-vertex using actual satellite tile bounds.
            # This accounts for tile-grid snapping differences between terrain and
            # satellite zoom levels — without this, the texture is offset by up to
            # one tile width (~200 m at zoom 17).
            # NOTE: pv.Texture flips the image array vertically during loading, so
            # v=0 maps to the south edge of the image. Invert vv accordingly.
            lat_top, lat_bot, lon_left, lon_right = texture_bounds
            uu = (lon_grid - lon_left) / (lon_right - lon_left)
            vv = 1.0 - (lat_grid - lat_top) / (lat_bot - lat_top)
            uu = np.clip(uu, 0.0, 1.0)
            vv = np.clip(vv, 0.0, 1.0)
        else:
            h, w = elevation.shape
            uu, vv = np.meshgrid(np.linspace(0, 1, w), np.linspace(1, 0, h))

        # Points are stored Fortran-order inside StructuredGrid
        tex_coords = np.column_stack([uu.ravel("F"), vv.ravel("F")])

        try:
            grid.active_texture_coordinates = tex_coords
        except AttributeError:
            grid.t_coords = tex_coords

        texture = pv.Texture(texture_image)
    else:
        grid["elevation"] = z.ravel(order="F")

    return grid, texture
