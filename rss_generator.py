"""rss_generator.py — Generate/update podcast RSS 2.0 feed (Apple/Spotify compliant)."""
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import logging

# Load .env if present (stdlib only)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

log = logging.getLogger(__name__)

# Namespaces
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS = "http://www.w3.org/2005/Atom"

ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("atom", ATOM_NS)

MAX_EPISODES = 30  # keep rolling 30 episodes in feed


def _it(tag: str) -> str:
    return f"{{{ITUNES_NS}}}{tag}"


def _seconds_to_hms(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _get_audio_duration(mp3_path: Path) -> float:
    """Use ffprobe to get duration in seconds."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path)],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except Exception as e:
        log.warning(f"ffprobe duration failed: {e}")
        return 0.0


def _load_or_create_feed(feed_path: Path) -> ET.Element:
    if feed_path.exists():
        try:
            tree = ET.parse(feed_path)
            return tree.getroot()
        except ET.ParseError as e:
            log.warning(f"Corrupt feed.xml, rebuilding: {e}")

    # Build fresh feed
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    feed_url = os.environ.get("FEED_URL", "https://example.com/feed.xml")
    site_url = os.environ.get("SITE_URL", "https://example.com")
    cover_url = os.environ.get("COVER_URL", f"{site_url}/cover.jpg")
    author = os.environ.get("PODCAST_AUTHOR", "PaperPulse AI")
    email = os.environ.get("PODCAST_EMAIL", "podcast@example.com")

    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {
        "href": feed_url, "rel": "self", "type": "application/rss+xml"
    })

    for tag, text in [
        ("title", "PaperPulse AI"),
        ("link", site_url),
        ("language", "en-us"),
        ("description", "Daily AI research digest — top arXiv papers explained in plain English by Alex and Sam."),
        ("generator", "PaperPulse AI v1.0"),
        ("copyright", f"© {datetime.now().year} PaperPulse AI"),
    ]:
        ET.SubElement(channel, tag).text = text

    ET.SubElement(channel, _it("title")).text = "PaperPulse AI"
    ET.SubElement(channel, _it("author")).text = author
    ET.SubElement(channel, _it("explicit")).text = "no"
    ET.SubElement(channel, _it("type")).text = "episodic"

    summary_el = ET.SubElement(channel, _it("summary"))
    summary_el.text = "Daily AI research digest — top arXiv papers explained in plain English."

    owner = ET.SubElement(channel, _it("owner"))
    ET.SubElement(owner, _it("name")).text = author
    ET.SubElement(owner, _it("email")).text = email

    cat = ET.SubElement(channel, _it("category"))
    cat.set("text", "Technology")
    subcat = ET.SubElement(cat, _it("category"))
    subcat.set("text", "Tech News")

    image = ET.SubElement(channel, _it("image"))
    image.set("href", cover_url)

    img = ET.SubElement(channel, "image")
    ET.SubElement(img, "url").text = cover_url
    ET.SubElement(img, "title").text = "PaperPulse AI"
    ET.SubElement(img, "link").text = site_url

    return rss


def add_episode(
    feed_path: Path,
    mp3_path: Path,
    papers: list[dict],
    episode_summary: str,
    episode_number: int | None = None,
) -> None:
    """
    Add a new episode to feed.xml.
    mp3_path: absolute path or URL to MP3 file.
    """
    rss = _load_or_create_feed(feed_path)
    channel = rss.find("channel")
    assert channel is not None

    base_url = os.environ.get("EPISODES_BASE_URL", "https://example.com/episodes")
    mp3_filename = mp3_path.name

    # Determine episode number if not provided
    existing = channel.findall("item")
    ep_num = episode_number if episode_number is not None else len(existing) + 1

    # Build title from papers
    if len(papers) == 1:
        ep_title = f"#{ep_num}: {papers[0]['title'][:80]}"
    else:
        titles = " + ".join(p["title"][:40] for p in papers[:2])
        ep_title = f"#{ep_num}: {titles}"

    pub_date = format_datetime(datetime.now(timezone.utc))
    guid = f"paperpulse-{mp3_filename}"
    mp3_url = f"{base_url}/{mp3_filename}"

    # Duration
    duration_s = _get_audio_duration(mp3_path)
    duration_hms = _seconds_to_hms(duration_s)

    # File size (bytes)
    file_size = mp3_path.stat().st_size if mp3_path.exists() else 0

    # Build description HTML
    paper_links = "\n".join(
        f'<li><a href="{p["arxiv_link"]}">{p["title"]}</a></li>'
        for p in papers
    )
    description_html = (
        f"<p>{episode_summary}</p>\n"
        f"<p><strong>Papers covered:</strong></p>\n"
        f"<ul>{paper_links}</ul>"
    )

    # Create item
    item = ET.Element("item")

    for tag, text in [
        ("title", ep_title),
        ("link", mp3_url),
        ("description", episode_summary[:500]),
        ("pubDate", pub_date),
    ]:
        ET.SubElement(item, tag).text = text
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = guid

    ET.SubElement(item, "enclosure", {
        "url": mp3_url,
        "length": str(file_size),
        "type": "audio/mpeg",
    })

    ET.SubElement(item, _it("title")).text = ep_title
    ET.SubElement(item, _it("summary")).text = episode_summary[:500]
    ET.SubElement(item, _it("duration")).text = duration_hms
    ET.SubElement(item, _it("explicit")).text = "no"
    ET.SubElement(item, _it("episodeType")).text = "full"
    ET.SubElement(item, _it("episode")).text = str(ep_num)

    content_el = ET.SubElement(item, f"{{{CONTENT_NS}}}encoded")
    content_el.text = description_html

    # Prepend item (newest first)
    channel.insert(list(channel).index(existing[0]) if existing else len(list(channel)), item)

    # Trim to MAX_EPISODES
    items = channel.findall("item")
    for old in items[MAX_EPISODES:]:
        channel.remove(old)

    # Update lastBuildDate
    last_build = channel.find("lastBuildDate")
    if last_build is None:
        last_build = ET.SubElement(channel, "lastBuildDate")
    last_build.text = pub_date

    # Write atomically
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = feed_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="unicode", xml_declaration=False)
    os.replace(tmp_path, feed_path)

    log.info(f"Feed updated: {feed_path} | Episode #{ep_num} | {duration_hms}")
