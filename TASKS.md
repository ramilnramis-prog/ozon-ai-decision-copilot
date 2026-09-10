# Ozon AI Decision Copilot - Fast-Track Backlog

**FAST TRACK TARGET: Working employer-demo MVP in 2-3 focused development days using coding agents.**

## Execution Rules

- Work on one task at a time in the listed order and satisfy every dependency first.
- Read `MASTER_SPEC.md` and `ARCHITECTURE.md` before starting implementation.
- Use `MASTER_SPEC.md` as the product source of truth and `ARCHITECTURE.md` as the technical source of truth.
- Do not silently change architecture, calculation definitions, safety rules, or scope.
- If a task conflicts with the approved documents or requires an unresolved choice, set it to `BLOCKED`, record the issue, and request a decision instead of improvising.
- Set a task to `DONE` only after its acceptance criteria and required tests pass.
- Keep each task to a reviewable diff and avoid unrelated refactors.
- Python owns every financial value, operational metric, classification, recommendation, and priority rank.
- The LLM consumes structured calculated facts only. It never supplies authoritative values or business decisions.
- Streamlit contains presentation and interaction logic only.
- Mock, demo, manual, imported, calculated, and future external data must have truthful provenance.
- No task may claim unsupported Ozon Seller API or competitor-data capabilities.
- No real Ozon API, Google Sheets, Telegram, n8n, external writes, or automatic repricing belongs to the Fast Track.
- No secrets, credentials, private seller data, or unredacted external payloads may be committed.
- Prefer the Python standard library where reasonable. Add a dependency only in the task that needs it and document why.
- Do not add microservices, a database server, brokers, queues, cloud infrastructure, or Docker to the Fast Track.

### Statuses

- `TODO` - not started.
- `IN_PROGRESS` - actively being implemented; only one task may have this status.
- `BLOCKED` - waiting for a required decision or prerequisite, with the reason recorded.
- `DONE` - acceptance criteria and required tests passed.

### Priorities

- `P0` - required for the Fast Track employer demo.
- `P1` - valuable immediately after the employer-demo MVP.
- `P2` - optional/future and requires explicit authorization and prerequisites.

## Backlog Summary

- Previous backlog: **67 tasks**, including **61 P0** tasks.
- New backlog: **34 tasks**.
- Fast Track: **20 P0** tasks.
- Post-MVP: **4 P1** tasks and **10 P2** tasks.
- Initial status: every task is `TODO`.

## FAST TRACK MVP - P0

### Phase A - Foundation and Demo Inputs

### MVP-001 - Scaffold the project and test runner

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Create the minimal modular-monolith package structure, project metadata, and one reliable test command.
- **Dependencies:** None.
- **Expected files:** `pyproject.toml`; package markers under `app/core`, `app/domain`, `app/providers`, `app/analytics`, `app/decisions`, `app/ai`, `app/services`, and `app/ui`; `tests/conftest.py`; `.gitignore` if needed.
- **Acceptance criteria:** Supported Python version and minimal dependencies are documented; packages import; tests are discoverable; no feature logic, API, UI, external adapter, or speculative infrastructure exists.
- **Required tests:** Clean-environment install check, package import smoke test, and empty/minimal suite through the documented command.

### MVP-002 - Implement essential core utilities and configuration

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Provide only the configuration, Decimal-money, clock, and error/validation primitives required by the demo.
- **Dependencies:** MVP-001.
- **Expected files:** `app/core/config.py`; `app/core/money.py`; `app/core/clock.py`; `app/core/errors.py`; focused tests under `tests/unit/core/`.
- **Acceptance criteria:** Money never enters business logic as binary float; safe-price upward rounding is explicit; analysis time is injectable; mock startup needs no secrets; errors distinguish invalid records from fatal configuration/provider failure without an elaborate hierarchy.
- **Required tests:** Decimal conversion/float rejection, currency quantization, upward price-step rounding, fixed clock, default/invalid configuration, and safe error-message tests.

