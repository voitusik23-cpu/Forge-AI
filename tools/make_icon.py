"""Draw the Forge AI product icon and write assets/forge.ico (multi-resolution).

The mark is what the product already stands for -- a forge anvil with sparks flying off it -- drawn
as a geometric product icon instead of an illustration.

Design rules, each answering a requirement:

* **One plate.** A rounded square in graphite (``#12151a`` at the top, ``#0b0d10`` at the bottom),
  corners cut out as real transparency, about 12% empty margin on every side.
* **One accent.** The lime pair the Light palette uses (``#7fa83c`` body, ``#a8c76b`` highlight), so
  the icon speaks the same colour as the window it opens.
* **Silhouette first.** Three bold masses only -- slab with a horn, waist, base -- nothing thinner
  than a few pixels at 16 px, and no detail that exists only at 256 px.
* **Optical sizes.** Every frame is drawn at 4x and reduced with LANCZOS through premultiplied
  alpha, so the cut corners never pick up a dark fringe. Below 48 px the masses thicken, the horn
  shortens, the sparks grow and the bloom is dropped: 16 px gets its own drawing, not a resample.
* **Six real frames.** 16-128 are stored as 32-bit BMP and 256 as PNG -- BMP is what every
  consumer of the format decodes (GDI+ included), and 256 is where a PNG pays for itself. Every
  frame comes from the vector definition, never from another.

Usage (any Python with Pillow; the repository ``.venv`` does not carry it, the DSH runtime does):

    python tools/make_icon.py
"""

from __future__ import annotations

import io
import math
import pathlib
import struct

from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_ICO = ROOT / "assets" / "forge.ico"
OUT_512 = ROOT / "assets" / "forge-icon-512.png"
OUT_64 = ROOT / "assets" / "forge-icon-64.png"
OUT_256 = ROOT / "assets" / "forge-256.png"

MASTER = 1024
SUPERSAMPLE = 4
ICO_SIZES = (256, 128, 64, 48, 32, 16)
PNG_FRAMES = (256,)  # stored PNG-compressed; everything else stays 32-bit BMP (GDI+ friendly)

PLATE_TOP = (18, 21, 26)  # #12151a
PLATE_BOTTOM = (11, 13, 16)  # #0b0d10
LIME = (127, 168, 60)  # #7fa83c
LIME_HI = (168, 199, 107)  # #a8c76b
LIME_DEEP = (92, 134, 54)  # #5c8636, the shaded end of the steel gradient
LIME_SMALL = (185, 211, 130)  # sparks at 16-32 px, lifted so they survive the reduction


