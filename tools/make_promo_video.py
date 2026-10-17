from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
ASSETS = ROOT / "assets"
OUTPUT = MEDIA / "IPActivityAuditor-promo-15s.mp4"

W, H = 1920, 1080
FPS = 60
DURATION = 15.0
FRAMES = int(FPS * DURATION)

INK = (7, 9, 13)
INK_SOFT = (12, 17, 26)
DARK_CARD = (16, 23, 34)
DARK_BORDER = (43, 55, 72)
CREAM = (245, 239, 230)
CREAM_SOFT = (210, 201, 188)
LIGHT_BG = (243, 238, 230)
LIGHT_CARD = (255, 253, 249)
LIGHT_BORDER = (226, 219, 209)
INK_TEXT = (27, 31, 39)
MUTED_DARK = (123, 134, 153)
MUTED_LIGHT = (124, 129, 138)
ORANGE = (255, 107, 26)
ORANGE_SOFT = (255, 151, 83)
TEAL = (34, 211, 166)
GREEN = (74, 222, 128)
RED = (255, 92, 97)
YELLOW = (255, 196, 54)
WHITE = (255, 255, 255)

WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
FONT_DIR = WINDIR / "Fonts"
FONT_CN = FONT_DIR / "msyhbd.ttc"
FONT_CN_REG = FONT_DIR / "msyh.ttc"
FONT_LATIN = FONT_DIR / "segoeuib.ttf"
FONT_LATIN_REG = FONT_DIR / "segoeui.ttf"
FONT_MONO = FONT_DIR / "consolab.ttf"
FONT_MONO_REG = FONT_DIR / "consola.ttf"
FONT_TECH = FONT_DIR / "bahnschrift.ttf"

random.seed(17)

# ------------------------------------------------------------------ utilities


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(a, b, t: float):
    t = clamp(t)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ease_out_expo(t: float) -> float:
    t = clamp(t)
    return 1.0 if t >= 1 else 1 - pow(2, -10 * t)


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1 - pow(1 - t, 3)


def ease_in_out_cubic(t: float) -> float:
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


def ease_out_back(t: float) -> float:
    t = clamp(t)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


def ease_in_cubic(t: float) -> float:
    t = clamp(t)
    return t * t * t


@lru_cache(maxsize=256)
def font(kind: str, size: int):
    paths = {
        "cn": FONT_CN,
        "cn_reg": FONT_CN_REG,
        "latin": FONT_LATIN,
        "latin_reg": FONT_LATIN_REG,
        "mono": FONT_MONO,
        "mono_reg": FONT_MONO_REG,
        "tech": FONT_TECH,
    }
    path = paths.get(kind, FONT_LATIN_REG)
    try:
        return ImageFont.truetype(str(path), size=size)
    except Exception:
        try:
            return ImageFont.truetype(str(FONT_LATIN_REG), size=size)
        except Exception:
            return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, f, tracking: float = 0) -> float:
    if not text:
        return 0
    width = sum(draw.textlength(char, font=f) for char in text)
    return width + tracking * (len(text) - 1)


def draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    f,
    fill,
    tracking: float = 2.0,
    anchor: str = "la",
):
    x, y = xy
    total = text_width(draw, text, f, tracking)
    if anchor.startswith("m"):
        x -= total / 2
    elif anchor.startswith("r"):
        x -= total
    for char in text:
        draw.text((x, y), char, font=f, fill=fill, anchor="l" + anchor[1:])
        x += draw.textlength(char, font=f) + tracking


def alpha_composite(dst: Image.Image, src: Image.Image, xy=(0, 0), alpha: float = 1.0):
    if alpha >= 1.0:
        dst.alpha_composite(src, (int(xy[0]), int(xy[1])))
        return
    layer = src.copy()
    a = layer.getchannel("A").point(lambda value: int(value * alpha))
    layer.putalpha(a)
    dst.alpha_composite(layer, (int(xy[0]), int(xy[1])))


def text_layer(size=(W, H)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def add_glow(base: Image.Image, color, center, radius: int, strength: float = 1.0):
    glow = radial_glow(tuple(color), radius)
    if strength < 1.0:
        glow = glow.copy()
        glow.putalpha(glow.getchannel("A").point(lambda v: int(v * strength)))
    base.alpha_composite(glow, (int(center[0] - radius), int(center[1] - radius)))


@lru_cache(maxsize=64)
def radial_glow(color: tuple[int, int, int], radius: int) -> Image.Image:
    size = radius * 2
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2) / radius
    alpha = np.clip(1.0 - distance, 0.0, 1.0) ** 2.2
    array = np.zeros((size, size, 4), dtype=np.uint8)
    array[..., 0] = color[0]
    array[..., 1] = color[1]
    array[..., 2] = color[2]
    array[..., 3] = (alpha * 255).astype(np.uint8)
    return Image.fromarray(array, "RGBA")


@lru_cache(maxsize=16)
def card_image(
    width: int,
    height: int,
    radius: int,
    fill: tuple[int, int, int],
    border: tuple[int, int, int],
    shadow: int = 34,
    shadow_alpha: int = 120,
) -> tuple[Image.Image, int]:
    pad = shadow
    scale = 2
    size = ((width + 2 * pad) * scale, (height + 2 * pad) * scale)
    image = Image.new("RGBA", size, (0, 0, 0, 0))

    shadow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sd.rounded_rectangle(
        (
            pad * scale + 10,
            pad * scale + 22,
            (pad + width) * scale - 10,
            (pad + height) * scale + 22,
        ),
        radius=radius * scale,
        fill=(0, 0, 0, shadow_alpha),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(18 * scale))
    image.alpha_composite(shadow_layer)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (
            pad * scale,
            pad * scale,
            (pad + width) * scale,
            (pad + height) * scale,
        ),
        radius=radius * scale,
        fill=fill + (255,),
        outline=border + (255,),
        width=2 * scale,
    )
    draw.line(
        (
            pad * scale + radius * scale,
            pad * scale + 1,
            (pad + width - radius) * scale,
            pad * scale + 1,
        ),
        fill=(255, 255, 255, 22),
        width=scale,
    )
    image = image.resize((width + 2 * pad, height + 2 * pad), Image.Resampling.LANCZOS)
    return image, pad


def paste_card(
    base: Image.Image,
    card: Image.Image,
    x: int,
    y: int,
    pad: int,
    alpha: float = 1.0,
    angle: float = 0.0,
    scale: float = 1.0,
):
    image = card
    if scale != 1.0:
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
    px = int(x - pad * scale + (card.width * scale - image.width) / 2)
    py = int(y - pad * scale + (card.height * scale - image.height) / 2)
    alpha_composite(base, image, (px, py), alpha)


