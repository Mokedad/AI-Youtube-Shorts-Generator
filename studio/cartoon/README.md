# Cartoon explainers

Storyboard JSON in, finished 9:16 Short out: original flat-style animation, free local
voice (Kokoro TTS), word-by-word captions.

```bash
pip install kokoro-onnx soundfile
# one-time model download (~350 MB) into $KOKORO_DIR (default /home/user/shorts-studio/tts):
#   kokoro-v1.0.onnx and voices-v1.0.bin from
#   https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
python studio/cartoon/engine.py studio/cartoon/boards/helmet-in-space.json out.mp4
```

Each storyboard line is one narration beat plus one scene; the scene lasts exactly as long
as its narration. Scene types: `hero`, `counter`, `versus`, `list`, `bigtext`, `cannon`,
`station_orbit`, `rocket`, `outro`. Sprites and options are listed at the top of `engine.py`.
Keep facts accurate: check each claim against NASA/ESA before writing it into a board.