### MVP-003 - Define the essential domain and policy models

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Create the smallest typed domain model set needed by providers, analytics, decisions, services, and the brief.
- **Dependencies:** MVP-002.
- **Expected files:** `app/domain/common.py`; `app/domain/catalog.py`; `app/domain/inventory.py`; `app/domain/economics.py`; `app/domain/recommendations.py`; focused tests under `tests/unit/domain/`.
- **Acceptance criteria:** Models cover SKU/Product, sales observations, inventory, inbound supply, lead time, policies, economics inputs/results, pricing scenarios, calculated evidence, recommendations, priority actions, provenance, availability status, and validation issues; missing and zero remain distinct; negative stock is invalid; models are source/framework independent.
- **Required tests:** Valid construction plus invalid SKU, negative stock/sales, invalid rates/currency/dates, missing lead time/economics, unavailable status, provenance, immutability, and contradictory-state tests.

### MVP-004 - Define replaceable provider interfaces and source mapping

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Isolate marketplace reads and unit-economics reads behind two simple independent interfaces.
- **Dependencies:** MVP-003.
- **Expected files:** `app/providers/contracts.py`; `app/providers/mapping.py`; provider-boundary tests.
- **Acceptance criteria:** `MarketplaceReadProvider` and `UnitEconomicsProvider` return normalized domain objects or typed issues; interfaces expose no file rows, HTTP details, assumed Ozon endpoints, competitor data, or writes; mappings attach provenance and validate source shape.
- **Required tests:** Fake-provider conformance, valid/invalid mapping, empty result, malformed money/date, duplicate key, missing field, negative stock, and provider-failure semantics.

### MVP-005 - Create the complete demo dataset

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Create realistic local marketplace and unit-economics inputs for approximately 30-50 SKUs in one coherent demo dataset.
- **Dependencies:** MVP-004.
- **Expected files:** `data/demo/README.md`; structured files under `data/demo/marketplace/` and `data/demo/unit_economics/`; dataset validation tests/fixtures.
- **Acceptance criteria:** Data is clearly mock/demo; SKU keys join; dates and as-of context are reproducible; the set supports healthy, near-stockout, already-late reorder, overstock, low-margin, unsafe-price, sales-decline, sales-increase, and price-increase-opportunity scenarios; source files contain inputs rather than precomputed answers.
- **Required tests:** Schema validation, referential integrity, Decimal-safe parsing, date-window coverage, all required scenario-presence assertions, provenance labels, and no-secret/no-live-claim checks.

### MVP-006 - Implement mock marketplace and local economics providers

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Load the demo dataset through replaceable adapters and prove basic substitutability without building a large contract framework.
- **Dependencies:** MVP-005.
- **Expected files:** `app/providers/mock_ozon.py`; `app/providers/local_unit_economics.py`; updates to `app/providers/mapping.py`; focused provider tests and one alternate in-memory fake.
- **Acceptance criteria:** `MockOzonProvider` supplies catalog, sales, inventory, inbound, and lead time; `LocalUnitEconomicsProvider` separately supplies prices/costs/rates; both return normalized provenance-rich models; per-record issues are safe; analytics never sees file-specific structures.
- **Required tests:** Successful deterministic reads, empty/missing SKU, malformed record, partial invalid batch, wrong path, provenance, provider-wide failure, and a minimal interface-swap test.

### Phase B - Deterministic Business Engine

### MVP-007 - Implement deterministic sales metrics

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Calculate average daily sales and configured period-over-period change from dated observations.
- **Dependencies:** MVP-003, MVP-006.
- **Expected files:** `app/analytics/sales.py`; `tests/unit/analytics/test_sales.py`.
- **Acceptance criteria:** As-of date and windows are explicit; missing days follow documented policy; zero sales remains zero; insufficient history is explicit; output includes periods/evidence; functions are pure and make no forecast or causal claim.
- **Required tests:** Normal, partial, empty, zero-sales, boundary-date, future-record, increase, decline, exact-threshold input, shuffled-input, and repeatability cases.

### MVP-008 - Implement inventory timing and replenishment analytics

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Calculate coverage, stockout timing, latest safe reorder/production date, reorder point, and replenishment quantity as one coherent inventory engine.
- **Dependencies:** MVP-007.
- **Expected files:** `app/analytics/inventory.py`; `tests/unit/analytics/test_inventory.py`.
- **Acceptance criteria:** Formulas match `MASTER_SPEC.md`; date and unit rounding are documented; only confirmed timely inbound reduces need; MOQ/pack rules are deterministic; zero sales and missing lead time return explicit states; functions have no I/O.
- **Required tests:** Healthy, zero stock, fractional coverage, already-late/exact-today start, safety buffer, zero sales, missing lead time, eligible/late/unconfirmed inbound, sufficient stock, MOQ, pack multiple, and repeatability cases.

