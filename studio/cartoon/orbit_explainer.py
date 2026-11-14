"""Cartoon explainer: "Why astronauts float" — flat-style animation synced to a voiceover.

Usage: python orbit_explainer.py <voice.mp3> <words.json> <out.mp4>

words.json is the word-timestamp transcript of the voiceover (studio/transcribe.py
format). Scene cuts are anchored to spoken phrases, so the picture follows the
narration even if the voice speed changes. Captions are burned in with the same
style as the clip renderer.
"""
import json
import math
import os
import random
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from render import build_ass  # noqa: E402

W, H, FPS = 1080, 1920, 30
INK = (20, 17, 43)
YELLOW = (255, 214, 40)
FONT = "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf"
SS = 2  # supersampling for anti-aliased sprites


def font(size):
    return ImageFont.truetype(FONT, size)


# ---------- easing ----------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_back(t):
    t = clamp(t)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_in_out(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


# ---------- sprites (drawn at 2x, downscaled for smooth edges) ----------
def sprite(w, h, draw_fn):
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(im), SS)
    return im.resize((w, h), Image.LANCZOS)


def make_background():
    rnd = random.Random(3)
    bg = Image.new("RGB", (W, H))
    top, bot = (38, 24, 92), (10, 12, 38)
    px = bg.load()
    for y in range(H):
        t = y / H
        c = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(bg)
    for _ in range(260):
        x, y, s = rnd.randrange(W), rnd.randrange(H), rnd.choice([2, 3, 3, 4, 5])
        a = rnd.randint(140, 255)
        d.ellipse([x, y, x + s, y + s], fill=(a, a, a))
    return bg


def make_earth(r):
    def fn(d, k):
        R = r * k
        o = 10 * k
        d.ellipse([o, o, o + 2 * R, o + 2 * R], fill=(64, 140, 255), outline=INK, width=10 * k)
        # continents: simple blobby shapes
        for cx, cy, rx, ry in [(0.35, 0.3, 0.22, 0.14), (0.62, 0.55, 0.2, 0.18),
                               (0.3, 0.68, 0.14, 0.1), (0.72, 0.25, 0.1, 0.07)]:
            x0 = o + (cx - rx) * 2 * R
            y0 = o + (cy - ry) * 2 * R
            d.ellipse([x0, y0, x0 + rx * 4 * R, y0 + ry * 4 * R], fill=(88, 204, 120))
        d.ellipse([o, o, o + 2 * R, o + 2 * R], outline=INK, width=10 * k)
        # shine
        d.ellipse([o + 0.25 * R, o + 0.18 * R, o + 0.55 * R, o + 0.38 * R], fill=(150, 200, 255))
    size = 2 * r + 20
    return sprite(size, size, fn)


def make_astronaut():
    def fn(d, k):
        def rr(box, rad, fill):
            d.rounded_rectangle([v * k for v in box], radius=rad * k, fill=fill, outline=INK, width=7 * k)
        rr((40, 150, 100, 260), 26, (230, 233, 245))     # left arm
        rr((200, 150, 260, 260), 26, (230, 233, 245))    # right arm
        rr((85, 255, 140, 370), 26, (230, 233, 245))     # left leg
        rr((160, 255, 215, 370), 26, (230, 233, 245))    # right leg
        rr((60, 120, 240, 300), 50, (245, 246, 252))     # body
        rr((95, 175, 205, 230), 14, (255, 120, 90))      # chest panel
        d.ellipse([k * 70, k * 10, k * 230, k * 170], fill=(245, 246, 252), outline=INK, width=7 * k)  # helmet
        d.rounded_rectangle([k * 95, k * 50, k * 205, k * 130], radius=38 * k, fill=(40, 70, 160), outline=INK, width=6 * k)
        d.ellipse([k * 110, k * 62, k * 140, k * 82], fill=(255, 255, 255, 200))  # visor shine
    return sprite(300, 380, fn)


def make_iss(scale=1.0):
    w, h = int(420 * scale), int(200 * scale)

    def fn(d, k):
        s = scale * k
        def box(x0, y0, x1, y1, fill, rad=6):
            d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s], radius=rad * s, fill=fill, outline=INK, width=max(2, int(5 * s)))
        for x in (10, 90, 270, 350):  # solar panels
            box(x, 20, x + 60, 180, (60, 110, 220), 4)
            for gy in range(40, 180, 25):
                d.line([(x + 4) * s, gy * s, (x + 56) * s, gy * s], fill=(130, 175, 255), width=max(1, int(2 * s)))
        box(70, 92, 350, 108, (180, 185, 200), 3)          # truss
        box(160, 70, 260, 130, (240, 242, 250), 20)        # module
        box(190, 40, 230, 160, (240, 242, 250), 14)        # module
    return sprite(w, h, fn)