@lru_cache(maxsize=8)
def grain_layer(variant: int) -> Image.Image:
    rng = np.random.default_rng(100 + variant)
    noise = rng.normal(0, 1, (H // 2, W // 2))
    noise = np.clip(noise * 20 + 128, 0, 255).astype(np.uint8)
    image = Image.fromarray(noise, "L").resize((W, H), Image.Resampling.BILINEAR)
    rgba = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    rgba.putalpha(image.point(lambda value: int(abs(value - 128) * 0.34)))
    return rgba


@lru_cache(maxsize=4)
def vignette_layer(light: bool) -> Image.Image:
    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx - W / 2) / (W / 2)
    ny = (yy - H / 2) / (H / 2)
    distance = np.sqrt(nx * nx + ny * ny * 1.25)
    strength = np.clip((distance - 0.55) / 0.75, 0, 1) ** 1.7
    alpha = (strength * (42 if light else 190)).astype(np.uint8)
    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    image.putalpha(Image.fromarray(alpha, "L"))
    return image


# ------------------------------------------------------------------ backgrounds


def draw_grid(draw, color, alpha, spacing=48, offset=(0.0, 0.0), width=1):
    ox = int(offset[0]) % spacing
    oy = int(offset[1]) % spacing
    for x in range(-spacing, W + spacing, spacing):
        draw.line((x + ox, 0, x + ox, H), fill=color + (alpha,), width=width)
    for y in range(-spacing, H + spacing, spacing):
        draw.line((0, y + oy, W, y + oy), fill=color + (alpha,), width=width)


def base_dark(t: float, intensity: float = 1.0) -> Image.Image:
    image = Image.new("RGBA", (W, H), INK + (255,))
    add_glow(
        image,
        ORANGE,
        (1520 + math.sin(t * 0.7) * 40, 250 + math.cos(t * 0.5) * 30),
        640,
        0.34 * intensity,
    )
    add_glow(
        image,
        TEAL,
        (250 + math.cos(t * 0.6) * 30, 900 + math.sin(t * 0.4) * 20),
        520,
        0.20 * intensity,
    )
    draw = ImageDraw.Draw(image)
    draw_grid(
        draw,
        (255, 255, 255),
        7,
        48,
        (t * 18, t * 9),
    )
    return image


def base_light(t: float) -> Image.Image:
    image = Image.new("RGBA", (W, H), LIGHT_BG + (255,))
    add_glow(image, ORANGE, (1620, 850), 520, 0.28)
    add_glow(image, TEAL, (260, 190), 430, 0.12)
    draw = ImageDraw.Draw(image)
    draw_grid(draw, INK_TEXT, 10, 52, (t * 12, t * 8))
    return image


def hud(image: Image.Image, t: float, light: bool = False, index: str = "01"):
    draw = ImageDraw.Draw(image)
    text_color = INK_TEXT if light else CREAM
    sub_color = MUTED_LIGHT if light else MUTED_DARK
    accent = ORANGE
    draw.rounded_rectangle((62, 54, 80, 72), radius=4, fill=accent + (255,))
    draw_tracked(
        draw,
        (94, 53),
        "IP ACTIVITY AUDITOR",
        font("tech", 17),
        sub_color + (230,),
        tracking=2.4,
    )
    draw.text((62, 88), f"{index} / 06", font=font("mono", 15), fill=accent + (220,))
    timecode = f"00:{int(t):02d}.{int((t - int(t)) * 100):02d}"
    draw.text((W - 150, 56), timecode, font=font("mono", 16), fill=sub_color + (210,))
    draw.ellipse((W - 176, 60, W - 166, 70), fill=accent + (240,))
    draw.line((62, H - 48, W - 62, H - 48), fill=text_color + (16,), width=1)
    draw_tracked(
        draw,
        (62, H - 38),
        "ARP  /  MULTI-CIDR  /  MAC  /  LOGS",
        font("tech", 14),
        sub_color + (180,),
        tracking=2.0,
    )
    draw_tracked(
        draw,
        (W - 62, H - 38),
        "MULAN PSL v2",
        font("tech", 14),
        sub_color + (180,),
        tracking=2.0,
        anchor="ra",
    )


# ------------------------------------------------------------------ scene 01


def draw_network(draw, t: float):
    center = (1480, 500)
    nodes = [
        (1260, 250),
        (1610, 220),
        (1780, 420),
        (1690, 690),
        (1420, 790),
        (1190, 620),
        (1150, 400),
        (1540, 510),
        (1350, 360),
    ]
    progress = clamp((t - 0.35) / 1.2)
    scan_x = 1080 + ease_out_cubic(clamp((t - 0.7) / 1.4)) * 760
    for idx, node in enumerate(nodes):
        target = nodes[(idx + 1) % len(nodes)]
        draw.line(
            (node[0], node[1], target[0], target[1]),
            fill=(255, 255, 255, int(20 * progress)),
            width=1,
        )
        draw.line(
            (center[0], center[1], node[0], node[1]),
            fill=(ORANGE[0], ORANGE[1], ORANGE[2], int(38 * progress)),
            width=2,
        )
        pulse = (math.sin(t * 5 + idx * 0.9) + 1) / 2
        radius = 5 + pulse * 1.8
        color = ORANGE if node[0] < scan_x else (255, 255, 255)
        alpha = int(120 + 135 * (1 if node[0] < scan_x else 0.2))
        draw.ellipse(
            (node[0] - radius, node[1] - radius, node[0] + radius, node[1] + radius),
            fill=color + (alpha,),
        )
    pulse = (math.sin(t * 4) + 1) / 2
    draw.ellipse(
        (
            center[0] - 18 - pulse * 6,
            center[1] - 18 - pulse * 6,
            center[0] + 18 + pulse * 6,
            center[1] + 18 + pulse * 6,
        ),
        outline=ORANGE + (130,),
        width=2,
    )
    draw.ellipse(
        (center[0] - 8, center[1] - 8, center[0] + 8, center[1] + 8),
        fill=ORANGE + (255,),
    )
    draw.line(
        (scan_x, 170, scan_x, 860),
        fill=ORANGE + (45,),
        width=2,
    )
    draw.line(
        (scan_x - 1, 170, scan_x - 1, 860),
        fill=ORANGE + (14,),
        width=12,
    )
    draw.text(
        (1268, 850),
        "ARP RESPONSE MAP",
        font=font("tech", 15),
        fill=MUTED_DARK + (180,),
    )


def kinetic_line(
    image: Image.Image,
    text: str,
    start_x: int,
    y: int,
    f,
    fill,
    local_t: float,
    stagger: float,
    rise: int = 80,
    delay: float = 0.0,
):
    draw = ImageDraw.Draw(image)
    x = start_x
    for index, char in enumerate(text):
        progress = clamp((local_t - delay - index * stagger) / 0.52)
        eased = ease_out_expo(progress)
        if eased <= 0:
            x += draw.textlength(char, font=f)
            continue
        alpha = int(255 * clamp(progress * 1.4))
        offset = (1 - eased) * rise
        draw.text(
            (x, y + offset),
            char,
            font=f,
            fill=fill + (alpha,),
        )
        x += draw.textlength(char, font=f)


def scene_hook(t: float) -> Image.Image:
    image = base_dark(t)
    draw = ImageDraw.Draw(image)
    draw.text(
        (1420, 700),
        "254",
        font=font("latin", 300),
        fill=(255, 255, 255, 9),
    )
    draw_network(draw, t)

    # orange rule
    line_progress = ease_out_expo(clamp(t / 0.55))
    draw.line(
        (122, 285, 122 + 230 * line_progress, 285),
        fill=ORANGE + (230,),
        width=4,
    )
    draw_tracked(
        draw,
        (122, 250),
        "01  /  LIVE AUDIT",
        font("tech", 18),
        ORANGE + (230,),
        tracking=2.2,
    )

    kinetic_line(
        image,
        "你的内网",
        120,
        330,
        font("cn", 138),
        CREAM,
        t,
        0.035,
        rise=90,
    )
    kinetic_line(
        image,
        "谁还在线？",
        120,
        505,
        font("cn", 138),
        ORANGE,
        t,
        0.035,
        rise=90,
        delay=0.42,
    )

    sub_alpha = clamp((t - 1.0) / 0.5)
    draw.text(
        (124, 710),
        "一次 ARP 扫描，看清每一个 IP",
        font=font("cn_reg", 38),
        fill=CREAM_SOFT + (int(220 * sub_alpha),),
    )
    chip_y = 800
    for idx, label in enumerate(("多网段", "MAC 审计", "无需 Npcap")):
        p = clamp((t - 1.1 - idx * 0.09) / 0.45)
        if p <= 0:
            continue
        w = 132 if idx != 2 else 164
        x = 124 + idx * (w + 14)
        draw.rounded_rectangle(
            (x, chip_y, x + w, chip_y + 42),
            radius=21,
            fill=(255, 255, 255, int(14 * p)),
            outline=(255, 255, 255, int(42 * p)),
            width=1,
        )
        draw.text(
            (x + w / 2, chip_y + 21),
            label,
            font=font("cn_reg", 20),
            fill=CREAM_SOFT + (int(220 * p),),
            anchor="mm",
        )
    hud(image, t, index="01")
    return image


# ------------------------------------------------------------------ scene 02


SCAN_ONLINE = {3, 8, 14, 21, 27, 33, 38, 49, 57, 66, 78, 91}


def render_scan_card(local_t: float) -> tuple[Image.Image, int, int, int]:
    width, height = 1040, 680
    base, pad = card_image(width, height, 26, DARK_CARD, DARK_BORDER, 38)
    draw = ImageDraw.Draw(base)
    ox, oy = pad, pad
    draw.rectangle((ox + 1, oy + 1, ox + width - 1, oy + 1), fill=(255, 255, 255, 12))
    draw.rounded_rectangle(
        (ox + 32, oy + 28, ox + 52, oy + 48), radius=5, fill=ORANGE + (255,)
    )
    draw.text(
        (ox + 68, oy + 25),
        "SCAN  /  192.168.0.0/24",
        font=font("mono", 22),
        fill=CREAM + (235,),
    )
    draw.text(
        (ox + width - 34, oy + 28),
        "● LIVE",
        font=font("mono", 17),
        fill=GREEN + (230,),
        anchor="ra",
    )
    draw.line(
        (ox + 32, oy + 78, ox + width - 32, oy + 78),
        fill=(255, 255, 255, 20),
        width=1,
    )

    gx, gy, gw, gh = ox + 44, oy + 118, width - 88, 380
    cols, rows = 26, 10
    step_x = gw / (cols - 1)
    step_y = gh / (rows - 1)
    sweep = (local_t * 0.7) % 1.0
    sweep_x = gx + sweep * gw
    draw.rectangle((gx, gy - 12, gx + gw, gy + gh + 12), fill=(0, 0, 0, 36))
    for row in range(rows):
        for col in range(cols):
            index = row * cols + col
            x = gx + col * step_x
            y = gy + row * step_y
            distance = abs(x - sweep_x)
            heat = clamp(1 - distance / 150)
            online = index in SCAN_ONLINE
            if online and x < sweep_x:
                color = GREEN
                alpha = 230
                radius = 4.6
            elif heat > 0.1:
                color = ORANGE
                alpha = int(80 + 160 * heat)
                radius = 3.4 + heat * 2.6
            else:
                color = (92, 108, 131)
                alpha = 100
                radius = 2.4
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color + (alpha,))
    glow = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    local_x = sweep_x
    gd.line(
        (local_x, gy - 18, local_x, gy + gh + 18),
        fill=ORANGE + (180,),
        width=2,
    )
    for offset, alpha in ((8, 30), (18, 18), (30, 10)):
        gd.rectangle(
            (local_x - offset, gy - 18, local_x + offset, gy + gh + 18),
            fill=ORANGE + (alpha,),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    base.alpha_composite(glow)

    draw.text(
        (ox + 32, oy + 520),
        "ARP SWEEP",
        font=font("tech", 15),
        fill=MUTED_DARK + (200,),
    )
    draw.text(
        (ox + 32, oy + 545),
        f"{int(254 * clamp(local_t / 1.1)):03d}",
        font=font("latin", 54),
        fill=CREAM + (245,),
    )
    labels = ("TOTAL IP", "ONLINE", "NEVER ACTIVE")
    values = (254, 12, 18)
    for idx, (label, value) in enumerate(zip(labels, values)):
        x = ox + 190 + idx * 250
        shown = int(value * ease_out_cubic(clamp((local_t - 0.1 - idx * 0.08) / 0.8)))
        draw.text(
            (x, oy + 530),
            f"{shown:03d}",
            font=font("latin", 44),
            fill=(ORANGE if idx == 1 else CREAM) + (240,),
        )
        draw.text(
            (x, oy + 586),
            label,
            font=font("tech", 14),
            fill=MUTED_DARK + (220,),
        )
    return base, pad, width, height


def scene_scan(local_t: float) -> Image.Image:
    image = base_dark(2.4 + local_t)
    draw = ImageDraw.Draw(image)
    entry = ease_out_expo(clamp(local_t / 0.45))
    card, pad, card_w, card_h = render_scan_card(local_t)
    paste_card(image, card, int(100 - (1 - entry) * 260), 200, pad, alpha=entry)

    # right side headline
    p = clamp((local_t - 0.05) / 0.55)
    draw_tracked(
        draw,
        (1220, 140),
        "SCAN  /  FAST",
        font("tech", 17),
        ORANGE + (int(230 * p),),
        tracking=2.2,
    )
    draw.text(
        (1220, 175),
        "秒级扫描",
        font=font("cn", 66),
        fill=CREAM + (int(255 * p),),
    )
    draw.text(
        (1222, 260),
        "自动识别在线设备与最后活跃日期",
        font=font("cn_reg", 24),
        fill=MUTED_DARK + (int(220 * p),),
    )

    online = int(12 * ease_out_cubic(clamp((local_t - 0.25) / 0.9)))
    draw.text(
        (1218, 340),
        f"{online:02d}",
        font=font("latin", 210),
        fill=ORANGE + (250,),
    )
    draw.text(
        (1230, 560),
        "台设备在线",
        font=font("cn_reg", 28),
        fill=CREAM_SOFT + (220,),
    )
    draw.line((1220, 620, 1700, 620), fill=(255, 255, 255, 22), width=1)

    rows = (
        ("SendARP 后端", "无需 Npcap", GREEN),
        ("MAC 地址记录", "自动写入库存", ORANGE),
        ("多网段支持", "自动去重 / 拒绝重叠", TEAL),
    )
    for idx, (title, detail, color) in enumerate(rows):
        p = clamp((local_t - 0.55 - idx * 0.12) / 0.45)
        if p <= 0:
            continue
        y = 660 + idx * 84
        x = 1220 - (1 - ease_out_expo(p)) * 90
        draw.ellipse((x, y, x + 16, y + 16), fill=color + (int(220 * p),))
        draw.text(
            (x + 32, y - 6),
            title,
            font=font("cn_reg", 24),
            fill=CREAM + (int(240 * p),),
        )
        draw.text(
            (x + 32, y + 28),
            detail,
            font=font("tech", 15),
            fill=MUTED_DARK + (int(220 * p),),
        )
    hud(image, 2.4 + local_t, index="02")
    return image


# ------------------------------------------------------------------ scene 03


def scene_multi(local_t: float) -> Image.Image:
    image = base_dark(5.4 + local_t, 0.9)
    draw = ImageDraw.Draw(image)
    draw.text(
        (1380, 610),
        "03",
        font=font("latin", 420),
        fill=(255, 255, 255, 9),
    )
    draw_tracked(
        draw,
        (120, 138),
        "03  /  MULTI-CIDR",
        font("tech", 17),
        ORANGE + (230,),
        tracking=2.2,
    )
    draw.text(
        (120, 172),
        "多网段，一网打尽",
        font=font("cn", 64),
        fill=CREAM + (250,),
    )
    draw.text(
        (124, 258),
        "自动去重 · 拒绝重叠 · 逐段扫描",
        font=font("cn_reg", 25),
        fill=MUTED_DARK + (220,),
    )

    cidrs = ("192.168.0.0/24", "192.168.1.0/24", "10.10.0.0/24")
    node = (875, 525)
    for idx, cidr in enumerate(cidrs):
        p = clamp((local_t - 0.12 - idx * 0.12) / 0.5)
        if p <= 0:
            continue
        eased = ease_out_expo(p)
        x = 130 - (1 - eased) * 260
        y = 380 + idx * 118
        draw.rounded_rectangle(
            (x, y, x + 540, y + 86),
            radius=18,
            fill=(255, 255, 255, int(15 * eased)),
            outline=(255, 255, 255, int(42 * eased)),
            width=1,
        )
        draw.text(
            (x + 22, y + 26),
            f"0{idx + 1}",
            font=font("mono", 20),
            fill=ORANGE + (int(240 * eased),),
        )
        draw.text(
            (x + 72, y + 22),
            cidr,
            font=font("mono", 28),
            fill=CREAM + (int(245 * eased),),
        )
        draw.text(
            (x + 500, y + 31),
            "●",
            font=font("mono", 15),
            fill=GREEN + (int(220 * eased),),
            anchor="ma",
        )
        line_p = clamp((local_t - 0.38 - idx * 0.12) / 0.45)
        if line_p > 0:
            ex = x + 540 + (node[0] - (x + 540)) * ease_in_out_cubic(line_p)
            ey = y + 43 + (node[1] - (y + 43)) * ease_in_out_cubic(line_p)
            draw.line(
                (x + 540, y + 43, ex, ey),
                fill=ORANGE + (int(120 * line_p),),
                width=2,
            )
    pulse = (math.sin(local_t * 6) + 1) / 2
    draw.ellipse(
        (
            node[0] - 15 - pulse * 10,
            node[1] - 15 - pulse * 10,
            node[0] + 15 + pulse * 10,
            node[1] + 15 + pulse * 10,
        ),
        outline=ORANGE + (100,),
        width=2,
    )
    draw.ellipse((node[0] - 7, node[1] - 7, node[0] + 7, node[1] + 7), fill=ORANGE + (255,))

    card, pad = card_image(760, 560, 28, DARK_CARD, DARK_BORDER, 38)
    cd = ImageDraw.Draw(card)
    ox, oy = pad, pad
    cd.text((ox + 36, oy + 34), "INVENTORY  /  MAC", font=font("mono", 19), fill=ORANGE + (240,))
    cd.line((ox + 36, oy + 74, ox + 724, oy + 74), fill=(255, 255, 255, 20), width=1)
    cd.text((ox + 36, oy + 112), "192.168.0.26", font=font("latin", 52), fill=CREAM + (248,))
    cd.text((ox + 38, oy + 180), "MAC 地址", font=font("cn_reg", 21), fill=MUTED_DARK + (230,))
    mac = "FC:AA:14:98:8E:FF"
    shown = min(len(mac), int(clamp((local_t - 0.75) / 0.055) * len(mac)))
    mac_text = mac[:shown]
    cd.text((ox + 36, oy + 224), mac_text, font=font("mono", 44), fill=CREAM + (250,))
    if shown < len(mac) or (local_t * 2) % 1 < 0.5:
        cursor_x = ox + 36 + cd.textlength(mac_text, font=font("mono", 44)) + 3
        cd.rectangle((cursor_x, oy + 226, cursor_x + 3, oy + 276), fill=ORANGE + (240,))
    cd.text((ox + 38, oy + 302), "LAST SEEN  2026-10-17 23:30", font=font("mono", 16), fill=MUTED_DARK + (220,))
    pill_alpha = clamp((local_t - 1.55) / 0.3)
    if pill_alpha > 0:
        cd.rounded_rectangle(
            (ox + 36, oy + 360, ox + 236, oy + 406),
            radius=23,
            fill=(GREEN[0], GREEN[1], GREEN[2], int(28 * pill_alpha)),
            outline=GREEN + (int(100 * pill_alpha),),
            width=1,
        )
        cd.text(
            (ox + 60, oy + 372),
            "✓  已写入库存",
            font=font("cn_reg", 19),
            fill=GREEN + (int(240 * pill_alpha),),
        )
    cd.text((ox + 36, oy + 456), "MULTI-CIDR  ·  AUTO DEDUP  ·  NO OVERLAP", font=font("tech", 13), fill=MUTED_DARK + (200,))

    p = clamp((local_t - 0.28) / 0.6)
    eased = ease_out_expo(p)
    paste_card(image, card, int(1010 + (1 - eased) * 240), 250, pad, alpha=eased)

    draw.text((126, 760), "3 个 CIDR", font=font("latin", 36), fill=ORANGE + (240,))
    draw.text((126, 805), "一次扫描完成", font=font("cn_reg", 22), fill=MUTED_DARK + (220,))
    hud(image, 5.4 + local_t, index="03")
    return image


# ------------------------------------------------------------------ scene 04


REPORT_ROWS = (
    ("192.168.0.27", "2026-10-17", "FC:AA:14:98:8E:FF", "否"),
    ("192.168.0.31", "2026-10-11", "4C:ED:FB:C7:22:06", "否"),
    ("192.168.0.44", "2026-09-28", "A4:5E:60:11:82:1C", "否"),
    ("192.168.0.37", "", "—", "是"),
    ("192.168.0.52", "2026-10-03", "00:1B:44:11:3A:B7", "否"),
    ("192.168.0.63", "", "—", "是"),
    ("192.168.0.71", "2026-10-16", "3C:52:82:9D:4E:10", "否"),
    ("192.168.0.84", "2026-09-21", "B8:27:EB:44:19:02", "否"),
)


def render_report_card(local_t: float) -> tuple[Image.Image, int]:
    width, height = 960, 780
    card, pad = card_image(width, height, 24, LIGHT_CARD, LIGHT_BORDER, 40, 80)
    draw = ImageDraw.Draw(card)
    ox, oy = pad, pad
    draw.rounded_rectangle((ox + 1, oy + 1, ox + width - 1, oy + 1), radius=20, fill=(255, 255, 255, 0))
    draw.text((ox + 34, oy + 30), "IP未使用清单_7天_20261017.csv", font=font("mono", 17), fill=MUTED_LIGHT + (240,))
    draw.rounded_rectangle(
        (ox + width - 118, oy + 24, ox + width - 34, oy + 62),
        radius=19,
        fill=(255, 239, 228, 255),
        outline=(255, 195, 158, 255),
        width=1,
    )
    draw.text((ox + width - 76, oy + 43), "查看", font=font("cn_reg", 18), fill=ORANGE, anchor="mm")
    draw.line((ox + 34, oy + 84, ox + width - 34, oy + 84), fill=(230, 224, 215, 255), width=1)
    headers = ("IP 地址", "最后活跃", "MAC 地址", "从未活跃")
    xs = (ox + 34, ox + 340, ox + 505, ox + 760)
    for x, label in zip(xs, headers):
        draw.text((x, oy + 106), label, font=font("cn_reg", 17), fill=MUTED_LIGHT)
    row_height = 64
    for idx, (ip, last, mac, never) in enumerate(REPORT_ROWS):
        p = clamp((local_t - 0.28 - idx * 0.065) / 0.42)
        if p <= 0:
            continue
        y = oy + 150 + idx * row_height
        slide = (1 - ease_out_expo(p)) * 70
        alpha = int(255 * p)
        if idx == 3:
            draw.rounded_rectangle(
                (ox + 24, y - 2, ox + width - 24, y + row_height - 8),
                radius=10,
                fill=(255, 238, 226, int(255 * p)),
            )
            draw.rounded_rectangle(
                (ox + 24, y + 8, ox + 28, y + row_height - 18),
                radius=2,
                fill=ORANGE + (alpha,),
            )
        elif idx % 2 == 0:
            draw.rounded_rectangle(
                (ox + 24, y - 2, ox + width - 24, y + row_height - 8),
                radius=10,
                fill=(248, 245, 239, int(255 * p)),
            )
        draw.text((xs[0] + slide, y + 18), ip, font=font("mono", 19), fill=INK_TEXT + (alpha,))
        draw.text((xs[1] + slide, y + 18), last or "—", font=font("mono", 17), fill=MUTED_LIGHT + (alpha,))
        draw.text((xs[2] + slide, y + 18), mac, font=font("mono", 16), fill=MUTED_LIGHT + (alpha,))
        color = ORANGE if never == "是" else MUTED_LIGHT
        draw.text((xs[3] + slide, y + 18), never, font=font("cn", 19), fill=color + (alpha,))
    draw.text((ox + 34, oy + height - 42), "UTF-8-SIG  ·  EXCEL READY", font=font("tech", 13), fill=MUTED_LIGHT + (180,))
    return card, pad


def scene_report(local_t: float) -> Image.Image:
    image = base_light(8.2 + local_t)
    draw = ImageDraw.Draw(image)
    draw_tracked(
        draw,
        (112, 148),
        "04  /  REPORT VIEW",
        font("tech", 17),
        ORANGE + (240,),
        tracking=2.2,
    )
    draw.text((110, 188), "报表，", font=font("cn", 76), fill=INK_TEXT + (250,))
    draw.text((110, 282), "直接在软件里看", font=font("cn", 76), fill=ORANGE + (250,))
    draw.text(
        (114, 408),
        "从未活跃 IP，一列看清",
        font=font("cn_reg", 34),
        fill=(86, 91, 101, 240),
    )

    pill_p = ease_out_back(clamp((local_t - 0.4) / 0.5))
    if pill_p > 0:
        draw.rounded_rectangle(
            (112, 500, 430, 596),
            radius=24,
            fill=(255, 236, 224, 255),
            outline=(255, 190, 150, 255),
            width=1,
        )
        draw.text((136, 520), "从未活跃", font=font("cn_reg", 18), fill=(150, 98, 67, 240))
        draw.text((136, 550), "18", font=font("latin", 62), fill=ORANGE + (255,))
        draw.text((288, 566), "个 IP", font=font("cn_reg", 20), fill=(150, 98, 67, 240))

    for idx, label in enumerate(("7 天", "15 天")):
        p = clamp((local_t - 0.6 - idx * 0.1) / 0.4)
        if p <= 0:
            continue
        x = 112 + idx * 130
        draw.rounded_rectangle(
            (x, 636, x + 112, 684),
            radius=24,
            fill=(255, 255, 255, int(190 * p)),
            outline=(215, 207, 196, int(255 * p)),
            width=1,
        )
        draw.text((x + 56, 660), label, font=font("cn_reg", 21), fill=INK_TEXT + (int(240 * p),), anchor="mm")

    draw.text((114, 736), "CSV  ·  MAC  ·  NEVER ACTIVE", font=font("tech", 14), fill=(143, 136, 126, 220))
    draw.line((114, 780, 780, 780), fill=(27, 31, 39, 26), width=1)
    draw.text((114, 806), "自动生成 · 软件内预览 · 一键打开", font=font("cn_reg", 20), fill=(120, 115, 107, 230))

    card, pad = render_report_card(local_t)
    p = clamp((local_t - 0.05) / 0.65)
    eased = ease_out_expo(p)
    angle = -5.0 * (1 - eased)
    scale = 0.94 + 0.06 * eased
    paste_card(
        image,
        card,
        int(850 + (1 - eased) * 100),
        146,
        pad,
        alpha=eased,
        angle=angle,
        scale=scale,
    )
    hud(image, 8.2 + local_t, light=True, index="04")
    return image


# ------------------------------------------------------------------ scene 05


LOG_LINES = (
    ("23:30:01", "INFO", "启动扫描：192.168.0.0/24, 192.168.1.0/24"),
    ("23:30:02", "INFO", "后端 SendARP · 254 个地址"),
    ("23:30:03", "INFO", "在线 12 · 离线 242"),
    ("23:30:04", "INFO", "MAC 已写入库存"),
    ("23:30:05", "INFO", "生成报表 IP未使用清单_7天_20261017.csv"),
    ("23:30:05", "INFO", "生成报表 IP未使用清单_15天_20261017.csv"),
    ("23:30:06", "WARN", "never_active 数量增加 2"),
    ("23:30:07", "ERROR", "timeout while probing 10.10.0.88"),
    ("23:30:07", "INFO", "重试成功 · 扫描继续"),
    ("23:30:08", "INFO", "托盘通知已发送"),
)


def scene_logs(local_t: float) -> Image.Image:
    image = base_dark(11.2 + local_t, 0.8)
    draw = ImageDraw.Draw(image)
    card, pad = card_image(1030, 690, 24, DARK_CARD, DARK_BORDER, 38)
    cd = ImageDraw.Draw(card)
    ox, oy = pad, pad
    cd.text((ox + 34, oy + 30), "LOGS", font=font("mono", 20), fill=ORANGE + (240,))
    cd.text((ox + 110, oy + 31), "ip_activity_auditor.log", font=font("mono", 15), fill=MUTED_DARK + (210,))
    for idx, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        cd.ellipse((ox + 890 + idx * 26, oy + 30, ox + 904 + idx * 26, oy + 44), fill=color + (220,))
    cd.line((ox + 34, oy + 76, ox + 996, oy + 76), fill=(255, 255, 255, 20), width=1)

    # search
    cd.rounded_rectangle((ox + 34, oy + 98, ox + 540, oy + 142), radius=12, fill=(255, 255, 255, 12), outline=(255, 255, 255, 34), width=1)
    cd.text((ox + 52, oy + 110), "⌕", font=font("latin_reg", 22), fill=MUTED_DARK + (240,))
    typed = "timeout"[: int(clamp((local_t - 0.15) / 0.06) * 7)]
    cd.text((ox + 84, oy + 112), typed, font=font("mono", 17), fill=CREAM + (235,))
    if (local_t * 2.5) % 1 < 0.55:
        cursor_x = ox + 84 + cd.textlength(typed, font=font("mono", 17)) + 2
        cd.rectangle((cursor_x, oy + 112, cursor_x + 2, oy + 134), fill=ORANGE + (240,))

    filters = (("ALL", False), ("INFO", False), ("WARN", False), ("ERROR", True))
    x = ox + 570
    for label, active in filters:
        width = 76 if label != "ERROR" else 90
        cd.rounded_rectangle(
            (x, oy + 99, x + width, oy + 141),
            radius=11,
            fill=(ORANGE + (40,)) if active else (255, 255, 255, 10),
            outline=(ORANGE + (150,)) if active else (255, 255, 255, 30),
            width=1,
        )
        cd.text((x + width / 2, oy + 120), label, font=font("tech", 13), fill=(ORANGE if active else MUTED_DARK) + (240,), anchor="mm")
        x += width + 10

    colors = {"INFO": GREEN, "WARN": YELLOW, "ERROR": RED}
    row_height = 44
    scroll = int(local_t * 55) % (len(LOG_LINES) * row_height)
    for idx in range(-1, 11):
        line_index = idx + scroll // row_height
        item = LOG_LINES[line_index % len(LOG_LINES)]
        timestamp, level, message = item
        y = oy + 176 + idx * row_height - (scroll % row_height)
        if y < oy + 160 or y > oy + 620:
            continue
        if level == "ERROR":
            cd.rounded_rectangle((ox + 30, y - 4, ox + 1000, y + 34), radius=8, fill=(RED[0], RED[1], RED[2], 18))
        cd.text((ox + 36, y + 8), timestamp, font=font("mono", 15), fill=MUTED_DARK + (210,))
        cd.text((ox + 148, y + 8), level, font=font("mono", 15), fill=colors.get(level, GREEN) + (235,))
        cd.text((ox + 242, y + 8), message, font=font("mono", 16), fill=CREAM + (225,))
    cd.text((ox + 34, oy + 646), "最近 2000 行 · 关键字过滤 · 级别过滤", font=font("tech", 13), fill=MUTED_DARK + (190,))

    p = ease_out_expo(clamp(local_t / 0.45))
    paste_card(image, card, int(92 - (1 - p) * 200), 196, pad, alpha=p)

    draw_tracked(draw, (1220, 220), "TRAY  /  BACKGROUND", font("tech", 17), ORANGE + (240,), tracking=2.2)
    draw.text((1218, 252), "托盘静默运行", font=font("cn", 56), fill=CREAM + (250,))
    draw.text((1222, 328), "关闭窗口，扫描继续", font=font("cn_reg", 27), fill=MUTED_DARK + (230,))

    # tray notification
    np_ = ease_out_back(clamp((local_t - 0.35) / 0.5))
    if np_ > 0:
        nx = int(1218 + (1 - np_) * 120)
        ny = 390
        alpha = int(255 * clamp(np_ * 1.3))
        draw.rounded_rectangle((nx, ny, nx + 590, ny + 158), radius=20, fill=(20, 29, 42, alpha), outline=(255, 255, 255, 36), width=1)
        logo = Image.open(ASSETS / "logo.png").convert("RGBA").resize((54, 54), Image.Resampling.LANCZOS)
        alpha_composite(image, logo, (nx + 22, ny + 22), clamp(np_ * 1.3))
        draw.text((nx + 94, ny + 26), "扫描完成", font=font("cn", 23), fill=CREAM + (alpha,))
        draw.text((nx + 94, ny + 64), "在线 12 台 · 耗时 4.2s", font=font("cn_reg", 18), fill=MUTED_DARK + (alpha,))
        draw.text((nx + 94, ny + 102), "刚刚 · IP 活跃度审计", font=font("tech", 13), fill=MUTED_DARK + (180,))
        draw.line((nx + 94, ny + 120, nx + 300, ny + 120), fill=(255, 255, 255, 18), width=1)

    # tray icon pulse
    icon_p = clamp((local_t - 0.2) / 0.4)
    if icon_p > 0:
        cx, cy = 1720, 220
        pulse = (math.sin(local_t * 7) + 1) / 2
        for ring in range(3):
            radius = 44 + ring * 18 + pulse * 8
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=ORANGE + (int(35 * icon_p),), width=1)
        logo = Image.open(ASSETS / "logo.png").convert("RGBA").resize((72, 72), Image.Resampling.LANCZOS)
        alpha_composite(image, logo, (cx - 36, cy - 36), icon_p)
        draw.text((cx, cy + 60), "TRAY", font=font("tech", 12), fill=MUTED_DARK + (int(200 * icon_p),), anchor="ma")

    chips = ("后台扫描", "完成通知", "开机自启")
    for idx, label in enumerate(chips):
        p = clamp((local_t - 0.65 - idx * 0.1) / 0.35)
        if p <= 0:
            continue
        x = 1218 + idx * 154
        draw.rounded_rectangle((x, 650, x + 138, 694), radius=22, fill=(255, 255, 255, int(12 * p)), outline=(255, 255, 255, int(38 * p)), width=1)
        draw.text((x + 69, 672), label, font=font("cn_reg", 18), fill=CREAM_SOFT + (int(220 * p),), anchor="mm")
    hud(image, 11.2 + local_t, index="05")
    return image


