# Deterministic Demo Dataset

This directory contains the fictional, local-only input data used by the Ozon AI Decision Copilot portfolio demo.

The dataset is fixed at `2026-09-02T09:00:00+03:00` (`Europe/Moscow` offset) so tests and demonstrations are repeatable. All names, SKUs, values, dates, policies, and scenarios are invented. They are not exports from Ozon, do not describe a real seller, and do not imply access to or capabilities of the Ozon Seller API.

## Contents

- `metadata.json`: dataset identity, fixed analysis time, and history boundaries.
- `policies.json`: explicit demo-only policy inputs. These are fixture assumptions, not production defaults.
- `scenarios.json`: coverage manifest linking intended demo situations to SKUs.
- `marketplace/products.json`: fictional catalog inputs.
- `marketplace/sales.json`: dated daily unit-sales inputs. Each `daily_units` item maps sequentially from `start_date`.
- `marketplace/inventory.json`: point-in-time sellable-stock inputs.
- `marketplace/inbound.json`: declared inbound-supply inputs.
- `marketplace/lead_times.json`: lead-time and ordering-policy inputs.
- `unit_economics/economics.json`: source unit-economics inputs.

## Data rules

- Files contain source inputs only. They intentionally contain no calculated coverage, forecasts, profit, margin, safe price, risk, recommendation, or priority result.
- Money and rate values are JSON strings so future adapters can construct `Decimal` values without passing through binary floats.
- Provenance is explicitly `demo` throughout.
- Missing values are intentional when named in `scenarios.json`.
- Inbound status and expected dates are source facts; whether a shipment mitigates a risk is deliberately left to later deterministic analytics.
- The future `MockOzonProvider` and local economics provider will read and normalize these fixtures. Provider implementations are outside MVP-005.