def text_sprite(txt, size, fill=(255, 255, 255), stroke=10, max_w=980):
    f = font(size)
    lines, line = [], ""
    for wd in txt.split():
        test = (line + " " + wd).strip()
        if f.getlength(test) > max_w and line:
            lines.append(line)
            line = wd
        else:
            line = test
    lines.append(line)
    lh = int(size * 1.08)
    h = lh * len(lines) + stroke * 2
    im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((W / 2, stroke + i * lh), ln, font=f, fill=fill, anchor="ma", stroke_width=stroke, stroke_fill=INK)
    return im


def paste_center(frame, spr, cx, cy, scale=1.0, angle=0.0, alpha=1.0):
    if scale <= 0.01 or alpha <= 0.01:
        return
    s = spr
    if scale != 1.0:
        s = s.resize((max(1, int(s.width * scale)), max(1, int(s.height * scale))), Image.BILINEAR)
    if angle:
        s = s.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1.0:
        a = s.split()[3].point(lambda v: int(v * alpha))
        s = s.copy()
        s.putalpha(a)
    frame.alpha_composite(s, (int(cx - s.width / 2), int(cy - s.height / 2)))


# ---------- physics: Newton's cannon ----------
def trajectory(v_frac, r_earth, r0, steps=4000):
    """Integrate a ball fired horizontally from the mountain top. Units: pixels."""
    gm = 1.0
    v_circ = math.sqrt(gm / r0)
    x, y = 0.0, -r0
    vx, vy = v_frac * v_circ, 0.0
    dt = (2 * math.pi * r0 / v_circ) / steps
    pts = [(x, y)]
    for _ in range(int(steps * 1.02)):
        r = math.hypot(x, y)
        ax, ay = -gm * x / r ** 3, -gm * y / r ** 3
        vx += ax * dt
        vy += ay * dt
        x += vx * dt
        y += vy * dt
        pts.append((x, y))
        if math.hypot(x, y) <= r_earth:
            break
    return pts


# ---------- scene timing from narration ----------
def norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def find(words, phrase, default):
    p = phrase.split()
    toks = [norm(w["w"]) for w in words]
    for i in range(len(toks) - len(p) + 1):
        if toks[i:i + len(p)] == p:
            return words[i]["s"]
    return default