# ------------------------------------------------------------------ scene 06


def scene_outro(local_t: float) -> Image.Image:
    image = base_dark(13.4 + local_t, 1.2)
    draw = ImageDraw.Draw(image)
    draw.text((1180, 610), "IPAA", font=font("latin", 360), fill=(255, 255, 255, 8))

    p = ease_out_back(clamp(local_t / 0.55))
    logo = Image.open(ASSETS / "logo.png").convert("RGBA")
    size = int(220 * (0.8 + 0.2 * p))
    logo = logo.resize((size, size), Image.Resampling.LANCZOS)
    glow = logo.filter(ImageFilter.GaussianBlur(22))
    glow.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.55)))
    alpha_composite(image, glow, (W / 2 - size / 2, 290 - size / 2 + 110), p)
    alpha_composite(image, logo, (W / 2 - size / 2, 290 - size / 2 + 110), p)

    name_p = clamp((local_t - 0.2) / 0.5)
    draw.text(
        (W / 2, 510),
        "IP 活跃度审计",
        font=font("cn", 64),
        fill=CREAM + (int(255 * name_p),),
        anchor="ma",
    )
    draw_tracked(
        draw,
        (W / 2, 590),
        "IP ACTIVITY AUDITOR",
        font("tech", 22),
        ORANGE + (int(240 * name_p),),
        tracking=4.0,
        anchor="ma",
    )

    tags = ("多网段", "MAC 审计", "无需 Npcap", "托盘运行")
    total_w = 0
    widths = []
    for label in tags:
        w = 40 + len(label) * 22
        widths.append(w)
        total_w += w
    total_w += 12 * (len(tags) - 1)
    x = W / 2 - total_w / 2
    for idx, label in enumerate(tags):
        p = clamp((local_t - 0.45 - idx * 0.07) / 0.35)
        if p <= 0:
            x += widths[idx] + 12
            continue
        w = widths[idx]
        draw.rounded_rectangle(
            (x, 660, x + w, 704),
            radius=22,
            fill=(255, 255, 255, int(13 * p)),
            outline=(255, 255, 255, int(40 * p)),
            width=1,
        )
        draw.text((x + w / 2, 682), label, font=font("cn_reg", 18), fill=CREAM_SOFT + (int(225 * p),), anchor="mm")
        x += w + 12

    git_p = clamp((local_t - 0.8) / 0.4)
    draw.text(
        (W / 2, 780),
        "github.com/miralexand/IPActivityAuditor",
        font=font("mono", 20),
        fill=MUTED_DARK + (int(220 * git_p),),
        anchor="ma",
    )
    draw.text(
        (W / 2, 824),
        "Mulan PSL v2  ·  Open Source",
        font=font("tech", 14),
        fill=MUTED_DARK + (int(170 * git_p),),
        anchor="ma",
    )

    # orange rule
    rule_p = ease_out_expo(clamp((local_t - 0.55) / 0.5))
    draw.line((W / 2 - 220 * rule_p, 740, W / 2 + 220 * rule_p, 740), fill=ORANGE + (200,), width=2)
    hud(image, 13.4 + local_t, index="06")
    return image