### MVP-009 - Implement complete unit economics and safe-price calculations

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Calculate cost components, profit, contribution margin, DRR where available, break-even, and minimum safe price in one Decimal-only engine.
- **Dependencies:** MVP-002, MVP-003, MVP-006.
- **Expected files:** `app/analytics/unit_economics.py`; `tests/unit/analytics/test_unit_economics.py`.
- **Acceptance criteria:** Fixed and proportional costs reconcile; DRR uses supported source data or is unavailable; all configured profit/margin/floor constraints are enforced; the maximum candidate wins; safe price rounds upward; invalid denominators fail explicitly; result is contribution economics, not falsely labeled accounting net profit.
- **Required tests:** Fixed/rate commission, DRR/per-unit ad cost, missing/zero ad revenue, positive/zero/negative profit, margin equality, each safe-price constraint winning, combined constraints, invalid denominators/rates, currency mismatch, precision, and upward-rounding boundaries.

### MVP-010 - Implement deterministic pricing what-if simulation

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Evaluate one or more hypothetical prices using the exact unit-economics and safe-price engine from MVP-009.
- **Dependencies:** MVP-009.
- **Expected files:** `app/analytics/pricing.py`; `tests/unit/analytics/test_pricing.py`.
- **Acceptance criteria:** Results are labeled hypothetical, preserve unchanged-cost assumptions, expose profit/margin/safe threshold/distance/status, and have no side effects or demand forecast.
- **Required tests:** One/multiple prices, below/equal/above safe threshold, invalid/non-positive input, missing economics, stable ordering, repeatability, and equivalence with direct economics calculation.

### MVP-011 - Implement deterministic decision rules and explanations

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Convert sales, inventory, and economics results into structured, evidence-backed decisions and recommendations.
- **Dependencies:** MVP-007, MVP-008, MVP-009, MVP-010.
- **Expected files:** `app/decisions/inventory_rules.py`; `app/decisions/pricing_rules.py`; `app/decisions/sales_rules.py`; shared recommendation helper if useful; decision tests.
- **Acceptance criteria:** Inventory rules cover critical/already late, due soon, healthy, overstock, and insufficient data; pricing labels stay within the approved set and never violate safe thresholds; sales rules report material increase/decline without causation; every result includes stable rule ID, evidence, threshold, deterministic reason, provenance, and read-only action wording.
- **Required tests:** Every rule and boundary, overlapping-rule precedence, zero sales/stock, missing inputs, safe-price equality, unsupported price opportunity, material sales thresholds, evidence integrity, deterministic IDs/text, and no-execution-language cases.

### MVP-012 - Implement deterministic Priority Action Center ranking

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Combine recommendations into stable priority actions answering what needs attention today.
- **Dependencies:** MVP-011.
- **Expected files:** `app/decisions/prioritizer.py`; updates to recommendation models only if necessary; `tests/unit/decisions/test_prioritizer.py`.
- **Acceptance criteria:** Categories include critical stock, reorder, profitability, unusual sales change, pricing opportunity, and insufficient data; rank uses configured severity, urgency, and stable tie-breaks; actions expose what happened, evidence, action, why, and status; the LLM has no role.
- **Required tests:** Severity/urgency order, exact ties, shuffled inputs, missing urgency, multiple categories per SKU, stable repeated output, and complete evidence/reason fields.
- **Preflight:** READY. The categorical ordering framework and all 18 current production rule mappings are approved in `MASTER_SPEC.md` Section 6.4.1 and ADR-001. Unknown future rules must fail closed.

### MVP-013 - Implement analysis and pricing orchestration

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Wire providers, analytics, decisions, priorities, pricing scenarios, partial-data behavior, and application bootstrap into simple in-process services.
- **Dependencies:** MVP-006, MVP-010, MVP-012.
- **Expected files:** `app/services/analysis.py`; `app/services/pricing.py`; `app/bootstrap.py`; minimal analysis-snapshot model; service/integration tests.
- **Acceptance criteria:** One analysis call returns a reproducible snapshot with results/actions/issues/config/provenance; pricing service reuses the same calculator; missing economics does not block inventory; one bad SKU does not appear healthy or block valid SKUs; mock-only bootstrap requires no credentials; no formula is duplicated in services.
- **Required tests:** Full happy path, fixed as-of repeatability, unknown SKU, missing economics, negative stock, missing lead time, zero sales, mixed valid/invalid batch, provider-wide/global-policy failure, pricing scenarios, alternate provider fake, and no-write/no-LLM operation.

