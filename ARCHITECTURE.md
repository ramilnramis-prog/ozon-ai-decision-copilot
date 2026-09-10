# Architecture

## 1. Architecture Objective

Implement the requirements in `MASTER_SPEC.md` as a small modular monolith: one Python codebase, one set of domain models and business rules, and thin interfaces for Streamlit and FastAPI. The architecture optimizes for deterministic behavior, testability, explainability, and a credible portfolio demonstration rather than production-scale distribution.

The MVP is read-only. It runs entirely with local demo data and does not require Ozon credentials, a database server, a broker, an event bus, or cloud infrastructure.

## 2. Non-Negotiable Boundaries

- Python is the source of truth for financial values, operational metrics, thresholds, risk classifications, eligibility, recommendations, and priority ordering.
- The LLM consumes structured calculated facts. It does not calculate or correct profit, margin, DRR, stock coverage, dates, quantities, prices, risk, or eligibility.
- Streamlit contains presentation and interaction logic only.
- FastAPI contains transport concerns only; it performs no business calculations.
- Provider implementations are replaceable behind explicit read contracts.
- Marketplace operational data, unit-economics data, and competitor data use separate provider abstractions because they have different sources and provenance.
- `MockOzonProvider` is the marketplace provider for the MVP. A future `OzonSellerApiProvider` must satisfy the same contract.
- No real Ozon write operation exists in the MVP.
- Every future external action requires explicit, action-specific human approval and revalidation before execution.
- No microservices or speculative infrastructure are introduced for the MVP.

## 3. Architectural Style and System Context

The application follows a ports-and-adapters shape without requiring a framework dedicated to that pattern:

- **Domain and deterministic engines** contain business meaning and rules.
- **Provider contracts** are outbound ports through which application services request normalized source data.
- **Mock/local and future external adapters** implement those provider contracts.
- **Application services** orchestrate complete use cases.
- **Streamlit and FastAPI** are inbound adapters calling the same application services.
- **The AI adapter** is optional and downstream of deterministic decisions.
- **The composition root** selects concrete adapters and wires dependencies from configuration.

Streamlit may call application services directly in-process for the simplest MVP. FastAPI exposes the same use cases for testing, reuse, and portfolio demonstration; Streamlit does not need to call FastAPI over HTTP. If both entry points are run, they remain interfaces over the same monolith, not separate business services.

## 4. Architecture Diagram

```text
 Local demo files                             Future external sources
 (marketplace, economics, competitor)         (Ozon, Sheets, competitor feed)
          |                                                |
          v                                                v
+-----------------------------------------------------------------------+
|  Data / Provider adapters                                             |
|  MarketplaceReadProvider | UnitEconomicsProvider | CompetitorProvider |
+-----------------------------------------------------------------------+
                                |
                                | validate, normalize, attach provenance
                                v
+-----------------------------------------------------------------------+
|  Domain models and business policy configuration                      |
+-----------------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------------+
|  Deterministic analytics                                              |
|  sales | inventory | unit economics | pricing scenarios               |
+-----------------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------------+
|  Deterministic decision engine                                        |
|  alerts | recommendations | severity | stable priority ordering        |
+-----------------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------------+
|  Application services / use-case orchestration                        |
+-----------------------------------------------------------------------+
               |                         |                         |
               |                         | structured facts        |
               v                         v                         v
       +---------------+        +----------------+        +---------------+
       | FastAPI       |        | Streamlit UI   |        | AI brief      |
       | thin adapter  |        | thin adapter   |        | interpreter   |
       +---------------+        +----------------+        +---------------+
                                          ^                        |
                                          | grounded prose only    |
                                          +------------------------+

 Future external action path (not implemented in MVP):

 Proposed action -> exact preview -> human approval -> revalidate
                 -> action provider -> external system -> audit outcome
```

The AI path never feeds values or decisions back into analytics or the decision engine. A brief is an optional representation of completed deterministic results.

## 5. Layers and Responsibilities

### 5.1 Data / Provider Layer

The provider layer retrieves source data and maps it into validated domain models. It must not calculate business metrics, classify risks, or make recommendations.

Provider ports:

- **`MarketplaceReadProvider`** supplies products/SKUs, dated sales observations, inventory snapshots, and confirmed inbound supply data where available.
- **`UnitEconomicsProvider`** supplies current prices, costs, rates, and profitability-policy inputs. Local structured data implements this port in the MVP; a future Google Sheets adapter may replace it.
- **`CompetitorPriceProvider`** supplies optional comparison prices with source type and observation time. It remains separate from the Ozon provider and may return no data.
- **`BriefModelProvider`** sends an already-built `GroundedBriefInput` to an LLM and returns a structured draft. This is an AI port, not a business-data source.
- **`MarketplaceActionProvider`** is a future, segregated write port. It must not be implemented or bound to Ozon in the MVP.