# ------------------------------------------------------------------ transitions


def add_boundary_flash(image: Image.Image, t: float, boundary: float, color, light_cut=False):
    delta = t - boundary
    if abs(delta) > 0.12:
        return image
    strength = math.exp(-((delta / 0.045) ** 2))
    if strength <= 0.02:
        return image
    overlay = Image.new("RGBA", (W, H), color + (int(205 * strength),))
    draw = ImageDraw.Draw(overlay)
    for idx in range(16):
        y = 60 + idx * 64 + int(math.sin(idx * 1.7) * 9)
        x0 = int((-200 + (idx * 137) % 420) * (1 - strength))
        draw.line((x0, y, x0 + 380 + (idx % 5) * 90, y), fill=(255, 255, 255, int(70 * strength)), width=2)
    image.alpha_composite(overlay)
    return image


def render_frame(t: float) -> Image.Image:
    if t < 2.4:
        image = scene_hook(t)
        image = add_boundary_flash(image, t, 2.4, ORANGE)
    elif t < 5.4:
        image = scene_scan(t - 2.4)
        image = add_boundary_flash(image, t, 5.4, ORANGE)
    elif t < 8.2:
        image = scene_multi(t - 5.4)
        image = add_boundary_flash(image, t, 8.2, (255, 233, 215))
    elif t < 11.2:
        image = scene_report(t - 8.2)
        image = add_boundary_flash(image, t, 11.2, ORANGE)
    elif t < 13.4:
        image = scene_logs(t - 11.2)
        image = add_boundary_flash(image, t, 13.4, ORANGE)
    else:
        image = scene_outro(t - 13.4)

    image.alpha_composite(grain_layer(int(t * 12) % 8), (0, 0))
    image.alpha_composite(vignette_layer(t >= 8.2 and t < 11.2), (0, 0))

    if t > DURATION - 0.45:
        fade = clamp((t - (DURATION - 0.45)) / 0.45)
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * fade)))
        image.alpha_composite(overlay)
    return image


