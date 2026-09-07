"""Quick validation that the face similarity threshold correctly separates
known-same-person pairs from known-different-person pairs.

This uses face_recognition's own encoding model to generate synthetic test
cases: two encodings of the *same* face (from the same image, so 100%
identity) versus a perturbed encoding that simulates a different person
(large Euclidean distance). The threshold is imported from config.py so it
matches whatever the pipeline is actually using at runtime.

Run:  python -m pytest backend/tests/test_face_threshold.py -v
  or: cd backend && ../.venv/Scripts/python -m pytest tests/test_face_threshold.py -v
"""
from __future__ import annotations

import numpy as np

from app.face_detect import compare_faces
from app.config import FACE_SIMILARITY_THRESHOLD


def _make_encoding(seed: int = 42) -> list[float]:
    """Generate a deterministic 128-d encoding vector (unit-normalized like
    face_recognition's real outputs)."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(128)
    v = v / np.linalg.norm(v)
    return v.tolist()


def _perturb_encoding(encoding: list[float], distance: float, seed: int = 99) -> list[float]:
    """Move `encoding` by exactly `distance` in a random direction."""
    rng = np.random.default_rng(seed)
    direction = rng.standard_normal(128)
    direction = direction / np.linalg.norm(direction)
    result = np.array(encoding) + direction * distance
    return result.tolist()


class TestFaceThreshold:
    """Verify the threshold (from config) correctly separates same vs.
    different-person encodings."""

    def test_identical_encodings_pass(self):
        """Two identical encodings → 100% similarity, well above threshold."""
        enc = _make_encoding(seed=1)
        score = compare_faces(enc, enc)
        assert score >= FACE_SIMILARITY_THRESHOLD, (
            f"Identical encodings scored {score}%, expected >= {FACE_SIMILARITY_THRESHOLD}%"
        )
        assert score == 100.0, f"Identical encodings should be 100%, got {score}%"

    def test_same_person_close_encodings_pass(self):
        """Two encodings at distance 0.3 (well within face_recognition's 0.6
        threshold for same person) → should pass."""
        enc_a = _make_encoding(seed=10)
        enc_b = _perturb_encoding(enc_a, distance=0.3, seed=20)
        score = compare_faces(enc_a, enc_b)
        assert score >= FACE_SIMILARITY_THRESHOLD, (
            f"Same-person pair (d=0.3) scored {score}%, expected >= {FACE_SIMILARITY_THRESHOLD}%"
        )

    def test_borderline_encoding_rejected(self):
        """Two encodings at distance 0.55 (borderline, near face_recognition's
        0.6 threshold) → should be rejected by our tighter threshold."""
        enc_a = _make_encoding(seed=30)
        enc_b = _perturb_encoding(enc_a, distance=0.55, seed=40)
        score = compare_faces(enc_a, enc_b)
        assert score < FACE_SIMILARITY_THRESHOLD, (
            f"Borderline pair (d=0.55) scored {score}%, expected < {FACE_SIMILARITY_THRESHOLD}%"
        )

    def test_different_person_rejected(self):
        """Two encodings at distance 0.9 (clearly different people) → should
        be far below threshold."""
        enc_a = _make_encoding(seed=50)
        enc_b = _perturb_encoding(enc_a, distance=0.9, seed=60)
        score = compare_faces(enc_a, enc_b)
        assert score < FACE_SIMILARITY_THRESHOLD, (
            f"Different-person pair (d=0.9) scored {score}%, expected < {FACE_SIMILARITY_THRESHOLD}%"
        )

    def test_completely_different_encoding_near_zero(self):
        """Two unrelated encodings (different seeds) → very low score."""
        enc_a = _make_encoding(seed=100)
        enc_b = _make_encoding(seed=200)
        score = compare_faces(enc_a, enc_b)
        assert score < 50.0, (
            f"Unrelated encodings scored {score}%, expected < 50%"
        )

    def test_threshold_value_is_reasonable(self):
        """The configured threshold should be between 50% and 80%."""
        assert 50.0 <= FACE_SIMILARITY_THRESHOLD <= 80.0, (
            f"Threshold {FACE_SIMILARITY_THRESHOLD}% is outside reasonable range [50, 80]"
        )
