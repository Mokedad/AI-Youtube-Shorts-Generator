"""Hi-graphic orbit style (ported from the user's 9:16 orbit storyboard design).

Glowing curved Earth horizon, dotted orbit arcs, white satellite with thruster glow,
dark low-orbit craft with drag sparks, labelled force vectors, top telemetry HUD,
yellow beat headers, word-synced captions. Voice: free local Kokoro (engine.synth).

Usage: python hd.py <board.json> <out.mp4>
Board: {"voice": "am_michael", "speed": 1.08,
        "lines": [{"say": "...", "beat": "hook|gravity|speed|dual|drag|payoff", "header": "..."}]}
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import engine  # noqa: E402
from render import build_ass  # noqa: E402

W, H, FPS = 1080, 1920, 30
K = W / 330.0  # design was laid out on a ~330px-wide canvas
MONO = "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf"
YELLOW = (250, 204, 21)
CYAN = (0, 212, 255)
RED = (255, 59, 48)
GREEN = (52, 199, 89)
ORANGE = (251, 146, 60)


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def lerp(a, b, t):
    return a + (b - a) * clamp(t)


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_io(t):
    t = clamp(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


# ---------------------------------------------------------------- static layers
def starfield():
    import numpy as np
    y = np.linspace(0, 1, H)[:, None, None]
    c0, c1, c2 = np.array([4, 8, 20]), np.array([6, 13, 31]), np.array([9, 23, 54])
    top = c0 * (1 - y / 0.55) + c1 * (y / 0.55)
    bot = c1 * (1 - (y - 0.55) / 0.45) + c2 * ((y - 0.55) / 0.45)
    arr = np.where(y < 0.55, top, bot).repeat(W, axis=1).clip(0, 255).astype("uint8")
    im = Image.fromarray(arr, "RGB").convert("RGBA")
    d = ImageDraw.Draw(im)
    rnd = random.Random(7)
    for i in range(110):
        x, yy = rnd.random() * W, rnd.random() * H * 0.85
        r = (0.7 + rnd.random() * 1.6) * 2.2
        a = int(120 + rnd.random() * 135)
        if i % 11 == 0:
            d.rectangle([x - r * 2.2, yy - 1.5, x + r * 2.2, yy + 1.5], fill=(210, 232, 255, a))
            d.rectangle([x - 1.5, yy - r * 2.2, x + 1.5, yy + r * 2.2], fill=(210, 232, 255, a))
        else:
            d.ellipse([x - r, yy - r, x + r, yy + r], fill=(210, 232, 255, a))
    return im


class Cam:
    """Camera zoom/pan about (W/2, 0.55H)."""
    def __init__(self, s=1.0, oy=0.0):
        self.s, self.oy = s, oy

    def p(self, x, y):
        return (W / 2 + (x - W / 2) * self.s, H * 0.55 + (y - H * 0.55) * self.s + self.oy)


EARTH_R = W * 0.88
EARTH_C = (W * 0.5, H * 0.72 + EARTH_R * 0.72)
HIGH_R = EARTH_R + 82 * K
LOW_R = EARTH_R + 36 * K

_earth_cache = {}


def earth_layer(cam):
    key = (round(cam.s, 3), round(cam.oy))
    if key in _earth_cache:
        return _earth_cache[key]
    cx, cy = cam.p(*EARTH_C)
    R = EARTH_R * cam.s
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([cx - R - 40 * K, cy - R - 40 * K, cx + R + 40 * K, cy + R + 40 * K], fill=CYAN + (120,))
    glow = glow.filter(ImageFilter.GaussianBlur(14 * K))
    lay.alpha_composite(glow)
    disk = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disk)
    dd.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(3, 105, 161, 255))
    # continents (flat vector blobs near the visible top of the disk)
    for dx, dy, rx, ry in [(-0.24, -0.74, 48, 24), (0.04, -0.82, 34, 16), (0.28, -0.72, 54, 26),
                           (-0.55, -0.55, 40, 22), (0.6, -0.5, 46, 24)]:
        ex, ey = cx + R * dx, cy + R * dy
        dd.ellipse([ex - rx * K * cam.s, ey - ry * K * cam.s, ex + rx * K * cam.s, ey + ry * K * cam.s],
                   fill=(52, 211, 153, 185))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([cx - R, cy - R, cx + R, cy + R], fill=255)
    lay.paste(disk, (0, 0), mask)
    ImageDraw.Draw(lay).ellipse([cx - R, cy - R, cx + R, cy + R], outline=(56, 189, 248, 255), width=int(4 * K))
    _earth_cache[key] = lay
    return lay


# ---------------------------------------------------------------- sprites
F = K * 1.5


def sat_sprite():
    def fn(d, k):
        s = k * F
        o = (60 * s, 40 * s)
        def P(x, y):
            return (o[0] + x * s, o[1] + y * s)
        for y0 in (-28, 10):
            d.rectangle([*P(-6, y0), *P(6, y0 + 18)], fill=(29, 78, 216), outline=(147, 197, 253), width=max(1, int(1.2 * s)))
            d.line([*P(0, y0), *P(0, y0 + 18)], fill=(147, 197, 253), width=max(1, int(s)))
            d.line([*P(-6, y0 + 9), *P(6, y0 + 9)], fill=(147, 197, 253), width=max(1, int(s)))
        d.rounded_rectangle([*P(-11, -7), *P(11, 7)], radius=3 * s, fill=(255, 255, 255))
        d.rectangle([*P(-3, -7), *P(2, 7)], fill=(14, 165, 233))
        d.arc([*P(8, -5), *P(18, 5)], -80, 80, fill=(226, 232, 240), width=int(2 * s))
        # thruster glow
        for i in range(6):
            a = int(200 * (1 - i / 6))
            d.polygon([P(-10 - i * 4, -4 + i * 0.6), P(-10 - (i + 1) * 4, 0), P(-10 - i * 4, 4 - i * 0.6)], fill=(56, 189, 248, a))
    return engine.sprite_canvas(int(120 * F), int(80 * F), fn)


def craft_sprite(hot):
    def fn(d, k):
        s = k * F
        o = (40 * s, 30 * s)
        def P(x, y):
            return (o[0] + x * s, o[1] + y * s)
        pts = [P(20, 0), P(-10, -14), P(-16, -6), P(-12, 0), P(-16, 6), P(-10, 14)]
        d.polygon(pts, fill=(17, 24, 39), outline=ORANGE if hot else (100, 116, 139), width=int(1.5 * s))
        d.polygon([P(15, 0), P(-6, -6), P(-6, 6)], fill=(31, 41, 55))
    return engine.sprite_canvas(int(80 * F), int(60 * F), fn)


def font(sz):
    return ImageFont.truetype(MONO, int(sz))


# ---------------------------------------------------------------- primitives
def dashed_arc(d, cam, r, color, alpha, a0=math.pi * 1.18, a1=math.pi * 1.82, dash=7, width=2.5):
    cx, cy = cam.p(*EARTH_C)
    R = r * cam.s
    seg = dash * K / R
    a, on = a0, True
    while a < a1:
        b = min(a1, a + seg)
        if on:
            d.line([cx + R * math.cos(a), cy + R * math.sin(a), cx + R * math.cos(b), cy + R * math.sin(b)],
                   fill=color + (int(255 * alpha),), width=int(width * K))
        a, on = b, not on


def arrow(d, x1, y1, x2, y2, color, label, lox=0, loy=0):
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 6:
        return
    ang = math.atan2(y2 - y1, x2 - x1)
    head = 12 * K
    sx, sy = x2 - math.cos(ang) * head * 0.7, y2 - math.sin(ang) * head * 0.7
    d.line([x1, y1, sx, sy], fill=color + (70,), width=int(10 * K))  # glow
    d.line([x1, y1, sx, sy], fill=color + (255,), width=int(4 * K))
    d.polygon([(x2, y2),
               (x2 - head * math.cos(ang - math.pi / 6), y2 - head * math.sin(ang - math.pi / 6)),
               (x2 - head * math.cos(ang + math.pi / 6), y2 - head * math.sin(ang + math.pi / 6))], fill=color + (255,))
    if label:
        f = font(9 * K)
        tw = f.getlength(label)
        lx, ly = x2 + lox * K, y2 + loy * K
        d.rounded_rectangle([lx - tw / 2 - 5 * K, ly - 8 * K, lx + tw / 2 + 5 * K, ly + 8 * K], radius=4 * K, fill=(6, 13, 31, 220))
        d.text((lx, ly), label, font=f, fill=color, anchor="mm")


def sparks(d, x, y, ang, t, inten):
    rnd = random.Random(3)
    for i in range(18):
        ph = i / 18
        spread = ((i % 5) - 2) * 3.2
        length = 10 + (i % 4) * 6
        c = (t * (1.8 + inten * 1.4) + ph) % 1
        sx = 16 - c * (42 + inten * 26)
        sy = spread * (0.7 + c * 0.6)
        ex = sx - length * inten
        a = int(255 * (1 - c) * inten)
        col = (255, 214, 10, a) if c < 0.35 else (255, 85, 0, a)
        ca, sa = math.cos(ang), math.sin(ang)
        p1 = (x + (sx * ca - sy * sa) * F, y + (sx * sa + sy * ca) * F)
        p2 = (x + (ex * ca - sy * sa) * F, y + (ex * sa + sy * ca) * F)
        d.line([p1, p2], fill=col, width=int(1.8 * K))
    hx, hy = x + 22 * K * math.cos(ang), y + 22 * K * math.sin(ang)
    d.ellipse([hx - 6 * K, hy - 6 * K, hx + 6 * K, hy + 6 * K], fill=(255, 170, 40, int(90 * inten)))


def hud(d, t, reveal, speed_kmh):
    if reveal <= 0.01:
        return
    x, wv, hv = 18 * K, W - 36 * K, 44 * K
    y = lerp(70 * K, 76 * K, ease_out(reveal))
    a = int(255 * clamp(reveal))
    d.rounded_rectangle([x, y, x + wv, y + hv], radius=10 * K, fill=(9, 17, 38, int(225 * reveal)), outline=(56, 189, 248, int(115 * reveal)), width=int(1.4 * K))
    f = font(10 * K)
    d.text((x + 10 * K, y + 12 * K), f"~{speed_kmh:,} KM/H", font=f, fill=CYAN + (a,), anchor="lm")
    d.text((x + wv - 10 * K, y + 12 * K), "DRAG: HIGH", font=f, fill=ORANGE + (a,), anchor="rm")
    tx, ty, tw, th = x + 10 * K, y + 25 * K, wv - 20 * K, 9 * K
    d.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=4.5 * K, fill=(15, 23, 42, a))
    cw = tw * 0.44
    d.rounded_rectangle([tx, ty, tx + cw, ty + th], radius=4.5 * K, fill=CYAN + (a,))
    ow = tw * 0.52 * clamp(0.45 + reveal * 0.5, 0.45, 0.94)
    d.rounded_rectangle([tx + cw, ty, tx + cw + ow, ty + th], radius=4.5 * K, fill=(255, 110, 20, a))


def header(d, text, alpha):
    if not text or alpha <= 0.01:
        return
    f = font(17 * K)
    d.text((W / 2, H * 0.235), text, font=f, fill=YELLOW + (int(255 * alpha),), anchor="mm",
           stroke_width=int(2.5 * K), stroke_fill=(4, 8, 20, int(235 * alpha)))


# ---------------------------------------------------------------- scene
def render_frame(bg, sat, craft_cold, craft_hot, t, beat, bt, bdur, first_t):
    """t: global time; beat: beat name; bt: time into beat; first_t: when each beat type first starts."""
    s, oy = 1.0, 0.0
    if "dual" in first_t and t >= first_t["dual"]:
        p = ease_io((t - first_t["dual"]) / 1.2)
        s, oy = lerp(1.0, 1.08, p), lerp(0, -14 * K, p)
    if "payoff" in first_t and t >= first_t["payoff"]:
        p = ease_io((t - first_t["payoff"]) / 1.4)
        s, oy = lerp(s, 0.94, p), lerp(oy, 12 * K, p)
    cam = Cam(s, oy)
    frame = bg.copy()
    d = ImageDraw.Draw(frame, "RGBA")
    show_low = "dual" in first_t and t >= first_t["dual"]
    dashed_arc(d, cam, HIGH_R, YELLOW, 0.85)
    if show_low:
        dashed_arc(d, cam, LOW_R, ORANGE, clamp((t - first_t["dual"]) / 0.8, 0, 0.75))
    frame.alpha_composite(earth_layer(cam))
    d = ImageDraw.Draw(frame, "RGBA")

    cx, cy = cam.p(*EARTH_C)
    wa = lerp(-math.pi * 0.64, -math.pi * 0.36, (t / 30.0) % 1)
    wx, wy = cx + math.cos(wa) * HIGH_R * s, cy + math.sin(wa) * HIGH_R * s
    tang = wa + math.pi / 2
    to_earth = math.atan2(cy - wy, cx - wx)
    if beat in ("gravity", "speed"):
        g = 54 * K * ease_out((t - first_t["gravity"] - 0.2) / 0.7) * (1 + math.sin(t * 6) * 0.06)
        arrow(d, wx, wy, wx + math.cos(to_earth) * g, wy + math.sin(to_earth) * g, RED, "GRAVITY", 0, 14)
    if beat == "speed":
        v = 64 * K * ease_out((bt - 0.1) / 0.7)
        arrow(d, wx, wy, wx + math.cos(tang) * v, wy + math.sin(tang) * v, GREEN, "SPEED", 0, -14)
    engine.paste(frame, sat, wx, wy, angle=-math.degrees(tang))
    d = ImageDraw.Draw(frame, "RGBA")

    if show_low:
        enter = ease_out((t - first_t["dual"]) / 0.9)
        mult = 1.55 if beat in ("drag", "payoff") else 1.25
        ba = lerp(-math.pi * 0.66, -math.pi * 0.34, ((t - first_t["dual"]) * mult / 14.0) % 1)
        bx, by = cx + math.cos(ba) * LOW_R * s, cy + math.sin(ba) * LOW_R * s
        btang = ba + math.pi / 2
        inten = 1.0 if beat in ("drag", "payoff") else 0.65 * enter
        sparks(d, bx, by, btang, t, inten)
        engine.paste(frame, craft_hot if inten > 0.8 else craft_cold, bx, by, angle=-math.degrees(btang), alpha=enter)
        d = ImageDraw.Draw(frame, "RGBA")
    if beat == "payoff":
        r = ease_out(bt / 0.6)
        arrow(d, wx, wy, wx + math.cos(to_earth) * 44 * K * r, wy + math.sin(to_earth) * 44 * K * r, RED, "PULL", -22, 10)
        arrow(d, wx, wy, wx + math.cos(tang) * 52 * K * r, wy + math.sin(tang) * 52 * K * r, GREEN, "SPEED", 0, -14)
    if "drag" in first_t and t >= first_t["drag"]:
        hud(d, t, clamp((t - first_t["drag"]) / 0.6), 28000 + int(math.sin(t * 4) * 150))
    return frame


def main():
    board_path, out = sys.argv[1:3]
    board = json.load(open(board_path))
    lines = board["lines"]
    work = tempfile.mkdtemp()
    wav = os.path.join(work, "voice.wav")
    spans, total = engine.synth([ln["say"] for ln in lines], board.get("voice", "am_michael"), board.get("speed", 1.08), wav)
    words = engine.transcribe_words(wav)
    dur = total + 0.4
    starts = [s for s, _ in spans] + [dur]
    starts[0] = 0.0
    first_t = {}
    for i, ln in enumerate(lines):
        first_t.setdefault(ln["beat"], starts[i])

    bg = starfield()
    sat = sat_sprite()
    craft_cold, craft_hot = craft_sprite(False), craft_sprite(True)
    noc = os.path.join(work, "nocap.mp4")
    pipe = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", wav, "-af", "apad", "-t", f"{dur:.2f}",
                             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-b:a", "160k", noc], stdin=subprocess.PIPE)
    for fi in range(int(dur * FPS)):
        t = fi / FPS
        i = max(j for j in range(len(lines)) if starts[j] <= t)
        ln = lines[i]
        frame = render_frame(bg, sat, craft_cold, craft_hot, t, ln["beat"], t - starts[i], starts[i + 1] - starts[i], first_t)
        header(ImageDraw.Draw(frame, "RGBA"), ln.get("header", ""), clamp((t - starts[i]) / 0.35))
        pipe.stdin.write(frame.convert("RGB").tobytes())
    pipe.stdin.close()
    if pipe.wait() != 0:
        raise RuntimeError("encode failed")
    ass = os.path.join(work, "cap.ass")
    with open(ass, "w") as f:
        f.write(build_ass(words, 0.0, dur, "x").replace("Dialogue: 1,", "Comment: 1,", 1))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", noc, "-vf", f"ass={ass}", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out], check=True)
    print("done", out, f"{dur:.1f}s")


if __name__ == "__main__":
    main()
