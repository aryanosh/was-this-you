"""Stage 3: fetch the actual content behind a chosen reverse-search match.

Takes one candidate dict as returned by `reverse_search.reverse_image_search`
(SerpApi's own `visual_matches` shape -- at minimum a `link` and an `image`
URL) and fetches the real image bytes plus best-effort page metadata
(caption, title). The image fetch is a hard requirement (Stage 4 hashing
needs real fetched bytes); the page-metadata fetch degrades gracefully since
many platforms (Instagram, LinkedIn, Facebook) actively block scraping --
that's surfaced as a warning, not a crash.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

REQUEST_TIMEOUT_S = 20
# A real browser UA -- SerpApi-listed sites commonly block the default
# `python-requests` UA outright.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


class ContentFetchError(Exception):
    """Base class for all Stage 3 errors."""


class DeadLinkError(ContentFetchError):
    pass


class BlockedError(ContentFetchError):
    pass


class UnsupportedContentError(ContentFetchError):
    pass


@dataclass
class FetchedMatch:
    source_url: str | None
    image_url: str
    image_bytes: bytes
    caption: str | None
    page_title: str | None
    scraped_at: str
    page_fetch_warning: str | None

    def to_dict(self, *, include_image_bytes: bool = False) -> dict:
        d = {
            "source_url": self.source_url,
            "image_url": self.image_url,
            "caption": self.caption,
            "page_title": self.page_title,
            "scraped_at": self.scraped_at,
            "page_fetch_warning": self.page_fetch_warning,
            "image_bytes_len": len(self.image_bytes),
        }
        if include_image_bytes:
            d["image_bytes"] = self.image_bytes
        return d


def fetch_image_bytes(image_url: str) -> bytes:
    try:
        resp = requests.get(image_url, timeout=REQUEST_TIMEOUT_S, headers=BROWSER_HEADERS)
    except requests.exceptions.Timeout as exc:
        raise DeadLinkError(f"Timed out fetching the matched image from {image_url}") from exc
    except requests.exceptions.RequestException as exc:
        raise DeadLinkError(f"Could not reach {image_url}: {exc}") from exc

    if resp.status_code == 404:
        raise DeadLinkError(f"Matched image is gone (HTTP 404) at {image_url}")
    if resp.status_code in (401, 403, 999):
        raise BlockedError(
            f"The host blocked fetching this image (HTTP {resp.status_code}) at {image_url}"
        )
    if resp.status_code != 200:
        raise ContentFetchError(
            f"Unexpected HTTP {resp.status_code} fetching matched image at {image_url}"
        )

    content = resp.content
    try:
        Image.open(io.BytesIO(content)).verify()
    except UnidentifiedImageError as exc:
        raise UnsupportedContentError(
            f"Fetched bytes from {image_url} are not a decodable image."
        ) from exc
    except OSError as exc:
        raise UnsupportedContentError(f"Fetched image at {image_url} appears corrupted: {exc}") from exc

    return content


def _extract_caption(soup: BeautifulSoup) -> str | None:
    for attrs in (
        {"property": "og:description"},
        {"name": "twitter:description"},
        {"name": "description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _fetch_page_metadata(link: str, fallback_title: str | None) -> tuple[str | None, str | None, str | None]:
    """Returns (caption, page_title, warning). Never raises -- best effort only."""
    try:
        resp = requests.get(link, timeout=REQUEST_TIMEOUT_S, headers=BROWSER_HEADERS)
    except requests.exceptions.RequestException as exc:
        return None, fallback_title, f"Could not fetch source page ({exc.__class__.__name__}): using search-result metadata only."

    if resp.status_code != 200:
        return (
            None,
            fallback_title,
            f"Source page returned HTTP {resp.status_code}; using search-result metadata only.",
        )

    soup = BeautifulSoup(resp.text, "html.parser")
    caption = _extract_caption(soup)
    page_title = soup.title.string.strip() if soup.title and soup.title.string else fallback_title
    warning = None if caption else "Page fetched but no caption/description meta tag was found."
    return caption, page_title, warning


def fetch_match_content(match: dict) -> FetchedMatch:
    """Fetch the real image bytes + best-effort metadata for a chosen candidate.

    `match` is expected to be one of the dicts returned by
    reverse_search.reverse_image_search (SerpApi's visual_matches shape).

    Tries the full-size `image` URL first, falling back to `thumbnail` if
    that specific URL is dead/blocked/unreachable (not just if `image` is
    entirely absent). This matters in practice: platforms like Instagram
    that block direct hotlinking of their own CDN's full-size image often
    still leave the search engine's own cached thumbnail URL (a different
    host entirely) fetchable, and face_verify.py already proved a thumbnail
    downloads fine and contains a real face -- so a candidate shouldn't be
    given up on just because its higher-resolution URL alone failed.
    """
    image_candidates = []
    for url in (match.get("image"), match.get("thumbnail")):
        if url and url not in image_candidates:
            image_candidates.append(url)
    if not image_candidates:
        raise ContentFetchError("Selected match has no image URL to fetch.")

    image_bytes = None
    image_url = None
    last_exc: ContentFetchError | None = None
    for candidate_url in image_candidates:
        try:
            image_bytes = fetch_image_bytes(candidate_url)
            image_url = candidate_url
            break
        except (DeadLinkError, BlockedError, UnsupportedContentError, ContentFetchError) as exc:
            last_exc = exc
    if image_bytes is None:
        raise last_exc

    link = match.get("link")
    fallback_title = match.get("title")
    if link:
        caption, page_title, warning = _fetch_page_metadata(link, fallback_title)
    else:
        caption, page_title, warning = None, fallback_title, "Match had no source page link; image-only fetch."

    return FetchedMatch(
        source_url=link,
        image_url=image_url,
        image_bytes=image_bytes,
        caption=caption,
        page_title=page_title,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        page_fetch_warning=warning,
    )
