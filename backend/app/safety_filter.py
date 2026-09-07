"""Safety and domain reputation filtering for reverse image search candidates.

Prevents illegitimate, explicit, adult, scam, or doorway domains from ever
entering the pipeline, being displayed in the UI, or being written to the
blockchain. Also applies domain authority/trust weighting to prioritize
reputable platforms (e.g. YouTube, Wikipedia, Filmibeat, News18, Pinterest)
over obscure, unverified, or spammy scrapers.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Patterns matching explicit, offensive, abusive, or spam doorway terms
# across English, Hindi/Hinglish, and Russian.
BLOCKED_PATTERNS = [
    # English explicit / adult
    r"\bsex\b",
    r"\bporn\b",
    r"\bxxx\b",
    r"\bnude\b",
    r"\bnudes\b",
    r"\berotic\b",
    r"\bescort\b",
    r"\badult\b",
    r"\bslut\b",
    r"\bwhore\b",
    r"\bboobs\b",
    r"\bpenis\b",
    r"\bvagina\b",
    r"\bpussy\b",
    r"\banal\b",
    # South Asian / Hindi explicit & abusive keywords
    r"baccho",
    r"bacchon",
    r"chudai",
    r"choda",
    r"chodi",
    r"gaand",
    r"bhosd",
    r"randi",
    # Russian explicit terms common in SEO doorway spam
    r"порно",
    r"секс",
    r"шлюх",
    r"интим",
    # Scam / malware / gambling doorway terms
    r"\bcasino\b",
    r"\bbetting\b",
    r"\bwarez\b",
    r"\btorrent\b",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

# Explicitly blocked doorway, piracy, and spam domains
BLOCKED_DOMAINS = {
    "rinokrf.ru",
    "shop.oldbluelast.beer",
    "umcs.org.ua",
    "avito.ru",
}

# Trusted, authoritative public platforms. When verified by facial recognition,
# these receive a trust bonus (+15.0%) so legitimate platforms are prioritized
# over low-reputation or scraper blogs.
TRUSTED_DOMAINS = {
    # Video & Social
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "reddit.com",
    "pinterest.com",
    "in.pinterest.com",
    "id.pinterest.com",
    "it.pinterest.com",
    "linkedin.com",
    # Encyclopedias & Knowledge
    "wikipedia.org",
    "en.wikipedia.org",
    "wikimedia.org",
    "commons.wikimedia.org",
    "wikidata.org",
    "imdb.com",
    # Major News & Entertainment Media
    "filmibeat.com",
    "news18.com",
    "thestatesman.com",
    "latestly.com",
    "indiatoday.in",
    "hindustantimes.com",
    "timesofindia.indiatimes.com",
    "ndtv.com",
    "indianexpress.com",
    "bbc.com",
    "cnn.com",
    "reuters.com",
    "forbes.com",
    # Portfolios, Photography & Communities
    "chess.com",
    "artstation.com",
    "behance.net",
    "flickr.com",
    "wallpaperbat.com",
}


def _extract_domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def is_safe_candidate(candidate: dict) -> tuple[bool, str]:
    """Examines a search candidate's URL, domain, title, and source.

    Returns (True, 'OK') if the candidate is clean and safe to process,
    or (False, reason) if it triggers safety or spam filters.
    """
    link = candidate.get("link") or ""
    title = candidate.get("title") or ""
    source = candidate.get("source") or ""

    domain = _extract_domain(link) or _extract_domain(source) or source.lower().replace("www.", "")

    # 1. Domain blocklist check
    if any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS):
        return False, f"Domain '{domain}' is on the blocked spam/doorway list"

    # 2. Text inspection: URL, title, and source
    text_to_check = unquote(f"{link} {title} {source}")
    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(text_to_check)
        if match:
            return False, f"Matched offensive/spam keyword filter: '{match.group(0)}'"

    return True, "OK"


def domain_trust_bonus(url: str | None) -> float:
    """Returns a +15.0 bonus score for recognized, authoritative platforms."""
    domain = _extract_domain(url)
    if not domain:
        return 0.0

    for td in TRUSTED_DOMAINS:
        if domain == td or domain.endswith("." + td):
            return 15.0

    return 0.0


def filter_safe_candidates(candidates: list[dict]) -> list[dict]:
    """Filters out any candidates that trigger safety or domain spam filters."""
    safe_list = []
    for c in candidates:
        safe, reason = is_safe_candidate(c)
        if safe:
            safe_list.append(c)
        else:
            logger.warning(
                "Dropping unsafe/spam candidate '%s' (%s): %s",
                c.get("source") or c.get("title") or "unknown",
                c.get("link") or "?",
                reason,
            )
    return safe_list
