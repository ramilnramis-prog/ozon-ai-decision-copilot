"""Structural tests for immutable grounded-input and AI-draft contracts."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, date, datetime

import pytest

from app.ai.contracts import BriefModelProvider
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
from app.domain.common import DateRange, Severity, SkuId, Urgency
from app.domain.recommendations import RecommendationCategory, RecommendationStatus


STAMP = datetime(2026, 9, 4, 9, tzinfo=UTC)


def fact(**changes) -> GroundedFact:
    values = {
        "fact_id": "fact:SKU-1:test:value",
        "sku": SkuId("SKU-1"),
        "name": "authoritative value",
        "value": "1233.00",
        "value_type": GroundedValueType.DECIMAL,
        "unit": "RUB",
        "period": DateRange(date(2026, 9, 1), date(2026, 9, 4)),
        "formula_or_rule_id": "test.rule",
        "source_refs": ("source:record:1", "policy:test:1"),
    }
    values.update(changes)
    return GroundedFact(**values)


def action(**changes) -> GroundedAction:
    values = {
        "action_ref": "recommendation:SKU-1:test.rule",
        "recommendation_id": "recommendation:SKU-1:test.rule",
        "sku": SkuId("SKU-1"),
        "product_name": "Test product",
        "rule_id": "test.rule",
        "category": RecommendationCategory.PROFITABILITY,
        "status": RecommendationStatus.PROPOSED,
        "rank": 1,
        "severity": Severity.HIGH,
        "urgency": Urgency.SOON,
        "fact_refs": ("fact:SKU-1:test:value",),
    }
    values.update(changes)
    return GroundedAction(**values)


def test_grounded_models_are_frozen_and_normalize_collections_to_tuples() -> None:
    source_fact = fact(source_refs=["source:record:1", "policy:test:1"])
    source_action = action(fact_refs=[source_fact.fact_id])
    grounded = GroundedBriefInput(STAMP, [source_action], [source_fact])

    assert grounded.actions == (source_action,)
    assert grounded.facts == (source_fact,)
    assert source_fact.source_refs == ("source:record:1", "policy:test:1")
    with pytest.raises(FrozenInstanceError):
        grounded.actions = ()
    with pytest.raises(FrozenInstanceError):
        source_action.rank = 2


def test_action_identity_blank_refs_and_duplicate_refs_fail_structurally() -> None:
    with pytest.raises(DataValidationError) as mismatch:
        action(action_ref="other")
    assert mismatch.value.code == "briefs.action_identity_mismatch"

    with pytest.raises(DataValidationError):
        action(fact_refs=(" ",))
    with pytest.raises(DataValidationError) as duplicate:
        action(fact_refs=("fact:SKU-1:test:value", "fact:SKU-1:test:value"))
    assert duplicate.value.code == "domain.duplicate_value"

    with pytest.raises(DataValidationError) as unavailable:
        action(status=RecommendationStatus.UNAVAILABLE)
    assert unavailable.value.code == "briefs.invalid_action_status"


@pytest.mark.parametrize(
    ("value", "value_type"),
    [
        ("1233.00", GroundedValueType.DECIMAL),
        ("-649.70", GroundedValueType.DECIMAL),
        (42, GroundedValueType.INTEGER),
        (True, GroundedValueType.BOOLEAN),
        ("insufficient_data", GroundedValueType.TEXT),
        ("2026-09-04", GroundedValueType.DATE),
        ("2026-09-04T09:00:00+00:00", GroundedValueType.DATETIME),
    ],
)
def test_grounded_fact_accepts_each_canonical_value_type(value, value_type) -> None:
    assert fact(value=value, value_type=value_type).value == value


@pytest.mark.parametrize(
    ("value", "value_type"),
    [
        ("01.00", GroundedValueType.DECIMAL),
        ("1E+3", GroundedValueType.DECIMAL),
        ("NaN", GroundedValueType.DECIMAL),
        (True, GroundedValueType.INTEGER),
        (1, GroundedValueType.BOOLEAN),
        ("", GroundedValueType.TEXT),
        ("04-09-2026", GroundedValueType.DATE),
        ("2026-09-04T09:00:00", GroundedValueType.DATETIME),
    ],
)
def test_grounded_fact_rejects_noncanonical_or_mismatched_values(value, value_type) -> None:
    with pytest.raises(DataValidationError) as error:
        fact(value=value, value_type=value_type)
    assert error.value.code == "briefs.invalid_grounded_value"


def test_grounded_input_rejects_duplicate_ids_unknown_refs_order_and_cross_sku() -> None:
    source_fact = fact()
    source_action = action()

    with pytest.raises(DataValidationError) as duplicate_fact:
        GroundedBriefInput(STAMP, (source_action,), (source_fact, source_fact))
    assert duplicate_fact.value.code == "briefs.duplicate_fact_id"

    with pytest.raises(DataValidationError) as duplicate_action:
        GroundedBriefInput(
            STAMP,
            (source_action, replace(source_action, rank=2)),
            (source_fact,),
        )
    assert duplicate_action.value.code == "briefs.duplicate_action_ref"

    with pytest.raises(DataValidationError) as unknown:
        GroundedBriefInput(
            STAMP,
            (replace(source_action, fact_refs=("fact:missing",)),),
            (source_fact,),
        )
    assert unknown.value.code == "briefs.invalid_evidence_closure"

    with pytest.raises(DataValidationError) as order:
        GroundedBriefInput(STAMP, (replace(source_action, rank=2),), (source_fact,))
    assert order.value.code == "briefs.invalid_action_order"

    with pytest.raises(DataValidationError) as cross_sku:
        GroundedBriefInput(
            STAMP,
            (source_action,),
            (replace(source_fact, sku=SkuId("SKU-2")),),
        )
    assert cross_sku.value.code == "briefs.cross_sku_evidence"



def test_same_sku_authoritative_evidence_may_have_a_different_formula_id() -> None:
    source_fact = fact(
        fact_id="fact:SKU-1:minimum_safe_price",
        name="minimum safe price",
        formula_or_rule_id="unit_economics.minimum_safe_price",
    )
    source_action = action(
        action_ref="recommendation:SKU-1:pricing.current_price_unsafe",
        recommendation_id="recommendation:SKU-1:pricing.current_price_unsafe",
        rule_id="pricing.current_price_unsafe",
        category=RecommendationCategory.PRICING,
        fact_refs=(source_fact.fact_id,),
    )

    grounded = GroundedBriefInput(STAMP, (source_action,), (source_fact,))

    assert grounded.actions[0].fact_refs == (source_fact.fact_id,)
    assert grounded.facts[0].formula_or_rule_id == "unit_economics.minimum_safe_price"


def test_grounded_input_rejects_naive_timestamp_and_unsorted_facts() -> None:
    first = fact(fact_id="fact:a")
    second = fact(fact_id="fact:b")
    source_action = action(fact_refs=(first.fact_id, second.fact_id))

    with pytest.raises(DataValidationError) as naive:
        GroundedBriefInput(datetime(2026, 9, 4, 9), (source_action,), (first, second))
    assert naive.value.code == "clock.naive_datetime"

    with pytest.raises(DataValidationError) as unsorted:
        GroundedBriefInput(STAMP, (source_action,), (second, first))
    assert unsorted.value.code == "briefs.invalid_fact_order"


def test_ai_draft_is_immutable_has_no_status_and_checks_local_structure() -> None:
    summary = AIBriefSummary("Summary", ("rec:1",), ("fact:1",))
    item = AIBriefItem("rec:1", "Explanation", ("fact:1",))
    draft = AIBriefDraft(summary, [item])

    assert draft.items == (item,)
    assert "status" not in {field.name for field in fields(AIBriefDraft)}
    assert "severity" not in {field.name for field in fields(AIBriefItem)}
    assert "rank" not in {field.name for field in fields(AIBriefItem)}
    with pytest.raises(FrozenInstanceError):
        draft.items = ()
    with pytest.raises(DataValidationError):
        AIBriefSummary("Summary", ("rec:1", "rec:1"), ())
    with pytest.raises(DataValidationError) as duplicate:
        AIBriefDraft(summary, (item, item))
    assert duplicate.value.code == "briefs.duplicate_draft_action_ref"


def test_empty_draft_structure_is_valid_for_future_empty_grounding() -> None:
    draft = AIBriefDraft(AIBriefSummary("", (), ()), ())

    assert draft.summary.text == ""
    assert draft.items == ()


def test_fake_provider_conforms_to_vendor_neutral_protocol() -> None:
    expected = AIBriefDraft(AIBriefSummary("", (), ()), ())

    class FakeProvider:
        def create_draft(self, brief_input: GroundedBriefInput) -> AIBriefDraft:
            assert brief_input == GroundedBriefInput(STAMP, (), ())
            return expected

    provider = FakeProvider()
    assert isinstance(provider, BriefModelProvider)
    assert provider.create_draft(GroundedBriefInput(STAMP, (), ())) is expected
