"""Draw the Forge AI icon and write assets/forge.ico (multi-resolution).

The mark repeats the one used in the interface: a rounded square in deep space navy with a
violet-to-cyan edge, and the anvil-and-spark glyph in ice white. Sizes from 16 to 256 are
written into one .ico so Explorer picks the right one at every scale.

Run it with the bundled runtime, which carries Pillow:
    "C:\\Users\\User\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe" \\
        tools/make_icon.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter

OUT = pathlib.Path("assets/forge.ico")
PREVIEW = pathlib.Path("assets/forge-256.png")
SIZES = (16, 24, 32, 48, 64, 128, 256)
SPACE = (6, 9, 21)
VIOLET = (124, 92, 255)
BLUE = (91, 140, 255)
CYAN = (103, 232, 249)
ICE = (238, 242, 255)


def gradient(size: int) -> Image.Image:
    """A diagonal violet to cyan gradient, used for the border and the glyph."""

    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1)) if size > 1 else 0.0
            if t < 0.55:
                k = t / 0.55
                colour = tuple(int(VIOLET[i] + (BLUE[i] - VIOLET[i]) * k) for i in range(3))
            else:
                k = (t - 0.55) / 0.45
                colour = tuple(int(BLUE[i] + (CYAN[i] - BLUE[i]) * k) for i in range(3))
            pixels[x, y] = colour
    return image


def draw_mark(size: int) -> Image.Image:
    scale = 4  # draw large, then downsample: the only anti-aliasing Pillow gives us cheaply
    canvas = size * scale
    gradient_layer = gradient(canvas).convert("RGBA")

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=int(canvas * 0.22), fill=255
    )

    body = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(body)
    unit = canvas / 48.0

    # Anvil and spark, the same silhouette the interface uses.
    draw.polygon(
        [
            (15 * unit, 33 * unit),
            (33 * unit, 33 * unit),
            (29.8 * unit, 28.4 * unit),
            (18.2 * unit, 28.4 * unit),
        ],
        fill=(*ICE, 235),
    )
    draw.polygon(
        [(21.4 * unit, 28.4 * unit), (21.4 * unit, 14.6 * unit), (30.6 * unit, 19.9 * unit)],
        fill=(*ICE, 255),
    )
    draw.line([(13.5 * unit, 36.6 * unit), (34.5 * unit, 36.6 * unit)], fill=(*ICE, 220), width=max(1, int(1.6 * unit)))

    glow = body.filter(ImageFilter.GaussianBlur(radius=canvas * 0.035))

    tile = Image.new("RGBA", (canvas, canvas), (*SPACE, 255))
    tile.paste(gradient_layer, (0, 0), mask)
    tile = Image.alpha_composite(tile, Image.composite(glow, Image.new("RGBA", glow.size, (0, 0, 0, 0)), glow.split()[3].point(lambda v: int(v * 0.55))))
    tile = Image.alpha_composite(tile, body)

    # Inner dark face, so the gradient reads as an edge rather than a fill.
    face = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(face).rounded_rectangle(
        (int(canvas * 0.055), int(canvas * 0.055), int(canvas * 0.945), int(canvas * 0.945)),
        radius=int(canvas * 0.19),
        fill=(*SPACE, 232),
    )
    face_mask = face.split()[3].point(lambda v: int(v * 0.88))
    tile = Image.alpha_composite(tile, Image.composite(face, Image.new("RGBA", face.size, (0, 0, 0, 0)), face_mask))
    tile = Image.alpha_composite(tile, body)

    return tile.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw_mark(size) for size in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(size, size) for size in SIZES])
    frames[-1].save(PREVIEW)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes) and {PREVIEW}")

    with Image.open(OUT) as check:
        print("ico sizes:", sorted(check.info.get("sizes", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
