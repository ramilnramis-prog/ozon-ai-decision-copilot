"""Representative demo data flowing through analytics into decision rules."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.analytics.inventory import InboundTreatment, calculate_inventory_analysis
from app.analytics.sales import calculate_sales_metrics
from app.analytics.unit_economics import calculate_unit_economics
from app.decisions.inventory_rules import evaluate_inventory_rules
from app.decisions.pricing_rules import evaluate_economics_rules
from app.decisions.sales_rules import evaluate_sales_rules
from app.domain.catalog import SalesPolicy
from app.domain.common import Currency, PolicyIdentity, SkuId
from app.domain.economics import EconomicsPolicy
from app.domain.inventory import InventoryPolicy
from app.providers.local_unit_economics import LocalUnitEconomicsProvider
from app.providers.mock_ozon import MockOzonProvider


AS_OF = date(2026, 9, 2)
STAMP = datetime(2026, 9, 2, 9, tzinfo=UTC)
SALES_POLICY = SalesPolicy(PolicyIdentity("demo-sales-policy", "1"), 14, 14, Decimal("0.25"))
ECONOMICS_POLICY = EconomicsPolicy(
    PolicyIdentity("demo-economics-policy", "1"),
    Currency.RUB,
    Decimal("100"),
    Decimal("0.20"),
    Decimal("300"),
    Decimal("0.01"),
    Decimal("1"),
)


def codes(evaluation) -> tuple[str, ...]:
    return tuple(item.rule_code for item in evaluation.recommendations)


@pytest.fixture(scope="module")
def demo_inputs():
    marketplace = MockOzonProvider()
    products = marketplace.get_products()
    sales = marketplace.get_sales_observations()
    inventory = marketplace.get_inventory_snapshots()
    inbound = marketplace.get_inbound_supplies()
    assert products.issues == sales.issues == inventory.issues == inbound.issues == ()
    return marketplace, sales.records, inventory.records, inbound.records


def evaluate_demo_sku(sku_text: str, demo_inputs):
    marketplace, sales_records, inventory_records, inbound_records = demo_inputs
    sku = SkuId(sku_text)
    sales = calculate_sales_metrics(
        sku,
        tuple(record for record in sales_records if record.sku == sku),
        SALES_POLICY,
        AS_OF,
    )
    lead_time = marketplace.get_lead_time(sku)
    constraints = marketplace.get_replenishment_constraints(sku)
    assert lead_time.value is not None and not lead_time.issues
    assert constraints.value is not None and not constraints.issues
    inventory_policy = InventoryPolicy(
        PolicyIdentity("demo-inventory-policy", "1"),
        lead_time.value,
        target_coverage_days=30,
        warning_window_days=7,
        overstock_threshold_days=90,
        minimum_order_quantity=constraints.value.minimum_order_quantity,
        pack_size=constraints.value.pack_size,
    )
    inventory_snapshot = next(record for record in inventory_records if record.sku == sku)
    inventory = calculate_inventory_analysis(
        inventory_snapshot,
        sales,
        tuple(record for record in inbound_records if record.sku == sku),
        lead_time.value,
        constraints.value,
        inventory_policy,
        AS_OF,
    )
    economics_input = LocalUnitEconomicsProvider().get_unit_economics(sku)
    assert economics_input.value is not None and not economics_input.issues
    economics = calculate_unit_economics(economics_input.value, ECONOMICS_POLICY)
    return (
        evaluate_sales_rules(sales, STAMP),
        evaluate_inventory_rules(inventory, STAMP),
        evaluate_economics_rules(economics, STAMP),
        inventory,
    )


@pytest.mark.parametrize(
    ("sku", "expected_sales", "expected_inventory", "expected_economics"),
    [
        ("DEMO-001", (), (), ()),
        ("DEMO-002", (), ("inventory.replenishment_already_late",), ("profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-004", (), ("inventory.replenishment_already_late",), ("profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-005", (), ("inventory.out_of_stock",), ()),
        ("DEMO-006", ("sales.material_decline",), ("inventory.overstock_candidate",), ("profitability.below_minimum_profit", "profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-010", ("sales.material_increase",), (), ("profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-012", (), ("inventory.missing_lead_time",), ("profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-015", (), (), ("profitability.loss_making", "profitability.below_minimum_profit", "profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-018", (), (), ("profitability.loss_making", "profitability.below_minimum_profit", "profitability.below_minimum_margin", "profitability.no_finite_safe_price")),
        ("DEMO-019", (), (), ("profitability.below_minimum_profit", "profitability.below_minimum_margin", "pricing.current_price_unsafe")),
        ("DEMO-021", (), ("inventory.replenishment_due_soon",), ("economics.insufficient_data",)),
        ("DEMO-037", ("sales.insufficient_history",), ("inventory.insufficient_sales_history",), ()),
    ],
)
def test_representative_demo_decisions(
    sku: str,
    expected_sales: tuple[str, ...],
    expected_inventory: tuple[str, ...],
    expected_economics: tuple[str, ...],
    demo_inputs,
) -> None:
    sales, inventory, economics, _ = evaluate_demo_sku(sku, demo_inputs)

    assert codes(sales) == expected_sales
    assert codes(inventory) == expected_inventory
    assert codes(economics) == expected_economics


def test_unconfirmed_demo_inbound_is_not_treated_as_available_stock(demo_inputs) -> None:
    _, inventory_decisions, _, inventory = evaluate_demo_sku("DEMO-006", demo_inputs)

    assert any(event.treatment is InboundTreatment.UNCONFIRMED for event in inventory.inbound_events)
    assert codes(inventory_decisions) == ("inventory.overstock_candidate",)
