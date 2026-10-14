"""Vector art for the Forge windows: a ship, two smiths, an anvil, a forge fire.

Everything is drawn from polygons, ovals and lines computed here, so the program needs no
image files, no network and no bundled assets -- and the geometry can be asserted in tests
without opening a window. The same helpers draw the full-screen introduction and the small
blacksmith emblem on the widget, so the two cannot drift apart.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence, Tuple

Point = Tuple[float, float]

#: Palette. One place, so the introduction and the emblem stay in the same world.
SPACE_DEEP = "#05070f"
SPACE_MID = "#0b1024"
NEBULA = "#1b2450"
STAR = "#e8eeff"
HULL = "#8f9bb3"
HULL_DARK = "#5b6579"
COCKPIT = "#7fe3ff"
FLAME_INNER = "#ffd27f"
FLAME_OUTER = "#ff7a3c"
SKIN = "#9fe6c8"
SKIN_DARK = "#6fbfa1"
APRON = "#3d2b1f"
IRON = "#c9d3e0"
IRON_HOT = "#ffb457"
FORGE_FIRE = "#ff8a2b"
FORGE_CORE = "#ffe9a8"


# --------------------------------------------------------------------------- starfield
def star_points(
    count: int, width: float, height: float, seed: int = 7
) -> List[Tuple[float, float, float]]:
    """``(x, y, radius)`` for ``count`` stars. Deterministic for a given seed."""

    generator = random.Random(seed)
    stars: List[Tuple[float, float, float]] = []
    for _ in range(max(0, count)):
        stars.append(
            (
                generator.uniform(0.0, float(width)),
                generator.uniform(0.0, float(height)),
                generator.uniform(0.4, 1.9),
            )
        )
    return stars


def drift(stars: Sequence[Tuple[float, float, float]], dy: float, height: float):
    """Move the field downward, wrapping around, which reads as travelling forward."""

    moved = []
    for x, y, radius in stars:
        new_y = (y + dy) % max(1.0, float(height))
        moved.append((x, new_y, radius))
    return moved


# -------------------------------------------------------------------------------- ship
def ship_polygon(cx: float, cy: float, scale: float) -> List[float]:
    """A courier ship seen from the side, as a flat list of coordinates."""

    points = [
        (-1.00, 0.00),
        (-0.55, -0.28),
        (0.35, -0.32),
        (0.95, -0.10),
        (1.00, 0.05),
        (0.45, 0.22),
        (-0.50, 0.26),
    ]
    flat: List[float] = []
    for x, y in points:
        flat.extend((cx + x * scale, cy + y * scale))
    return flat


def ship_flame(cx: float, cy: float, scale: float, flicker: float) -> List[float]:
    """The engine plume; ``flicker`` in 0..1 changes its length."""

    length = (0.9 + 0.5 * max(0.0, min(1.0, flicker))) * scale
    half = 0.16 * scale
    return [
        cx - 1.00 * scale,
        cy,
        cx - 1.00 * scale - length,
        cy - half,
        cx - 1.00 * scale - length * 0.75,
        cy,
        cx - 1.00 * scale - length,
        cy + half,
    ]


# ------------------------------------------------------------------------------- smith
def smith_parts(x: float, y: float, scale: float, swing: float) -> Dict[str, List[float]]:
    """An alien smith: body, head and the hammer arm raised by ``swing`` (0..1).

    ``y`` is the floor line, ``x`` the centre of the figure; everything is measured in
    units of ``scale`` so one function draws both the 900-pixel introduction and the
    60-pixel emblem.
    """

    body = [
        x - 0.34 * scale,
        y,
        x - 0.22 * scale,
        y - 0.78 * scale,
        x + 0.22 * scale,
        y - 0.78 * scale,
        x + 0.34 * scale,
        y,
    ]
    head_radius = 0.19 * scale
    head_centre = (x, y - 0.95 * scale)
    arm_angle = math.radians(150.0 - 105.0 * max(0.0, min(1.0, swing)))
    shoulder = (x + 0.20 * scale, y - 0.70 * scale)
    elbow = (
        shoulder[0] + 0.30 * scale * math.cos(arm_angle),
        shoulder[1] - 0.30 * scale * math.sin(arm_angle),
    )
    hand = (
        elbow[0] + 0.34 * scale * math.cos(arm_angle - 0.25),
        elbow[1] - 0.34 * scale * math.sin(arm_angle - 0.25),
    )
    handle_length = 0.62 * scale
    hammer_head = (
        hand[0] + handle_length * math.cos(arm_angle - 0.25),
        hand[1] - handle_length * math.sin(arm_angle - 0.25),
    )
    return {
        "body": body,
        "head_centre": [head_centre[0], head_centre[1], head_radius],
        "arm": [shoulder[0], shoulder[1], elbow[0], elbow[1], hand[0], hand[1]],
        "handle": [hand[0], hand[1], hammer_head[0], hammer_head[1]],
        "hammer_head": [
            hammer_head[0] - 0.10 * scale,
            hammer_head[1] - 0.07 * scale,
            hammer_head[0] + 0.16 * scale,
            hammer_head[1] + 0.07 * scale,
        ],
    }


def hammer_contact(swing: float) -> bool:
    """True at the bottom of the swing, when the hammer meets the blade."""

    return swing <= 0.02


# ------------------------------------------------------------------------------- anvil
def anvil_polygon(x: float, y: float, scale: float) -> List[float]:
    """A classic anvil silhouette, horn to the left, standing on its base."""

    points = [
        (-0.60, 0.00),
        (0.60, 0.00),
        (0.60, -0.10),
        (0.26, -0.10),
        (0.26, -0.30),
        (0.42, -0.34),
        (-0.34, -0.34),
        (-0.62, -0.26),
        (-0.40, -0.18),
        (-0.26, -0.10),
        (-0.60, -0.10),
    ]
    flat: List[float] = []
    for px, py in points:
        flat.extend((x + px * scale, y + py * scale))
    return flat


# ----------------------------------------------------------------------- forge and fire
def fire_polygon(x: float, y: float, scale: float, phase: float) -> List[float]:
    """A flame whose tongue height follows ``phase`` (radians, animated)."""

    height = scale * (0.85 + 0.22 * math.sin(phase))
    width = scale * 0.42
    return [
        x - width,
        y,
        x - width * 0.55,
        y - height * 0.45,
        x - width * 0.20,
        y - height * 0.75,
        x,
        y - height,
        x + width * 0.22,
        y - height * 0.70,
        x + width * 0.58,
        y - height * 0.40,
        x + width,
        y,
    ]


def sparks(count: int, x: float, y: float, phase: float, seed: int = 11):
    """Sparks flying off the anvil; deterministic in ``seed`` and ``phase``."""

    generator = random.Random(seed + int(phase * 1000))
    result = []
    for _ in range(max(0, count)):
        angle = generator.uniform(math.radians(20), math.radians(160))
        distance = generator.uniform(0.2, 1.0)
        result.append(
            (
                x + math.cos(angle) * distance * 90.0,
                y - math.sin(angle) * distance * 60.0,
                generator.uniform(1.0, 2.6),
            )
        )
    return result


# ---------------------------------------------------------------------- canvas drawing
def fill_polygon(canvas, points: Sequence[float], colour: str, outline: str = "") -> int:
    return canvas.create_polygon(*points, fill=colour, outline=outline or colour)


def draw_starfield(canvas, stars, colour: str = STAR) -> None:
    for x, y, radius in stars:
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=colour, outline="")


def draw_ship(canvas, cx: float, cy: float, scale: float, flicker: float) -> None:
    fill_polygon(canvas, ship_flame(cx, cy, scale, flicker), FLAME_OUTER)
    fill_polygon(canvas, ship_polygon(cx, cy, scale), HULL, HULL_DARK)
    canvas.create_oval(
        cx + 0.35 * scale,
        cy - 0.22 * scale,
        cx + 0.80 * scale,
        cy + 0.02 * scale,
        fill=COCKPIT,
        outline="",
    )


def draw_forge(canvas, x: float, y: float, scale: float, phase: float) -> None:
    """The distant furnace: a stone arch with fire inside it."""

    canvas.create_rectangle(
        x - 0.9 * scale,
        y - 1.1 * scale,
        x + 0.9 * scale,
        y,
        fill="#2a2118",
        outline="#463726",
    )
    fill_polygon(canvas, fire_polygon(x, y - 0.05 * scale, scale, phase), FORGE_FIRE)
    canvas.create_oval(
        x - 0.28 * scale,
        y - 0.62 * scale,
        x + 0.28 * scale,
        y - 0.12 * scale,
        fill=FORGE_CORE,
        outline="",
    )


def _mirror(points: Sequence[float], centre_x: float, facing: int) -> List[float]:
    """Mirror an ``x, y, x, y`` list around ``centre_x`` when ``facing`` is negative."""

    if facing >= 0:
        return list(points)
    mirrored: List[float] = []
    for index in range(0, len(points) - 1, 2):
        mirrored.append(centre_x + (points[index] - centre_x) * facing)
        mirrored.append(points[index + 1])
    return mirrored


def draw_smith(canvas, x: float, y: float, scale: float, swing: float, facing: int = 1) -> None:
    parts = smith_parts(x, y, scale, swing)
    fill_polygon(canvas, _mirror(parts["body"], x, facing), SKIN_DARK)
    canvas.create_oval(
        parts["head_centre"][0] - parts["head_centre"][2],
        parts["head_centre"][1] - parts["head_centre"][2],
        parts["head_centre"][0] + parts["head_centre"][2],
        parts["head_centre"][1] + parts["head_centre"][2],
        fill=SKIN,
        outline=SKIN_DARK,
    )
    canvas.create_line(
        *_mirror(parts["arm"], x, facing), fill=SKIN, width=max(2, int(0.10 * scale))
    )
    canvas.create_line(
        *_mirror(parts["handle"], x, facing), fill=APRON, width=max(2, int(0.07 * scale))
    )
    fill_polygon(canvas, _mirror(parts["hammer_head"], x, facing), IRON, HULL_DARK)


def draw_emblem(canvas, size: float, *, background: str = SPACE_MID) -> None:
    """The small blacksmith mark: two smiths, one anvil, a fire behind them.

    Drawn to fit a square of ``size`` pixels, which is what the widget shows in its
    header.
    """

    canvas.create_rectangle(0, 0, size, size, fill=background, outline="")
    horizon = size * 0.86
    draw_forge(canvas, size * 0.50, horizon, size * 0.15, phase=0.6)
    fill_polygon(canvas, anvil_polygon(size * 0.50, horizon, size * 0.30), IRON, HULL_DARK)
    draw_smith(canvas, size * 0.24, horizon, size * 0.36, swing=0.65, facing=1)
    draw_smith(canvas, size * 0.76, horizon, size * 0.36, swing=0.10, facing=-1)
    for x, y, radius in sparks(9, size * 0.50, horizon - size * 0.10, 1.2):
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=IRON_HOT, outline="")
