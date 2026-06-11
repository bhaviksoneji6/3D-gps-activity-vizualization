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


def build_human_marker(position: Tuple[float, float, float]) -> pv.PolyData:
    """Stick-figure human silhouette (replaces map-pin cone+sphere)."""
    pos = np.array(position, dtype=float)
    pos[2] += 15.0

    s = 12.0  # scale unit — overall figure height ≈ 72 m, similar visual weight to old pin

    head = pv.Sphere(radius=s, center=pos + [0.0, 0.0, 5.0 * s])

    torso = pv.Cylinder(
        center=pos + [0.0, 0.0, 3.1 * s],
        direction=[0.0, 0.0, 1.0],
        radius=0.45 * s,
        height=2.5 * s,
        resolution=8,
    )

    # Arms angled slightly upward from the shoulder
    arm_l = pv.Cylinder(
        center=pos + [-1.3 * s, 0.0, 3.7 * s],
        direction=[1.0, 0.0, 0.4],
        radius=0.28 * s,
        height=2.0 * s,
        resolution=6,
    )
    arm_r = pv.Cylinder(
        center=pos + [1.3 * s, 0.0, 3.7 * s],
        direction=[-1.0, 0.0, 0.4],
        radius=0.28 * s,
        height=2.0 * s,
        resolution=6,
    )

    # Legs angled slightly apart
    leg_l = pv.Cylinder(
        center=pos + [-0.55 * s, 0.0, 1.1 * s],
        direction=[-0.2, 0.0, 1.0],
        radius=0.32 * s,
        height=2.5 * s,
        resolution=6,
    )
    leg_r = pv.Cylinder(
        center=pos + [0.55 * s, 0.0, 1.1 * s],
        direction=[0.2, 0.0, 1.0],
        radius=0.32 * s,
        height=2.5 * s,
        resolution=6,
    )

    return head.merge(torso).merge(arm_l).merge(arm_r).merge(leg_l).merge(leg_r)


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