### Phase C - Grounded AI and Streamlit Demo

### MVP-014 - Define grounded brief input and replaceable AI contract

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Build the allow-listed `GroundedBriefInput`, brief result/status, AI-provider interface, and disabled/fake providers before any live model adapter.
- **Dependencies:** MVP-013.
- **Expected files:** `app/domain/briefs.py`; `app/ai/contracts.py`; `app/ai/brief_input.py`; optional offline adapter; `tests/unit/ai/test_brief_input.py`; `tests/unit/ai/test_contract.py`.
- **Acceptance criteria:** Input contains only calculated facts, already-ranked actions, issues, metadata, and reference IDs; raw payloads/secrets are excluded; the provider cannot return authoritative business fields; disabled mode is first-class.
- **Required tests:** Allow-list serialization, deterministic order, reference integrity, raw-field exclusion, empty/partial snapshot, fake-provider conformance, disabled mode, and input immutability.
- **Preflight:** READY. The action-centric grounded-input allow-list, action/fact identity, canonical serialization, structured draft, generation-status ownership, and minimum grounding validation are approved in `MASTER_SPEC.md` Sections 6.5.1 through 6.5.5 and ADR-003. LLM vendor selection remains a separate MVP-015 decision.

### MVP-015 - Generate and validate the AI Daily Brief

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Add one explicitly selected LLM adapter, versioned interpretation-only prompt, grounding validator, and graceful brief service.
- **Dependencies:** MVP-014; approved OpenAI runtime and output contract in ADR-004 and `MASTER_SPEC.md` Sections 6.5.4 through 6.5.8.
- **Expected files:** `app/ai/openai_provider.py`; `app/ai/prompts.py`; `app/ai/brief_generator.py`; `app/ai/grounding.py`; `app/services/briefs.py`; `app/bootstrap.py`; `pyproject.toml` for the approved official `openai` SDK; AI/service tests.
- **Acceptance criteria:** Output is structured, AI-labeled, and references source facts; unknown SKUs, numbers, statuses, severities, recommendations, or provenance claims fail closed; unavailable/invalid AI affects only the prose brief; mock-only startup still needs no key.
- **Required tests:** Mocked valid structured response; exact brief/result invariants and grounding digest; unknown fact/SKU/number/status/action and changed severity; block-local reference rules; identifier, Unicode-digit, numeric-format, ISO/reformatted/relative date-time claim boundaries; malformed/refusal output; typed timeout/rate-limit/5xx mapping; fatal missing/invalid credentials; disabled and empty-input modes; grounding failure; zero retry/fallback; unchanged analysis snapshot; and a no-live-call default suite.
- **Preflight:** READY. OpenAI, Responses API, `gpt-5.6-terra`, official Python `openai` SDK, strict Structured Outputs, secret ownership, failure taxonomy, exact `DailyBrief`/`BriefGenerationResult` schemas, Russian prompt rules, claim grammar, and zero-retry/no-fallback policy are approved in `MASTER_SPEC.md` Sections 6.5.4 through 6.5.8 and ADR-004. Implementation is in progress under this approved contract.

### MVP-016 - Build Streamlit shell, executive summary, and priority actions

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Create the first usable employer-demo screen centered on what requires attention today.
- **Dependencies:** MVP-013.
- **Expected files:** `streamlit_app.py`; `app/ui/presenters.py`; `app/ui/components.py`; `pyproject.toml` for Streamlit; focused presenter/UI tests.
- **Acceptance criteria:** UI calls services directly in-process; executive counts and action order come unchanged from the snapshot; priority cards/table expose severity, action, evidence, and why; as-of and mock/demo labels are visible; no UI formula or execution control exists.
- **Required tests:** Presenter formatting, normal/empty/partial summary, action-order preservation, evidence rendering, missing-vs-zero display, read-only labeling, import/startup smoke, and no-recalculation checks.