Concrete MVP adapters:

- `MockOzonProvider` reads marketplace demo files and implements `MarketplaceReadProvider`.
- `LocalUnitEconomicsProvider` reads unit-economics demo files and implements `UnitEconomicsProvider`.
- `LocalCompetitorPriceProvider` reads optional mock/manual/imported comparison data and implements `CompetitorPriceProvider`.

Each provider returns normalized domain objects or typed provider/data-quality errors. Every returned record carries provenance such as `demo`, `mock`, `manual`, `imported`, `calculated`, or, in a future adapter, `ozon_api`. A provider may perform transport, parsing, field mapping, pagination, and source-shape validation, but no cross-source business calculation.

### 5.2 Domain Models

Domain models define typed business concepts, value constraints, units, dates, status enums, and evidence structures. They contain no Streamlit, FastAPI, file-format, HTTP-client, or LLM-vendor concerns.

Domain construction enforces universal invariants such as non-empty identifiers, explicit currency, timezone-aware timestamps where time is used, non-negative sellable stock, valid rate ranges, and `Decimal` money values. Use-case completeness is checked later because some valid source records are intentionally partial.

The main anticipated entities are defined in Section 6.

### 5.3 Deterministic Analytics Layer

This layer contains pure Python functions wherever practical. A function receives validated domain values and explicit policy/configuration, returns a result object, and has no network, filesystem, UI, clock, random, or LLM side effects.

Responsibilities:

- calculate sales metrics for explicit windows;
- calculate stock coverage, stockout timing, safe start dates, reorder points, and replenishment quantities;
- calculate commission, advertising cost/DRR, total variable cost, profit, contribution margin, break-even price, and minimum safe price;
- calculate each what-if pricing scenario using the same unit-economics function used for current prices;
- return explicit `not_applicable`, `insufficient_data`, or validation outcomes rather than inventing values.

The as-of date and all policy inputs are function inputs. Money uses shared `Decimal` and rounding rules from `core/money.py`.

### 5.4 Decision / Recommendation Layer

This layer converts completed analytics results into structured business decisions. It is deterministic and does not call the LLM.

Responsibilities:

- apply inventory-risk rules;
- detect reorder-now and reorder-soon conditions;
- apply profitability and safe-price rules;
- identify configured material sales changes;
- create only evidence-supported pricing opportunities;
- create structured recommendations and alerts with rule identifiers;
- rank `PriorityAction` objects by configured severity, urgency, and stable tie-breakers.

Every recommendation records the calculated evidence, policy thresholds, reason code, human-readable deterministic explanation, provenance, and analysis timestamp. Free-form AI text may restate this result but may not change it.

### 5.5 AI Interpretation Layer

The AI layer is an optional outbound interpretation adapter. It receives the action-centric `GroundedBriefInput` defined in `MASTER_SPEC.md` Sections 6.5.1 through 6.5.5: the source analysis timestamp, all already-ranked priority actions, and only their reachable calculated evidence facts. Service issues, actionless-SKU analytics, and raw snapshot/provider data are excluded.

Responsibilities:

- serialize the exact allow-listed grounded input schema without changing Decimal, date, datetime, enum, or identifier semantics;
- prompt the LLM to summarize and explain without calculating;
- require structured output containing references to source fact/alert IDs;
- validate that referenced IDs, SKUs, numbers, statuses, and recommendations exist in the input;
- reject structurally unusable or ungrounded output as `INVALID`, without conflating it with provider availability;
- return a labeled validated `DailyBrief` inside a Python-owned generation result, or a separate `DISABLED`, `UNAVAILABLE`, or `INVALID` result with no brief.

It must not receive raw provider payloads when a normalized or calculated fact is available. It must never be imported by analytics or decision modules. If the LLM is disabled, unreachable, or invalid, deterministic analysis and all non-AI screens continue to work; only the natural-language brief degrades.

The concrete MVP adapter implements the existing `BriefModelProvider` with the official Python `openai` SDK, OpenAI Responses API, model `gpt-5.6-terra`, reasoning effort `low`, and strict Structured Outputs matching `AIBriefDraft`. It receives only `GroundedBriefInput` or its canonical serialization. Tools, browsing, file search, function calls, background mode, streaming, storage, application retries, fallback models, and fallback prose are disabled. Static instructions request concise Russian prose and treat the grounded payload as data, never as system/developer instructions.

