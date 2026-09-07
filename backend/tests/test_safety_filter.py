"""Unit tests for content safety filtering and domain reputation weighting."""
from __future__ import annotations

import pytest

from app.safety_filter import (
    BLOCKED_DOMAINS,
    BLOCKED_PATTERNS,
    TRUSTED_DOMAINS,
    domain_trust_bonus,
    filter_safe_candidates,
    is_safe_candidate,
)
from app.face_verify import VerifiedMatch, verify_candidates


class TestSafetyFilter:
    def test_blocks_explicit_keywords_in_url(self):
        candidate = {
            "link": "https://example.com/baccho+ki+chudai+video",
            "title": "Video Gallery",
            "source": "example.com",
        }
        safe, reason = is_safe_candidate(candidate)
        assert not safe
        assert "offensive/spam keyword" in reason

    def test_blocks_adult_keywords_in_title(self):
        candidate = {
            "link": "https://example.com/gallery/123",
            "title": "Celebrity nude photo leak",
            "source": "example.com",
        }
        safe, reason = is_safe_candidate(candidate)
        assert not safe
        assert "offensive/spam keyword" in reason

    def test_blocks_blocked_domains(self):
        for bad_domain in ("rinokrf.ru", "shop.oldbluelast.beer", "umcs.org.ua"):
            candidate = {
                "link": f"https://{bad_domain}/some/page",
                "title": "Clean Title",
                "source": bad_domain,
            }
            safe, reason = is_safe_candidate(candidate)
            assert not safe, f"Expected {bad_domain} to be blocked"
            assert "blocked spam/doorway list" in reason

    def test_allows_clean_legitimate_sites(self):
        clean_candidates = [
            {
                "link": "https://www.youtube.com/@mumayop",
                "title": "Samay Raina O-Fans - YouTube",
                "source": "youtube.com",
            },
            {
                "link": "https://en.wikipedia.org/wiki/File:Samay_raina.jpg",
                "title": "File:Samay raina.jpg - Wikipedia",
                "source": "en.wikipedia.org",
            },
            {
                "link": "https://www.filmibeat.com/bollywood/viral/2025/samay-raina.html",
                "title": "Samay Raina Is BACK On Social Media - Filmibeat",
                "source": "filmibeat.com",
            },
            {
                "link": "https://in.pinterest.com/pin/123456789/",
                "title": "Virat Kohli HD Wallpaper",
                "source": "in.pinterest.com",
            },
        ]
        for c in clean_candidates:
            safe, reason = is_safe_candidate(c)
            assert safe, f"Expected {c['link']} to be allowed, but got: {reason}"

    def test_domain_trust_bonus(self):
        assert domain_trust_bonus("https://www.youtube.com/watch?v=123") == 15.0
        assert domain_trust_bonus("https://en.wikipedia.org/wiki/Person") == 15.0
        assert domain_trust_bonus("https://filmibeat.com/news") == 15.0
        assert domain_trust_bonus("https://in.pinterest.com/pin/123") == 15.0
        assert domain_trust_bonus("https://unknown-personal-blog-1234.com/post") == 0.0

    def test_filter_safe_candidates_removes_bad_entries(self):
        mixed = [
            {"link": "https://rinokrf.ru/page", "title": "bad", "source": "rinokrf.ru"},
            {"link": "https://youtube.com/watch", "title": "Samay Raina", "source": "youtube.com"},
            {"link": "https://site.com/chudai", "title": "bad keyword", "source": "site.com"},
            {"link": "https://wikipedia.org/wiki/Samay", "title": "Samay", "source": "wikipedia.org"},
        ]
        filtered = filter_safe_candidates(mixed)
        assert len(filtered) == 2
        assert filtered[0]["source"] == "youtube.com"
        assert filtered[1]["source"] == "wikipedia.org"


class TestReputationRanking:
    def test_trusted_platform_prioritized_over_unknown_site(self):
        # A trusted site with 85% similarity should beat an unknown site with 87% similarity
        # because the trusted site gets a +15.0 bonus (effective 100.0 vs 87.0)
        trusted_candidate = {
            "link": "https://www.youtube.com/watch?v=123",
            "title": "Official YouTube Video",
            "source": "youtube.com",
        }
        unknown_candidate = {
            "link": "https://random-scraper-blog.com/photo",
            "title": "Random Blog Photo",
            "source": "random-scraper-blog.com",
        }

        vm_trusted = VerifiedMatch(
            original_match=trusted_candidate,
            face_similarity=85.0,
            face_found=True,
            passed=True,
            reject_reason=None,
        )
        vm_unknown = VerifiedMatch(
            original_match=unknown_candidate,
            face_similarity=87.0,
            face_found=True,
            passed=True,
            reject_reason=None,
        )

        candidates = [vm_unknown, vm_trusted]
        # Rank by composite score
        from app.face_verify import domain_trust_bonus
        candidates.sort(key=lambda vm: (vm.face_similarity or 0.0) + domain_trust_bonus(vm.original_match.get("link")), reverse=True)

        assert candidates[0].original_match["source"] == "youtube.com", "Trusted platform should be ranked #1"