### MVP-017 - Add inventory, economics, and SKU-detail views

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Make the deterministic analysis inspectable at portfolio-demo depth without adding business logic to Streamlit.
- **Dependencies:** MVP-016.
- **Expected files:** `streamlit_app.py`; `app/ui/components.py`; `app/ui/presenters.py`; focused UI/presenter tests.
- **Acceptance criteria:** Inventory shows stock, sales rate, coverage, stockout/safe-start dates, lead time, risk, quantity, and reason; economics shows costs, profit, margin, DRR status, break-even, safe price, and recommendation; SKU detail traces facts to rule IDs/provenance; missing/invalid states are honest.
- **Required tests:** Healthy, critical, late, overstock, zero-sales, missing-lead-time, low-margin, unsafe-price, unavailable DRR/economics, unknown SKU, provenance/evidence, and formatting cases.

### MVP-018 - Add pricing simulator, Daily Brief, and graceful UI states

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Complete the interactive demo with hypothetical pricing and optional grounded management prose.
- **Dependencies:** MVP-015, MVP-017.
- **Expected files:** `streamlit_app.py`; `app/ui/components.py`; `app/ui/presenters.py`; focused simulator/brief UI tests.
- **Acceptance criteria:** Manager can compare hypothetical prices with service-calculated profit/margin/safety; current and hypothetical states are distinct; no demand forecast/write exists; valid brief and source references display; disabled/unavailable/invalid AI leaves every deterministic screen usable; provenance and AI labels are consistent.
- **Required tests:** Below/equal/above safe price, invalid input, missing economics, scenario-service failure, valid brief, disabled/unavailable/ungrounded brief, source-reference display, deterministic-screen continuity, and startup with no AI key.

### Phase D - Verification and Employer-Demo Packaging

### MVP-019 - Add end-to-end and regression coverage

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Lock the critical demo path with a compact high-value suite rather than an exhaustive test bureaucracy.
- **Dependencies:** MVP-018.
- **Expected files:** `tests/integration/test_end_to_end.py`; deterministic expected fixtures; minimal startup/provider-swap tests; corrections required by failures.
- **Acceptance criteria:** Demo data runs from providers through analytics, decisions, services, and Streamlit-ready output without network access; all nine required business scenarios are asserted; alternate fake providers work; AI failure does not affect facts; full suite is stable and repeatable.
- **Required tests:** End-to-end scenario matrix, fixed-clock repeated run, provider swap, partial-data batch, mock-only startup, AI-disabled/invalid mode, pricing boundaries, and the complete unit/integration suite through one documented command.

### MVP-020 - Package the employer-demo portfolio

- **Status:** `DONE`
- **Priority:** `P0`
- **Goal:** Make the finished MVP understandable and reproducible for an AI Automation/Ozon employer.
- **Dependencies:** MVP-019.
- **Expected files:** `README.md`; `docs/demo-script.md`; selected screenshots under `docs/screenshots/`; short video checklist; `.gitignore`/project metadata only as needed.
- **Acceptance criteria:** README explains the problem, architecture, setup, deterministic/AI/human boundaries, mock provenance, limitations, and exact run/test commands; demo script shows priorities, inventory timing, economics, price simulation, evidence, brief, and no-LLM mode; screenshots visibly say demo/mock; repository has no secrets or unsupported live-Ozon claims.
- **Required tests:** Fresh-environment setup rehearsal, final test command, Streamlit startup, demo walkthrough twice, link/image check, secret/private-data review, and clean repository-status review.

## POST-MVP - P1/P2

Post-MVP tasks are deliberately outside the 2-3 day critical path. They must not delay or destabilize MVP-001 through MVP-020.

### POST-101 - Add a read-only FastAPI adapter

- **Status:** `TODO`
- **Priority:** `P1`
- **Goal:** Expose analysis, SKU detail, pricing scenarios, brief status, and health over thin HTTP routes.
- **Dependencies:** MVP-019.
- **Expected files:** `app/api/*`; `pyproject.toml`; `tests/api/*`.
- **Acceptance criteria:** Routes call existing services, preserve Decimal/provenance/issues, and contain no calculations, provider construction, auth system, or writes.
- **Required tests:** App/health, analysis/detail, pricing, brief unavailable, validation/error mapping, serialization, and no-recalculation tests.

