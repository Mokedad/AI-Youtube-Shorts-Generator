"""Cartoon explainer engine: storyboard JSON -> voiced, captioned, animated 9:16 Short.

Usage: python engine.py <storyboard.json> <out.mp4>

Storyboard:
{
  "title": "...", "hook": "...", "voice": "am_michael", "speed": 1.08,
  "lines": [
    {"say": "Narration for this beat.", "scene": {"type": "hero", "sprite": "astronaut", "title": "TEXT"}},
    ...
  ]
}

Each line is voiced separately (free local Kokoro TTS), so every scene lasts exactly
as long as its narration. Scene types: hero, counter, versus, list, bigtext, cannon,
station_orbit, outro. Sprites: see SPRITES.
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from render import build_ass  # noqa: E402

W, H, FPS = 1080, 1920, 30
INK = (20, 17, 43)
WHITE = (245, 246, 252)
YELLOW = (255, 214, 40)
RED = (255, 80, 80)
FONT = "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf"
TTS_DIR = os.environ.get("KOKORO_DIR", "/home/user/shorts-studio/tts")
SS = 2
GAP = 0.28  # pause between narration lines (s)


# ---------------------------------------------------------------- helpers
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def pop(t, d=0.35):
    t = clamp(t / d)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def smooth(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def font(size):
    return ImageFont.truetype(FONT, size)


def sprite_canvas(w, h, fn):
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    fn(ImageDraw.Draw(im), SS)
    return im.resize((w, h), Image.LANCZOS)


def paste(frame, spr, cx, cy, scale=1.0, angle=0.0, alpha=1.0):
    if scale <= 0.02 or alpha <= 0.02:
        return
    s = spr
    if abs(scale - 1.0) > 1e-3:
        s = s.resize((max(1, int(s.width * scale)), max(1, int(s.height * scale))), Image.BILINEAR)
    if angle:
        s = s.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1.0:
        s = s.copy()
        s.putalpha(s.split()[3].point(lambda v: int(v * alpha)))
    frame.alpha_composite(s, (int(cx - s.width / 2), int(cy - s.height / 2)))


def text_sprite(txt, size, fill=WHITE, stroke=10, max_w=960, box=None):
    f = font(size)
    lines, line = [], ""
    for wd in str(txt).split():
        test = (line + " " + wd).strip()
        if f.getlength(test) > max_w and line:
            lines.append(line)
            line = wd
        else:
            line = test
    lines.append(line)
    lh = int(size * 1.1)
    pad = 28 if box else 0
    h = lh * len(lines) + stroke * 2 + pad * 2
    im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if box:
        wmax = max(f.getlength(ln) for ln in lines)
        d.rounded_rectangle([W / 2 - wmax / 2 - pad, 0, W / 2 + wmax / 2 + pad, h - 1], radius=26,
                            fill=box, outline=INK, width=8)
    for i, ln in enumerate(lines):
        d.text((W / 2, pad + stroke + i * lh), ln, font=f, fill=fill, anchor="ma",
               stroke_width=0 if box else stroke, stroke_fill=INK)
    return im


# ---------------------------------------------------------------- sprites
def sp_earth(k, r=250):
    def fn(d, s):
        R, o = r * s, 10 * s
        d.ellipse([o, o, o + 2 * R, o + 2 * R], fill=(64, 140, 255))
        for cx, cy, rx, ry in [(0.35, 0.3, 0.22, 0.14), (0.62, 0.55, 0.2, 0.18),
                               (0.3, 0.68, 0.14, 0.1), (0.72, 0.25, 0.1, 0.07)]:
            x0, y0 = o + (cx - rx) * 2 * R, o + (cy - ry) * 2 * R
            d.ellipse([x0, y0, x0 + rx * 4 * R, y0 + ry * 4 * R], fill=(88, 204, 120))
        d.ellipse([o + 0.3 * R, o + 0.22 * R, o + 0.6 * R, o + 0.4 * R], fill=(150, 200, 255))
        d.ellipse([o, o, o + 2 * R, o + 2 * R], outline=INK, width=10 * s)
    return sprite_canvas(2 * r + 20, 2 * r + 20, fn)


def sp_planet(color, spots, r=230, ring=False):
    def fn(d, s):
        R, o = r * s, 10 * s
        if ring:
            oy = o + R * 0.7
        else:
            oy = o
        cx0 = o + (0.5 * R if ring else 0)
        d.ellipse([cx0, oy, cx0 + 2 * R, oy + 2 * R], fill=color)
        rnd = random.Random(len(spots) + r)
        for sc in spots:
            rr = R * rnd.uniform(0.12, 0.25)
            x = cx0 + R + rnd.uniform(-0.55, 0.55) * R
            y = oy + R + rnd.uniform(-0.55, 0.55) * R
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=sc)
        d.ellipse([cx0, oy, cx0 + 2 * R, oy + 2 * R], outline=INK, width=10 * s)
        if ring:
            d.ellipse([o, oy + 0.75 * R, o + 3 * R, oy + 1.25 * R], outline=(255, 214, 140), width=22 * s)
            d.ellipse([o, oy + 0.75 * R, o + 3 * R, oy + 1.25 * R], outline=INK, width=5 * s)
    w = int(2 * r + 20 + (r if ring else 0))
    h = int(2 * r + 20 + (1.4 * r if ring else 0))
    return sprite_canvas(w, h, fn)


def sp_astronaut():
    def fn(d, k):
        def rr(box, rad, fill):
            d.rounded_rectangle([v * k for v in box], radius=rad * k, fill=fill, outline=INK, width=7 * k)
        rr((40, 150, 100, 260), 26, (230, 233, 245))
        rr((200, 150, 260, 260), 26, (230, 233, 245))
        rr((85, 255, 140, 370), 26, (230, 233, 245))
        rr((160, 255, 215, 370), 26, (230, 233, 245))
        rr((60, 120, 240, 300), 50, WHITE)
        rr((95, 175, 205, 230), 14, (255, 120, 90))
        d.ellipse([k * 70, k * 10, k * 230, k * 170], fill=WHITE, outline=INK, width=7 * k)
        d.rounded_rectangle([k * 95, k * 50, k * 205, k * 130], radius=38 * k, fill=(40, 70, 160), outline=INK, width=6 * k)
        d.ellipse([k * 110, k * 62, k * 140, k * 82], fill=(170, 200, 255))
    return sprite_canvas(300, 380, fn)


def sp_iss(scale=1.0):
    def fn(d, k):
        s = scale * k
        def box(x0, y0, x1, y1, fill, rad=6):
            d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s], radius=rad * s, fill=fill, outline=INK, width=max(2, int(5 * s)))
        for x in (10, 90, 270, 350):
            box(x, 20, x + 60, 180, (60, 110, 220), 4)
            for gy in range(40, 180, 25):
                d.line([(x + 4) * s, gy * s, (x + 56) * s, gy * s], fill=(130, 175, 255), width=max(1, int(2 * s)))
        box(70, 92, 350, 108, (180, 185, 200), 3)
        box(160, 70, 260, 130, (240, 242, 250), 20)
        box(190, 40, 230, 160, (240, 242, 250), 14)
    return sprite_canvas(int(420 * scale), int(200 * scale), fn)


def sp_rocket():
    def fn(d, k):
        d.polygon([(110 * k, 300 * k), (60 * k, 400 * k), (110 * k, 380 * k)], fill=(255, 90, 90), outline=INK, width=7 * k)
        d.polygon([(190 * k, 300 * k), (240 * k, 400 * k), (190 * k, 380 * k)], fill=(255, 90, 90), outline=INK, width=7 * k)
        d.rounded_rectangle([100 * k, 120 * k, 200 * k, 400 * k], radius=40 * k, fill=WHITE, outline=INK, width=8 * k)
        d.polygon([(100 * k, 140 * k), (150 * k, 20 * k), (200 * k, 140 * k)], fill=(255, 90, 90), outline=INK, width=8 * k)
        d.ellipse([122 * k, 180 * k, 178 * k, 236 * k], fill=(90, 170, 255), outline=INK, width=7 * k)
    return sprite_canvas(300, 420, fn)


def sp_flame():
    def fn(d, k):
        d.polygon([(110 * k, 0), (190 * k, 0), (150 * k, 200 * k)], fill=(255, 150, 40))
        d.polygon([(125 * k, 0), (175 * k, 0), (150 * k, 120 * k)], fill=(255, 230, 90))
    return sprite_canvas(300, 210, fn)


def sp_sun():
    def fn(d, k):
        c = 260 * k
        for i in range(12):
            a = i * math.pi / 6
            d.polygon([(c + 170 * k * math.cos(a - 0.12), c + 170 * k * math.sin(a - 0.12)),
                       (c + 250 * k * math.cos(a), c + 250 * k * math.sin(a)),
                       (c + 170 * k * math.cos(a + 0.12), c + 170 * k * math.sin(a + 0.12))],
                      fill=(255, 170, 40), outline=INK)
        d.ellipse([c - 175 * k, c - 175 * k, c + 175 * k, c + 175 * k], fill=(255, 214, 40), outline=INK, width=10 * k)
    return sprite_canvas(520, 520, fn)


def sp_blackhole():
    def fn(d, k):
        d.ellipse([20 * k, 170 * k, 580 * k, 330 * k], outline=(255, 150, 50), width=40 * k)
        d.ellipse([170 * k, 100 * k, 430 * k, 360 * k], fill=(5, 5, 12), outline=(255, 200, 90), width=10 * k)
        d.arc([20 * k, 170 * k, 580 * k, 330 * k], start=0, end=180, fill=(255, 170, 60), width=40 * k)
    return sprite_canvas(600, 460, fn)


def sp_satellite():
    def fn(d, k):
        d.rectangle([10 * k, 80 * k, 120 * k, 160 * k], fill=(60, 110, 220), outline=INK, width=6 * k)
        d.rectangle([280 * k, 80 * k, 390 * k, 160 * k], fill=(60, 110, 220), outline=INK, width=6 * k)
        d.rectangle([120 * k, 112 * k, 280 * k, 128 * k], fill=(180, 185, 200), outline=INK, width=4 * k)
        d.rounded_rectangle([150 * k, 60 * k, 250 * k, 180 * k], radius=14 * k, fill=(255, 200, 70), outline=INK, width=7 * k)
        d.line([200 * k, 60 * k, 200 * k, 20 * k], fill=INK, width=6 * k)
        d.ellipse([186 * k, 6 * k, 214 * k, 34 * k], fill=RED, outline=INK, width=4 * k)
    return sprite_canvas(400, 200, fn)


def sp_asteroid():
    def fn(d, k):
        rnd = random.Random(5)
        pts = []
        for i in range(14):
            a = i / 14 * 2 * math.pi
            r = rnd.uniform(150, 200) * k
            pts.append((210 * k + r * math.cos(a), 210 * k + r * math.sin(a)))
        d.polygon(pts, fill=(150, 140, 135), outline=INK, width=9 * k)
        for x, y, r in [(150, 160, 30), (260, 230, 40), (190, 280, 22)]:
            d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=(115, 105, 100), outline=INK, width=5 * k)
    return sprite_canvas(420, 420, fn)


def sp_clock():
    def fn(d, k):
        d.ellipse([10 * k, 10 * k, 390 * k, 390 * k], fill=WHITE, outline=INK, width=12 * k)
        for i in range(12):
            a = i * math.pi / 6
            d.line([200 * k + 150 * k * math.cos(a), 200 * k + 150 * k * math.sin(a),
                    200 * k + 170 * k * math.cos(a), 200 * k + 170 * k * math.sin(a)], fill=INK, width=8 * k)
    return sprite_canvas(400, 400, fn)


def sp_sleepingbag():
    def fn(d, k):
        d.rounded_rectangle([80 * k, 120 * k, 260 * k, 460 * k], radius=80 * k, fill=(90, 190, 120), outline=INK, width=8 * k)
        d.line([170 * k, 200 * k, 170 * k, 440 * k], fill=INK, width=5 * k)
        d.ellipse([110 * k, 20 * k, 230 * k, 140 * k], fill=(255, 205, 170), outline=INK, width=7 * k)
        d.arc([135 * k, 70 * k, 165 * k, 95 * k], 200, 340, fill=INK, width=5 * k)
        d.arc([175 * k, 70 * k, 205 * k, 95 * k], 200, 340, fill=INK, width=5 * k)
    return sprite_canvas(340, 480, fn)


def sp_thermo():
    def fn(d, k):
        d.rounded_rectangle([110 * k, 10 * k, 190 * k, 360 * k], radius=40 * k, fill=WHITE, outline=INK, width=8 * k)
        d.ellipse([75 * k, 300 * k, 225 * k, 450 * k], fill=RED, outline=INK, width=8 * k)
        d.rounded_rectangle([132 * k, 120 * k, 168 * k, 330 * k], radius=18 * k, fill=RED)
    return sprite_canvas(300, 460, fn)


def sp_star():
    def fn(d, k):
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            r = (190 if i % 2 == 0 else 80) * k
            pts.append((200 * k + r * math.cos(a), 200 * k + r * math.sin(a)))
        d.polygon(pts, fill=YELLOW, outline=INK, width=9 * k)
    return sprite_canvas(400, 400, fn)


def sp_drop():
    def fn(d, k):
        pts = []
        for i in range(60):
            t = i / 60 * 2 * math.pi
            x = 150 + 100 * math.sin(t) * (1 - math.cos(t)) / 1.6
            y = 20 + 145 * (1 - math.cos(t))
            pts.append((x * k, y * k))
        d.polygon(pts, fill=(80, 170, 255), outline=INK, width=8 * k)
        d.ellipse([100 * k, 190 * k, 135 * k, 235 * k], fill=(200, 230, 255))
    return sprite_canvas(300, 330, fn)


def sp_mark(ok):
    def fn(d, k):
        d.ellipse([10 * k, 10 * k, 290 * k, 290 * k], fill=(70, 200, 110) if ok else RED, outline=INK, width=10 * k)
        if ok:
            d.line([80 * k, 155 * k, 130 * k, 205 * k, 220 * k, 100 * k], fill=WHITE, width=30 * k, joint="curve")
        else:
            d.line([90 * k, 90 * k, 210 * k, 210 * k], fill=WHITE, width=30 * k)
            d.line([210 * k, 90 * k, 90 * k, 210 * k], fill=WHITE, width=30 * k)
    return sprite_canvas(300, 300, fn)


def sp_car():
    """Generic cartoon sports car (no real-world or game design)."""
    def fn(d, k):
        d.polygon([(30*k,170*k),(60*k,120*k),(170*k,105*k),(250*k,55*k),(380*k,55*k),(450*k,115*k),
                   (560*k,130*k),(590*k,170*k),(590*k,210*k),(30*k,210*k)], fill=(255,80,80), outline=INK, width=8*k)
        d.polygon([(262*k,70*k),(370*k,70*k),(420*k,115*k),(215*k,115*k)], fill=(150,215,255), outline=INK, width=6*k)
        d.line([318*k,70*k,318*k,115*k], fill=INK, width=6*k)
        d.rectangle([540*k,150*k,585*k,170*k], fill=(255,230,120), outline=INK, width=4*k)
        for x in (140, 470):
            d.ellipse([(x-55)*k,155*k,(x+55)*k,265*k], fill=(40,40,55), outline=INK, width=7*k)
            d.ellipse([(x-25)*k,185*k,(x+25)*k,235*k], fill=(200,200,215), outline=INK, width=5*k)
    return sprite_canvas(620, 280, fn)



def sp_bike():
    def fn(d, k):
        for cx in (130, 390):
            d.ellipse([(cx-95)*k, 190*k, (cx+95)*k, 380*k], outline=INK, width=16*k)
            d.ellipse([(cx-80)*k, 205*k, (cx+80)*k, 365*k], outline=(90,200,255), width=8*k)
            for ang in range(0, 360, 45):
                import math as _m
                x = cx + 75 * _m.cos(_m.radians(ang)); y = 285 + 75 * _m.sin(_m.radians(ang))
                d.line([cx*k, 285*k, x*k, y*k], fill=(90,200,255), width=5*k)
        d.line([130*k, 285*k, 260*k, 150*k, 390*k, 285*k], fill=(255,90,90), width=18*k)
        d.line([260*k, 150*k, 230*k, 285*k, 130*k, 285*k], fill=(255,90,90), width=14*k)
        d.line([250*k, 135*k, 290*k, 115*k], fill=INK, width=14*k)
        d.line([380*k, 150*k, 410*k, 150*k], fill=INK, width=12*k)
    return sprite_canvas(520, 400, fn)


def sp_treadmill():
    def fn(d, k):
        d.polygon([(40*k,300*k),(480*k,300*k),(520*k,340*k),(0,340*k)], fill=(70,70,90), outline=INK, width=8*k)
        d.rounded_rectangle([60*k,260*k,460*k,300*k], radius=12*k, fill=(40,40,55), outline=INK, width=6*k)
        d.line([430*k,260*k,440*k,80*k], fill=INK, width=14*k)
        d.rounded_rectangle([380*k,40*k,470*k,90*k], radius=10*k, fill=(255,214,40), outline=INK, width=6*k)
    return sprite_canvas(520, 360, fn)


def sp_weights():
    def fn(d, k):
        d.rounded_rectangle([60*k,120*k,460*k,170*k], radius=20*k, fill=(160,160,175), outline=INK, width=8*k)
        for x in (40, 440):
            d.rounded_rectangle([(x-20)*k,60*k,(x+40)*k,230*k], radius=14*k, fill=(60,60,80), outline=INK, width=8*k)
        d.rounded_rectangle([200*k,250*k,320*k,320*k], radius=16*k, fill=(200,80,80), outline=INK, width=6*k)
        d.line([260*k,250*k,260*k,120*k], fill=(120,220,255), width=10*k)
    return sprite_canvas(500, 340, fn)


def sp_running_astro():
    return sp_astronaut()

def sp_bench():
    def fn(d, k):
        d.rounded_rectangle([40*k,250*k,480*k,290*k], radius=14*k, fill=(90,90,110), outline=INK, width=8*k)
        d.rectangle([120*k,290*k,150*k,360*k], fill=(60,60,80), outline=INK, width=6*k)
        d.rectangle([370*k,290*k,400*k,360*k], fill=(60,60,80), outline=INK, width=6*k)
    return sprite_canvas(520, 380, fn)


def sp_barbell():
    def fn(d, k):
        d.rounded_rectangle([0*k,70*k,520*k,90*k], radius=8*k, fill=(170,170,185), outline=INK, width=5*k)
        for x in (30, 60, 460, 490):
            d.rounded_rectangle([(x-14)*k,30*k,(x+14)*k,130*k], radius=8*k, fill=(200,80,80), outline=INK, width=5*k)
    return sprite_canvas(520, 160, fn)


SPRITE_FACTORIES = {
    "bench": sp_bench,
    "barbell": sp_barbell,
    "bike": sp_bike,
    "treadmill": sp_treadmill,
    "weights": sp_weights,
    "car": sp_car,
    "astronaut": sp_astronaut,
    "earth": lambda: sp_earth(1, 250),
    "earth_big": lambda: sp_earth(1, 520),
    "moon": lambda: sp_planet((200, 200, 210), [(160, 160, 172)] * 6, 220),
    "mars": lambda: sp_planet((230, 110, 70), [(190, 80, 55)] * 5, 230),
    "jupiter": lambda: sp_planet((225, 180, 140), [(200, 140, 100), (240, 220, 190), (210, 90, 70)], 260),
    "saturn": lambda: sp_planet((235, 205, 140), [(215, 180, 120)] * 3, 200, ring=True),
    "sun": sp_sun,
    "iss": lambda: sp_iss(1.3),
    "rocket": sp_rocket,
    "blackhole": sp_blackhole,
    "satellite": sp_satellite,
    "asteroid": sp_asteroid,
    "clock": sp_clock,
    "sleepingbag": sp_sleepingbag,
    "thermometer": sp_thermo,
    "star": sp_star,
    "water": sp_drop,
    "check": lambda: sp_mark(True),
    "cross": lambda: sp_mark(False),
}
_cache = {}


def S(name):
    if name not in _cache:
        if name not in SPRITE_FACTORIES:
            raise KeyError(f"unknown sprite {name!r}; choose from {sorted(SPRITE_FACTORIES)}")
        _cache[name] = SPRITE_FACTORIES[name]()
    return _cache[name]


_tcache = {}


def T(txt, size, fill=WHITE, stroke=10, box=None):
    key = (txt, size, fill, stroke, box)
    if key not in _tcache:
        _tcache[key] = text_sprite(txt, size, fill, stroke, box=box)
    return _tcache[key]


def background():
    rnd = random.Random(3)
    y = np.linspace(0, 1, H)[:, None, None]
    top, bot = np.array([38, 24, 92]), np.array([10, 12, 38])
    arr = (top * (1 - y) + bot * y).repeat(W, axis=1).astype(np.uint8)
    bg = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(bg)
    for _ in range(260):
        x, yy, s = rnd.randrange(W), rnd.randrange(H), rnd.choice([2, 3, 3, 4, 5])
        a = rnd.randint(140, 255)
        d.ellipse([x, yy, x + s, yy + s], fill=(a, a, a))
    return bg.convert("RGBA")


# ---------------------------------------------------------------- scenes
CY = 820          # vertical centre of the action area (captions sit near y=1360)
TITLE_Y = 330


def scene_title(frame, spec, lt):
    if spec.get("title"):
        paste(frame, T(spec["title"], spec.get("title_size", 84), YELLOW if spec.get("title_yellow") else WHITE),
              W / 2, TITLE_Y, scale=pop(lt - 0.1))


def fx(d, kind, lt, cx, cy):
    if kind == "bubbles":
        rnd = random.Random(1)
        for i in range(16):
            ph = rnd.random()
            t = (lt * 0.6 + ph) % 1.0
            x = cx + rnd.uniform(-170, 170) + math.sin(lt * 3 + i) * 12
            y = cy - 120 - t * 420
            r = rnd.uniform(10, 26) * (0.6 + t)
            d.ellipse([x - r, y - r, x + r, y + r], outline=(170, 215, 255, int(255 * (1 - t))), width=5)
    elif kind == "sparkles":
        rnd = random.Random(2)
        for i in range(14):
            x, y = cx + rnd.uniform(-380, 380), cy + rnd.uniform(-360, 360)
            a = abs(math.sin(lt * 3 + i))
            r = 6 + 10 * a
            d.polygon([(x, y - r * 2), (x + r * 0.5, y), (x, y + r * 2), (x - r * 0.5, y)], fill=(255, 230, 120, int(255 * a)))
            d.polygon([(x - r * 2, y), (x, y + r * 0.5), (x + r * 2, y), (x, y - r * 0.5)], fill=(255, 230, 120, int(255 * a)))


def draw_phase(frame, d, move, lt, label):
    """Original astronaut doing a drawn exercise; lt is time into the phase."""
    ph = math.sin(lt * 3.0)
    if move == "squat":
        sy = 0.62 + 0.38 * (0.5 + 0.5 * ph)
        a = S("astronaut")
        a = a.resize((int(a.width * 0.95), int(a.height * 0.95 * sy)), Image.BILINEAR)
        frame.alpha_composite(a, (int(W / 2 - a.width / 2), int(CY + 160 - a.height / 2 + 120 * (1 - sy))))
    elif move == "bench":
        frame.alpha_composite(S("bench"), (int(W / 2 - 260), int(CY + 40)))
        lift = 90 * (0.5 + 0.5 * ph)
        paste(frame, S("barbell"), W / 2, CY - 40 - lift, scale=0.9)
        a = S("astronaut").rotate(90, resample=Image.BICUBIC, expand=True)
        a = a.resize((int(a.width * 0.85), int(a.height * 0.85)), Image.BILINEAR)
        frame.alpha_composite(a, (int(W / 2 - a.width / 2), int(CY + 60 - a.height / 2 + 40)))
    elif move == "bike":
        paste(frame, S("bike"), W / 2 + 80, CY + 150, scale=1.0)
        paste(frame, S("astronaut"), W / 2 - 150, CY - 40 + math.sin(lt * 4) * 14, scale=0.85)
    elif move == "treadmill":
        paste(frame, S("treadmill"), W / 2, CY + 200, scale=1.3)
        paste(frame, S("astronaut"), W / 2 - 40, CY - 40 + abs(math.sin(lt * 9)) * 14, scale=0.8)
    elif move == "broken":
        paste(frame, S("treadmill"), W / 2, CY + 200, scale=1.3, angle=-6)
        paste(frame, T("!", 200, RED, 12), W / 2 + 200, CY - 60, scale=pop(lt, 0.3))
    elif move == "deadlift":
        bar_y = CY + 200 - 180 * smooth(0.5 + 0.5 * ph)
        paste(frame, S("barbell"), W / 2, bar_y, scale=1.0)
        paste(frame, S("astronaut"), W / 2, CY + 60, scale=0.8 + 0.1 * (0.5 + 0.5 * ph))
    elif move == "machine":
        paste(frame, S("weights"), W / 2 + 60, CY + 160, scale=1.1)
        paste(frame, S("astronaut"), W / 2 - 200, CY - 40 + math.sin(lt * 3) * 10, scale=0.8)
    if label:
        paste(frame, T(label, 84, YELLOW, 10), W / 2, CY + 370, scale=pop(lt - 0.2, 0.35))


def sc_exseq(frame, d, lt, dur, spec):
    t = lt
    for ph in spec["phases"]:
        if t < ph["dur"]:
            draw_phase(frame, d, ph["move"], t, ph.get("label"))
            break
        t -= ph["dur"]
    scene_title(frame, spec, lt)



def sc_hero(frame, d, lt, dur, spec):
    spr = S(spec.get("sprite", "astronaut"))
    sc = spec.get("scale", 1.5)
    bob = math.sin(lt * 2.2) * 26
    ang = math.sin(lt * 1.3) * spec.get("wobble", 8)
    paste(frame, spr, W / 2, CY + 40 + bob, scale=sc * pop(lt, 0.45), angle=ang)
    fx(d, spec.get("fx"), lt, W / 2, CY + 40 + bob)
    scene_title(frame, spec, lt)
    if spec.get("stamp"):
        st = spec.get("stamp_at", 0.55) * dur
        if lt >= st:
            col = RED if spec.get("stamp_color", "red") == "red" else YELLOW
            paste(frame, T(spec["stamp"], 150, col, 14), W / 2, CY + 60, scale=0.6 + 0.5 * pop(lt - st, 0.3), angle=-10)


def sc_counter(frame, d, lt, dur, spec):
    a, b = spec.get("from", 0), spec["to"]
    v = a + (b - a) * smooth((lt - 0.3) / max(0.6, dur * 0.55))
    fmt = spec.get("format", "{:,.0f}")
    txt = spec.get("prefix", "") + fmt.format(v) + spec.get("suffix", "")
    paste(frame, text_sprite(txt, spec.get("size", 190), YELLOW, 14), W / 2, TITLE_Y + 40, scale=pop(lt, 0.4))
    if spec.get("label"):
        paste(frame, T(spec["label"], 66), W / 2, TITLE_Y + 210, alpha=clamp((lt - 0.4) / 0.3))
    if spec.get("sprite"):
        cy = CY + 230 + math.sin(lt * 2) * 18
        sc = spec.get("scale", 1.0) * pop(lt - 0.2, 0.45)
        paste(frame, S(spec["sprite"]), W / 2, cy, scale=sc)
        if spec["sprite"] == "clock" and sc > 0.3:
            r = 150 * sc
            ang = -math.pi / 2 + (v - a) / max(1e-6, b - a) * 2 * math.pi
            d.line([W / 2, cy, W / 2 + r * math.cos(ang), cy + r * math.sin(ang)], fill=RED, width=14)
            d.line([W / 2, cy, W / 2, cy - r * 0.6], fill=INK, width=16)
            d.ellipse([W / 2 - 16, cy - 16, W / 2 + 16, cy + 16], fill=INK)


def sc_versus(frame, d, lt, dur, spec):
    for side, x, delay in (("left", W * 0.27, 0.0), ("right", W * 0.73, 0.35)):
        it = spec[side]
        k = pop(lt - delay, 0.45)
        paste(frame, S(it["sprite"]), x, CY + math.sin(lt * 2 + delay * 5) * 16, scale=it.get("scale", 0.7) * k)
        if it.get("label"):
            lab = text_sprite(it["label"], 58, it.get("color", WHITE) if isinstance(it.get("color"), tuple) else
                              (YELLOW if it.get("yellow") else WHITE), 8, max_w=440)
            paste(frame, lab, x, CY + 280, scale=k)
    paste(frame, T(spec.get("vs", "VS"), 110, YELLOW, 12), W / 2, CY, scale=pop(lt - 0.2, 0.4))
    scene_title(frame, spec, lt)


def sc_list(frame, d, lt, dur, spec):
    scene_title(frame, spec, lt)
    items = spec["items"]
    gap = dur * 0.8 / max(1, len(items))
    f = font(64)
    for i, it in enumerate(items):
        t0 = 0.3 + i * gap
        if lt < t0:
            break
        y = TITLE_Y + 240 + i * 190
        k = pop(lt - t0, 0.35)
        paste(frame, S(spec.get("bullet", "check")), 150, y, scale=0.3 * k)
        dx = (1 - smooth((lt - t0) / 0.3)) * 60
        d.text((240 + dx, y), it, font=f, fill=WHITE, anchor="lm", stroke_width=8, stroke_fill=INK)


def sc_bigtext(frame, d, lt, dur, spec):
    if spec.get("sprite"):
        paste(frame, S(spec["sprite"]), W / 2, CY + 230 + math.sin(lt * 2) * 16,
              scale=spec.get("scale", 0.9) * pop(lt - 0.15, 0.45), angle=math.sin(lt * 1.5) * 6)
    paste(frame, T(spec["text"], spec.get("size", 110), YELLOW if spec.get("yellow", True) else WHITE, 12),
          W / 2, TITLE_Y + 120, scale=pop(lt, 0.4))


class Cannon:
    """Newton's cannon with real integration, shared across consecutive cannon scenes."""
    def __init__(self):
        self.ecx, self.ecy, self.er = W // 2, CY + 60, 340
        self.r_mtn = self.er + 52
        self.earth = sp_earth(1, 340)
        self.done = []
        self.trajs = {k: self._traj(v) for k, v in (("short", 0.42), ("mid", 0.68), ("orbit", 1.0))}

    def _traj(self, v_frac, steps=4000):
        r0 = self.r_mtn
        v_circ = math.sqrt(1.0 / r0)
        x, y, vx, vy = 0.0, -r0, v_frac * v_circ, 0.0
        dt = (2 * math.pi * r0 / v_circ) / steps
        pts = [(x, y)]
        for _ in range(int(steps * 1.02)):
            r = math.hypot(x, y)
            vx -= x / r ** 3 * dt
            vy -= y / r ** 3 * dt
            x += vx * dt
            y += vy * dt
            pts.append((x, y))
            if math.hypot(x, y) <= self.er:
                break
        return pts

    def ball(self, d, key, prog, color=YELLOW):
        pts = self.trajs[key]
        n = max(1, int(len(pts) * clamp(prog)))
        for i in range(0, n, max(1, len(pts) // 90)):
            x, y = pts[i]
            d.ellipse([self.ecx + x - 6, self.ecy + y - 6, self.ecx + x + 6, self.ecy + y + 6], fill=(255, 255, 255, 190))
        x, y = pts[n - 1]
        d.ellipse([self.ecx + x - 28, self.ecy + y - 28, self.ecx + x + 28, self.ecy + y + 28], fill=color, outline=INK, width=7)

    def draw(self, frame, d, lt, dur, spec):
        paste(frame, self.earth, self.ecx, self.ecy)
        e, r = self.ecx, self.ecy
        d.polygon([(e - 95, r - self.er + 20), (e, r - self.r_mtn - 8), (e + 95, r - self.er + 20)],
                  fill=(140, 110, 90), outline=INK, width=6)
        for k in self.done:
            self.ball(d, k, 1.0, (200, 200, 200))
        stage = spec.get("stage", "short")
        self.ball(d, stage, (lt - 0.3) / max(0.8, dur * 0.75))
        scene_title(frame, spec, lt)

    def finish(self, spec):
        st = spec.get("stage", "short")
        if st not in self.done and st != "orbit":
            self.done.append(st)


def sc_station_orbit(frame, d, lt, dur, spec):
    ecx, ecy = W // 2, CY + 60
    paste(frame, S("earth"), ecx, ecy, scale=1.25)
    a = -math.pi / 2 + lt * 1.3
    rx, ry = ecx + 440 * math.cos(a), ecy + 440 * math.sin(a)
    d.ellipse([ecx - 440, ecy - 440, ecx + 440, ecy + 440], outline=(255, 255, 255, 90), width=5)
    paste(frame, S(spec.get("sprite", "iss")), rx, ry, scale=0.5, angle=-math.degrees(a) - 90)
    scene_title(frame, spec, lt)


def sc_rocket(frame, d, lt, dur, spec):
    y = CY + 200 - smooth((lt - 0.4) / max(1.0, dur * 0.8)) * 900
    shake = math.sin(lt * 60) * 4 if lt > 0.3 else 0
    paste(frame, S("rocket"), W / 2 + shake, y, scale=1.3)
    if lt > 0.3:
        paste(frame, sp_flame_cached(), W / 2 + shake, y + 400, scale=1.3 + 0.15 * math.sin(lt * 40))
    scene_title(frame, spec, lt)


_flame = None


def sp_flame_cached():
    global _flame
    if _flame is None:
        _flame = sp_flame()
    return _flame


def sc_outro(frame, d, lt, dur, spec):
    paste(frame, S("earth"), W / 2, CY + 30, scale=0.85 * pop(lt, 0.5))
    d.ellipse([W / 2 - 470, CY + 30 - 130, W / 2 + 470, CY + 30 + 130], outline=YELLOW, width=16)
    paste(frame, T(spec.get("brand", "ORBIT CLIPS"), 120), W / 2, TITLE_Y, scale=pop(lt - 0.2, 0.4))
    paste(frame, T("FOLLOW FOR MORE", 70, YELLOW, 10), W / 2, CY + 330, scale=pop(lt - 0.35, 0.35))



def sc_demo(frame, d, lt, dur, spec):
    """Original drawn demo: astronaut doing an exercise with a prop, floating and bobbing."""
    prop = S(spec.get("prop", "bike"))
    paste(frame, prop, W / 2 + 90, CY + 130, scale=spec.get("prop_scale", 0.9) * pop(lt, 0.4))
    bob = math.sin(lt * 2.4) * 40
    lean = math.sin(lt * 2.4) * 10
    paste(frame, S("astronaut"), W / 2 - 130, CY - 40 + bob, scale=0.85 * pop(lt - 0.1, 0.45), angle=lean)
    if spec.get("label"):
        paste(frame, T(spec["label"], 80, YELLOW, 10), W / 2, CY + 330, scale=pop(lt - 0.3, 0.35))
    scene_title(frame, spec, lt)


SCENES = {
    "exseq": sc_exseq,
    "demo": sc_demo,"hero": sc_hero, "counter": sc_counter, "versus": sc_versus, "list": sc_list,
          "bigtext": sc_bigtext, "station_orbit": sc_station_orbit, "rocket": sc_rocket, "outro": sc_outro}


# ---------------------------------------------------------------- audio
def synth(lines, voice, speed, wav_path):
    from kokoro_onnx import Kokoro
    k = Kokoro(os.path.join(TTS_DIR, "kokoro-v1.0.onnx"), os.path.join(TTS_DIR, "voices-v1.0.bin"))
    lang = "en-gb" if voice.startswith("b") else "en-us"
    chunks, spans, t, sr = [], [], 0.0, 24000
    for ln in lines:
        a, sr = k.create(ln, voice=voice, speed=speed, lang=lang)
        a = np.asarray(a, dtype=np.float32)
        spans.append((t, t + len(a) / sr))
        chunks += [a, np.zeros(int(GAP * sr), dtype=np.float32)]
        t += len(a) / sr + GAP
    sf.write(wav_path, np.concatenate(chunks), sr)
    return spans, t


def transcribe_words(wav):
    tmp = tempfile.mkdtemp()
    subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "transcribe.py"), wav, os.path.join(tmp, "w")],
                   check=True, capture_output=True)
    return [w for seg in json.load(open(os.path.join(tmp, "w.json"))) for w in seg["words"]]