`OPENAI_API_KEY` is resolved only by the composition root when AI is enabled and is injected into the concrete adapter. Missing or invalid local secret/authentication configuration is a fatal `ConfigurationError`, not provider unavailability. Connection, timeout, rate-limit, and transient/server-side 5xx failures become typed `AIUnavailableError` and then `UNAVAILABLE`; a returned refusal, malformed/otherwise unusable draft, schema failure, or grounding failure becomes `INVALID`. Unexpected programming errors propagate. No result contains raw provider response or exception text.

### 5.6 API Layer

FastAPI is a thin inbound adapter over application services. Its responsibilities are request parsing, transport-schema validation, calling one use case, mapping typed outcomes to HTTP responses, and exposing health/readiness information.

Likely read-only MVP routes are:

- analysis/dashboard snapshot;
- SKU analysis detail;
- pricing what-if simulation;
- optional AI Daily Brief generation/status;
- health/readiness.

API schemas are transport DTOs, not alternate domain models. Routes must not contain formulas, risk thresholds, recommendation rules, provider construction, or LLM prompts. Dependency wiring happens in the composition root.

### 5.7 UI Layer

`streamlit_app.py` is the Streamlit entry point. UI helpers translate service output into tables, cards, filters, charts, explanations, and scenario inputs.

Responsibilities:

- present executive summary and priority actions first;
- display inventory, unit-economics, SKU-detail, pricing-simulator, and AI-brief sections;
- collect and parse user-entered scenario values;
- call application services and render returned view data;
- label demo/mock provenance, hypothetical values, and AI-generated prose;
- distinguish missing data from numeric zero and proposed actions from executed actions.

The UI must not calculate totals, margins, dates, safe prices, severity, or ordering. Cosmetic formatting may format an already-calculated value for display but must not change its business meaning. Presentation mapping belongs in `app/ui/presenters.py` so it can be tested without Streamlit.

### 5.8 Automation / Integration Layer (Future)

Future Telegram, n8n, scheduled runs, and external write workflows must call application services through explicit adapters. They may trigger a use case or deliver a completed output, but they may not duplicate calculations or bypass decision and approval policies.

External sends and marketplace mutations are side effects. A future action flow must create an exact proposal, show its target and values to a human, obtain action-specific approval, re-read/revalidate affected state, execute through a segregated action provider, and record the outcome. Neither an LLM response nor an n8n workflow constitutes approval.

None of these adapters is required or implemented in the MVP.

### 5.9 Testing Layer

Tests follow the same boundaries as production code:

- calculation unit tests prove formulas, edge cases, date handling, and Decimal rounding;
- decision-rule tests prove reason codes, thresholds, precedence, and stable ordering;
- shared provider contract tests run against every provider implementation;
- AI input and grounding tests verify allow-listed input, source references, and rejection of invented claims;
- service integration tests exercise end-to-end use cases with in-memory/fake ports and demo adapters;
- FastAPI tests check transport mapping rather than recalculating expected business logic;
- focused Streamlit/UI smoke tests cover startup and critical rendering paths only.

## 6. Main Domain Entities

These are planned conceptual models, not implementation code:

