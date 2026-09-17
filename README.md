# PaperPulse AI

Daily AI research podcast — top arXiv papers explained by Alex & Sam, auto-generated and published via RSS 2.0.

## Stack

| Component | Tool |
|---|---|
| Paper fetch | Arxiv API (`urllib` + `xml.etree`) |
| Script | OpenAI-compatible LLM (`urllib`) |
| TTS | `edge-tts` (async, parallel) |
| Audio | FFmpeg (concat + EBU R128 loudnorm + 128kbps CBR MP3) |
| Feed | RSS 2.0, Apple Podcasts + Spotify compliant |

No external Python deps except `edge-tts`. Everything else: stdlib.

## Setup

```bash
cd paperpulse
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — add LLM_API_KEY, EPISODES_BASE_URL, FEED_URL
```

**Prerequisites:** `ffmpeg` and `ffprobe` must be on `PATH`.

## Run

```bash
python main.py --run
# or dry-run (fetch + LLM only, no TTS/audio):
python main.py --dry-run
```

## Directory Structure

```
paperpulse/
├── main.py            # Orchestrator
├── fetcher.py         # Arxiv paper fetcher
├── writer.py          # LLM script generator
├── tts_engine.py      # edge-tts async renderer
├── mastering.py       # FFmpeg audio mastering
├── rss_generator.py   # RSS 2.0 feed builder
├── requirements.txt
├── .env.example
├── .env               # your secrets (gitignore this)
├── assets/            # optional: intro.mp3, outro.mp3
├── feed.xml           # generated RSS feed (serve this publicly)
└── output/
    ├── episodes/      # final mastered MP3s
    └── temp/          # transient files (auto-cleaned after each run)
```

## Cron Job (Linux VPS)

```bash
# Run daily at 06:00 UTC
0 6 * * * cd /home/ubuntu/paperpulse && /home/ubuntu/paperpulse/.venv/bin/python main.py --run >> /var/log/paperpulse.log 2>&1
```

## Spotify / Apple Podcasts Setup

1. Serve `feed.xml` and `output/episodes/*.mp3` at public HTTPS URLs.
2. Update `.env`: `FEED_URL`, `EPISODES_BASE_URL`, `COVER_URL`.
3. Submit `FEED_URL` to:
   - Spotify: https://podcasters.spotify.com
   - Apple: https://podcastsconnect.apple.com
4. Cover art: ≥1400×1400px JPEG/PNG (3000×3000 recommended).

## Optional Assets

Drop these in `assets/` for jingle support:
- `assets/intro.mp3` — played before episode content (≤5s recommended)
- `assets/outro.mp3` — played after episode content

## Custom LLM

Point to any OpenAI-compatible endpoint:

```env
LLM_BASE_URL=http://localhost:11434/v1   # Ollama
LLM_API_KEY=ollama
LLM_MODEL=llama3.1:8b
```
