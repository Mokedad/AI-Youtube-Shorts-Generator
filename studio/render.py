"""Render picked highlights into finished 1080x1920 Shorts with burned-in captions.

Usage: python render.py <source.mp4> <transcript.json> <highlights.json> <out_dir>

highlights.json: [{"slug", "title", "hook", "start", "end"}, ...]
Uses the repo's face-tracking vertical crop, then adds a hook headline at the
top and word-by-word animated captions near the bottom.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shorts_generator.local.clipper import crop_clip_local  # noqa: E402

WORDS_PER_CHUNK = 3
HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Inter Display,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,560,1
Style: Hook,Inter Display,60,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,18,0,8,80,80,190,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean(w: str) -> str:
    return w.strip().replace("{", "").replace("}", "")


def build_ass(words, start, end, hook):
    lines = [HEADER]
    lines.append(f"Dialogue: 1,{ts(0)},{ts(end - start)},Hook,,0,0,0,,{hook.upper()}")
    clip_words = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.3]
    chunks = [clip_words[i:i + WORDS_PER_CHUNK] for i in range(0, len(clip_words), WORDS_PER_CHUNK)]
    for ci, chunk in enumerate(chunks):
        chunk_end = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else chunk[-1]["e"]
        for wi, w in enumerate(chunk):
            w_end = chunk[wi + 1]["s"] if wi + 1 < len(chunk) else chunk_end
            parts = []
            for k, other in enumerate(chunk):
                txt = clean(other["w"]).upper()
                parts.append("{\\c&H00F2FF&}" + txt + "{\\c&HFFFFFF&}" if k == wi else txt)
            a, b = w["s"] - start, min(w_end, end) - start
            if b > a:
                lines.append(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def main():
    src, tjson, hjson, out_dir = sys.argv[1:5]
    os.makedirs(out_dir, exist_ok=True)
    words = [w for seg in json.load(open(tjson)) for w in seg["words"]]
    for h in json.load(open(hjson)):
        slug, start, end = h["slug"], float(h["start"]), float(h["end"])
        raw = os.path.join(out_dir, f".{slug}.raw.mp4")
        ass = os.path.join(out_dir, f".{slug}.ass")
        final = os.path.join(out_dir, f"{slug}.mp4")
        print(f"[render] {slug}  {start:.1f}-{end:.1f}s", flush=True)
        crop_clip_local(src, start, end, "9:16", raw)
        with open(ass, "w") as f:
            f.write(build_ass(words, start, end, h["hook"]))
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", raw,
            "-vf", f"scale=1080:1920:flags=lanczos,unsharp=5:5:0.7:5:5:0.0,ass={ass}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", final,
        ], check=True)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-ss", "4", "-i", final,
            "-frames:v", "1", "-vf", "scale=360:-2", os.path.join(out_dir, f"{slug}.jpg"),
        ], check=True)
        os.remove(raw)
        os.remove(ass)
    print("[render] done", flush=True)


if __name__ == "__main__":
    main()