- **`Product` / `SKU`**: stable SKU identity, display metadata, marketplace identifiers, and active status.
- **`SalesObservation`**: units, revenue where applicable, observation date, source, and provenance.
- **`InventorySnapshot`**: sellable stock, snapshot time, unit, location/aggregation context where known, and provenance.
- **`InboundSupply`**: confirmed inbound quantity, expected arrival date, confirmation status, and provenance.
- **`LeadTime`**: production/procurement days, delivery days, safety-buffer days, and source/configuration identity.
- **`AnalysisPolicy`**: explicit sales windows, warning thresholds, target coverage, minimum profitability constraints, cost treatment, and rounding policy.
- **`SalesMetrics`**: observation window, observed days, units, average daily sales, comparative change, and availability status.
- **`InventoryAnalysisResult`**: coverage, dates, reorder point, target stock, eligible inbound, recommended quantity, and calculation status.
- **`InventoryRisk`**: risk class, severity, trigger rule, thresholds, evidence, and status.
- **`UnitEconomicsInput`**: price, currency, cost of goods, fixed and proportional cost components, source periods, and provenance.
- **`UnitEconomicsResult`**: calculated cost components, profit/contribution, margin, DRR status, break-even price, minimum safe price, and safe/unsafe status.
- **`PricingScenario`**: SKU, hypothetical price, as-of context, unchanged-cost assumption, and optional explicitly supplied supported cost scenario.
- **`PricingScenarioResult`**: scenario economics, distance from safe price, constraints, and eligibility status.
- **`CalculatedFact` / `Evidence`**: typed value, unit, time period, formula/rule ID, source IDs, and provenance used to explain a result.
- **`Recommendation`**: action label, reason code, deterministic explanation, evidence references, and guardrails.
- **`PriorityAction`**: recommendation plus category, severity, urgency, stable rank key, and status.
- **`AnalysisSnapshot`**: immutable aggregate returned by a full analysis run, including results, alerts, data-quality issues, as-of time, and configuration identity.
- **`GroundedBriefInput`**: action-centric immutable input containing the source analysis timestamp, all ordered actions, and the exact calculated-fact evidence closure supplied to the LLM; it excludes service issues and unrelated analytics.
- **`AIBriefDraft`**: untrusted structured model output containing a referenced summary and exactly one ordered referenced item per grounded action; it has no generation status or authoritative business fields.
- **`DailyBrief`**: immutable successfully validated AI prose containing exactly the copied analysis timestamp, SHA-256 grounding digest, validated summary, and ordered validated items; it contains no duplicated authoritative calculated fields or failure status.
- **`BriefGenerationResult`**: immutable Python-owned wrapper containing exactly `status` and `brief`; `GENERATED` requires a `DailyBrief`, while `DISABLED`, `UNAVAILABLE`, and `INVALID` require `brief = null`.
- **`DataProvenance`**: source type, provider, source timestamp, ingestion timestamp, and optional source record identifier.
- **`ValidationIssue`**: scope, code, severity, affected field/SKU, and safe user-facing message.
- **`ActionProposal`, `ApprovalRecord`, `ActionOutcome`**: future models for exact proposals, human authorization, revalidation, execution, and audit; unused by MVP Ozon writes.

## 7. Proposed Repository Structure

Only the planning documents exist now. The following is the intended implementation structure; it is not created during architecture planning.

