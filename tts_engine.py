"""tts_engine.py — Render dialog segments to MP3 via edge-tts (async, parallel)."""
import asyncio
import logging
import os
from pathlib import Path
import edge_tts

log = logging.getLogger(__name__)


async def _render_segment(index: int, voice: str, text: str, out_dir: Path, retries: int = 3) -> Path:
    out_path = out_dir / f"seg_{index:04d}.mp3"
    delay = 2.0
    for attempt in range(retries):
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(out_path))

            if not out_path.exists() or out_path.stat().st_size < 100:
                raise RuntimeError("edge-tts produced empty or truncated output")

            log.debug(f"Rendered segment {index} ({voice}): {out_path.stat().st_size} bytes")
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
            return await _render_segment(i, seg["voice"], seg["text"], out_dir)

    tasks = [bounded(i, seg) for i, seg in enumerate(dialog)]
    results = await asyncio.gather(*tasks)
    return list(results)


def render_segments(dialog: list[dict], out_dir: Path) -> list[Path]:
    """Render all dialog segments to MP3 files. Returns ordered list of paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Rendering {len(dialog)} TTS segments to {out_dir}...")
    paths = asyncio.run(_render_all(dialog, out_dir))
    log.info(f"TTS complete: {len(paths)} files.")
    return paths
