"""mastering.py — Assemble, master, and export final podcast MP3 via FFmpeg."""
import subprocess
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Silence duration between segments (milliseconds)
GAP_MS = 300

# Industry standard podcast loudness (EBU R128) & broadcast warmth EQ
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
WARMTH_FILTER = "highpass=f=75,equalizer=f=280:t=q:w=1.2:g=1.5,equalizer=f=3400:t=q:w=1.5:g=1.0"


def _ffmpeg(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", *args]
    log.debug(f"ffmpeg: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-800:]}")
    return result


def _get_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _make_silence(duration_ms: int, out_path: Path):
    duration_s = duration_ms / 1000
    _ffmpeg(
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=mono",
        "-t", str(duration_s),
        "-q:a", "9",
        str(out_path),
    )


def _concat_files(file_list: list[Path], out_path: Path):
    """Concatenate audio files using FFmpeg concat demuxer."""
    list_content = "\n".join(f"file '{f}'" for f in file_list)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(list_content)
        list_file = f.name
    try:
        _ffmpeg(
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            str(out_path),
        )
    finally:
        os.unlink(list_file)


def master(
    segment_files: list[Path],
    output_path: Path,
    assets_dir: Path | None = None,
    gap_ms: int = GAP_MS,
) -> Path:
    """
    Assemble segments with gaps, add intro/outro jingles if present,
    apply EBU R128 loudnorm, export as 128kbps CBR MP3.
    Returns final output_path.
    """
    import uuid
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / f"_mastering_tmp_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Dynamic organic silence gaps (mimic human conversation pacing)
    silences = {
        150: tmp_dir / "silence_150.mp3",
        240: tmp_dir / "silence_240.mp3",
        350: tmp_dir / "silence_350.mp3",
        480: tmp_dir / "silence_480.mp3",
    }
    for ms, path in silences.items():
        _make_silence(ms, path)

    # Build ordered file list
    ordered: list[Path] = []

    intro = (assets_dir / "intro.mp3") if assets_dir else None
    outro = (assets_dir / "outro.mp3") if assets_dir else None

    if intro and intro.exists():
        ordered.append(intro)
        ordered.append(silences[350])
        log.info("Intro jingle included.")

    gap_pattern = [240, 150, 350, 240, 480, 240, 150, 350]
    for i, seg in enumerate(segment_files):
        ordered.append(seg)
        if i < len(segment_files) - 1:
            chosen_gap = gap_pattern[i % len(gap_pattern)]
            ordered.append(silences[chosen_gap])

    if outro and outro.exists():
        ordered.append(silences[350])
        ordered.append(outro)
        log.info("Outro jingle included.")

    # Step 1: Concat raw
    raw_concat = tmp_dir / "raw_concat.mp3"
    log.info(f"Concatenating {len(ordered)} audio segments...")
    _concat_files(ordered, raw_concat)

    # Step 2: Loudnorm two-pass with broadcast warmth EQ
    log.info("Applying broadcast EQ & EBU R128 loudnorm (pass 1: measure)...")
    pass1 = _ffmpeg(
        "-i", str(raw_concat),
        "-af", f"{WARMTH_FILTER},{LOUDNORM}:print_format=json",
        "-f", "null", "-",
        check=False,
    )

    # Parse measured values from stderr for two-pass (if available)
    import json, re
    measured = {}
    try:
        json_matches = re.findall(r'(\{[^{}]*"input_i"[^{}]*\})', pass1.stderr, re.DOTALL)
        if json_matches:
            measured = json.loads(json_matches[-1])
    except Exception as e:
        log.warning(f"Failed to parse loudnorm pass 1 stats: {e}")

    log.info("Applying broadcast EQ & EBU R128 loudnorm (pass 2: apply)...")
    if measured.get("input_i") and measured.get("input_lra") and measured.get("input_tp"):
        loudnorm_filter = (
            f"{LOUDNORM}:"
            f"measured_I={measured['input_i']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_thresh={measured.get('input_thresh', '-70')}:"
            f"offset={measured.get('target_offset', '0')}:"
            f"linear=true:print_format=none"
        )
    else:
        # Single-pass fallback
        loudnorm_filter = LOUDNORM

    _ffmpeg(
        "-i", str(raw_concat),
        "-af", f"{WARMTH_FILTER},{loudnorm_filter}",
        "-ar", "44100",
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        str(output_path),
    )

    duration = _get_duration_seconds(output_path)
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"Mastered: {output_path} | {duration:.1f}s | {size_mb:.2f} MB")

    # Cleanup temp
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return output_path
