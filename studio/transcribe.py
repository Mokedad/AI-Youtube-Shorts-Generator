"""Transcribe a video with word-level timestamps (needed for animated captions).

Usage: python studio/transcribe.py <video.mp4> <out_prefix>
Writes <out_prefix>.json (segments + words) and <out_prefix>.txt (readable, timestamped).

Audio is decoded with ffmpeg first, which sidesteps PyAV version mismatches.
"""
import json
import subprocess
import sys

import numpy as np
from faster_whisper import WhisperModel


def main():
    src, out = sys.argv[1], sys.argv[2]
    pcm = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(audio, language="en", word_timestamps=True, vad_filter=True)
    data = []
    with open(out + ".txt", "w") as txt:
        for s in segs:
            words = [{"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words]
            data.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(), "words": words})
            txt.write(f"[{s.start:7.1f}-{s.end:7.1f}] {s.text.strip()}\n")
    with open(out + ".json", "w") as f:
        json.dump(data, f)
    print("done", out, len(data))


if __name__ == "__main__":
    main()
