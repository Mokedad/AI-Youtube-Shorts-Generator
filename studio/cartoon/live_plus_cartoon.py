"""Live-plus-cartoon Short: NASA public-domain footage alternating with cartoon scenes.

The astronaut's own audio runs continuously; the picture flips between live footage
(9:16 face-tracked crop) and cartoon scenes every few seconds.

Usage: python live_plus_cartoon.py <source.mp4> <transcript.json> <plan.json> <out.mp4>

plan.json: {"start": 137.5, "end": 176.5,
            "segments": [{"kind": "live", "dur": 4.0},
                         {"kind": "cartoon", "dur": 3.0, "scene": {...engine scene spec...}}, ...]}
Segment durations must add up to end - start.
"""
import json
import os
import subprocess
import sys
import tempfile

from PIL import ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import engine  # noqa: E402
from render import build_ass  # noqa: E402
from shorts_generator.local.clipper import crop_clip_local  # noqa: E402

W, H, FPS = engine.W, engine.H, engine.FPS


def cartoon_clip(spec, dur, out):
    bg = engine.background()
    n = max(1, round(dur * FPS))
    pipe = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
                             "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", out],
                            stdin=subprocess.PIPE)
    for i in range(n):
        lt = i / FPS
        frame = bg.copy()
        d = ImageDraw.Draw(frame, "RGBA")
        engine.SCENES[spec["type"]](frame, d, lt, dur, spec)
        pipe.stdin.write(frame.convert("RGB").tobytes())
    pipe.stdin.close()
    if pipe.wait() != 0:
        raise RuntimeError("cartoon segment failed")


def live_clip(src, t0, dur, out):
    tmp = out + ".cut.mp4"
    crop_clip_local(src, t0, t0 + dur, "9:16", tmp)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-an", "-t", f"{dur:.3f}",
                    "-r", str(FPS), "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", out],
                   check=True)
    os.remove(tmp)


def main():
    src, tjson, plan_path, out = sys.argv[1:5]
    plan = json.load(open(plan_path))
    start, end = float(plan["start"]), float(plan["end"])
    words = [w for seg in json.load(open(tjson)) for w in seg["words"]]
    work = tempfile.mkdtemp(prefix="lpc-", dir="/home/user/shorts-studio/format-test/parts")

    parts, t = [], start
    for i, seg in enumerate(plan["segments"]):
        p = os.path.join(work, f"p{i:02d}.mp4")
        if seg["kind"] == "live":
            live_clip(src, t, seg["dur"], p)
        else:
            cartoon_clip(seg["scene"], seg["dur"], p)
        parts.append(p)
        t += seg["dur"]
    assert abs(t - end) < 0.05, f"segments total {t - start:.2f}s, expected {end - start:.2f}s"

    listf = os.path.join(work, "list.txt")
    with open(listf, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    video = os.path.join(work, "video.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", video], check=True)

    ass = os.path.join(work, "cap.ass")
    with open(ass, "w") as f:
        f.write(build_ass(words, start, end, "x").replace("Dialogue: 1,", "Comment: 1,", 1))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                    "-i", src, "-map", "0:v", "-map", "1:a:0", "-vf", f"ass={ass}", "-ss", "0",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out], check=True)
    print("done", out, f"{end - start:.1f}s")


if __name__ == "__main__":
    main()
