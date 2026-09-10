"""Application service for optional grounded Daily Brief generation."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.brief_generator import generate_daily_brief
from app.ai.brief_input import build_grounded_brief_input
from app.ai.contracts import BriefModelProvider
from app.ai.grounding import grounded_brief_input_digest
from app.core.errors import ConfigurationError
from app.domain.briefs import BriefGenerationResult
from app.services.models import AnalysisSnapshot


@dataclass(frozen=True, slots=True, repr=False)
class BriefService:
    """Build approved grounding and invoke one configured model adapter."""

    provider: BriefModelProvider | None = None
    enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ConfigurationError(
                "AI enabled flag must be boolean.",
                code="configuration.invalid_ai_enabled",
                scope="ai_enabled",
            )
        if self.enabled and self.provider is None:
            raise ConfigurationError(
                "Enabled AI generation requires a configured provider.",
                code="configuration.missing_ai_provider",
                scope="AI provider",
            )

    def generate(self, snapshot: AnalysisSnapshot) -> BriefGenerationResult:
        grounded_input = build_grounded_brief_input(snapshot)
        return generate_daily_brief(
            grounded_input,
            self.provider,
            enabled=self.enabled,
        )

    def grounding_digest(self, snapshot: AnalysisSnapshot) -> str:
        """Identify current grounding without invoking the configured model provider."""

        grounded_input = build_grounded_brief_input(snapshot)
        return grounded_brief_input_digest(grounded_input)


__all__ = ("BriefService",)
