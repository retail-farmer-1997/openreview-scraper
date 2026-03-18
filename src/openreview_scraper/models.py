"""Data models for paper management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Review:
    """Represents a review on a paper."""

    id: str
    paper_id: str  # forum ID
    reviewer: str  # usually anonymized like "Reviewer 1"
    rating: str | None = None
    confidence: str | None = None
    summary: str | None = None
    strengths: str | None = None
    weaknesses: str | None = None
    questions: str | None = None
    limitations: str | None = None
    soundness: str | None = None
    presentation: str | None = None
    contribution: str | None = None
    recommendation: str | None = None
    full_text: str | None = None  # raw content for display
    created_at: datetime | None = None

    @classmethod
    def from_openreview_note(cls, note) -> "Review":
        """Create a Review from an OpenReview note object."""
        content = note.content

        def get_value(f):
            val = content.get(f, {})
            if isinstance(val, dict):
                return val.get("value")
            return val

        # Try to extract reviewer identity from signatures
        reviewer = "Anonymous"
        if note.signatures:
            sig = note.signatures[0]
            # Extract reviewer number from signature like ".../Reviewer_ABC"
            if "Reviewer" in sig:
                parts = sig.split("/")
                for p in parts:
                    if "Reviewer" in p:
                        reviewer = p.replace("_", " ")
                        break

        # Build full text representation
        full_parts = []
        for field_name in ["summary", "strengths", "weaknesses", "questions", "limitations"]:
            val = get_value(field_name)
            if val:
                full_parts.append(f"**{field_name.title()}**:\\n{val}")

        return cls(
            id=note.id,
            paper_id=note.forum,
            reviewer=reviewer,
            rating=get_value("rating"),
            confidence=get_value("confidence"),
            summary=get_value("summary"),
            strengths=get_value("strengths"),
            weaknesses=get_value("weaknesses"),
            questions=get_value("questions"),
            limitations=get_value("limitations"),
            soundness=get_value("soundness"),
            presentation=get_value("presentation"),
            contribution=get_value("contribution"),
            recommendation=get_value("recommendation"),
            full_text="\\n\\n".join(full_parts) if full_parts else None,
            created_at=datetime.fromtimestamp(note.cdate / 1000) if note.cdate else None,
        )


@dataclass
class DiscussionPost:
    """Represents a single post in a paper discussion thread."""

    id: str
    paper_id: str  # forum ID
    reply_to: str | None  # ID of parent post (None if top-level)
    author: str  # who posted (may be anonymized)
    content: str
    post_type: str  # "review", "comment", "rebuttal", "meta_review", "decision"
    created_at: datetime | None = None
    title: str | None = None

    @classmethod
    def from_openreview_note(cls, note) -> "DiscussionPost":
        """Create a DiscussionPost from an OpenReview note object."""
        content = note.content

        def get_value(f):
            val = content.get(f, {})
            if isinstance(val, dict):
                return val.get("value")
            return val

        # Determine post type from invitation
        invitations = note.invitations or []
        invitation = invitations[0] if invitations else ""
        post_type = "comment"
        if "Review" in invitation:
            post_type = "review"
        elif "Rebuttal" in invitation or "Author" in invitation:
            post_type = "rebuttal"
        elif "Meta" in invitation:
            post_type = "meta_review"
        elif "Decision" in invitation:
            post_type = "decision"
        elif "Comment" in invitation:
            post_type = "comment"

        # Extract author from signatures
        author = "Anonymous"
        if note.signatures:
            sig = note.signatures[0]
            if "Author" in sig:
                author = "Authors"
            elif "Reviewer" in sig:
                parts = sig.split("/")
                for p in parts:
                    if "Reviewer" in p:
                        author = p.replace("_", " ")
                        break
            elif "Area_Chair" in sig or "AC" in sig:
                author = "Area Chair"
            elif "Program_Chair" in sig or "PC" in sig:
                author = "Program Chair"

        # Build content from various possible fields
        content_text = get_value("comment") or get_value("review") or get_value("content") or ""
        if not content_text:
            # Try to build from structured fields
            parts = []
            for f in ["summary", "strengths", "weaknesses", "questions", "decision"]:
                val = get_value(f)
                if val:
                    parts.append(f"**{f.title()}**: {val}")
            content_text = "\\n\\n".join(parts)

        return cls(
            id=note.id,
            paper_id=note.forum,
            reply_to=note.replyto if note.replyto != note.forum else None,
            author=author,
            content=content_text,
            post_type=post_type,
            created_at=datetime.fromtimestamp(note.cdate / 1000) if note.cdate else None,
            title=get_value("title"),
        )


@dataclass
class PaperDiscussion:
    """Full discussion thread for a paper with metadata."""

    paper_id: str
    paper_title: str
    posts: list[DiscussionPost] = field(default_factory=list)
    review_count: int = 0
    comment_count: int = 0
    has_author_response: bool = False
    has_decision: bool = False

    def get_reviews(self) -> list[DiscussionPost]:
        """Get only review posts."""
        return [p for p in self.posts if p.post_type == "review"]

    def get_rebuttals(self) -> list[DiscussionPost]:
        """Get only author rebuttals."""
        return [p for p in self.posts if p.post_type == "rebuttal"]

    def get_thread(self, post_id: str) -> list[DiscussionPost]:
        """Get a discussion thread starting from a specific post."""
        thread = []
        current_id = post_id
        id_to_post = {p.id: p for p in self.posts}

        # Walk up the tree to root
        while current_id and current_id in id_to_post:
            thread.insert(0, id_to_post[current_id])
            current_id = id_to_post[current_id].reply_to

        # Now get all descendants
        def get_descendants(pid):
            children = [p for p in self.posts if p.reply_to == pid]
            result = []
            for c in children:
                result.append(c)
                result.extend(get_descendants(c.id))
            return result

        thread.extend(get_descendants(post_id))
        return thread


@dataclass
class Paper:
    """Represents an OpenReview paper."""

    id: str
    title: str
    authors: list[str]
    abstract: str
    venue: str
    venueid: str
    primary_area: str | None = None
    keywords: list[str] | None = None
    pdf_path: str | None = None

    @classmethod
    def from_openreview_note(cls, note) -> "Paper":
        """Create a Paper from an OpenReview note object."""
        content = note.content

        def get_value(field):
            """Extract value from OpenReview content field."""
            val = content.get(field, {})
            if isinstance(val, dict):
                return val.get("value")
            return val

        return cls(
            id=note.id,
            title=get_value("title") or "Unknown",
            authors=get_value("authors") or [],
            abstract=get_value("abstract") or "",
            venue=get_value("venue") or "",
            venueid=get_value("venueid") or "",
            primary_area=get_value("primary_area"),
            keywords=get_value("keywords"),
        )
