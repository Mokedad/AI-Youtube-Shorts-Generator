# Studio: captioned Shorts for clients

A no-API-key workflow that makes finished, sellable Shorts: vertical 1080x1920,
face-tracked, with a headline hook at the top and word-by-word animated captions.

## Steps

```bash
pip install -r requirements-local.txt

# 1. Transcribe (word timings for captions)
python studio/transcribe.py client.mp4 work/client

# 2. Pick highlights: read work/client.txt (or ask Claude to) and write
#    work/client.highlights.json:
#    [{"slug": "01-name", "title": "...", "hook": "Top headline", "start": 118.0, "end": 166.8}]

# 3. Render
python studio/render.py client.mp4 work/client.json work/client.highlights.json output/
```

Each clip becomes `output/<slug>.mp4` plus a `<slug>.jpg` thumbnail.

See `SALES-KIT.md` for pricing, a Fiverr gig, and an outreach message.
Only clip footage you have rights to (client-owned, or public domain like NASA).
