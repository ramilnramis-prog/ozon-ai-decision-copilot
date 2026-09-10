"""Small orchestration boundary for optional AI Daily Brief generation."""

from __future__ import annotations

from app.ai.contracts import BriefModelProvider
from app.ai.grounding import BriefGroundingError, validate_brief_draft
from app.core.errors import AIInvalidOutputError, AIUnavailableError, ConfigurationError
from app.domain.briefs import (
    BriefGenerationResult,
    BriefGenerationStatus,
    GroundedBriefInput,
)
from app.domain.common import require_instance


def generate_daily_brief(
    grounded_input: GroundedBriefInput,
    provider: BriefModelProvider | None,
    *,
    enabled: bool = True,
) -> BriefGenerationResult:
    """Generate at most once and map only approved AI boundary failures."""

    grounded_input = require_instance(
        grounded_input,
        GroundedBriefInput,
        field_name="grounded brief input",
    )
    if not isinstance(enabled, bool):
        raise ConfigurationError(
            "AI enabled flag must be boolean.",
            code="configuration.invalid_ai_enabled",
            scope="ai_enabled",
        )
    if not enabled:
        return BriefGenerationResult(BriefGenerationStatus.DISABLED, None)
    if provider is None:
        raise ConfigurationError(
            "Enabled AI generation requires a configured provider.",
            code="configuration.missing_ai_provider",
            scope="AI provider",
        )

    try:
        draft = provider.create_draft(grounded_input)
    except AIUnavailableError:
        return BriefGenerationResult(BriefGenerationStatus.UNAVAILABLE, None)
    except AIInvalidOutputError:
        return BriefGenerationResult(BriefGenerationStatus.INVALID, None)

    try:
        brief = validate_brief_draft(grounded_input, draft)
    except BriefGroundingError:
        return BriefGenerationResult(BriefGenerationStatus.INVALID, None)
    return BriefGenerationResult(BriefGenerationStatus.GENERATED, brief)


__all__ = ("generate_daily_brief",)
