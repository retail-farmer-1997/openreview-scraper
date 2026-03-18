"""Unit tests for OpenReview note parsing models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openreview_scraper.models import DiscussionPost, Paper, Review


class ModelParsingTests(unittest.TestCase):
    def test_paper_from_openreview_note_parses_nested_values(self) -> None:
        note = SimpleNamespace(
            id="paper-1",
            content={
                "title": {"value": "Test Paper"},
                "authors": {"value": ["A. Author", "B. Author"]},
                "abstract": {"value": "Abstract body"},
                "venue": {"value": "ICLR 2025 Oral"},
                "venueid": {"value": "ICLR.cc/2025/Conference"},
                "primary_area": {"value": "Optimization"},
                "keywords": {"value": ["diffusion", "rl"]},
            },
        )

        paper = Paper.from_openreview_note(note)
        self.assertEqual(paper.id, "paper-1")
        self.assertEqual(paper.title, "Test Paper")
        self.assertEqual(paper.authors, ["A. Author", "B. Author"])
        self.assertEqual(paper.abstract, "Abstract body")
        self.assertEqual(paper.venue, "ICLR 2025 Oral")
        self.assertEqual(paper.venueid, "ICLR.cc/2025/Conference")
        self.assertEqual(paper.primary_area, "Optimization")
        self.assertEqual(paper.keywords, ["diffusion", "rl"])

    def test_review_from_openreview_note_parses_reviewer_and_sections(self) -> None:
        note = SimpleNamespace(
            id="review-1",
            forum="paper-1",
            signatures=["ICLR.cc/2025/Conference/Paper1/Reviewer_ABC"],
            cdate=1700000000000,
            content={
                "rating": {"value": "8: Strong Accept"},
                "confidence": {"value": "4: High"},
                "summary": {"value": "Solid work."},
                "strengths": {"value": "Clear and novel."},
                "weaknesses": {"value": "Limited ablations."},
            },
        )

        review = Review.from_openreview_note(note)
        self.assertEqual(review.id, "review-1")
        self.assertEqual(review.paper_id, "paper-1")
        self.assertEqual(review.reviewer, "Reviewer ABC")
        self.assertEqual(review.rating, "8: Strong Accept")
        self.assertEqual(review.confidence, "4: High")
        self.assertIn("Summary", review.full_text or "")
        self.assertIsInstance(review.created_at, datetime)

    def test_discussion_post_detects_post_type_and_content(self) -> None:
        note = SimpleNamespace(
            id="post-1",
            forum="paper-1",
            replyto="paper-1",
            signatures=["ICLR.cc/2025/Conference/Paper1/Authors"],
            invitations=["ICLR.cc/2025/Conference/Paper1/-/Author_Rebuttal"],
            cdate=1700000000000,
            content={
                "title": {"value": "Author response"},
                "summary": {"value": "We thank reviewers."},
                "questions": {"value": "No open questions."},
            },
        )

        post = DiscussionPost.from_openreview_note(note)
        self.assertEqual(post.id, "post-1")
        self.assertEqual(post.paper_id, "paper-1")
        self.assertIsNone(post.reply_to)
        self.assertEqual(post.author, "Authors")
        self.assertEqual(post.post_type, "rebuttal")
        self.assertIn("Summary", post.content)
        self.assertEqual(post.title, "Author response")


if __name__ == "__main__":
    unittest.main()
