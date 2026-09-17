"""writer.py — Generate structured humanized podcast dialog JSON via LLM."""
import json
import os
import time
import logging
import urllib.request
import urllib.error
import ssl
import re

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


SYSTEM_PROMPT = """You are writing a script for "PaperPulse AI", an authentic, unscripted-feeling tech podcast where two senior engineers discuss fresh AI research over coffee:
- Alex (Voice: en-US-AndrewMultilingualNeural): Senior backend/ML infrastructure engineer. Pragmatic, skeptical of academic claims, cares about production latency, memory, and deployment costs. Speaks with punchy, conversational energy and dry humor.
- Sam (Voice: en-US-AvaMultilingualNeural): Research scientist. Deeply understands the math and optimization theory, but is honest about benchmark hacking, data contamination, and study limitations.

Output ONLY a valid JSON array. No markdown fences, no explanatory prose.
Schema: [{"speaker": "Alex"|"Sam", "voice": "en-US-AndrewMultilingualNeural"|"en-US-AvaMultilingualNeural", "text": "..."}]

CRITICAL HUMANIZATION RULES:
1. NEVER SOUND LIKE A TEXTBOOK OR SLIDE DECK:
   - Do NOT give dictionary definitions ("BPE optimizes compression via bottom-up merges...").
   - Instead, explain how it actually works colloquially ("Basically, BPE starts with letters and smashes the most common pairs together...").
2. CONVERSATIONAL CADENCE & MICRO-REACTIONS:
   - Real people do not trade 5-sentence monologues back and forth.
   - Insert quick natural reactions: "Wait, seriously?", "That makes zero sense.", "Hold on a second...", "Right, exactly.", "I mean... look at the benchmarks."
   - Let Alex interrupt or cut to the chase when Sam gets too deep into academic weeds.
   - Use pauses and vocal rhythm: use ellipses "..." for brief thinking hesitations and em dashes "—" for natural self-corrections.
3. ABSOLUTELY BANNED AI PHRASES & TROPES:
   - NEVER start with: "What if I told you...", "Welcome back to...", "In today's fast-paced world...", "Have you ever wondered..."
   - NEVER use filler tropes: "delve", "unpack", "game-changer", "landscape", "pivotal moment", "testament", "at the end of the day", "double down", "it turns out".
   - NO childish metaphors: "Imagine cutting a pizza...", "Like building a Lego castle..." Speak to the audience like senior software engineers.
   - NO sycophantic praise: Do NOT have them flatter each other ("Great question, Alex!", "You're absolutely right!").
   - NO TV wrap-ups: Never say "Join us tomorrow as we explore..." End naturally on a humorous realization, an open question, or a cynical production reality.
4. TARGET LENGTH:
   - 28 to 36 dialog turns total.
   - Word count: 800 to 1100 words.
"""


def _normalize_speech_text(text: str) -> str:
    """Clean text for natural TTS pronunciation (remove AI tics & pronunciation traps)."""
    # Fix arXiv pronunciation: Edge-TTS says 'ar-ex-eye-vee', replace with 'archive'
    text = re.sub(r'\barXiv\b', 'archive', text, flags=re.IGNORECASE)
    # Common abbreviations that sound robotic when spelled out
    replacements = [
        (r'\be\.g\.,?\b', 'for example,'),
        (r'\bi\.e\.,?\b', 'that is,'),
        (r'\bvs\.\b', 'versus'),
        (r'\bvs\b', 'versus'),
        (r'\betc\.\b', 'and so on'),
        (r'\bSOTA\b', 'state of the art'),
        (r'—', ', '),  # em-dashes into natural micro-pauses
        (r'–', ', '),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text.strip()


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
        f"Write a {count}-paper authentic conversational podcast dialogue between Alex and Sam for PaperPulse AI.\n\n"
        f"{papers_block}\n\n"
        f"Output ONLY the JSON array now."
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
        "temperature": 0.8,
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

    valid_voices = {"en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural"}
    for i, seg in enumerate(dialog):
        if not isinstance(seg, dict):
            raise ValueError(f"Segment {i} is not a dict")
        if "speaker" not in seg or "text" not in seg:
            raise ValueError(f"Segment {i} missing speaker or text")
        
        default_voice = "en-US-AndrewMultilingualNeural" if seg["speaker"] == "Alex" else "en-US-AvaMultilingualNeural"
        if seg.get("voice") not in valid_voices:
            seg["voice"] = default_voice

        # Clean speech phonetics
        seg["text"] = _normalize_speech_text(seg["text"])

    log.info(f"Script generated: {len(dialog)} segments, ~{sum(len(s['text'].split()) for s in dialog)} words.")
    return dialog