### POST-102 - Expand reusable provider contract suites

- **Status:** `TODO`
- **Priority:** `P1`
- **Goal:** Generalize the minimal MVP swap proof into reusable contracts for every current and future provider.
- **Dependencies:** MVP-019.
- **Expected files:** `tests/contracts/providers/*`; contract fixtures; narrow corrections to provider interfaces.
- **Acceptance criteria:** Suites cover normalized output, provenance, empty/missing data, partial errors, provider-wide failure, and deterministic reads for each provider family.
- **Required tests:** Run shared contracts against mock/local providers and alternate fakes.

### POST-103 - Expand the UI and API test matrix

- **Status:** `TODO`
- **Priority:** `P1`
- **Goal:** Add broader adapter-level smoke, rendering, accessibility, and transport coverage after the demo is stable.
- **Dependencies:** MVP-019, POST-101 for API coverage.
- **Expected files:** `tests/ui/*`; `tests/api/*`; optional browser-test configuration only if justified.
- **Acceptance criteria:** Critical states render/serialize consistently without moving business assertions into adapters; tests remain stable and proportionate.
- **Required tests:** Cross-screen provenance/status matrix, accessibility smoke, critical API schema matrix, startup/shutdown, and failure-state coverage.

### POST-104 - Add a separate competitor-price provider

- **Status:** `TODO`
- **Priority:** `P1`
- **Goal:** Support optional local/manual/imported competitor comparisons behind `CompetitorPriceProvider`.
- **Dependencies:** MVP-019, POST-102.
- **Expected files:** provider contract and local adapter; `data/demo/competitors/*`; provider/decision/UI tests.
- **Acceptance criteria:** Source type and observation time are visible; data can be absent; opportunities require explicit evidence; nothing claims the data came from Ozon Seller API.
- **Required tests:** Valid/empty/stale/malformed data, unknown SKU, provenance, contract conformance, evidence rule, and no-Ozon-source-claim tests.

### POST-201 - Add Google Sheets source adapters

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Replace local economics or other explicitly approved narrow inputs with Google Sheets adapters.
- **Dependencies:** POST-102; approved sheet schema, authentication method, and freshness policy.
- **Expected files:** narrow Sheets provider adapters, configuration, and contract/integration tests.
- **Acceptance criteria:** Analytics/services remain unchanged; spreadsheet formulas are not authoritative calculations; credentials are external; provenance and revision/freshness are explicit.
- **Required tests:** Contract suite, mocked API mapping, missing/stale sheet, schema drift, auth failure, and local-provider parity tests.

### POST-202 - Implement a real Ozon read provider

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Add `OzonSellerApiProvider` only after real access and verified API contracts exist.
- **Dependencies:** POST-102; verified Ozon documentation, credentials, permissions, field semantics, rate limits, and approved integration decision.
- **Expected files:** Ozon read adapter, mapping/configuration, contract tests, and approved-environment integration tests.
- **Acceptance criteria:** Only verified capabilities are implemented; domain/analytics/decisions/services/UI remain unchanged; competitor/economics provenance is not misattributed; failures/freshness are explicit.
- **Required tests:** Provider contracts, mocked pagination/rate-limit/retry/auth/schema cases, normalized parity, and approved non-production integration smoke test.

### POST-203 - Add Telegram brief delivery

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Deliver completed snapshots or validated briefs through a narrow notifier after an explicitly approved send.
- **Dependencies:** MVP-020; approved recipient, consent, secret handling, and send-approval design.
- **Expected files:** Telegram notifier adapter, service/configuration, and tests.
- **Acceptance criteria:** Notifier cannot calculate, reprioritize, or execute marketplace actions; every send has a known target and approval/audit outcome.
- **Required tests:** Mocked success/failure/timeout, wrong target prevention, approval requirement, secret redaction, and duplicate-send protection.

### POST-204 - Add n8n orchestration

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Allow approved scheduling/orchestration through stable service/API contracts without moving rules into n8n.
- **Dependencies:** POST-101; approved scheduling and action-boundary design.
- **Expected files:** versioned workflow definitions, integration documentation, API contract tests, and fixtures.
- **Acceptance criteria:** n8n cannot bypass services or approval, contains no business formulas, and has explicit idempotency/error behavior.
- **Required tests:** Contract fixtures, scheduled read flow, failure/retry behavior, approval enforcement, and no-direct-provider-access checks.