```text
.
|-- app/
|   |-- __init__.py
|   |-- bootstrap.py
|   |-- core/
|   |   |-- config.py
|   |   |-- money.py
|   |   |-- clock.py
|   |   |-- errors.py
|   |   `-- validation.py
|   |-- domain/
|   |   |-- common.py
|   |   |-- catalog.py
|   |   |-- inventory.py
|   |   |-- economics.py
|   |   |-- recommendations.py
|   |   `-- briefs.py
|   |-- providers/
|   |   |-- contracts.py
|   |   |-- mock_ozon.py
|   |   |-- local_unit_economics.py
|   |   |-- local_competitors.py
|   |   `-- mapping.py
|   |-- analytics/
|   |   |-- sales.py
|   |   |-- inventory.py
|   |   |-- unit_economics.py
|   |   `-- pricing.py
|   |-- decisions/
|   |   |-- inventory_rules.py
|   |   |-- pricing_rules.py
|   |   |-- sales_rules.py
|   |   `-- prioritizer.py
|   |-- ai/
|   |   |-- contracts.py
|   |   |-- brief_input.py
|   |   |-- brief_generator.py
|   |   |-- prompts.py
|   |   `-- grounding.py
|   |-- services/
|   |   |-- analysis.py
|   |   |-- pricing.py
|   |   |-- briefs.py
|   |   `-- approvals.py
|   |-- api/
|   |   |-- main.py
|   |   |-- schemas.py
|   |   |-- dependencies.py
|   |   `-- routes/
|   |       |-- analysis.py
|   |       |-- pricing.py
|   |       |-- briefs.py
|   |       `-- health.py
|   `-- ui/
|       |-- presenters.py
|       `-- components.py
|-- tests/
|   |-- unit/
|   |   |-- analytics/
|   |   `-- decisions/
|   |-- contracts/
|   |   `-- providers/
|   |-- ai/
|   |-- integration/
|   |-- api/
|   |-- ui/
|   `-- fixtures/
|-- data/
|   `-- demo/
|       |-- marketplace/
|       |-- unit_economics/
|       `-- competitors/
|-- streamlit_app.py
|-- MASTER_SPEC.md
|-- ARCHITECTURE.md
|-- TASKS.md
|-- DECISIONS.md
`-- AGENTS.md
```

### 7.1 Important Module Responsibilities

- **`app/bootstrap.py`** is the composition root. It loads configuration, chooses concrete providers, builds services, and is the only ordinary module that needs to know both interfaces and concrete adapters.
- **`app/core/config.py`** loads and validates environment/application policy configuration; it contains no business calculation.
- **`app/core/money.py`** owns currency-safe `Decimal` construction, precision, quantization, comparison, and upward safe-price rounding conventions.
- **`app/core/clock.py`** supplies an injectable as-of date/time so tests and analyses are reproducible.
- **`app/core/errors.py`** defines typed application, provider, validation, AI, and future action errors.
- **`app/core/validation.py`** contains reusable boundary-validation helpers; domain-specific invariants remain with domain models.
- **`app/domain/*.py`** define the entities and value objects in Section 6, grouped by business concept rather than source system.
- **`app/providers/contracts.py`** defines the independent marketplace-read, unit-economics, competitor-price, and future action protocols.
- **`app/providers/mock_ozon.py`** maps local marketplace demo records to normalized domain models.
- **`app/providers/local_unit_economics.py`** maps local prices and cost inputs independently of marketplace operations.
- **`app/providers/local_competitors.py`** maps optional comparison data and requires explicit non-Ozon provenance.
- **`app/providers/mapping.py`** holds source-to-domain parsing shared by local adapters, without business rules.
- **`app/analytics/*.py`** implement pure calculations in the four calculation areas and return typed results plus calculation evidence.
- **`app/decisions/*.py`** apply explicit policy rules and create/rank structured recommendations without AI.
- **`app/ai/contracts.py`** defines the LLM/brief port so vendors are replaceable.
- **`app/ai/brief_input.py`** converts one completed `AnalysisSnapshot` into the minimal action-centric `GroundedBriefInput` without provider access, calculation, decision evaluation, or reprioritization.
- **`app/ai/openai_provider.py`** is the only concrete MVP LLM adapter; it implements `BriefModelProvider`, sends canonical grounding through Responses API Structured Outputs, and translates only approved provider failures.
- **`app/ai/brief_generator.py`** coordinates one model invocation with zero application retry/fallback and maps typed outcomes; it has no formulas.
- **`app/ai/prompts.py`** contains versioned Russian interpretation-only instructions and keeps business payloads in the data portion of the request.
- **`app/ai/grounding.py`** enforces exact block-local fact references and the normative claim grammar in `MASTER_SPEC.md` Section 6.5.7, then computes the SHA-256 grounding digest for accepted output.
- **`app/services/analysis.py`** orchestrates provider reads, analytics, decisions, prioritization, and snapshot assembly.
- **`app/services/pricing.py`** runs read-only price scenarios through the shared economics engine.
- **`app/services/briefs.py`** requests and validates an optional brief for an existing analysis snapshot.
- **`app/services/approvals.py`** is reserved for the future action-approval workflow; it must not enable Ozon writes in the MVP.
- **`app/api/*`** define thin FastAPI transport schemas, dependency lookup, routes, and status mapping.
- **`app/ui/presenters.py`** turns service DTOs into display-ready values without changing calculations.
- **`app/ui/components.py`** provides reusable Streamlit render functions.
- **`streamlit_app.py`** controls page layout, filters, session state, and service calls only.
- **`data/demo/*`** will hold clearly labeled local source fixtures for separate provider domains; it contains no executable logic.

Future adapters such as `ozon_api.py`, `google_sheets_unit_economics.py`, `telegram.py`, or `n8n.py` are added only when their integration phase is approved; empty speculative modules are not created in the MVP.

## 8. Dependency Direction

Data flow and source-code dependency are related but not identical.

### 8.1 Runtime Data Flow

```text
providers -> normalized domain data -> analytics -> decisions -> services
                                                           |-> API
                                                           |-> UI
                                                           `-> AI interpretation -> UI/API
```

### 8.2 Allowed Code Dependencies

- `core` depends only on the Python standard library where practical.
- `domain` may depend on `core`, but not on providers, analytics, decisions, services, AI, API, or UI.
- `analytics` depends on `domain` and `core`.
- `decisions` depends on `domain`, `core`, and analytics result types; it does not depend on providers or AI.
- provider contracts depend on domain types; concrete providers depend on those contracts, domain types, and boundary utilities.
- `ai` depends on its contract and the domain brief/fact types, never on raw provider payloads or calculation internals.
- `services` depend on provider/AI abstractions plus domain, analytics, and decisions; they orchestrate but do not reimplement rules.
- `api`, `ui`, and future automation adapters depend on services and transport/presentation models.
- `bootstrap` may depend on all concrete adapters required to assemble the application.

The domain and calculation layers never import FastAPI, Streamlit, an Ozon client, a file parser, a Google Sheets client, an LLM SDK, Telegram, or n8n.

## 9. Validation, Precision, and Provenance

Validation occurs at four explicit boundaries:

1. **Transport/source validation:** providers and API routes validate file/HTTP shape, required source fields, parsing, and basic types before mapping.
2. **Domain validation:** construction enforces universal invariants such as identifiers, units, currencies, allowed rates, non-negative stock, and valid dates.
3. **Use-case/calculation validation:** services and pure calculators verify that the complete input set and policy needed for a particular result are present and mathematically valid.
4. **AI-output validation:** grounding checks ensure the brief cites existing structured inputs and introduces no unsupported facts or decisions.

Per-SKU validation failures should normally become `ValidationIssue` entries so valid SKUs can still be analyzed. A provider-wide failure that makes an analysis meaningless fails that use case explicitly.

All financial inputs become `Decimal` at the source boundary. Float values must not cross into money calculations. `core/money.py` owns currency quantization and the distinct rule that minimum safe prices round upward to the configured price increment. Analytics retain sufficient intermediate precision, and decision comparisons use the same canonical rounded values exposed to users.

Every normalized input and calculated fact records source/provenance metadata. Derived facts reference their source record IDs plus calculation/rule IDs, enabling explanations and future audit without embedding provider payloads in domain logic.

## 10. Main Data Flows

### A. Load Marketplace Demo Data

1. `bootstrap.py` selects `MockOzonProvider` from local configuration.
2. `AnalysisService` requests products, sales observations, inventory, and inbound data through `MarketplaceReadProvider`.
3. The adapter reads `data/demo/marketplace`, validates record shapes, maps fields, and labels provenance as mock/demo.
4. Domain invariants are checked during mapping; invalid records become data-quality issues rather than guessed values.
5. The service receives normalized domain objects and never sees file-specific rows or paths.

Unit-economics and competitor demo files enter separately through their own providers; `MockOzonProvider` must not masquerade as their source.

### B. Calculate Inventory / Stockout Risk

1. `AnalysisService` gathers SKU, sales, inventory, inbound, lead-time, and policy inputs.
2. `analytics/sales.py` calculates average daily sales and comparison metrics for explicit windows.
3. `analytics/inventory.py` calculates coverage, stockout date, safe start date, reorder point, and replenishment quantity.
4. `decisions/inventory_rules.py` classifies critical, due-soon, healthy, overstock-candidate, or insufficient-data status.
5. The result includes values, units, thresholds, rule IDs, and evidence references.

### C. Calculate Unit Economics

1. `AnalysisService` requests cost inputs through `UnitEconomicsProvider`, independently of marketplace data.
2. The provider maps all money to `Decimal` and validates source/provenance.
3. `analytics/unit_economics.py` calculates cost components, profit/contribution, margin, DRR where available, break-even, and minimum safe price.
4. `decisions/pricing_rules.py` assigns safe/unsafe state and an evidence-supported recommendation.
5. Missing required economics produces an insufficient-data result for that SKU without blocking unrelated inventory analysis.

### D. Run Pricing What-If Simulation

1. Streamlit or FastAPI validates the requested SKU and hypothetical price syntax.
2. `PricingService` loads the same normalized economics inputs and policy used by current-price analysis.
3. It constructs a `PricingScenario` marked hypothetical.
4. The shared unit-economics calculator recalculates results; no formula exists in UI/API code.
5. Pricing rules return safe/unsafe status, constraint distance, and reason.
6. The result is read-only and makes no volume, demand, or conversion prediction.

### E. Generate Priority Actions

1. Decision modules convert inventory, sales-change, and economics results into structured recommendations.
2. `decisions/prioritizer.py` assigns a stable rank key from configured severity, urgency, and deterministic tie-break fields.
3. `AnalysisService` returns ordered actions and their complete evidence in `AnalysisSnapshot`.
4. No LLM is called to decide eligibility, severity, action, or order.

### F. Generate Grounded AI Daily Brief

1. `BriefService` receives an existing, completed `AnalysisSnapshot`.
2. `ai/brief_input.py` preserves every priority action in authoritative order and selects only the calculated facts reachable through their evidence references; it excludes service issues and unrelated analytics.
3. When enabled, `bootstrap.py` resolves `OPENAI_API_KEY` and constructs the OpenAI `BriefModelProvider`; missing/invalid local configuration fails setup.
4. The adapter submits canonical grounded data to `gpt-5.6-terra` through the Responses API with strict Structured Outputs and no tools, storage, streaming, retries, or fallback.
5. The provider returns an untrusted `AIBriefDraft` with one ordered item per action.
6. `ai/grounding.py` validates reference closure, item cardinality/order, and block-local verbatim support for numeric/date/time claims, then binds the accepted brief to the canonical input with a SHA-256 digest.
7. A valid brief is labeled AI-generated and returned alongside, never in place of, source facts.
8. Python service orchestration owns `GENERATED`, `DISABLED`, `UNAVAILABLE`, and `INVALID`. Every non-`GENERATED` outcome leaves deterministic results available.

### G. Display Results in Streamlit

1. `streamlit_app.py` obtains configured services from the composition root.
2. It requests a dashboard snapshot or pricing scenario.
3. Presenters format service output for display without deriving new business values.
4. Components render the executive summary, ordered actions, evidence, inventory, economics, scenario results, SKU detail, and optional brief.
5. Demo provenance, missing data, hypothetical inputs, and AI prose are visibly labeled.

### H. Future Migration to `OzonSellerApiProvider`

1. Confirm actual Ozon API access, documentation, permissions, field semantics, and rate limits; none are assumed now.
2. Implement `OzonSellerApiProvider` as a new `MarketplaceReadProvider` adapter with authentication, HTTP mapping, pagination, retries, and source validation.
3. Run the shared provider contract suite plus adapter integration tests against an approved test environment.
4. Select the adapter in `bootstrap.py` through configuration.
5. Keep domain, analytics, decisions, services, API, and UI unchanged.

The adapter must not imply that competitor prices or unit costs come from Ozon unless verified source capabilities explicitly provide them and product decisions are updated. Separate provider contracts remain the default boundary.

### I. Future Approved Write Action to Ozon

1. Deterministic rules create an `ActionProposal`; an LLM may explain it but cannot create authoritative target values or authorize it.
2. The UI shows the exact account/SKU, current value, proposed value, evidence, warnings, expiry, and proposal version/hash.
3. A human explicitly approves that single proposal.
4. The approval service reloads relevant state, reruns validation and safety rules, and rejects stale or changed proposals.
5. Only then may a separately configured `MarketplaceActionProvider` execute the exact approved mutation.
6. The system records approver, proposal, timestamps, request, provider response, and confirmed/failed/unknown outcome.
7. Failures never appear as success, and retry behavior must avoid duplicate mutations.

This flow is an architectural boundary only. No action provider, write route, or real Ozon mutation is part of the MVP.

## 11. Error Boundaries and Degradation

| Condition | Owning boundary | Required behavior |
|---|---|---|
| Invalid source data | Provider mapping/domain validation | Reject or quarantine the affected record, emit a specific `ValidationIssue`, continue only where results remain valid, and never substitute an LLM guess. |
| Missing unit economics | Unit-economics provider/service | Mark economics and dependent pricing decisions `insufficient_data`; inventory analysis remains usable. |
| Zero sales | Sales/inventory analytics | Return average sales of zero; do not divide by zero or invent stockout/reorder dates. Return documented not-applicable statuses and allow a configured no-demand/overstock flag if supported. |
| Negative stock | Provider/domain boundary | Treat as invalid source data; do not coerce to zero or issue a normal risk calculation. |
| Missing lead time | Inventory use-case validation | Preserve available stock/sales facts but mark stockout-action timing and replenishment decision `insufficient_data`. |
| AI connection failure, timeout, rate limit, or transient/server-side 5xx | OpenAI adapter/brief service | Raise typed `AIUnavailableError`; return `UNAVAILABLE` with no brief and keep deterministic results usable. MVP performs no application-level retry. |
| AI structured-output/schema failure, refusal/non-draft response, malformed completed output, or grounding failure | OpenAI adapter/brief service | Reject the output; return `INVALID` with no brief and keep deterministic results usable. |
| Missing/invalid AI secret or local authentication configuration | Startup/composition root | Raise `ConfigurationError` and fail setup when AI is enabled; never classify local misconfiguration as `UNAVAILABLE`. |
| Unexpected AI-path programming error | Owning code boundary | Propagate; do not mask an invariant or programming defect as graceful provider degradation. |
| Future unavailable Ozon API | Ozon adapter/service | Return a typed provider-unavailable state, expose freshness, and do not report stale/partial data as current unless a future cache policy explicitly labels it. |
| One invalid SKU among valid SKUs | Analysis service | Include the issue and continue valid independent SKU analyses; do not mark the bad SKU healthy. |
| Invalid global policy/configuration | Startup/composition root | Fail startup or the affected use case clearly because all dependent results would be unreliable. |
| Future action response unknown | Action adapter/approval service | Record `unknown`, do not claim success, and require reconciliation before retry. |

Technical logs may contain correlation and rule IDs but must not expose credentials or sensitive provider payloads. User-facing errors should say what is unavailable and which result is affected.

## 12. Future Integration Boundaries

### 12.1 Ozon Seller API

Add a read adapter implementing `MarketplaceReadProvider` only after access and actual contracts are known. Authentication, request schemas, pagination, rate limits, retries, and Ozon-specific mapping stay inside the adapter. Any later writes use a separate `MarketplaceActionProvider` and approval workflow.

### 12.2 Google Sheets

A Google Sheets adapter may implement `UnitEconomicsProvider` and, if explicitly designed, other narrow source contracts. Sheet column mapping, revision/freshness handling, and authentication stay in the adapter. Calculations do not move into spreadsheet formulas, and switching from local files must not change analytics.

### 12.3 Competitor Data

Competitor data always enters through `CompetitorPriceProvider`, whether sourced from demo files, manual uploads, or a future licensed external feed. Results must display source type and observation time. The Ozon read provider is not used as a proxy for unverified competitor data.

### 12.4 Telegram

A future notifier consumes completed snapshots or validated briefs and handles message formatting/delivery. It cannot calculate, reprioritize, or execute marketplace actions. Because sending is an external side effect, enabling a send requires an explicit user-approved workflow and auditable target.

### 12.5 n8n

n8n may schedule or orchestrate calls to stable service/API use cases. It cannot become the source of business rules, bypass approval, or call an Ozon write adapter directly. Inputs and outputs remain versioned schemas, and external side effects retain the same approval boundary.

## 13. Configuration, State, and Runtime

- Business policy configuration is loaded once, validated, and assigned an identity/version recorded in analysis snapshots.
- The as-of clock is injected so the same dataset and configuration reproduce the same result.
- MVP demo source data lives in local structured files under `data/demo`; formats are selected during implementation and validated at ingestion.
- Results may remain in memory for the MVP. A database server is not justified unless later requirements add durable multi-user state, caching, or action audit records.
- Streamlit and FastAPI reuse the same bootstrap and services. They may be launched independently for development/demo convenience without splitting the system into microservices.
- Secrets are unnecessary for mock-only deterministic operation or intentionally disabled AI. MVP AI uses only `OPENAI_API_KEY`, resolved at bootstrap/concrete-adapter composition and never stored in grounded/domain/result objects or exposed through safe errors/logs. The configurable model identifier defaults exactly to `gpt-5.6-terra` and is never read from grounded business content.

## 14. Test Architecture

### 14.1 Calculation Unit Tests

Test pure analytics directly with fixed dates and `Decimal` inputs. Cover normal cases plus zero sales, zero stock, rounding edges, denominator failures, missing inputs, invalid rates, boundary equality, late reorder dates, inbound timing, pack sizes, and conflicting profitability constraints.

### 14.2 Decision-Rule Tests

Test each rule independently and test precedence when multiple alerts apply. Assert rule IDs, evidence, exact severity, recommendation eligibility, and stable priority tie-breaking.

### 14.3 Provider Contract Tests

Define reusable tests for normalized types, provenance, missing-record behavior, validation errors, and deterministic reads. Run them against `MockOzonProvider`, local economics/competitor adapters, and every future replacement provider.

### 14.4 AI Grounding and Input Tests

Use fixed structured fixtures to prove that raw provider payloads and service issues are excluded, only the priority-action evidence closure is serialized, references resolve, action order/cardinality are exact, and invented numbers, dates, SKUs, statuses, or recommendations are rejected. Test exact local-reference enforcement; identifier exclusions; non-ASCII digits; grouped, signed, percentage, and scientific numeric claims; ISO/reformatted/relative date-time claims; empty input; digest stability; all four result invariants; disabled/configuration/unavailable/invalid mappings; and zero retry/fallback without requiring live model access in the default suite.

### 14.5 Integration Tests

Exercise the main analysis, pricing simulation, priority generation, brief degradation, and API serialization flows with deterministic providers/fakes. Include a provider-swap test demonstrating that the same service use case and expected domain behavior work through another contract-conforming adapter.

### 14.6 UI Tests

Keep UI testing small: verify application startup, main section rendering, safe/unsafe scenario display, provenance labels, missing-data states, and LLM-unavailable behavior. Business-value assertions belong to analytics and decision tests, not duplicated Streamlit calculations.

## 15. Deliberate MVP Omissions

The architecture intentionally excludes microservices, distributed queues, event buses, message brokers, a database server, containers-orchestration design, live marketplace writes, automatic repricing, background automation, and speculative integration clients. These components may be reconsidered only when a concrete requirement justifies their operational cost.