# ------------------------------------------------------------------ audio


def synth_audio(path: Path) -> None:
    sr = 48000
    total = int(sr * DURATION)
    time_axis = np.arange(total) / sr
    left = np.zeros(total, dtype=np.float64)
    right = np.zeros(total, dtype=np.float64)

    bpm = 126.0
    beat = 60.0 / bpm
    bar = beat * 4

    def add(signal, start, target_l, target_r, gain=1.0):
        index = int(start * sr)
        if index >= total:
            return
        length = min(len(signal), total - index)
        if length <= 0:
            return
        target_l[index : index + length] += signal[:length] * gain
        target_r[index : index + length] += signal[:length] * gain

    def kick(duration=0.36):
        t = np.arange(int(sr * duration)) / sr
        freq = 44 + 95 * np.exp(-t / 0.045)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        body = np.sin(phase) * np.exp(-t / 0.16)
        click = np.random.default_rng(3).normal(0, 1, len(t)) * np.exp(-t / 0.012) * 0.25
        return np.tanh((body + click) * 1.6)

    def hat(duration=0.055, tone=0.5):
        t = np.arange(int(sr * duration)) / sr
        noise = np.random.default_rng(11).normal(0, 1, len(t))
        noise = np.diff(noise, prepend=0)
        return noise * np.exp(-t / 0.018) * tone

    def snare(duration=0.2):
        t = np.arange(int(sr * duration)) / sr
        noise = np.random.default_rng(21).normal(0, 1, len(t)) * np.exp(-t / 0.09)
        tone = np.sin(2 * np.pi * 175 * t) * np.exp(-t / 0.06) * 0.6
        return (noise + tone) * 0.55

    def impact(duration=0.5):
        t = np.arange(int(sr * duration)) / sr
        freq = 30 + 90 * np.exp(-t / 0.08)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        body = np.sin(phase) * np.exp(-t / 0.28)
        noise = np.random.default_rng(31).normal(0, 1, len(t)) * np.exp(-t / 0.14) * 0.25
        return np.tanh((body + noise) * 1.4)

    def riser(duration=0.55):
        t = np.arange(int(sr * duration)) / sr
        noise = np.random.default_rng(41).normal(0, 1, len(t))
        filtered = np.zeros_like(noise)
        state = 0.0
        cutoff = 0.01 + 0.15 * (t / duration) ** 2
        for i, sample in enumerate(noise):
            state += cutoff[i] * (sample - state)
            filtered[i] = state
        return filtered * (t / duration) ** 2 * 1.6

    kick_signal = kick()
    snare_signal = snare()
    hat_signal = hat()
    hat_open = hat(0.16, 0.28)
    impact_signal = impact()
    riser_signal = riser()

    total_beats = int(DURATION / beat) + 1
    for b in range(total_beats):
        start = b * beat
        add(kick_signal, start, left, right, 0.95)
        if b % 4 in (1, 3):
            add(snare_signal, start, left, right, 0.45)
        add(hat_signal, start + beat * 0.5, left, right, 0.30)
        add(hat_signal, start + beat * 0.25, left, right, 0.12)
        add(hat_signal, start + beat * 0.75, left, right, 0.12)
    for b in range(total_beats // 2):
        add(hat_open, b * beat * 2 + beat * 1.5, left, right, 0.12)

    # bass and pad
    chords = [
        (110.0, (220.0, 261.63, 329.63)),
        (87.31, (174.61, 220.0, 261.63)),
        (130.81, (261.63, 329.63, 392.0)),
        (98.0, (196.0, 246.94, 293.66)),
    ]
    for b in range(int(DURATION / beat) + 1):
        chord = chords[int(b // 4) % len(chords)]
        root = chord[0]
        start = b * beat
        length = beat * 0.92
        t = np.arange(int(sr * length)) / sr
        attack = np.clip(t / 0.02, 0, 1)
        decay = np.exp(-t / 0.45)
        bass_wave = np.sin(2 * np.pi * root * t) + 0.28 * np.sin(2 * np.pi * root * 2 * t)
        add(bass_wave * attack * decay, start, left, right, 0.20)

    for bar_index in range(int(DURATION / bar) + 1):
        root, notes = chords[bar_index % len(chords)]
        start = bar_index * bar
        length = min(bar * 1.02, DURATION - start + 0.1)
        if length <= 0:
            continue
        t = np.arange(int(sr * length)) / sr
        env = np.clip(t / 0.35, 0, 1) * np.exp(-t / 2.6)
        for note_index, note in enumerate(notes):
            detune = 1.0 + (0.002 if note_index == 1 else 0.0)
            left_note = np.sin(2 * np.pi * note * detune * t) * 0.5
            right_note = np.sin(2 * np.pi * note / detune * t) * 0.5
            add(left_note * env, start, left, right, 0.055)
            add(right_note * env, start, left, right, 0.045)

    # sidechain pad and impact points
    for boundary in (0.0, 2.4, 5.4, 8.2, 11.2, 13.4):
        add(impact_signal, boundary, left, right, 0.6)
    for start in (1.95, 4.95, 7.75, 10.75, 12.95):
        add(riser_signal, start, left, right, 0.14)

    # subtle stereo movement
    delay = int(sr * 0.012)
    right_delayed = np.zeros_like(right)
    right_delayed[delay:] = right[:-delay] * 0.16
    right += right_delayed

    mix = np.stack([left, right], axis=1)
    mix = np.tanh(mix * 1.1)
    peak = np.max(np.abs(mix))
    if peak > 0:
        mix *= 0.9 / peak
    fade_in = np.clip(time_axis / 0.08, 0, 1)
    fade_out = np.clip((DURATION - time_axis) / 0.5, 0, 1)
    mix *= (fade_in * fade_out)[:, None]
    pcm = (mix * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ export


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        Path(r"D:\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"),
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("ffmpeg not found. Install ffmpeg or update the path in tools/make_promo_video.py.")


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    audio_path = MEDIA / "ipaa-promo-audio.wav"
    print("Generating soundtrack...")
    synth_audio(audio_path)

    ffmpeg = find_ffmpeg()
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "17",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(OUTPUT),
    ]
    print(f"Rendering {FRAMES} frames at {FPS}fps...")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdin is not None
    poster_frame = None
    contact_frames = {}
    contact_times = (0.8, 3.2, 6.4, 9.8, 12.2, 14.2)
    contact_indices = {int(time_value * FPS): time_value for time_value in contact_times}
    try:
        for index in range(FRAMES):
            t = index / FPS
            frame = render_frame(t)
            if index == int(9.8 * FPS):
                poster_frame = frame.convert("RGB")
            if index in contact_indices:
                contact_frames[contact_indices[index]] = frame.convert("RGB")
            process.stdin.write(frame.convert("RGB").tobytes())
            if index % 60 == 0:
                print(f"  frame {index:04d} / {FRAMES}  ({t:4.1f}s)")
        process.stdin.close()
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"ffmpeg exited with code {code}")
    finally:
        if process.poll() is None:
            process.kill()

    if poster_frame is not None:
        poster_frame.save(MEDIA / "ipaa-promo-poster.jpg", quality=92)

    if contact_frames:
        thumbs = []
        for time_value in contact_times:
            frame = contact_frames.get(time_value)
            if frame is None:
                continue
            thumb = frame.resize((640, 360), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (640, 396), (10, 13, 19))
            canvas.paste(thumb, (0, 0))
            d = ImageDraw.Draw(canvas)
            d.text((14, 370), f"{time_value:04.1f}s", font=font("mono", 14), fill=(180, 190, 205))
            thumbs.append(canvas)
        if thumbs:
            sheet = Image.new("RGB", (640 * 2 + 24, 396 * 3 + 32), (8, 11, 16))
            for idx, thumb in enumerate(thumbs[:6]):
                x = (idx % 2) * (640 + 16)
                y = (idx // 2) * (396 + 12)
                sheet.paste(thumb, (x, y))
            sheet.save(MEDIA / "ipaa-promo-contact-sheet.jpg", quality=92)

    audio_path.unlink(missing_ok=True)
    print(f"Video written: {OUTPUT}")
    print(f"Poster: {MEDIA / 'ipaa-promo-poster.jpg'}")
    print(f"Contact sheet: {MEDIA / 'ipaa-promo-contact-sheet.jpg'}")


if __name__ == "__main__":
    main()