### POST-205 - Add optional Docker packaging

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Package the proven application only if deployment or reviewer setup benefits from it.
- **Dependencies:** MVP-020; an approved deployment/use-case need.
- **Expected files:** `Dockerfile`; `.dockerignore`; optional compose file only if genuinely needed; documentation.
- **Acceptance criteria:** Image runs the mock demo without embedded secrets and does not introduce databases, brokers, or distributed services.
- **Required tests:** Clean image build, container startup, Streamlit health/manual smoke, no-secret scan, and documented run command.

### POST-206 - Design and implement approved external write actions

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Add exact action proposals, action-specific human approval, revalidation, segregated execution, and durable audit only after read integration is proven.
- **Dependencies:** POST-202; separately approved action scope, security model, audit persistence, and failure/idempotency design.
- **Expected files:** action/approval domain models, `MarketplaceActionProvider`, approval service/UI, audit storage, and security/integration tests.
- **Acceptance criteria:** No LLM can set authoritative target values or approve; stale proposals fail; exact targets/values are shown; outcomes include confirmed/failed/unknown; bulk/background approval is impossible.
- **Required tests:** Approval absent/mismatched/expired/stale, revalidation, exact-payload lock, provider failure/unknown outcome, audit completeness, idempotency, and authorization tests.

### POST-207 - Add a technical product-card auditor

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Evaluate separately specified product-card quality rules without coupling them to core economics/inventory analytics.
- **Dependencies:** MVP-020; approved card-data source and rule specification.
- **Expected files:** separate domain/analytics/decision modules, provider extension, UI section, and tests.
- **Acceptance criteria:** Findings are evidence-based, source capabilities are verified, and AI interpretation cannot invent eligibility or compliance facts.
- **Required tests:** Rule boundaries, missing card data, provider errors, grounding, and integration isolation tests.

### POST-208 - Add validated forecasting and price elasticity

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Introduce forecasts only after sufficient historical data, evaluation criteria, and model governance are approved.
- **Dependencies:** MVP-020; validated dataset, baseline, accuracy metrics, uncertainty policy, and explicit product decision.
- **Expected files:** isolated forecasting package, evaluation fixtures/reports, service/UI extensions, and tests.
- **Acceptance criteria:** Forecasts are labeled estimates with uncertainty; measured performance is reported; existing what-if results never imply demand response when this model is absent.
- **Required tests:** Backtesting, leakage checks, baseline comparison, reproducibility, uncertainty display, missing-history fallback, and separation from deterministic economics.

### POST-209 - Add a voice interface

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Add an optional voice adapter over existing read-only services after accessibility and privacy requirements are defined.
- **Dependencies:** MVP-020; approved speech provider, consent, privacy, and retention decisions.
- **Expected files:** voice adapter/UI, configuration, privacy documentation, and tests.
- **Acceptance criteria:** Voice cannot bypass grounding, decisions, or approval; transcripts/audio follow explicit privacy handling; text UI remains fully usable.
- **Required tests:** Mocked transcription/output, permission failure, redaction, grounding preservation, no-write behavior, and graceful unavailable mode.

### POST-210 - Add an external competitor-data feed

- **Status:** `TODO`
- **Priority:** `P2`
- **Goal:** Replace or supplement local comparison inputs with a verified and legally usable external source.
- **Dependencies:** POST-104; approved provider, licensing, field semantics, freshness, and rate-limit decisions.
- **Expected files:** external competitor adapter, configuration, contract/integration tests, and provenance documentation.
- **Acceptance criteria:** Source and freshness are explicit; provider satisfies the separate competitor contract; no Ozon-source claim is made unless verified; data does not authorize automatic repricing.
- **Required tests:** Contract suite, mocked pagination/rate limits/schema drift/auth failure, staleness, provenance, and absent-feed degradation.

## Scope Guardrail

Automatic repricing remains outside this backlog. It may be proposed only after POST-206 is proven and must receive its own deterministic policy, risk analysis, per-action approval design, and tests. No Post-MVP task weakens Python calculation ownership, provider replaceability, provenance, graceful degradation, or explicit human approval.
