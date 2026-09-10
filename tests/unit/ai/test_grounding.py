"""Direct tests for pure deterministic AI-prose grounding validation."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
import hashlib

import pytest

from app.ai.brief_input import serialize_grounded_brief_input
from app.ai.grounding import BriefGroundingError, validate_brief_draft
from app.core.errors import DataValidationError
from app.domain.briefs import (
    AIBriefDraft,
    AIBriefItem,
    AIBriefSummary,
    GroundedAction,
    GroundedBriefInput,
    GroundedFact,
    GroundedValueType,
)
from app.domain.common import Severity, SkuId, Urgency
from app.domain.recommendations import RecommendationCategory, RecommendationStatus


STAMP = datetime(2026, 9, 7, 9, tzinfo=UTC)
SKU = SkuId("SKU-1")


def fact(
    fact_id: str = "fact:a",
    value: str | int | bool = "1233.00",
    value_type: GroundedValueType = GroundedValueType.DECIMAL,
) -> GroundedFact:
    return GroundedFact(
        fact_id=fact_id,
        sku=SKU,
        name="authoritative value",
        value=value,
        value_type=value_type,
        unit="unit",
        period=None,
        formula_or_rule_id="test.rule",
        source_refs=("source:test",),
    )


def action(
    action_ref: str = "recommendation:SKU-1:a",
    rank: int = 1,
    fact_refs: tuple[str, ...] = ("fact:a",),
) -> GroundedAction:
    return GroundedAction(
        action_ref=action_ref,
        recommendation_id=action_ref,
        sku=SKU,
        product_name="Test product",
        rule_id="test.rule",
        category=RecommendationCategory.INVENTORY,
        status=RecommendationStatus.PROPOSED,
        rank=rank,
        severity=Severity.HIGH,
        urgency=Urgency.SOON,
        fact_refs=fact_refs,
    )


def single_input(
    value: str | int | bool = "1233.00",
    value_type: GroundedValueType = GroundedValueType.DECIMAL,
) -> GroundedBriefInput:
    source_fact = fact(value=value, value_type=value_type)
    return GroundedBriefInput(STAMP, (action(),), (source_fact,))


def two_action_input(*, shared: bool = False) -> GroundedBriefInput:
    first = fact("fact:a", "1233.00")
    if shared:
        actions = (
            action("recommendation:SKU-1:a", 1, (first.fact_id,)),
            action("recommendation:SKU-1:b", 2, (first.fact_id,)),
        )
        return GroundedBriefInput(STAMP, actions, (first,))
    second = fact("fact:b", "500.00")
    actions = (
        action("recommendation:SKU-1:a", 1, (first.fact_id,)),
        action("recommendation:SKU-1:b", 2, (second.fact_id,)),
    )
    return GroundedBriefInput(STAMP, actions, (first, second))


def draft_for(
    grounded: GroundedBriefInput,
    *,
    item_text: str = "Требуется внимание.",
    item_fact_refs: tuple[str, ...] = (),
    summary_text: str = "Краткая сводка.",
    summary_action_refs: tuple[str, ...] = (),
    summary_fact_refs: tuple[str, ...] = (),
) -> AIBriefDraft:
    return AIBriefDraft(
        AIBriefSummary(summary_text, summary_action_refs, summary_fact_refs),
        tuple(
            AIBriefItem(source.action_ref, item_text, item_fact_refs)
            for source in grounded.actions
        ),
    )


def assert_invalid(
    grounded: GroundedBriefInput,
    draft: AIBriefDraft,
    code: str,
) -> None:
    with pytest.raises(BriefGroundingError) as error:
        validate_brief_draft(grounded, draft)
    assert error.value.code == code


def test_valid_draft_preserves_exact_text_references_order_and_timestamp() -> None:
    grounded = single_input()
    source = draft_for(
        grounded,
        item_text="Значение 1233.00 требует внимания.",
        item_fact_refs=("fact:a",),
        summary_text="Сводка по значению 1233.00.",
        summary_action_refs=(grounded.actions[0].action_ref,),
        summary_fact_refs=("fact:a",),
    )

    result = validate_brief_draft(grounded, source)

    assert result.analysis_timestamp is grounded.analysis_timestamp
    assert result.summary is source.summary
    assert result.items is source.items


def test_missing_and_extra_action_items_are_rejected() -> None:
    grounded = single_input()
    missing = AIBriefDraft(AIBriefSummary("", (), ()), ())
    extra = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (
            AIBriefItem(grounded.actions[0].action_ref, "", ()),
            AIBriefItem("recommendation:SKU-1:extra", "", ()),
        ),
    )

    assert_invalid(grounded, missing, "brief_grounding.missing_action")
    assert_invalid(grounded, extra, "brief_grounding.extra_action")


def test_duplicate_draft_action_is_rejected_by_structural_contract() -> None:
    item = AIBriefItem("recommendation:SKU-1:a", "", ())
    with pytest.raises(DataValidationError) as error:
        AIBriefDraft(AIBriefSummary("", (), ()), (item, item))
    assert error.value.code == "briefs.duplicate_draft_action_ref"


def test_reordered_and_unknown_action_references_are_rejected() -> None:
    grounded = two_action_input()
    reordered = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (
            AIBriefItem(grounded.actions[1].action_ref, "", ()),
            AIBriefItem(grounded.actions[0].action_ref, "", ()),
        ),
    )
    unknown = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (
            AIBriefItem("recommendation:SKU-1:unknown", "", ()),
            AIBriefItem(grounded.actions[1].action_ref, "", ()),
        ),
    )

    assert_invalid(grounded, reordered, "brief_grounding.action_order")
    assert_invalid(grounded, unknown, "brief_grounding.unknown_action_ref")


def test_unknown_and_cross_action_fact_references_are_rejected() -> None:
    grounded = two_action_input()
    unknown = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (
            AIBriefItem(grounded.actions[0].action_ref, "", ("fact:unknown",)),
            AIBriefItem(grounded.actions[1].action_ref, "", ()),
        ),
    )
    borrowed = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (
            AIBriefItem(grounded.actions[0].action_ref, "500.00", ("fact:b",)),
            AIBriefItem(grounded.actions[1].action_ref, "", ()),
        ),
    )

    assert_invalid(grounded, unknown, "brief_grounding.unknown_fact_ref")
    assert_invalid(grounded, borrowed, "brief_grounding.cross_action_fact_ref")


def test_shared_authoritative_fact_is_valid_for_both_action_items() -> None:
    grounded = two_action_input(shared=True)
    source = AIBriefDraft(
        AIBriefSummary("", (), ()),
        tuple(
            AIBriefItem(item.action_ref, "Значение 1233.00.", ("fact:a",))
            for item in grounded.actions
        ),
    )

    assert validate_brief_draft(grounded, source).items == source.items


def test_unknown_summary_action_and_fact_references_are_rejected() -> None:
    grounded = single_input()
    unknown_action = draft_for(
        grounded,
        summary_action_refs=("recommendation:unknown",),
    )
    unknown_fact = draft_for(grounded, summary_fact_refs=("fact:unknown",))

    assert_invalid(
        grounded,
        unknown_action,
        "brief_grounding.unknown_summary_action_ref",
    )
    assert_invalid(
        grounded,
        unknown_fact,
        "brief_grounding.unknown_summary_fact_ref",
    )


@pytest.mark.parametrize(
    ("canonical", "claim", "valid"),
    [
        ("1233.00", "1233.00", True),
        ("1233.00", "1233", False),
        ("1233.00", "1233.0", False),
        ("1233.00", "1234.00", False),
        ("1233.00", "1,233.00", False),
        ("1233.00", "1 233,00", False),
        ("1233.00", "1233,00", False),
        ("1233.00", "+1233.00", False),
        ("-649.70", "-649.70", True),
        ("-649.70", "-649.7", False),
        ("-649.70", "649.70", False),
        ("0.20", "0.20", True),
        ("0.20", "20%", False),
        ("0.20", "0.2", False),
        ("1000", "1000", True),
        ("1000", "1e3", False),
        ("1000", "1E3", False),
        ("0.00012", "1.2E-4", False),
    ],
)
def test_numeric_claims_require_exact_canonical_text(
    canonical: str,
    claim: str,
    valid: bool,
) -> None:
    value_type = GroundedValueType.INTEGER if canonical == "1000" else GroundedValueType.DECIMAL
    value: str | int = 1000 if value_type is GroundedValueType.INTEGER else canonical
    grounded = single_input(value, value_type)
    source = draft_for(grounded, item_text=claim, item_fact_refs=("fact:a",))

    if valid:
        assert validate_brief_draft(grounded, source).items == source.items
    else:
        assert_invalid(
            grounded,
            source,
            "brief_grounding.unsupported_numeric_claim",
        )


def test_identifier_digits_are_ignored_but_standalone_number_is_a_claim() -> None:
    grounded = single_input()
    identifiers = draft_for(
        grounded,
        item_text="DEMO-015 SKU123 S24 Model_X5",
        item_fact_refs=(),
    )
    standalone = draft_for(
        grounded,
        item_text="Model 15",
        item_fact_refs=(),
    )

    assert validate_brief_draft(grounded, identifiers).items == identifiers.items
    assert_invalid(
        grounded,
        standalone,
        "brief_grounding.unsupported_numeric_claim",
    )


@pytest.mark.parametrize("claim", ["١٢٣٣", "１２３３", "Значение ١.٢٣"])
def test_non_ascii_decimal_digits_are_always_rejected(claim: str) -> None:
    grounded = single_input()
    source = draft_for(grounded, item_text=claim, item_fact_refs=("fact:a",))

    assert_invalid(grounded, source, "brief_grounding.unicode_digit_claim")


@pytest.mark.parametrize(
    ("claim", "valid", "code"),
    [
        ("2026-09-11", True, ""),
        ("11.09.2026", False, "brief_grounding.unsupported_date_claim"),
        ("11/09/2026", False, "brief_grounding.unsupported_date_claim"),
        ("2026/09/11", False, "brief_grounding.unsupported_date_claim"),
    ],
)
def test_date_claims_require_exact_iso_date(
    claim: str,
    valid: bool,
    code: str,
) -> None:
    grounded = single_input("2026-09-11", GroundedValueType.DATE)
    source = draft_for(grounded, item_text=claim, item_fact_refs=("fact:a",))

    if valid:
        assert validate_brief_draft(grounded, source).items == source.items
    else:
        assert_invalid(grounded, source, code)


@pytest.mark.parametrize(
    ("claim", "valid", "code"),
    [
        ("2026-09-11T15:30:00+03:00", True, ""),
        (
            "2026-09-11T12:30:00+00:00",
            False,
            "brief_grounding.unsupported_datetime_claim",
        ),
        (
            "2026-09-11T15:30:00",
            False,
            "brief_grounding.unsupported_datetime_claim",
        ),
        ("2026-09-11", False, "brief_grounding.unsupported_date_claim"),
        ("15:30", False, "brief_grounding.unsupported_time_claim"),
    ],
)
def test_datetime_claims_are_exact_and_cannot_launder_substrings(
    claim: str,
    valid: bool,
    code: str,
) -> None:
    grounded = single_input(
        "2026-09-11T15:30:00+03:00",
        GroundedValueType.DATETIME,
    )
    source = draft_for(grounded, item_text=claim, item_fact_refs=("fact:a",))

    if valid:
        assert validate_brief_draft(grounded, source).items == source.items
    else:
        assert_invalid(grounded, source, code)


@pytest.mark.parametrize(
    "claim",
    [
        "сегодня",
        "ЗАВТРА",
        "через 4 дня",
        "на следующей неделе",
        "следующую пятницу",
        "today",
        "TOMORROW",
        "in 4 days",
        "next week",
        "next Friday",
    ],
)
def test_relative_temporal_claims_are_rejected_without_resolution(claim: str) -> None:
    grounded = single_input("2026-09-11", GroundedValueType.DATE)
    source = draft_for(grounded, item_text=claim, item_fact_refs=("fact:a",))

    assert_invalid(grounded, source, "brief_grounding.relative_time_claim")


def test_item_and_summary_claims_cannot_launder_global_facts() -> None:
    first = fact("fact:a", "1233.00")
    second = fact("fact:b", "500.00")
    grounded = GroundedBriefInput(
        STAMP,
        (action(fact_refs=(first.fact_id, second.fact_id)),),
        (first, second),
    )
    item_laundering = draft_for(
        grounded,
        item_text="500.00",
        item_fact_refs=("fact:a",),
    )
    summary_laundering = draft_for(
        grounded,
        summary_text="500.00",
        summary_fact_refs=("fact:a",),
    )

    assert_invalid(
        grounded,
        item_laundering,
        "brief_grounding.unsupported_numeric_claim",
    )
    assert_invalid(
        grounded,
        summary_laundering,
        "brief_grounding.unsupported_numeric_claim",
    )


def test_empty_grounding_accepts_only_zero_items_and_claim_safe_summary() -> None:
    grounded = GroundedBriefInput(STAMP, (), ())
    valid = AIBriefDraft(AIBriefSummary("Нет приоритетных действий.", (), ()), ())
    invented = AIBriefDraft(
        AIBriefSummary("", (), ()),
        (AIBriefItem("recommendation:invented", "", ()),),
    )

    assert validate_brief_draft(grounded, valid).items == ()
    assert_invalid(grounded, invented, "brief_grounding.extra_action")


def test_digest_is_exact_deterministic_and_changes_with_canonical_input() -> None:
    grounded = single_input()
    source = draft_for(grounded)
    expected = hashlib.sha256(
        serialize_grounded_brief_input(grounded).encode("utf-8")
    ).hexdigest()

    first = validate_brief_draft(grounded, source)
    repeated = validate_brief_draft(grounded, source)
    changed_fact = replace(grounded.facts[0], value="1234.00")
    changed_input = GroundedBriefInput(STAMP, grounded.actions, (changed_fact,))
    changed = validate_brief_draft(changed_input, source)

    assert first.grounding_digest == expected
    assert repeated == first
    assert changed.grounding_digest != first.grounding_digest


def test_validation_does_not_mutate_inputs_or_draft() -> None:
    grounded = single_input()
    source = draft_for(grounded)
    grounded_before = grounded
    source_before = source

    validate_brief_draft(grounded, source)

    assert grounded == grounded_before
    assert source == source_before
    with pytest.raises(FrozenInstanceError):
        source.items = ()

