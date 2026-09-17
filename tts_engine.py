"""tts_engine.py — Render dialog segments to MP3 via edge-tts (dynamic prosody & async)."""
import asyncio
import logging
import os
from pathlib import Path
import edge_tts

log = logging.getLogger(__name__)


def _compute_prosody(speaker: str, text: str) -> tuple[str, str]:
    """Calculate natural speech rate and pitch based on conversational context."""
    words = text.split()
    word_count = len(words)
    is_question = text.strip().endswith("?")
    is_short_reaction = word_count <= 4

    if speaker == "Alex":
        # Base: energetic, conversational (+5%)
        rate_val = 5
        pitch_val = 0

        if is_short_reaction:
            # Snappy interjection: fast and alert
            rate_val += 4
            pitch_val += 2
        elif is_question:
            pitch_val += 2
            rate_val += 2
        elif word_count > 25:
            # Slightly more measured on long explanations
            rate_val += 1
    else:  # Sam
        # Base: articulate, scientific, calm (+1%)
        rate_val = 1
        pitch_val = -1

        if is_short_reaction:
            rate_val += 3
            pitch_val += 1
        elif is_question:
            pitch_val += 1
        elif word_count > 25:
            # Deliberate pacing when explaining complex math/ablation
            rate_val -= 1
            pitch_val -= 1

    rate_str = f"+{rate_val}%" if rate_val >= 0 else f"{rate_val}%"
    pitch_str = f"+{pitch_val}Hz" if pitch_val >= 0 else f"{pitch_val}Hz"
    return rate_str, pitch_str


async def _render_segment(index: int, speaker: str, voice: str, text: str, out_dir: Path, retries: int = 3) -> Path:
    out_path = out_dir / f"seg_{index:04d}.mp3"
    delay = 2.0
    rate, pitch = _compute_prosody(speaker, text)

    for attempt in range(retries):
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        try:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=rate,
                pitch=pitch,
            )
            await communicate.save(str(out_path))

            if not out_path.exists() or out_path.stat().st_size < 100:
                raise RuntimeError("edge-tts produced empty or truncated output")

            log.debug(f"Rendered segment {index} ({speaker}, rate={rate}, pitch={pitch}): {out_path.stat().st_size} bytes")
            return out_path
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"Segment {index} failed after {retries} attempts: {e}") from e
            log.warning(f"Segment {index} attempt {attempt+1} failed: {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay *= 2


async def _render_all(dialog: list[dict], out_dir: Path, concurrency: int = 4) -> list[Path]:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(i, seg):
        async with semaphore:
            return await _render_segment(
                i,
                seg["speaker"],
                seg["voice"],
                seg["text"],
                out_dir,
            )

    tasks = [bounded(i, seg) for i, seg in enumerate(dialog)]
    results = await asyncio.gather(*tasks)
    return list(results)


def render_segments(dialog: list[dict], out_dir: Path) -> list[Path]:
    """Render all dialog segments to MP3 files. Returns ordered list of paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Rendering {len(dialog)} TTS segments with dynamic prosody to {out_dir}...")
    paths = asyncio.run(_render_all(dialog, out_dir))
    log.info(f"TTS complete: {len(paths)} files.")
    return paths