def main():
    voice, words_json, out = sys.argv[1:4]
    words = [w for seg in json.load(open(words_json)) for w in seg["words"]]
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", voice], capture_output=True, text=True).stdout) + 0.6
    T = {
        "myth": find(words, "thats a myth", 2.5),
        "up": find(words, "up where", 3.5),
        "why": find(words, "so why", 8.0),
        "imagine": find(words, "imagine", 10.0),
        "faster": find(words, "throw it faster", 13.0),
        "now": find(words, "now throw", 16.0),
        "kmh": find(words, "28000", find(words, "kilometers", 24.0) - 1.2),
        "never": find(words, "it never lands", 22.0),
        "orbit": find(words, "thats an orbit", 23.0),
        "station": find(words, "the space station and", 24.5),
        "follow": find(words, "follow for", dur - 3),
    }

    bg = make_background()
    earth_big = make_earth(520)
    earth = make_earth(340)
    astro = make_astronaut()
    iss = make_iss(1.35)
    iss_small = make_iss(0.6)

    t_title = text_sprite("NO GRAVITY IN SPACE?", 92)
    t_myth = text_sprite("MYTH!", 170, fill=(255, 70, 70), stroke=14)
    t_90 = {n: text_sprite(f"{n}%", 200, fill=YELLOW, stroke=14) for n in range(0, 91)}
    t_gravity = text_sprite("GRAVITY UP THERE", 70)
    t_q = text_sprite("?", 360, fill=YELLOW, stroke=16)
    t_speed = text_sprite("28,000 km/h", 110, fill=YELLOW, stroke=12)
    t_orbit = text_sprite("FALLING... FOREVER", 96, fill=YELLOW, stroke=12)
    t_brand = text_sprite("ORBIT CLIPS", 120)
    t_follow = text_sprite("Follow for more space secrets", 62)

    ecx, ecy, er = W // 2, 880, 340
    r_mtn = er + 52
    trajs = {k: trajectory(v, er, r_mtn) for k, v in (("short", 0.42), ("mid", 0.68), ("orbit", 1.0))}

    def mountain(d):
        d.polygon([(ecx - 95, ecy - er + 20), (ecx, ecy - r_mtn - 8), (ecx + 95, ecy - er + 20)],
                  fill=(140, 110, 90), outline=INK, width=6)

    def draw_ball(frame, d, key, prog, color=YELLOW):
        pts = trajs[key]
        n = max(1, int(len(pts) * clamp(prog)))
        step = max(1, len(pts) // 90)
        for i in range(0, n, step):
            x, y = pts[i]
            d.ellipse([ecx + x - 6, ecy + y - 6, ecx + x + 6, ecy + y + 6], fill=(255, 255, 255, 190))
        x, y = pts[n - 1]
        d.ellipse([ecx + x - 28, ecy + y - 28, ecx + x + 28, ecy + y + 28], fill=color, outline=INK, width=7)

    pipe = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", voice,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-af", "apad", "-t", f"{dur:.2f}", "-c:a", "aac", "-b:a", "160k", out + ".nocap.mp4",
    ], stdin=subprocess.PIPE)

    n_frames = int(dur * FPS)
    for fi in range(n_frames):
        t = fi / FPS
        frame = bg.copy().convert("RGBA")
        d = ImageDraw.Draw(frame, "RGBA")

        if t < T["up"]:  # hook: floating astronaut, "no gravity?" -> MYTH stamp
            bob = math.sin(t * 2.2) * 30
            paste_center(frame, astro, W / 2, 900 + bob, scale=1.6 * ease_out_back(t / 0.5), angle=math.sin(t * 1.4) * 12)
            paste_center(frame, t_title, W / 2, 360, scale=ease_out_back((t - 0.2) / 0.4))
            if t >= T["myth"]:
                k = ease_out_back((t - T["myth"]) / 0.3)
                paste_center(frame, t_myth, W / 2, 900, scale=0.6 + 0.6 * k, angle=-12)
        elif t < T["imagine"]:  # gravity is still 90%
            lt = t - T["up"]
            paste_center(frame, earth_big, W / 2, 1720, scale=1.0)
            paste_center(frame, iss, W / 2 + math.sin(lt * 0.8) * 40, 760, scale=ease_out_back(lt / 0.5))
            if t < T["why"]:
                n = int(90 * ease_in_out((lt - 0.6) / 1.6))
                paste_center(frame, t_90[max(0, n)], W / 2, 380, scale=ease_out_back((lt - 0.4) / 0.4))
                paste_center(frame, t_gravity, W / 2, 540, alpha=clamp((lt - 0.8) / 0.4))
            else:
                qt = t - T["why"]
                paste_center(frame, t_q, W / 2, 420 + math.sin(qt * 6) * 10, scale=ease_out_back(qt / 0.4))
        elif t < T["station"]:  # Newton's cannon
            paste_center(frame, earth, ecx, ecy)
            mountain(d)
            if t < T["faster"]:
                draw_ball(frame, d, "short", (t - T["imagine"] - 0.8) / 1.6)
            elif t < T["now"]:
                draw_ball(frame, d, "short", 1.0, color=(200, 200, 200))
                draw_ball(frame, d, "mid", (t - T["faster"] - 0.3) / 1.8)
            else:
                draw_ball(frame, d, "short", 1.0, color=(200, 200, 200))
                draw_ball(frame, d, "mid", 1.0, color=(200, 200, 200))
                prog = (t - T["kmh"]) / max(1.0, T["orbit"] - T["kmh"])
                if prog > 0:
                    draw_ball(frame, d, "orbit", prog)
                if t >= T["kmh"]:
                    paste_center(frame, t_speed, W / 2, 420, scale=ease_out_back((t - T["kmh"]) / 0.4))
                if t >= T["orbit"]:
                    # ball keeps circling after the first loop
                    a = -math.pi / 2 + (t - T["orbit"]) * 2.4
                    bx, by = ecx + r_mtn * math.cos(a), ecy + r_mtn * math.sin(a)
                    d.ellipse([bx - 28, by - 28, bx + 28, by + 28], fill=YELLOW, outline=INK, width=7)
        elif t < T["follow"]:  # the station is falling around Earth
            lt = t - T["station"]
            paste_center(frame, earth, ecx, ecy)
            a = -math.pi / 2 + lt * 1.3
            rx, ry = ecx + 440 * math.cos(a), ecy + 440 * math.sin(a)
            d.ellipse([ecx - 440, ecy - 440, ecx + 440, ecy + 440], outline=(255, 255, 255, 90), width=5)
            paste_center(frame, iss_small, rx, ry, angle=-math.degrees(a) - 90)
            paste_center(frame, astro, rx + 70 * math.cos(a), ry + 70 * math.sin(a), scale=0.4, angle=lt * 90)
            paste_center(frame, t_orbit, W / 2, 330, scale=ease_out_back(lt / 0.4))
        else:  # outro
            lt = t - T["follow"]
            paste_center(frame, earth, W / 2, 850, scale=0.85 * ease_out_back(lt / 0.5))
            d.ellipse([W / 2 - 470, 850 - 130, W / 2 + 470, 850 + 130], outline=YELLOW, width=16)
            paste_center(frame, t_brand, W / 2, 330, scale=ease_out_back((lt - 0.2) / 0.4))

        pipe.stdin.write(frame.convert("RGB").tobytes())
    pipe.stdin.close()
    pipe.wait()

    noc = out + ".nocap.mp4"
    ass = out + ".ass"
    with open(ass, "w") as f:
        f.write(build_ass(words, 0.0, dur, "").replace(
            "Dialogue: 1,0:00:00.00", "Comment: 1,0:00:00.00", 1))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", noc, "-vf", f"ass={ass}",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", out], check=True)
    os.remove(noc)
    os.remove(ass)
    print("done", out, f"{dur:.1f}s", T)


if __name__ == "__main__":
    main()
