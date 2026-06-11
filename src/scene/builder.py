import numpy as np
import pyvista as pv
from typing import List, Tuple, Optional


def build_ghost_track(
    coords: List[Tuple[float, float, float]],
    tube_radius: float = 5.0,
) -> pv.PolyData:
    """Full route as a dim background reference tube."""
    points = np.array(coords, dtype=float)
    points[:, 2] += 8.0

    n = len(points)
    lines = np.column_stack([
        np.full(n - 1, 2),
        np.arange(n - 1),
        np.arange(1, n),
    ]).ravel()

    poly = pv.PolyData()
    poly.points = points
    poly.lines = lines
    return poly.tube(radius=tube_radius)


def build_trail_segment(
    coords: List[Tuple[float, float, float]],
    end_idx: int,
    tube_radius: float = 8.0,
) -> pv.PolyData:
    """Active trail from point 0 to end_idx, colored by progress."""
    end_idx = max(2, end_idx)
    points = np.array(coords[:end_idx], dtype=float)
    points[:, 2] += 10.0

    n = len(points)
    lines = np.column_stack([
        np.full(n - 1, 2),
        np.arange(n - 1),
        np.arange(1, n),
    ]).ravel()

    poly = pv.PolyData()
    poly.points = points
    poly.lines = lines

    trail = poly.tube(radius=tube_radius)
    trail["progress"] = np.linspace(0, 1, trail.n_points)
    return trail


def build_arrow_marker(
    position: Tuple[float, float, float],
    direction: np.ndarray,
) -> pv.PolyData:
    """
    Google Maps-style chevron arrow: flat triangle with a V-notch at the tail,
    extruded slightly for 3D depth, oriented to the direction of travel.
    """
    pos = np.array(position, dtype=float)
    pos[2] += 12.0

    fwd = np.array(direction, dtype=float)
    fwd[2] = 0.0
    norm = np.linalg.norm(fwd)
    fwd   = fwd / norm if norm > 1e-6 else np.array([1.0, 0.0, 0.0])
    right = np.array([-fwd[1], fwd[0], 0.0])   # 90° left of fwd in XY
    z_up  = np.array([0.0, 0.0, 1.0])

    size  = 40.0              # overall size in metres
    thick = size * 0.07       # extrusion height

    # Six vertices in local (right, fwd) space, matching Google Maps chevron
    shape = np.array([
        ( 0.00,  1.00),   # 0: tip
        ( 0.50,  0.22),   # 1: right shoulder
        ( 0.24, -0.62),   # 2: right leg tip
        ( 0.00, -0.18),   # 3: center notch (concave)
        (-0.24, -0.62),   # 4: left leg tip
        (-0.50,  0.22),   # 5: left shoulder
    ])

    bot = np.array([pos + v[0] * right * size + v[1] * fwd * size for v in shape])
    top = bot + z_up * thick
    pts = np.vstack([bot, top])   # (12, 3)

    n = len(shape)   # 6

    # Triangulate the concave hexagon (bottom face, top face, sides)
    b_tris = [[0, 1, 5], [1, 3, 5], [1, 2, 3], [5, 3, 4]]
    t_tris = [[a + n, c + n, b_ + n] for a, b_, c in b_tris]   # reversed winding
    s_tris = []
    for i in range(n):
        j = (i + 1) % n
        s_tris += [[i, j, j + n], [i, j + n, i + n]]

    faces = np.array(
        [[3, a, b_, c] for a, b_, c in b_tris + t_tris + s_tris]
    ).ravel()

    mesh = pv.PolyData(pts, faces)
    mesh.compute_normals(auto_orient_normals=True, inplace=True)
    return mesh


def build_end_marker(
    coords: List[Tuple[float, float, float]],
    radius: float = 20.0,
) -> pv.PolyData:
    end = np.array(coords[-1])
    end[2] += 10.0
    return pv.Sphere(radius=radius, center=end)


def assemble_scene_video(
    terrain_mesh: Optional[pv.StructuredGrid],
    satellite_texture: Optional[pv.Texture],
    ghost_track: pv.PolyData,
    end_marker: pv.PolyData,
) -> pv.Plotter:
    plotter = pv.Plotter(off_screen=True, window_size=(1920, 1080))

    if terrain_mesh is not None:
        if satellite_texture is not None:
            plotter.add_mesh(terrain_mesh, texture=satellite_texture)
        else:
            plotter.add_mesh(terrain_mesh, scalars="elevation", cmap="terrain",
                             show_scalar_bar=False)

    plotter.add_mesh(ghost_track, color="white", opacity=0.15, name="ghost")
    plotter.add_mesh(end_marker, color="red")
    plotter.set_background("black")
    return plotter
