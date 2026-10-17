from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 512


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build_gradient() -> Image.Image:
    top = (249, 115, 22)
    bottom = (194, 65, 12)
    image = Image.new("RGB", (SIZE, SIZE))
    pixels = image.load()
    for y in range(SIZE):
        for x in range(SIZE):
            t = (x * 0.35 + y * 0.65) / SIZE
            pixels[x, y] = lerp(top, bottom, min(max(t, 0.0), 1.0))

    glow = Image.new("L", (SIZE, SIZE), 0)
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-120, -140, 330, 310), fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    highlight = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    image = Image.composite(highlight, image, glow.point(lambda value: value // 3))
    return image


def rounded_mask() -> Image.Image:
    mask = Image.new("L", (SIZE, SIZE), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=118, fill=255)
    return mask


def draw_network(image: Image.Image) -> None:
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    center = (256, 252)
    radius = 142
    nodes = []
    for index in range(6):
        angle = math.radians(-90 + index * 60)
        nodes.append(
            (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)
        )

    for node in nodes:
        draw.line((center, node), fill=(255, 255, 255, 150), width=10)
    for index, node in enumerate(nodes):
        draw.line((node, nodes[(index + 1) % len(nodes)]), fill=(255, 255, 255, 60), width=6)

    for node in nodes:
        draw.ellipse(
            (node[0] - 25, node[1] - 25, node[0] + 25, node[1] + 25),
            fill=(255, 255, 255, 235),
        )
        draw.ellipse(
            (node[0] - 11, node[1] - 11, node[0] + 11, node[1] + 11),
            fill=(194, 65, 12, 255),
        )

    draw.ellipse(
        (center[0] - 46, center[1] - 46, center[0] + 46, center[1] + 46),
        fill=(255, 255, 255, 245),
    )
    draw.ellipse(
        (center[0] - 18, center[1] - 18, center[0] + 18, center[1] + 18),
        fill=(15, 118, 110, 255),
    )

    accent = (15, 118, 110, 255)
    draw.ellipse((360, 355, 432, 427), fill=accent)
    draw.ellipse((376, 371, 416, 411), fill=(255, 255, 255, 255))

    image.alpha_composite(overlay)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    base = build_gradient().convert("RGBA")
    base.putalpha(rounded_mask())
    draw_network(base)

    logo = base.resize((256, 256), Image.LANCZOS)
    logo.save(ASSETS / "logo.png")

    icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    base.save(ASSETS / "icon.ico", sizes=icon_sizes)
    print(f"Generated {ASSETS / 'icon.ico'} and {ASSETS / 'logo.png'}")


if __name__ == "__main__":
    main()
