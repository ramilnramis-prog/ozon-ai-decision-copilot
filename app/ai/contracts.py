"""Vendor-neutral structural contract for future AI brief adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.briefs import AIBriefDraft, GroundedBriefInput


@runtime_checkable
class BriefModelProvider(Protocol):
    """Return an untrusted structured draft from approved grounding only."""

    def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
        """Create prose without changing or calculating authoritative facts."""

