"""Pure deterministic validation of untrusted AI Daily Brief drafts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from app.ai.brief_input import serialize_grounded_brief_input
from app.core.errors import AIInvalidOutputError
from app.domain.briefs import (
    AIBriefDraft,
    DailyBrief,
    GroundedBriefInput,
    GroundedFact,
    GroundedValueType,
)
from app.domain.common import require_instance


class BriefGroundingError(AIInvalidOutputError):
    """An untrusted draft violates one deterministic grounding rule."""

    default_code = "brief_grounding.invalid"


def grounded_brief_input_digest(grounded_input: GroundedBriefInput) -> str:
    """Return the canonical SHA-256 identity for one grounded brief input."""

    grounded_input = require_instance(
        grounded_input,
        GroundedBriefInput,
        field_name="grounded brief input",
    )
    serialized = serialize_grounded_brief_input(grounded_input)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?=[A-Za-z0-9_-]*[A-Za-z])"
    r"(?=[A-Za-z0-9_-]*[0-9])"
    r"[A-Za-z0-9_-]+"
    r"(?![A-Za-z0-9_-])"
)
_SCIENTIFIC_IDENTIFIER_PATTERN = re.compile(
    r"[+-]?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+%?"
)
_DATETIME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?"
    r"(?![A-Za-z0-9_])"
)
_ISO_DATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![A-Za-z0-9_])"
)
_REFORMATTED_DATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[0-9]{1,4}[./][0-9]{1,2}[./][0-9]{1,4}"
    r"(?![A-Za-z0-9_])"
)
_TIME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?"
    r"(?![A-Za-z0-9_])"
)
_NUMERIC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_+.,%-])"
    r"[+-]?"
    r"(?:"
    r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?"
    r"|[0-9]{1,3}(?: [0-9]{3})+(?:[.,][0-9]+)?"
    r"|[0-9]+(?:[.,][0-9]+)?(?:[eE][+-]?[0-9]+)?"
    r")"
    r"%?"
    r"(?![A-Za-z0-9_%]|[.,][0-9])"
)

_ENGLISH_RELATIVE_PATTERNS = (
    r"today",
    r"tomorrow",
    r"yesterday",
    r"tonight",
    r"next\s+(?:week|month|year)",
    r"last\s+(?:week|month|year)",
    r"in\s+[0-9]+\s+(?:day|days|week|weeks|month|months|year|years)",
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
)
_RUSSIAN_RELATIVE_PATTERNS = (
    r"сегодня",
    r"завтра",
    r"вчера",
    r"послезавтра",
    r"позавчера",
    r"на\s+следующей\s+неделе",
    r"в\s+следующем\s+месяце",
    r"в\s+следующем\s+году",
    r"через\s+[0-9]+\s+(?:день|дня|дней)",
    r"через\s+[0-9]+\s+(?:неделю|недели|недель)",
    r"через\s+[0-9]+\s+(?:месяц|месяца|месяцев)",
    r"через\s+[0-9]+\s+(?:год|года|лет)",
    r"следующий\s+понедельник",
    r"следующий\s+вторник",
    r"следующую\s+среду",
    r"следующий\s+четверг",
    r"следующую\s+пятницу",
    r"следующую\s+субботу",
    r"следующее\s+воскресенье",
)
_RELATIVE_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join((*_ENGLISH_RELATIVE_PATTERNS, *_RUSSIAN_RELATIVE_PATTERNS))
    + r")(?!\w)",
    re.IGNORECASE,
)


def _grounding_error(message: str, *, code: str, scope: str) -> BriefGroundingError:
    return BriefGroundingError(message, code=code, scope=scope)


def _mask(buffer: list[str], span: tuple[int, int]) -> None:
    for index in range(*span):
        buffer[index] = " "


def _overlaps(span: tuple[int, int], others: Iterable[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in others)


def _canonical_claim_sets(
    facts: Iterable[GroundedFact],
) -> tuple[set[str], set[str], set[str]]:
    numeric: set[str] = set()
    dates: set[str] = set()
    datetimes: set[str] = set()
    for fact in facts:
        if fact.value_type is GroundedValueType.DECIMAL:
            numeric.add(fact.value)  # type: ignore[arg-type]
        elif fact.value_type is GroundedValueType.INTEGER:
            numeric.add(str(fact.value))
        elif fact.value_type is GroundedValueType.DATE:
            dates.add(fact.value)  # type: ignore[arg-type]
        elif fact.value_type is GroundedValueType.DATETIME:
            datetimes.add(fact.value)  # type: ignore[arg-type]
    return numeric, dates, datetimes


def _validate_text_claims(
    text: str,
    facts: Iterable[GroundedFact],
    *,
    scope: str,
) -> None:
    if any(character.isdecimal() and character not in "0123456789" for character in text):
        raise _grounding_error(
            "AI prose contains a non-ASCII decimal digit.",
            code="brief_grounding.unicode_digit_claim",
            scope=scope,
        )

    numeric, dates, datetimes = _canonical_claim_sets(facts)
    buffer = list(text)
    temporal_candidates = tuple(
        match.span()
        for pattern in (
            _DATETIME_PATTERN,
            _ISO_DATE_PATTERN,
            _REFORMATTED_DATE_PATTERN,
            _TIME_PATTERN,
        )
        for match in pattern.finditer(text)
    )

    for match in _IDENTIFIER_PATTERN.finditer(text):
        if _SCIENTIFIC_IDENTIFIER_PATTERN.fullmatch(match.group()) is not None:
            continue
        if not _overlaps(match.span(), temporal_candidates):
            _mask(buffer, match.span())

    visible = "".join(buffer)
    for match in _DATETIME_PATTERN.finditer(visible):
        if match.group() not in datetimes:
            raise _grounding_error(
                "AI prose contains an unsupported datetime claim.",
                code="brief_grounding.unsupported_datetime_claim",
                scope=scope,
            )
        _mask(buffer, match.span())

    visible = "".join(buffer)
    for match in _ISO_DATE_PATTERN.finditer(visible):
        if match.group() not in dates:
            raise _grounding_error(
                "AI prose contains an unsupported date claim.",
                code="brief_grounding.unsupported_date_claim",
                scope=scope,
            )
        _mask(buffer, match.span())

    visible = "".join(buffer)
    for match in _REFORMATTED_DATE_PATTERN.finditer(visible):
        if match.group() not in dates:
            raise _grounding_error(
                "AI prose contains an unsupported reformatted date claim.",
                code="brief_grounding.unsupported_date_claim",
                scope=scope,
            )
        _mask(buffer, match.span())

    visible = "".join(buffer)
    for match in _TIME_PATTERN.finditer(visible):
        raise _grounding_error(
            "AI prose contains an unsupported standalone time claim.",
            code="brief_grounding.unsupported_time_claim",
            scope=scope,
        )

    if _RELATIVE_PATTERN.search(text) is not None:
        raise _grounding_error(
            "AI prose contains a relative date or time claim.",
            code="brief_grounding.relative_time_claim",
            scope=scope,
        )

    visible = "".join(buffer)
    for match in _NUMERIC_PATTERN.finditer(visible):
        if match.group() not in numeric:
            raise _grounding_error(
                "AI prose contains an unsupported numeric claim.",
                code="brief_grounding.unsupported_numeric_claim",
                scope=scope,
            )
        _mask(buffer, match.span())

    if re.search(r"[0-9]", "".join(buffer)) is not None:
        raise _grounding_error(
            "AI prose contains an unsupported numeric claim.",
            code="brief_grounding.unsupported_numeric_claim",
            scope=scope,
        )


def validate_brief_draft(
    grounded_input: GroundedBriefInput,
    draft: AIBriefDraft,
) -> DailyBrief:
    """Validate one untrusted draft and return its immutable grounded form."""

    grounded_input = require_instance(
        grounded_input,
        GroundedBriefInput,
        field_name="grounded brief input",
    )
    if not isinstance(draft, AIBriefDraft):
        raise _grounding_error(
            "AI provider returned an invalid draft type.",
            code="brief_grounding.invalid_draft_type",
            scope="draft",
        )

    expected_actions = grounded_input.actions
    if len(draft.items) < len(expected_actions):
        raise _grounding_error(
            "AI draft is missing an action item.",
            code="brief_grounding.missing_action",
            scope="items",
        )
    if len(draft.items) > len(expected_actions):
        raise _grounding_error(
            "AI draft contains an extra action item.",
            code="brief_grounding.extra_action",
            scope="items",
        )

    facts_by_id = {fact.fact_id: fact for fact in grounded_input.facts}
    action_refs = {action.action_ref for action in expected_actions}
    for index, (action, item) in enumerate(zip(expected_actions, draft.items, strict=True)):
        if item.action_ref not in action_refs:
            raise _grounding_error(
                "AI draft references an unknown action.",
                code="brief_grounding.unknown_action_ref",
                scope=f"items[{index}]",
            )
        if item.action_ref != action.action_ref:
            raise _grounding_error(
                "AI draft action order differs from authoritative priority order.",
                code="brief_grounding.action_order",
                scope=f"items[{index}]",
            )
        allowed_refs = set(action.fact_refs)
        for fact_ref in item.fact_refs:
            if fact_ref not in facts_by_id:
                raise _grounding_error(
                    "AI draft references an unknown fact.",
                    code="brief_grounding.unknown_fact_ref",
                    scope=f"items[{index}]",
                )
            if fact_ref not in allowed_refs:
                raise _grounding_error(
                    "AI draft item borrows evidence from another action.",
                    code="brief_grounding.cross_action_fact_ref",
                    scope=f"items[{index}]",
                )
        _validate_text_claims(
            item.text,
            (facts_by_id[fact_ref] for fact_ref in item.fact_refs),
            scope=f"items[{index}].text",
        )

    for action_ref in draft.summary.action_refs:
        if action_ref not in action_refs:
            raise _grounding_error(
                "AI summary references an unknown action.",
                code="brief_grounding.unknown_summary_action_ref",
                scope="summary",
            )
    for fact_ref in draft.summary.fact_refs:
        if fact_ref not in facts_by_id:
            raise _grounding_error(
                "AI summary references an unknown fact.",
                code="brief_grounding.unknown_summary_fact_ref",
                scope="summary",
            )
    _validate_text_claims(
        draft.summary.text,
        (facts_by_id[fact_ref] for fact_ref in draft.summary.fact_refs),
        scope="summary.text",
    )

    return DailyBrief(
        analysis_timestamp=grounded_input.analysis_timestamp,
        grounding_digest=grounded_brief_input_digest(grounded_input),
        summary=draft.summary,
        items=draft.items,
    )


__all__ = (
    "BriefGroundingError",
    "grounded_brief_input_digest",
    "validate_brief_draft",
)
