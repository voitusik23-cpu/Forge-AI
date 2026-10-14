"""Render the Forge AI opening as real imagery: procedural space, then the forge inside.

This is a build-time tool, not part of the program. It needs numpy and Pillow, which the
project virtual environment does not carry (the bundled DSH runtime does: Python 3.12 with
numpy 2.3.5 and Pillow 12.3.0), so it is run like this:

    "C:\\Users\\User\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe" \\
        tools/render_intro.py --scene space --time 6.0 --out .forge\\frames\\space-06.png

Everything is computed: no image files, no network, no AI model. The look comes from
value-noise nebulae, procedural spiral galaxies, additive starfields, a scanline polygon
rasteriser for the hull and the figures, separable bloom, then film grain, chromatic
aberration, vignette and letterbox bars.
"""

from __future__ import annotations

import argparse
import math
from typing import List, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter

RGB = np.ndarray


# ------------------------------------------------------------------------------ noise
def smooth_noise(height: int, width: int, scale: int, seed: int) -> RGB:
    """Smooth noise in 0..1, produced by upscaling a tiny random field bicubically."""

    rng = np.random.default_rng(seed)
    small = rng.random((max(2, height // scale), max(2, width // scale))).astype(np.float32)
    image = Image.fromarray((small * 255).astype(np.uint8), mode="L")
    image = image.resize((width, height), Image.BICUBIC)
    return np.asarray(image, dtype=np.float32) / 255.0


def fractal_noise(height: int, width: int, seed: int, octaves: int = 5) -> RGB:
    """Sum of octaves: the cheap stand-in for a real fBm, and enough for nebulae."""

    total = np.zeros((height, width), dtype=np.float32)
    weight = 0.0
    for index in range(octaves):
        scale = 2 ** (octaves - index + 2)
        total += smooth_noise(height, width, scale, seed + index * 977) / (index + 1.0)
        weight += 1.0 / (index + 1.0)
    return total / max(weight, 1e-6)


# ------------------------------------------------------------------------------- blur
def blur(image: RGB, radius: int) -> RGB:
    """Separable box blur, three passes: fast, and close enough to a Gaussian for glow."""

    if radius < 1:
        return image
    result = image.astype(np.float32)
    for _ in range(3):
        result = _blur_axis(result, radius, axis=0)
        result = _blur_axis(result, radius, axis=1)
    return result


def _blur_axis(array: RGB, radius: int, axis: int) -> RGB:
    count = array.shape[axis]
    padding = [(0, 0)] * array.ndim
    padding[axis] = (radius, radius)
    padded = np.pad(array, padding, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.float32)
    zeros_shape = list(cumulative.shape)
    zeros_shape[axis] = 1
    cumulative = np.concatenate(
        [np.zeros(zeros_shape, dtype=np.float32), cumulative], axis=axis
    )
    window = 2 * radius + 1
    high = [slice(None)] * array.ndim
    low = [slice(None)] * array.ndim
    high[axis] = slice(window, window + count)
    low[axis] = slice(0, count)
    return (cumulative[tuple(high)] - cumulative[tuple(low)]) / window


def bloom(rgb: RGB, threshold: float = 0.62, radius: int = 18, strength: float = 0.85) -> RGB:
    """Additive glow from the bright areas: this is what makes light look like light."""

    luminance = rgb.max(axis=2, keepdims=True)
    excess = np.clip(luminance - threshold, 0.0, None) / max(1e-6, 1.0 - threshold)
    bright = rgb * excess
    return rgb + blur(bright, radius) * strength


# ------------------------------------------------------------------- polygon filling
def fill_polygon(
    canvas: RGB, points: Sequence[Tuple[float, float]], colour: Sequence[float], alpha: float = 1.0
) -> None:
    """Rasterise a filled polygon by scanlines, in place.

    Written here because neither numpy nor the standard library draws polygons, and the
    ship, the smiths and the anvil are all polygons.
    """

    if len(points) < 3:
        return
    height, width = canvas.shape[:2]
    rows: dict[int, List[float]] = {}
    for index in range(len(points)):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % len(points)]
        if y0 == y1:
            continue
        step = 1.0 if y1 > y0 else -1.0
        start = int(math.ceil(min(y0, y1)))
        end = int(math.floor(max(y0, y1)))
        for y in range(max(0, start), min(height, end + 1)):
            if y < min(y0, y1) or y > max(y0, y1):
                continue
            t = (y - y0) / (y1 - y0)
            if t < 0.0 or t > 1.0:
                continue
            rows.setdefault(y, []).append(x0 + t * (x1 - x0))
        del step
    for y, crossings in rows.items():
        crossings.sort()
        for index in range(0, len(crossings) - 1, 2):
            left = int(max(0, math.ceil(crossings[index])))
            right = int(min(width - 1, math.floor(crossings[index + 1])))
            if right < left:
                continue
            target = canvas[y, left : right + 1]
            colour_array = np.asarray(colour, dtype=np.float32)
            canvas[y, left : right + 1] = target * (1.0 - alpha) + colour_array * alpha


# ----------------------------------------------------------------------------- scenes
def space_background(height: int, width: int, seed: int = 4) -> RGB:
    """Nebulae, galaxy clusters and a three-layer starfield."""

    canvas = np.zeros((height, width, 3), dtype=np.float32)

    # Nebula: two fractal fields, tinted, and pushed into the corners so the middle stays
    # dark enough for the ship to read.
    field = fractal_noise(height, width, seed, octaves=5)
    detail = fractal_noise(height, width, seed + 31, octaves=4)
    cloud = np.clip(field * 1.35 - 0.35, 0.0, 1.0) ** 1.6
    violet = np.array([0.16, 0.10, 0.42], dtype=np.float32)
    teal = np.array([0.05, 0.30, 0.38], dtype=np.float32)
    canvas += cloud[..., None] * violet * (0.9 + 0.5 * detail[..., None])
    canvas += (cloud**2.2)[..., None] * teal * 0.8

    # Galaxy clusters: spirals at different scales and angles, brightest in the distance.
    galaxies = (
        (0.20, 0.22, 190.0, 0.6, 0.42, 11),
        (0.82, 0.16, 130.0, -0.9, 0.34, 23),
        (0.68, 0.78, 240.0, 0.25, 0.30, 37),
        (0.36, 0.62, 96.0, 1.4, 0.22, 53),
        (0.93, 0.52, 74.0, -0.4, 0.18, 71),
    )
    for cx_f, cy_f, radius, angle, brightness, galaxy_seed in galaxies:
        canvas += spiral_galaxy(
            height, width, cx_f * width, cy_f * height, radius, angle, brightness, galaxy_seed
        )

    # Starfield in three parallax layers.
    canvas += star_layer(height, width, 1500, seed + 3, brightness=0.55, radius=0.8)
    canvas += star_layer(height, width, 420, seed + 5, brightness=0.95, radius=1.1)
    canvas += star_layer(height, width, 90, seed + 7, brightness=1.5, radius=1.9)
    return canvas


def spiral_galaxy(
    height: int,
    width: int,
    cx: float,
    cy: float,
    radius: float,
    angle: float,
    brightness: float,
    seed: int,
) -> RGB:
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dx = xx - cx
    dy = yy - cy
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    along = dx * cos_a + dy * sin_a
    across = (-dx * sin_a + dy * cos_a) / 0.42  # the disk is seen at a shallow tilt
    distance = np.sqrt(along**2 + across**2) + 1e-3
    theta = np.arctan2(across, along)

    core = np.exp(-((distance / (0.14 * radius)) ** 2))
    disk = np.exp(-(distance / radius) ** 0.85)
    arms = 0.55 + 0.45 * np.cos(2.0 * theta - 3.1 * np.log(distance / radius + 0.12))
    dust = 1.0 - 0.45 * np.exp(-(((distance / radius) - 0.55) ** 2) / 0.02)
    intensity = (core * 1.5 + disk * arms * 0.85) * dust * brightness

    rng = np.random.default_rng(seed)
    tilt = rng.uniform(0.9, 1.1)
    warm = np.array([1.00 * tilt, 0.93, 0.80], dtype=np.float32)
    cool = np.array([0.62, 0.74, 1.00], dtype=np.float32)
    blend = np.clip(distance / (0.75 * radius), 0.0, 1.0)[..., None]
    return intensity[..., None] * (warm * (1.0 - blend) + cool * blend)


def star_layer(
    height: int, width: int, count: int, seed: int, brightness: float, radius: float
) -> RGB:
    rng = np.random.default_rng(seed)
    layer = np.zeros((height, width, 3), dtype=np.float32)
    xs = rng.integers(0, width, count)
    ys = rng.integers(0, height, count)
    magnitudes = rng.power(2.6, count).astype(np.float32)
    colours = rng.normal(1.0, 0.12, (count, 3)).astype(np.float32)
    colours[:, 0] *= 1.02
    colours[:, 2] *= 1.06
    for index in range(count):
        x = int(xs[index])
        y = int(ys[index])
        value = float(magnitudes[index]) * brightness
        layer[y, x] = np.maximum(layer[y, x], np.clip(colours[index] * value, 0.0, 4.0))
        if radius > 1.4 and value > 0.6:
            halo = value * 0.16
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                px, py = x + ox, y + oy
                if 0 <= px < width and 0 <= py < height:
                    layer[py, px] = np.maximum(layer[py, px], colours[index] * halo)
    return layer


def ship(canvas: RGB, cx: float, cy: float, scale: float, glow: float = 1.0) -> None:
    """A courier ship: hull, panels, windows and an engine plume."""

    height, width = canvas.shape[:2]
    hull = [
        (-1.00, 0.00),
        (-0.72, -0.18),
        (-0.30, -0.34),
        (0.42, -0.30),
        (0.86, -0.14),
        (1.00, 0.02),
        (0.62, 0.20),
        (0.10, 0.30),
        (-0.62, 0.24),
        (-0.92, 0.12),
    ]
    points = [(cx + x * scale, cy + y * scale) for x, y in hull]

    # Engine plume first, so the hull overlaps it.
    plume = [
        (cx - 1.00 * scale, cy),
        (cx - (1.00 + 1.6 * glow) * scale, cy - 0.10 * scale),
        (cx - (1.00 + 2.4 * glow) * scale, cy),
        (cx - (1.00 + 1.4 * glow) * scale, cy + 0.11 * scale),
    ]
    fill_polygon(canvas, plume, (1.00, 0.55, 0.22), alpha=0.75)
    fill_polygon(
        canvas,
        [(cx - 1.0 * scale, cy), (cx - (1.0 + 1.0 * glow) * scale, cy - 0.05 * scale),
         (cx - (1.0 + 1.0 * glow) * scale, cy + 0.06 * scale)],
        (1.00, 0.92, 0.70),
        alpha=0.9,
    )

    fill_polygon(canvas, points, (0.42, 0.46, 0.55), alpha=1.0)
    # Upper plating catches the starlight, lower plating falls away: cheap volume.
    fill_polygon(
        canvas,
        [(cx + x * scale, cy + y * scale) for x, y in hull[:5]],
        (0.66, 0.71, 0.82),
        alpha=0.85,
    )
    fill_polygon(
        canvas,
        [(cx + x * scale, cy + y * scale) for x, y in hull[5:]],
        (0.24, 0.27, 0.34),
        alpha=0.85,
    )
    # Cockpit.
    fill_polygon(
        canvas,
        [
            (cx + 0.34 * scale, cy - 0.20 * scale),
            (cx + 0.62 * scale, cy - 0.16 * scale),
            (cx + 0.66 * scale, cy - 0.02 * scale),
            (cx + 0.30 * scale, cy - 0.06 * scale),
        ],
        (0.52, 0.92, 1.00),
        alpha=0.95,
    )
    # A row of lit windows along the flank.
    count = max(3, int(scale / 26))
    for index in range(count):
        fraction = index / max(1, count - 1)
        wx = cx + (-0.55 + 0.95 * fraction) * scale
        wy = cy + (0.05 + 0.05 * math.sin(index * 1.7)) * scale
        size = max(1.0, 0.022 * scale)
        fill_polygon(
            canvas,
            [(wx, wy), (wx + size, wy), (wx + size, wy + size * 1.4), (wx, wy + size * 1.4)],
            (1.00, 0.86, 0.55),
            alpha=0.9,
        )
    del height, width


def forge_interior(canvas: RGB, time_s: float, scale: float = 1.0) -> None:
    """The inside: two smiths, one anvil, a blade, and a furnace in the distance."""

    height, width = canvas.shape[:2]
    horizon = height * 0.80
    flicker = 0.85 + 0.15 * math.sin(time_s * 7.3) + 0.06 * math.sin(time_s * 17.1)

    # Room: floor, back wall, and a warm pool of light from the furnace.
    canvas[:, :] *= 0.12
    fill_polygon(canvas, [(0, horizon), (width, horizon), (width, height), (0, height)],
                 (0.10, 0.075, 0.06), alpha=1.0)
    fill_polygon(canvas, [(0, 0), (width, 0), (width, horizon), (0, horizon)],
                 (0.055, 0.045, 0.05), alpha=1.0)

    # The furnace: an arch with a fire that breathes.
    fx, fy = width * 0.83, horizon + 6
    for step in range(26, 0, -1):
        radius = step * 7.5 * scale
        alpha = 0.055 * (1.0 - step / 26.0) + 0.02
        fill_polygon(
            canvas,
            [(fx - radius, fy), (fx, fy - radius * 1.25), (fx + radius, fy)],
            (1.0 * flicker, 0.52 * flicker, 0.16 * flicker),
            alpha=alpha,
        )
    fill_polygon(
        canvas,
        [(fx - 46 * scale, fy), (fx - 22 * scale, fy - 92 * scale),
         (fx + 16 * scale, fy - 78 * scale), (fx + 40 * scale, fy)],
        (1.0, 0.78, 0.34),
        alpha=0.95,
    )
    fill_polygon(
        canvas,
        [(fx - 20 * scale, fy), (fx - 6 * scale, fy - 58 * scale),
         (fx + 8 * scale, fy - 50 * scale), (fx + 18 * scale, fy)],
        (1.0, 0.96, 0.78),
        alpha=0.95,
    )

    # Anvil, then the blade across it, white hot where the hammer lands.
    ax, ay = width * 0.45, horizon + 12
    fill_polygon(
        canvas,
        [(ax - 92 * scale, ay), (ax + 92 * scale, ay), (ax + 92 * scale, ay - 16 * scale),
         (ax + 34 * scale, ay - 16 * scale), (ax + 34 * scale, ay - 44 * scale),
         (ax + 58 * scale, ay - 52 * scale), (ax - 52 * scale, ay - 52 * scale),
         (ax - 88 * scale, ay - 40 * scale), (ax - 60 * scale, ay - 28 * scale),
         (ax - 34 * scale, ay - 16 * scale), (ax - 92 * scale, ay - 16 * scale)],
        (0.52, 0.56, 0.62),
        alpha=1.0,
    )
    strike = max(0.0, 1.0 - (time_s * 1.1) % 1.0)
    heat = 0.35 + 0.65 * (1.0 - strike)
    fill_polygon(
        canvas,
        [(ax - 76 * scale, ay - 62 * scale), (ax + 76 * scale, ay - 70 * scale),
         (ax + 70 * scale, ay - 60 * scale), (ax - 78 * scale, ay - 52 * scale)],
        (1.0, 0.55 + 0.35 * heat, 0.25 + 0.5 * heat),
        alpha=0.98,
    )

    # Sparks from the strike, thrown up and to the left.
    rng = np.random.default_rng(int(time_s * 60) + 5)
    if strike < 0.25:
        for _ in range(90):
            angle = rng.uniform(math.radians(15), math.radians(150))
            distance = rng.uniform(0.2, 1.4)
            sx = ax + math.cos(angle) * distance * 150 * scale
            sy = ay - 60 * scale - math.sin(angle) * distance * 110 * scale
            size = rng.uniform(1.0, 2.4)
            if 0 <= int(sy) < height and 0 <= int(sx) < width:
                fill_polygon(
                    canvas,
                    [(sx, sy), (sx + size, sy), (sx + size, sy + size), (sx, sy + size)],
                    (1.0, 0.72, 0.30),
                    alpha=0.95,
                )

    # Two smiths in silhouette with a rim light from the fire: the rim is what sells it.
    _smith(canvas, width * 0.30, horizon + 12, 150 * scale, strike, facing=1, rim=(1.0, 0.62, 0.28))
    _smith(canvas, width * 0.62, horizon + 12, 150 * scale, 0.08, facing=-1, rim=(1.0, 0.55, 0.24))


def _smith(
    canvas: RGB,
    x: float,
    floor_y: float,
    scale: float,
    swing: float,
    facing: int,
    rim: Sequence[float],
) -> None:
    """One alien smith: body, head, arm and hammer, lit from the furnace side."""

    body = [
        (x - 0.34 * scale, floor_y),
        (x - 0.24 * scale, floor_y - 0.80 * scale),
        (x + 0.24 * scale, floor_y - 0.80 * scale),
        (x + 0.34 * scale, floor_y),
    ]
    if facing < 0:
        body = [(2 * x - bx, by) for bx, by in body]
    fill_polygon(canvas, body, (0.05, 0.06, 0.07), alpha=1.0)
    # Rim light along the furnace side.
    rim_side = x + facing * 0.34 * scale
    fill_polygon(
        canvas,
        [
            (rim_side, floor_y),
            (rim_side - facing * 0.07 * scale, floor_y - 0.80 * scale),
            (rim_side - facing * 0.13 * scale, floor_y - 0.78 * scale),
            (rim_side - facing * 0.05 * scale, floor_y),
        ],
        rim,
        alpha=0.85,
    )
    head = (x, floor_y - 0.98 * scale)
    head_radius = 0.20 * scale
    steps = 18
    for index in range(steps):
        angle = math.pi * index / steps
        px = head[0] + math.cos(angle) * head_radius
        py = head[1] - math.sin(angle) * head_radius * 1.15
        width = max(1.0, head_radius * 0.16)
        fill_polygon(
            canvas,
            [(px, py), (px + width, py), (px + width, py + head_radius * 0.7), (px, py + head_radius * 0.7)],
            (0.05, 0.06, 0.07),
            alpha=1.0,
        )
    fill_polygon(
        canvas,
        [
            (head[0] + facing * head_radius * 0.45, head[1] - head_radius * 0.55),
            (head[0] + facing * head_radius * 1.02, head[1] - head_radius * 0.15),
            (head[0] + facing * head_radius * 0.95, head[1] + head_radius * 0.35),
            (head[0] + facing * head_radius * 0.40, head[1] + head_radius * 0.55),
        ],
        rim,
        alpha=0.75,
    )
    # Arm and hammer: the angle follows the strike.
    shoulder = (x + facing * 0.22 * scale, floor_y - 0.72 * scale)
    arm_angle = math.radians(150.0 - 105.0 * max(0.0, min(1.0, swing)))
    elbow = (
        shoulder[0] + facing * 0.30 * scale * math.cos(arm_angle),
        shoulder[1] - 0.30 * scale * math.sin(arm_angle),
    )
    hand = (
        elbow[0] + facing * 0.34 * scale * math.cos(arm_angle - 0.25),
        elbow[1] - 0.34 * scale * math.sin(arm_angle - 0.25),
    )
    for start, end, thickness, colour in (
        (shoulder, elbow, 0.11, (0.06, 0.07, 0.08)),
        (elbow, hand, 0.10, (0.06, 0.07, 0.08)),
        (hand, (hand[0] + facing * 0.62 * scale * math.cos(arm_angle - 0.25),
                hand[1] - 0.62 * scale * math.sin(arm_angle - 0.25)), 0.05, (0.30, 0.20, 0.13)),
    ):
        fill_polygon(
            canvas,
            _thick_line(start, end, thickness * scale),
            colour,
            alpha=1.0,
        )
    head_x = hand[0] + facing * 0.66 * scale * math.cos(arm_angle - 0.25)
    head_y = hand[1] - 0.66 * scale * math.sin(arm_angle - 0.25)
    fill_polygon(
        canvas,
        [
            (head_x - 0.16 * scale, head_y - 0.09 * scale),
            (head_x + 0.16 * scale, head_y - 0.06 * scale),
            (head_x + 0.15 * scale, head_y + 0.09 * scale),
            (head_x - 0.17 * scale, head_y + 0.06 * scale),
        ],
        (0.62, 0.66, 0.72),
        alpha=1.0,
    )


def _thick_line(
    start: Tuple[float, float], end: Tuple[float, float], thickness: float
) -> List[Tuple[float, float]]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * thickness, dx / length * thickness
    return [
        (start[0] + nx, start[1] + ny),
        (end[0] + nx, end[1] + ny),
        (end[0] - nx, end[1] - ny),
        (start[0] - nx, start[1] - ny),
    ]


# ------------------------------------------------------------------------- post-process
def post_process(rgb: RGB, time_s: float, letterbox: bool = True, grain: float = 0.035) -> RGB:
    height, width = rgb.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = width / 2.0, height / 2.0
    radius = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
    vignette = np.clip(1.0 - 0.32 * radius**2.4, 0.0, 1.0)
    result = rgb * vignette[..., None]

    # Chromatic aberration: shift red and blue by a pixel at the edges.
    shift = 2
    result[..., 0] = np.roll(result[..., 0], shift, axis=1)
    result[..., 2] = np.roll(result[..., 2], -shift, axis=1)

    rng = np.random.default_rng(int(time_s * 240) + 17)
    result += rng.normal(0.0, grain, result.shape).astype(np.float32)

    if letterbox:
        bar = int(height * 0.055)
        result[:bar, :, :] = 0.0
        result[-bar:, :, :] = 0.0
    return np.clip(result, 0.0, 1.0)


def wordmark(rgb: RGB, progress: float) -> RGB:
    """The title sweeps towards the camera with a faint trail behind it."""

    height, width = rgb.shape[:2]
    canvas = rgb.copy()
    scale = 1.0 + 2.6 * (1.0 - progress) ** 2
    centre_y = height * (0.46 + 0.05 * progress)
    bar_height = max(4.0, 26.0 * scale)
    bar_width = width * (0.40 + 0.12 * progress) * min(1.0, scale)
    left = width / 2 - bar_width / 2
    for index in range(7):
        ghost = index * 0.012 * width
        alpha = 0.16 * (1.0 - index / 7.0) * progress
        fill_polygon(
            canvas,
            [
                (left - ghost, centre_y - bar_height / 2),
                (left + bar_width - ghost, centre_y - bar_height / 2),
                (left + bar_width - ghost, centre_y + bar_height / 2),
                (left - ghost, centre_y + bar_height / 2),
            ],
            (0.98, 0.86, 0.62),
            alpha=alpha,
        )
    fill_polygon(
        canvas,
        [
            (left, centre_y - bar_height / 2),
            (left + bar_width, centre_y - bar_height / 2),
            (left + bar_width, centre_y + bar_height / 2),
            (left, centre_y + bar_height / 2),
        ],
        (1.0, 0.95, 0.86),
        alpha=0.92 * progress,
    )
    underline_y = centre_y + bar_height * 1.35
    fill_polygon(
        canvas,
        [
            (width * 0.30, underline_y),
            (width * 0.70 * progress + width * 0.30, underline_y),
            (width * 0.70 * progress + width * 0.30, underline_y + 3.0 * scale),
            (width * 0.30, underline_y + 3.0 * scale),
        ],
        (0.95, 0.70, 0.35),
        alpha=0.85 * progress,
    )
    return canvas


def render(scene: str, time_s: float, height: int, width: int, slogan: bool = False) -> RGB:
    if scene == "space":
        canvas = space_background(height, width)
        approach = min(1.0, max(0.0, (time_s - 2.5) / 7.5))
        scale = 90.0 + 230.0 * approach**1.6
        cx = -240.0 + (width * 0.56 + 240.0) * min(1.0, approach * 1.15)
        cy = height * 0.46 - 34.0 * approach + 6.0 * math.sin(time_s * 0.9)
        ship(canvas, cx, cy, scale, glow=0.8 + 0.4 * abs(math.sin(time_s * 5.0)))
    elif scene == "dive":
        canvas = space_background(height, width) * (1.0 - min(1.0, time_s / 3.0))
        centre = (width / 2, height / 2)
        for index in range(6):
            spread = 60.0 + (time_s / 3.0 * 1.3 + index * 0.17) * width * 0.62
            fill_polygon(
                canvas,
                [(centre[0] - spread, centre[1] - spread * 0.60),
                 (centre[0] + spread, centre[1] - spread * 0.60),
                 (centre[0] + spread, centre[1] + spread * 0.60),
                 (centre[0] - spread, centre[1] + spread * 0.60)],
                (0.05, 0.06, 0.08),
                alpha=0.22,
            )
        radius = 26.0 + 150.0 * min(1.0, time_s / 3.0)
        fill_polygon(
            canvas,
            [(centre[0] - radius, centre[1]), (centre[0], centre[1] - radius),
             (centre[0] + radius, centre[1]), (centre[0], centre[1] + radius)],
            (0.42, 0.86, 1.0),
            alpha=0.55,
        )
    elif scene == "forge":
        canvas = np.zeros((height, width, 3), dtype=np.float32)
        forge_interior(canvas, time_s)
    else:
        raise SystemExit(f"unknown scene: {scene}")

    rgb = bloom(canvas, threshold=0.60, radius=max(6, height // 42), strength=0.95)
    if slogan:
        rgb = wordmark(rgb, min(1.0, max(0.0, time_s / 2.0)))
        rgb = bloom(rgb, threshold=0.70, radius=max(8, height // 30), strength=0.75)
    return post_process(rgb, time_s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render one frame of the Forge opening")
    parser.add_argument("--scene", default="space", choices=("space", "dive", "forge"))
    parser.add_argument("--time", type=float, default=6.0)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--slogan", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    frame = render(args.scene, args.time, args.height, args.width, slogan=args.slogan)
    image = Image.fromarray((frame * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    image.save(args.out)
    print(f"wrote {args.out} ({image.width}x{image.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