# ---------------------------------------------------------------- main
def render(board_path, out):
    board = json.load(open(board_path))
    lines = board["lines"]
    work = tempfile.mkdtemp()
    wav = os.path.join(work, "voice.wav")
    spans, total = synth([ln["say"] for ln in lines], board.get("voice", "am_michael"), board.get("speed", 1.08), wav)
    words = transcribe_words(wav)
    dur = total + 0.3
    if board.get("outro", True):  # silent branded end card: no narration time spent on "follow"
        lines = lines + [{"say": "", "scene": {"type": "outro"}}]
        spans = spans + [(dur, dur + 1.3)]
        dur += 1.3

    bg = background()
    cannon = Cannon()
    noc = os.path.join(work, "nocap.mp4")
    pipe = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-", "-i", wav, "-af", "apad", "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", noc], stdin=subprocess.PIPE)

    # scene i runs from the start of its line to the start of the next line
    starts = [s for s, _ in spans] + [dur]
    starts[0] = 0.0
    cur = -1
    for fi in range(int(dur * FPS)):
        t = fi / FPS
        i = max(j for j in range(len(lines)) if starts[j] <= t)
        if i != cur:
            if cur >= 0 and lines[cur]["scene"]["type"] == "cannon":
                cannon.finish(lines[cur]["scene"])
            cur = i
        spec = lines[i]["scene"]
        lt, sdur = t - starts[i], starts[i + 1] - starts[i]
        frame = bg.copy()
        d = ImageDraw.Draw(frame, "RGBA")
        if spec["type"] == "cannon":
            cannon.draw(frame, d, lt, sdur, spec)
        else:
            SCENES[spec["type"]](frame, d, lt, sdur, spec)
        pipe.stdin.write(frame.convert("RGB").tobytes())
    pipe.stdin.close()
    if pipe.wait() != 0:
        raise RuntimeError("ffmpeg failed while encoding frames")

    ass = os.path.join(work, "cap.ass")
    with open(ass, "w") as f:
        f.write(build_ass(words, 0.0, dur, "x").replace("Dialogue: 1,", "Comment: 1,", 1))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", noc, "-vf", f"ass={ass}",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", out], check=True)
    print(f"done {out} {dur:.1f}s")


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2])
