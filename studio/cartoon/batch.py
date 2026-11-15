"""Render every storyboard in a folder, in parallel, plus one QA contact sheet.

Usage: python batch.py <boards_dir> <out_dir> [jobs]

Writes <out_dir>/<slug>.mp4 for each <slug>.json and <out_dir>/_sheet.jpg: one row per
video, one frame from the middle of every scene, so the whole batch is checked at once.
"""
import concurrent.futures as cf
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def render(board, out_dir):
    slug = os.path.splitext(os.path.basename(board))[0]
    out = os.path.join(out_dir, slug + ".mp4")
    r = subprocess.run([sys.executable, os.path.join(HERE, "engine.py"), board, out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return slug, None, r.stderr[-800:]
    return slug, out, None


def probe(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", path], capture_output=True, text=True).stdout)


def sheet(results, boards, out_dir):
    rows = []
    for slug, out, _ in results:
        if not out:
            continue
        n = len(json.load(open(boards[slug]))["lines"]) + 1
        dur = probe(out)
        tiles = []
        for i in range(n):
            t = dur - 0.4 if i == n - 1 else (dur - 1.3) * (i + 0.6) / (n - 1)
            tile = os.path.join(out_dir, f".{slug}_{i}.jpg")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", out,
                            "-frames:v", "1", "-vf", "scale=150:-2", tile], check=True)
            tiles.append(tile)
        row = os.path.join(out_dir, f".{slug}_row.jpg")
        args = sum([["-i", t] for t in tiles], [])
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex",
                        f"hstack={len(tiles)}" if len(tiles) > 1 else "null", row], check=True)
        rows.append(row)
        for t in tiles:
            os.remove(t)
    if not rows:
        return None
    # pad rows to equal width, then stack
    out = os.path.join(out_dir, "_sheet.jpg")
    args = sum([["-i", r] for r in rows], [])
    pads = "".join(f"[{i}]pad=1200:ih[r{i}];" for i in range(len(rows)))
    stack = "".join(f"[r{i}]" for i in range(len(rows)))
    flt = pads + (f"{stack}vstack={len(rows)}" if len(rows) > 1 else "[r0]null")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex", flt, out], check=True)
    for r in rows:
        os.remove(r)
    return out


def main():
    boards_dir, out_dir = sys.argv[1], sys.argv[2]
    jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    os.makedirs(out_dir, exist_ok=True)
    boards = {os.path.splitext(os.path.basename(b))[0]: b for b in sorted(glob.glob(os.path.join(boards_dir, "*.json")))}
    with cf.ThreadPoolExecutor(jobs) as ex:
        results = list(ex.map(lambda b: render(b, out_dir), boards.values()))
    for slug, out, err in results:
        print(f"{'OK ' if out else 'ERR'} {slug}" + (f"  {probe(out):.1f}s" if out else f"\n{err}"))
    s = sheet(results, boards, out_dir)
    print("sheet", s)


if __name__ == "__main__":
    main()
