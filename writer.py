"""writer.py — Generate structured podcast dialog JSON via LLM."""
import json
import os
import time
import logging
import urllib.request
import urllib.error
import ssl

log = logging.getLogger(__name__)


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

SYSTEM_PROMPT = """You are a scriptwriter for "PaperPulse AI", an authentic, high-signal conversational podcast between two real practitioners:
- Alex (Voice: en-US-AndrewMultilingualNeural): pragmatic engineer, slightly skeptical, cares about what breaks in production, talks with punchy rhythm and dry humor.
- Sam (Voice: en-US-AvaMultilingualNeural): research scientist, deep in the math, honest about study limitations, candid about hype vs reality.

Output ONLY a valid JSON array. No markdown, no code fences.
Schema: [{"speaker": "Alex"|"Sam", "voice": "en-US-AndrewMultilingualNeural"|"en-US-AvaMultilingualNeural", "text": "..."}]

ANTI-AI WRITING RULES (STRICT):
1. BANNED PHRASES: Never use "What if I told you", "In today's fast-paced world", "At the end of the day", "Deep dive", "Unpack", "Game-changer", "It turns out", "Double down", "Delve", "Testament", "Pivotal", "Landscape".
2. BANNED PATTERNS:
   - No sycophantic praise ("Great point!", "You're absolutely right!", "Exactly!"). Real colleagues challenge each other or build directly without flattery.
   - No kindergarten metaphors ("Imagine a pizza...", "Like a Lego castle..."). Speak like engineers talking to senior engineers.
   - No rhetorical warm-ups or meta-signposting ("For our listeners today...", "Let's explore..."). Jump straight into the core technical tension.
   - No formulaic TV sign-offs ("Join us tomorrow as we delve into..."). End abruptly on an authentic punchline, realization, or skeptical thought.
3. CONVERSATIONAL REALISM:
   - Varied turn lengths: Mix short reactive interjections ("Wait, seriously?", "That's wild.", "No way.") with substantive 2-3 sentence explanations.
   - Disagreements & skepticism: Have Alex question whether benchmark improvements actually translate to production latency or cost.
   - Natural spoken cadence: Use contractions (didn't, haven't, that's), informal transitions, and honest uncertainty ("I honestly have no idea how they got that number", "Their ablation section is pretty thin").
   - Length: 25-35 turns total, totaling 800-1100 words across all segments.
"""


def _build_user_prompt(papers: list[dict]) -> str:
    parts = []
    for i, p in enumerate(papers, 1):
        authors_str = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors_str += f" et al."
        parts.append(
            f"Paper {i}: {p['title']}\n"
            f"Authors: {authors_str}\n"
            f"Abstract: {p['abstract'][:800]}\n"
            f"Link: {p['arxiv_link']}"
        )
    papers_block = "\n\n".join(parts)
    count = len(papers)
    return (
        f"Write a {count}-paper podcast episode for PaperPulse AI.\n\n"
        f"{papers_block}\n\n"
        f"Output the dialog JSON array now."
    )


def _call_llm(prompt: str, system: str, retries: int = 3) -> str:
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "4096"))

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    url = f"{base_url}/chat/completions"
    delay = 2.0
    ctx = _get_ssl_context()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                data = json.loads(r.read())
                return data["choices"][0]["message"]["content"]
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                log.warning("SSL cert verify failed; falling back to unverified context.")
                ctx = ssl._create_unverified_context()
            if attempt == retries - 1:
                raise
            log.warning(f"LLM attempt {attempt+1} failed: {e}. Retry in {delay}s...")
            time.sleep(delay)
            delay *= 2
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            log.warning(f"LLM HTTP {e.code} attempt {attempt+1}: {body[:200]}")
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            log.warning(f"LLM attempt {attempt+1} failed: {e}. Retry in {delay}s...")
            time.sleep(delay)
            delay *= 2


def generate_script(papers: list[dict]) -> list[dict]:
    """Return list of dialog segments: [{speaker, voice, text}, ...]"""
    prompt = _build_user_prompt(papers)
    log.info("Calling LLM to generate podcast script...")
    raw = _call_llm(prompt, SYSTEM_PROMPT)

    # Strip markdown fences if model adds them
    content = raw.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(
            l for l in lines if not l.startswith("```")
        ).strip()

    try:
        dialog = json.loads(content)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed. Raw output:\n{content[:500]}")
        raise ValueError(f"LLM returned invalid JSON: {e}") from e

    # Validate schema
    valid_voices = {"en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural"}
    for i, seg in enumerate(dialog):
        if not isinstance(seg, dict):
            raise ValueError(f"Segment {i} is not a dict")
        if "speaker" not in seg or "text" not in seg:
            raise ValueError(f"Segment {i} missing speaker or text")
        default_voice = "en-US-AndrewMultilingualNeural" if seg["speaker"] == "Alex" else "en-US-AvaMultilingualNeural"
        if seg.get("voice") not in valid_voices:
            seg["voice"] = default_voice

    log.info(f"Script generated: {len(dialog)} segments, ~{sum(len(s['text'].split()) for s in dialog)} words.")
    return dialog
