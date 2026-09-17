"""main.py — PaperPulse AI orchestrator. Usage: python main.py --run"""
import argparse
import json
import logging
import os
import shutil
import sys
import time
import urllib.request
import ssl
from datetime import datetime, timezone
from pathlib import Path

# Load .env if present (stdlib only)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from fetcher import fetch_papers
from writer import generate_script
from tts_engine import render_segments
from mastering import master
from rss_generator import add_episode


def _get_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        ctx = ssl.create_default_context()
        ctx.load_default_certs()
        return ctx
    except Exception:
        return ssl._create_unverified_context()


def setup_logging(level: str = "INFO"):
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=fmt)


def make_dirs(base: Path) -> tuple[Path, Path, Path]:
    episodes_dir = base / "output" / "episodes"
    temp_dir = base / "output" / "temp"
    assets_dir = base / "assets"
    for d in (episodes_dir, temp_dir, assets_dir):
        d.mkdir(parents=True, exist_ok=True)
    return episodes_dir, temp_dir, assets_dir


def episode_summary_from_papers(papers: list[dict]) -> str:
    """Generate a short episode summary from paper titles."""
    titles = [p["title"] for p in papers]
    if len(titles) == 1:
        return f"Today we explore: {titles[0]}."
    joined = "; ".join(titles[:-1]) + f"; and {titles[-1]}"
    return f"Today's episode covers {len(titles)} papers: {joined}."


def _ping_healthcheck(url: str | None, fail: bool = False):
    if not url:
        return
    ping_url = f"{url.rstrip('/')}/fail" if fail else url
    ctx = _get_ssl_context()
    try:
        with urllib.request.urlopen(ping_url, timeout=10, context=ctx) as resp:
            pass
    except Exception as e:
        logging.getLogger("main").warning(f"Healthcheck ping failed ({ping_url}): {e}")


def _validate_env(dry_run: bool):
    log = logging.getLogger("main")
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key or api_key == "sk-...":
        log.error("LLM_API_KEY is not configured in .env.")
        sys.exit(1)

    if not dry_run:
        feed_url = os.environ.get("FEED_URL", "")
        ep_url = os.environ.get("EPISODES_BASE_URL", "")
        if "example.com" in feed_url or "example.com" in ep_url:
            log.warning("FEED_URL or EPISODES_BASE_URL still points to example.com.")


def _cleanup_old_dialogs(temp_dir: Path, keep_last: int = 5):
    dialogs = sorted(temp_dir.glob("dialog_*.json"), key=os.path.getmtime, reverse=True)
    for old_file in dialogs[keep_last:]:
        try:
            old_file.unlink()
        except OSError:
            pass


def run(base: Path, dry_run: bool = False):
    log = logging.getLogger("main")
    hc_url = os.environ.get("HEALTHCHECK_URL")

    # Lock file check
    lock_file = base / ".paperpulse.lock"
    if lock_file.exists():
        log.warning(f"Lock file exists ({lock_file}). Another process is running. Aborting.")
        sys.exit(0)

    try:
        lock_file.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to create lock file: {e}")
        sys.exit(1)

    try:
        _validate_env(dry_run)
        episodes_dir, temp_dir, assets_dir = make_dirs(base)

        # Step 1: Fetch papers
        log.info("=== Step 1/5: Fetching papers ===")
        papers = fetch_papers(max_results=int(os.environ.get("MAX_PAPERS", "2")))
        if not papers:
            raise RuntimeError("No papers fetched from Arxiv.")
        log.info(f"Papers: {[p['title'][:60] for p in papers]}")

        # Step 2: Generate script
        log.info("=== Step 2/5: Generating podcast script ===")
        dialog = generate_script(papers)

        # Save dialog JSON for audit/debug
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dialog_path = temp_dir / f"dialog_{ts}.json"
        dialog_path.write_text(json.dumps(dialog, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"Dialog saved: {dialog_path}")

        if dry_run:
            log.info("Dry run: stopping after script generation.")
            print(json.dumps(dialog, indent=2))
            _ping_healthcheck(hc_url, fail=False)
            return

        # Step 3: TTS
        log.info("=== Step 3/5: TTS rendering ===")
        seg_dir = temp_dir / f"segs_{ts}"
        segment_files = render_segments(dialog, seg_dir)

        # Step 4: Mastering
        log.info("=== Step 4/5: Audio mastering ===")
        ep_filename = f"paperpulse_{ts}.mp3"
        ep_path = episodes_dir / ep_filename
        master(segment_files, ep_path, assets_dir=assets_dir)

        # Step 5: RSS feed
        log.info("=== Step 5/5: Updating RSS feed ===")
        feed_path = base / "feed.xml"
        summary = episode_summary_from_papers(papers)

        existing_eps = [f for f in episodes_dir.glob("paperpulse_*.mp3") if f != ep_path]
        ep_num = len(existing_eps) + 1

        add_episode(feed_path, ep_path, papers, summary, episode_number=ep_num)

        # Sync to public/ directory for static hosting (Cloudflare Pages)
        pub_dir = base / "public"
        pub_dir.mkdir(parents=True, exist_ok=True)
        (pub_dir / "episodes").mkdir(parents=True, exist_ok=True)
        shutil.copy2(feed_path, pub_dir / "feed.xml")
        shutil.copy2(ep_path, pub_dir / "episodes" / ep_path.name)
        if (base / "cover.png").exists():
            shutil.copy2(base / "cover.png", pub_dir / "cover.png")

        # Cleanup transient files
        try:
            shutil.rmtree(seg_dir, ignore_errors=True)
            _cleanup_old_dialogs(temp_dir, keep_last=5)
            log.info(f"Cleaned temp segments: {seg_dir}")
        except Exception as e:
            log.warning(f"Temp cleanup warning: {e}")

        log.info(f"=== Done! Episode #{ep_num}: {ep_path} | Feed: {feed_path} ===")
        print(f"SUCCESS: {ep_path}")
        _ping_healthcheck(hc_url, fail=False)

    except Exception as e:
        log.error(f"Execution failed: {e}", exc_info=True)
        _ping_healthcheck(hc_url, fail=True)
        sys.exit(1)
    finally:
        if lock_file.exists():
            try:
                lock_file.unlink()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="PaperPulse AI — daily podcast generator")
    parser.add_argument("--run", action="store_true", help="Run full pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + generate script only (no TTS/audio)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--base-dir", default=str(Path(__file__).parent), help="Project base directory")
    args = parser.parse_args()

    setup_logging(args.log_level)

    if not args.run and not args.dry_run:
        parser.print_help()
        sys.exit(0)

    base = Path(args.base_dir).resolve()
    run(base, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
