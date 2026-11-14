"""Local clipping: ffmpeg subclip + OpenCV face-aware vertical crop.

Two stages per highlight:
  1. Cut the source video to [start, end] with ffmpeg (re-encoded, audio kept).
  2. Reframe the cut to the target aspect ratio. For 9:16 we slide a vertical
     window horizontally across the frame to keep faces centred (Haar
     cascade — same approach as the original repo, no external models).
"""
import os
import subprocess
from typing import Dict, List, Optional, Tuple

from ..config import LOCAL_OUTPUT_DIR


def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' → 9/16, '1:1' → 1.0."""
    try:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0


def _cut_subclip(source_path: str, start: float, end: float, out_path: str) -> str:
    """ffmpeg -ss start -to end → re-encoded mp4 with audio."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        # Seek before -i (fast keyframe seek; re-encode keeps it frame-accurate)
        # so long sources aren't decoded from the start for every clip.
        "-ss", f"{start:.3f}",
        "-i", source_path,
        "-t", f"{end - start:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path



def plan_camera_path(
    samples: List[Tuple[int, float]],
    n_frames: int,
    fps: float,
    src_w: int,
    crop_w: int,
) -> List[float]:
    """Turn noisy per-frame face x-positions into a steady virtual camera path.

    A face detector jitters by tens of pixels between frames and sometimes
    fires on background clutter. Chasing it frame by frame makes the crop shake.
    Instead: drop outliers, median-filter, then hold the camera still until the
    face drifts outside a dead zone, and pan there slowly.
    """
    if n_frames <= 0:
        return []
    if not samples:
        return [src_w / 2.0] * n_frames

    import numpy as np

    idx = np.array([f for f, _ in samples], dtype=float)
    xs = np.array([x for _, x in samples], dtype=float)

    # Drop detections far from the clip's typical face position (false positives).
    med = np.median(xs)
    keep = np.abs(xs - med) <= max(crop_w * 0.6, 1.0)
    if keep.sum() >= 1:
        idx, xs = idx[keep], xs[keep]

    # Rolling median over ~1s of samples to kill jitter.
    half = max(1, int(fps / 5 / 2))
    xs = np.array([np.median(xs[max(0, k - half):k + half + 1]) for k in range(len(xs))])

    raw = np.interp(np.arange(n_frames), idx, xs)

    # Dead zone + slow ease: stay put unless the face leaves the middle of the frame.
    dead = crop_w * 0.12
    ease = 1.0 / max(1.0, fps * 0.6)  # ~0.6s to settle
    lo, hi = crop_w / 2.0, src_w - crop_w / 2.0
    pos = float(np.clip(np.median(raw[: max(1, int(fps))]), lo, hi))
    out = []
    for t in raw:
        err = t - pos
        if abs(err) > dead:
            pos += (err - np.sign(err) * dead) * ease
        pos = float(np.clip(pos, lo, hi))
        out.append(pos)
    return out


def _reframe_vertical(in_path: str, out_path: str, aspect_ratio: str) -> str:
    """Crop the cut clip to the target aspect ratio, tracking faces if possible."""
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "opencv-python is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    target_ratio = _ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {in_path}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Compute the largest crop that fits inside the frame at the target ratio.
    if target_ratio < src_w / src_h:
        crop_h = src_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(crop_w / target_ratio)
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    # Pass 1: sample face positions across the whole clip.
    detect_every = 5
    scale = 480.0 / src_h if src_h > 480 else 1.0  # detect on a small copy: faster, same result
    samples: List[Tuple[int, float]] = []
    n_frames = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if n_frames % detect_every == 0:
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            min_side = max(24, int(gray.shape[0] * 0.08))
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
            if len(faces) > 0:
                x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
                samples.append((n_frames, (x + w / 2) / scale))
        n_frames += 1
    cap.release()

    path = plan_camera_path(samples, n_frames, fps, src_w, crop_w)

    # Pass 2: write the crops along the planned (stable) path.
    cap = cv2.VideoCapture(in_path)
    silent_path = out_path + ".silent.mp4"
    writer = cv2.VideoWriter(silent_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (crop_w, crop_h))
    y0 = max(0, (src_h - crop_h) // 2)
    i = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cx = path[min(i, len(path) - 1)] if path else src_w / 2
        x0 = int(round(max(0, min(src_w - crop_w, cx - crop_w / 2))))
        writer.write(frame[y0:y0 + crop_h, x0:x0 + crop_w])
        i += 1
    cap.release()
    writer.release()

    # Mux audio from the cut clip back onto the silent reframed video.
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", silent_path,
        "-i", in_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(silent_path)
    return out_path


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
) -> str:
    """Cut + reframe one highlight, returning the local mp4 path."""
    cut_path = out_path + ".cut.mp4"
    try:
        _cut_subclip(source_path, start_time, end_time, cut_path)
        _reframe_vertical(cut_path, out_path, aspect_ratio)
    finally:
        if os.path.exists(cut_path):
            os.remove(cut_path)
    return out_path


def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    aspect_ratio: str = "9:16",
    out_dir: Optional[str] = None,
) -> List[Dict]:
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    results: List[Dict] = []
    for i, h in enumerate(highlights, 1):
        out_path = os.path.join(out_dir, f"short_{i:02d}.mp4")
        print(f"[clip/local] {i}/{len(highlights)}: {h.get('title', '(untitled)')}", flush=True)
        try:
            crop_clip_local(
                source_path,
                float(h["start_time"]),
                float(h["end_time"]),
                aspect_ratio,
                out_path,
            )
            results.append({**h, "clip_url": out_path})
        except Exception as e:
            print(f"[clip/local] {i} failed: {e}", flush=True)
            results.append({**h, "clip_url": None, "error": str(e)})
    return results
