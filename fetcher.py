"""fetcher.py — Fetch top Arxiv papers (cs.AI, cs.LG, cs.CL) from last 24h."""
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import time
import logging
import ssl

log = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


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


def _fetch_url(url: str, retries: int = 3, delay: float = 2.0) -> bytes:
    ctx = _get_ssl_context()
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30, context=ctx) as r:
                return r.read()
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                log.warning("SSL cert verify failed; falling back to unverified context.")
                ctx = ssl._create_unverified_context()
            if attempt == retries - 1:
                raise
            log.warning(f"Fetch attempt {attempt+1} failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)
            delay *= 2
        except Exception as e:
            if attempt == retries - 1:
                raise
            log.warning(f"Fetch attempt {attempt+1} failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)
            delay *= 2


def fetch_papers(max_results: int = 3) -> list[dict]:
    """Return list of top papers from last 24h across cs.AI, cs.LG, cs.CL."""
    cat_query = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    # Fetch recent papers; date-filter client-side (Arxiv submittedDate filter is fragile)
    query = urllib.parse.urlencode({
        "search_query": cat_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max(max_results * 6, 18),  # fetch extra to survive date filter
        "start": 0,
    })
    url = f"{ARXIV_API}?{query}"
    log.info(f"Fetching Arxiv: {url}")
    raw = _fetch_url(url)
    root = ET.fromstring(raw)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    papers = []

    for entry in root.findall("atom:entry", NS):
        if len(papers) >= max_results:
            break

        published_str = entry.findtext("atom:published", "", NS)
        try:
            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        if published < cutoff:
            continue

        # Extract fields
        arxiv_id_raw = entry.findtext("atom:id", "", NS)
        arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw

        title = (entry.findtext("atom:title", "", NS) or "").replace("\n", " ").strip()
        abstract = (entry.findtext("atom:summary", "", NS) or "").replace("\n", " ").strip()

        authors = [
            a.findtext("atom:name", "", NS)
            for a in entry.findall("atom:author", NS)
        ]

        pdf_link = ""
        arxiv_link = arxiv_id_raw
        for link in entry.findall("atom:link", NS):
            rel = link.get("rel", "")
            title_attr = link.get("title", "")
            href = link.get("href", "")
            if title_attr == "pdf":
                pdf_link = href
            elif rel == "alternate":
                arxiv_link = href

        papers.append({
            "id": arxiv_id,
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "arxiv_link": arxiv_link,
            "pdf_link": pdf_link,
            "published": published_str,
        })

    if not papers:
        # Fallback: if no papers in last 24h (weekend/holiday gap), grab latest regardless
        log.warning("No papers in last 24h; falling back to latest submissions.")
        query_fallback = urllib.parse.urlencode({
            "search_query": cat_query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        })
        raw = _fetch_url(f"{ARXIV_API}?{query_fallback}")
        root = ET.fromstring(raw)
        for entry in root.findall("atom:entry", NS):
            if len(papers) >= max_results:
                break
            arxiv_id_raw = entry.findtext("atom:id", "", NS)
            arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw
            title = (entry.findtext("atom:title", "", NS) or "").replace("\n", " ").strip()
            abstract = (entry.findtext("atom:summary", "", NS) or "").replace("\n", " ").strip()
            authors = [a.findtext("atom:name", "", NS) for a in entry.findall("atom:author", NS)]
            pdf_link = ""
            arxiv_link = arxiv_id_raw
            for link in entry.findall("atom:link", NS):
                if link.get("title") == "pdf":
                    pdf_link = link.get("href", "")
                elif link.get("rel") == "alternate":
                    arxiv_link = link.get("href", "")
            papers.append({
                "id": arxiv_id,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "arxiv_link": arxiv_link,
                "pdf_link": pdf_link,
                "published": entry.findtext("atom:published", "", NS),
            })

    log.info(f"Fetched {len(papers)} papers.")
    return papers