def vertical_gradient(height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """A one-pixel-wide column stretched to a square: cheap, exact, and smooth at every size."""

    column = Image.new("RGB", (1, height))
    last = max(height - 1, 1)
    for y in range(height):
        k = y / last
        column.putpixel(
            (0, y),
            tuple(round(top[i] + (bottom[i] - top[i]) * k) for i in range(3)),
        )
    return column.resize((height, height), Image.NEAREST).convert("RGBA")


def anvil_polygons(small: bool) -> list[list[tuple[float, float]]]:
    """The anvil in canvas fractions, listed clockwise.

    The detailed profile is one outline -- horn, slab, undercut, waist, flare, foot -- so the steel
    reads as a single forged piece. The small variant is a separate drawing on purpose: three plain
    masses with a thick waist, because at 16 px the undercut and the foot ledge are sub-pixel and
    only turn the silhouette to mud.
    """

    if small:
        return [
            [(0.166, 0.400), (0.256, 0.348), (0.858, 0.348), (0.858, 0.462), (0.256, 0.462)],
            [(0.388, 0.462), (0.644, 0.462), (0.716, 0.640), (0.316, 0.640)],
            [(0.256, 0.640), (0.776, 0.640), (0.840, 0.815), (0.192, 0.815)],
        ]
    return [
        [
            (0.094, 0.398),  # horn tip
            (0.210, 0.350),
            (0.870, 0.350),
            (0.870, 0.444),
            (0.730, 0.460),
            (0.674, 0.552),  # waist, right
            (0.756, 0.646),
            (0.820, 0.702),
            (0.840, 0.790),  # foot, bottom right
            (0.188, 0.790),
            (0.208, 0.702),
            (0.272, 0.646),
            (0.354, 0.552),  # waist, left
            (0.290, 0.460),
            (0.210, 0.444),
        ]
    ]


def spark_stars(small: bool) -> list[tuple[float, float, float, float]]:
    """Sparks above the anvil: (x, y, radius, angle in degrees) of a four-point star.

    A star rather than a dot: at 32 px and above the thin points read as a spark, while at 16 px
    they simply dissolve into a clean, correctly placed dot.
    """

    if small:
        return [(0.646, 0.300, 0.064, 0.0), (0.772, 0.236, 0.048, 12.0)]
    return [
        (0.630, 0.298, 0.044, 0.0),
        (0.756, 0.236, 0.032, 14.0),
        (0.844, 0.316, 0.022, 28.0),
    ]


def spark_polygon(cx: float, cy: float, radius: float, angle: float) -> list[tuple[float, float]]:
    """A four-point star: four long points, four pinched valleys, rotated by ``angle``."""

    points = []
    for index in range(8):
        theta = math.radians(angle + index * 45.0)
        reach = radius if index % 2 == 0 else radius * 0.20
        points.append((cx + math.cos(theta) * reach, cy + math.sin(theta) * reach))
    return points


def shape_mask(canvas: int, small: bool) -> tuple[Image.Image, Image.Image]:
    """The anvil silhouette and the spark silhouette, both as 8-bit masks."""

    scale = 1.0
    if small:
        # Small sizes get a slightly larger mark: the eye needs the shapes, not the margin.
        scale, origin_x, origin_y = 1.06, 0.5, 0.6
    else:
        origin_x, origin_y = 0.5, 0.5

    def fit(x: float, y: float) -> tuple[float, float]:
        return (origin_x + (x - origin_x) * scale, origin_y + (y - origin_y) * scale)

    body = Image.new("L", (canvas, canvas), 0)
    body_draw = ImageDraw.Draw(body)
    for polygon in anvil_polygons(small):
        body_draw.polygon([(x * canvas, y * canvas) for x, y in (fit(*point) for point in polygon)], fill=255)

    sparks = Image.new("L", (canvas, canvas), 0)
    spark_draw = ImageDraw.Draw(sparks)
    for x, y, radius, angle in spark_stars(small):
        cx, cy = fit(x, y)
        spark_draw.polygon(
            [(px * canvas, py * canvas) for px, py in spark_polygon(cx, cy, radius * scale, angle)],
            fill=255,
        )
    return body, sparks


def tinted(mask: Image.Image, colour: tuple[int, int, int], opacity: float = 1.0) -> Image.Image:
    """A flat colour shaped by an 8-bit mask, optionally scaled down in alpha."""

    layer = Image.new("RGBA", mask.size, (*colour, 0))
    alpha = mask if opacity >= 1.0 else mask.point(lambda value: round(value * opacity))
    layer.putalpha(alpha)
    return layer


def render(size: int) -> Image.Image:
    """One frame at its own optical size: draw at 4x, then reduce through premultiplied alpha."""

    canvas = size * SUPERSAMPLE
    small = size < 48

    plate_mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(plate_mask).rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=round(canvas * 0.205), fill=255
    )
    icon = vertical_gradient(canvas, PLATE_TOP, PLATE_BOTTOM)
    icon.putalpha(plate_mask)

    body_mask, spark_mask = shape_mask(canvas, small)

    # A lime bloom behind the steel reads as heat without drawing a single outline.
    if not small:
        icon = Image.alpha_composite(
            icon, tinted(body_mask.filter(ImageFilter.GaussianBlur(canvas * 0.045)), LIME, 0.30)
        )

    # The steel itself: highlight at the top of the slab, brand lime at the base.
    body_box = body_mask.getbbox()
    body = Image.new("RGBA", (canvas, canvas), (*LIME, 0))
    if body_box:
        top, bottom = body_box[1], body_box[3]
        gradient = vertical_gradient(bottom - top, LIME_HI, LIME_DEEP)
        body.paste(gradient.resize((canvas, bottom - top), Image.LANCZOS), (0, top))
    body.putalpha(body_mask)
    icon = Image.alpha_composite(icon, body)

    if not small:
        icon = Image.alpha_composite(
            icon, tinted(spark_mask.filter(ImageFilter.GaussianBlur(canvas * 0.018)), LIME_HI, 0.55)
        )
    icon = Image.alpha_composite(icon, tinted(spark_mask, LIME_SMALL if small else LIME_HI))

    return icon.convert("RGBa").resize((size, size), Image.LANCZOS).convert("RGBA")


def bmp_frame(image: Image.Image) -> bytes:
    """A 32-bit bottom-up DIB with an empty AND mask: the container's uncompressed frame."""

    width, height = image.size
    pixels = image.load()
    rows = []
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            row += bytes((blue, green, red, alpha))
        rows.append(bytes(row))
    bitmap = b"".join(rows)
    mask = bytes(((width + 31) // 32) * 4) * height
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, len(bitmap) + len(mask), 0, 0, 0, 0)
    return header + bitmap + mask


def ico_bytes(frames: dict[int, Image.Image]) -> bytes:
    """Assemble the container by hand: it is the only way to keep every frame a native drawing."""

    entries: list[bytes] = []
    blobs: list[bytes] = []
    offset = 6 + 16 * len(frames)
    for size in sorted(frames, reverse=True):
        if size in PNG_FRAMES:
            buffer = io.BytesIO()
            frames[size].save(buffer, format="PNG", optimize=True)
            blob = buffer.getvalue()
        else:
            blob = bmp_frame(frames[size])
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(frames)) + b"".join(entries) + b"".join(blobs)


def preview(master: Image.Image, size: int) -> Image.Image:
    return master.convert("RGBa").resize((size, size), Image.LANCZOS).convert("RGBA")


def main() -> int:
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    master = render(MASTER)
    frames = {size: render(size) for size in ICO_SIZES}

    preview(master, 512).save(OUT_512)
    frames[64].save(OUT_64)
    preview(master, 256).save(OUT_256)
    OUT_ICO.write_bytes(ico_bytes(frames))

    for path in (OUT_ICO, OUT_512, OUT_64, OUT_256):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")

    with Image.open(OUT_ICO) as check:
        print("ico frames:", sorted(check.info.get("sizes", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
