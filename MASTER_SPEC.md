# Ozon AI Decision Copilot — Master Specification

## 1. Document Purpose

This document defines the product scope and acceptance criteria for the first portfolio MVP of **Ozon AI Decision Copilot**. It is the product-level source of truth for what the MVP must and must not do. Detailed component design belongs in `ARCHITECTURE.md`; implementation sequencing belongs in `TASKS.md`; durable design choices belong in `DECISIONS.md`.

## 2. Product Definition

Ozon AI Decision Copilot is an AI-assisted decision-support system for an Ozon marketplace business. It analyzes operational data, inventory, sales, unit economics, supply lead times, and pricing constraints; detects risks and opportunities; calculates safe recommendations deterministically; and tells a manager what requires attention today.

The MVP is inspired by the operating needs of an Ozon-focused business, but its business logic must remain reusable for other marketplace sellers. Marketplace-specific data access must be isolated behind replaceable provider interfaces.

### 2.1 Core Principle

> Python calculates. AI interprets and explains. Human approves actions.

- Deterministic Python logic is the sole source of truth for all financial values, operational metrics, classifications, thresholds, and final eligibility rules.
- The LLM may summarize, explain, and prioritize facts already calculated by Python and may produce a management brief from those facts.
- The LLM must not calculate, infer, correct, or invent business-critical values.
- No external write or operational action may occur without explicit human approval.
- The first MVP is read-only and performs no real Ozon write operations.

## 3. Product Goals

The MVP must:

1. Give a marketplace manager a concise, evidence-based view of what needs attention today.
2. Detect inventory and replenishment risk using stock, sales velocity, lead times, and a safety buffer.
3. Calculate transparent unit economics and enforce configurable profitability constraints.
4. Let a manager compare hypothetical prices without claiming unsupported demand effects.
5. Convert structured, calculated facts into a grounded AI Daily Brief.
6. Demonstrate a safe combination of deterministic analytics, generative AI, and human control.
7. Run as a convincing portfolio demo without Ozon credentials or live integrations.
8. Allow the mock data provider to be replaced later without rewriting analytics or business rules.

## 4. Users and Primary Jobs

### 4.1 Primary User

The primary user is a marketplace owner, commercial manager, or operations manager responsible for inventory availability, replenishment timing, pricing, and SKU profitability.

### 4.2 Primary Jobs to Be Done

- See the most urgent risks and opportunities without reviewing every SKU manually.
- Know when replenishment or production must start to avoid a likely stockout.
- Understand why a SKU was classified as risky.
- See whether a current or proposed price satisfies profitability rules.
- Compare price scenarios using consistent unit-economics calculations.
- Read a short daily management brief grounded in current calculated facts.
- Inspect the evidence and assumptions behind every recommendation.

## 5. Operating Constraints and Assumptions

### 5.1 Data Availability

- Real Ozon Seller API access is not available and must not be assumed.
- The MVP must load realistic demo data through a `MockOzonProvider`.
- The future `OzonSellerApiProvider` must be replaceable at the provider boundary without changes to analytics or business logic.
- Unit-economics inputs initially come from local, structured demo data.
- A future Google Sheets source must be possible, but is outside the first MVP.
- Competitor pricing is not assumed to be available from Ozon Seller API.
- MVP competitor prices, when present, must be identified as mock, manual, or imported structured data. The product must never imply that such data came from Ozon Seller API.

### 5.2 Configuration

Business policies must be explicit configuration rather than hidden constants. At minimum, configuration must support:

- calculation as-of date;
- sales averaging window;
- production or procurement lead time;
- delivery lead time;
- safety-buffer days;
- target post-arrival stock coverage;
- minimum profit per unit;
- minimum contribution-margin percentage;
- commission rates or per-unit commission inputs;
- logistics, advertising, and other variable-cost inputs;
- risk and material-change thresholds;
- monetary and quantity rounding rules.

Configuration values used in a result must be available to its explanation.

## 6. MVP Scope

### 6.0 Analysis Selection Contract

The analysis service uses the following deterministic SKU-selection semantics:

- `analyze(None)` analyzes every catalog SKU whose existing product `active` state is `true`; inactive products are excluded from this default operational view.
- `analyze(())` is a valid explicit empty request and returns an empty `AnalysisSnapshot` with no SKU results, recommendations, priority actions, or filler issues.
- A non-empty explicit selection analyzes exactly the requested known SKUs. Explicit selection includes an inactive SKU rather than applying the default active-only filter.
- An explicitly requested SKU that is absent from the catalog is rejected.
- Duplicate explicitly requested SKUs are rejected rather than silently deduplicated.

Default active filtering is an application-orchestration selection rule. Providers expose normalized catalog state, while analytics, decisions, and prioritization remain independent of product-selection policy.

### 6.1 Inventory and Supply Advisor

For each SKU, the system must deterministically calculate or return an explicit unavailable status for:

- current sellable stock;
- average daily sales;
- estimated stock coverage in days;
- estimated stockout date;
- production or procurement lead time;
- delivery lead time;
- configured safety buffer;
- latest safe reorder or production-start date;
- stockout risk level;
- recommended replenishment quantity.

The module must answer more than “is stock low?” It must determine whether action needs to begin now based on the time required to produce or procure and deliver stock.

#### 6.1.1 Deterministic Inventory Definitions

All calculations use a single explicit `as_of_date` and normalized daily sales history.

- **Average daily sales** = units sold during the configured observation window / number of observed calendar days in that window.
- **Stock coverage days** = current sellable stock / average daily sales.
- **Supply lead time** = production or procurement lead days + delivery lead days.
- **Required coverage horizon** = supply lead days + safety-buffer days.
- **Estimated stockout date** = `as_of_date` + stock coverage days, using one documented date-rounding convention consistently.
- **Latest safe start date** = estimated stockout date - supply lead days - safety-buffer days.
- **Reorder point units** = average daily sales × required coverage horizon, rounded upward to a whole sellable unit.
- **Target stock units** = average daily sales × (required coverage horizon + configured target post-arrival coverage days), rounded upward.
- **Base replenishment quantity** = max(0, target stock units - current sellable stock - eligible confirmed inbound units).
- **Recommended replenishment quantity** = base quantity adjusted deterministically for any configured minimum order quantity or pack-size multiple.

Confirmed inbound stock may reduce a recommendation only when its quantity and expected arrival date are present and it is expected to arrive in time under the documented rule. Missing or unconfirmed inbound stock must not be treated as available.

If average daily sales is zero or required input is missing, the system must not divide by zero or invent a date. It must return a documented status such as `not_applicable` or `insufficient_data`, preserve the known facts, and explain which input prevents the calculation.

#### 6.1.2 Inventory Risk Classification

Risk levels must be generated by explicit, testable rules. The exact configurable thresholds must be recorded with each result. The rule set must distinguish at least:

- **Critical / already late:** the latest safe start date is before or on the as-of date, or stock is already zero while demand is positive.
- **Reorder due soon:** the latest safe start date falls within a configured warning window.
- **Healthy:** available coverage exceeds the action horizon and no stronger risk rule applies.
- **Overstock candidate:** coverage exceeds a configured overstock threshold; this is an attention flag, not proof that stock is economically excessive.
- **Insufficient data:** a required value is missing or invalid.

Each alert must include the SKU, classification, calculated evidence, relevant configuration, recommended action, and a plain-language deterministic reason.

### 6.2 Unit Economics and Pricing Advisor

For each SKU and evaluated selling price, Python must calculate:

- selling price;
- cost of goods;
- marketplace commission;
- logistics cost;
- advertising cost and DRR when source data is available;
- other configured variable expenses;
- total variable cost;
- profit or contribution per unit;
- contribution-margin percentage;
- configured break-even and safe-price boundaries;
- minimum safe selling price;
- safe or unsafe status;
- any permitted pricing recommendation and its calculation-based reason.

#### 6.2.1 Deterministic Economics Definitions

Inputs must be normalized into per-unit fixed amounts and price-proportional rates before calculation.

- **Commission** = selling price × commission rate, unless an explicit per-unit amount is the configured source.
- **Advertising cost per unit** = selling price × DRR when DRR is available and selected by policy; otherwise it must use an explicit per-unit input or be marked unavailable according to configuration.
- **Total variable cost** = cost of goods + commission + logistics + advertising + other configured variable expenses.
- **Profit per unit / contribution per unit** = selling price - total variable cost.
- **Contribution-margin percentage** = profit per unit / selling price × 100.
- **DRR** = advertising spend / attributable revenue × 100 for the documented period; it is unavailable when the denominator or source data is not valid.
- **Break-even price** = the lowest price at which calculated profit per unit is not negative under the configured cost model.
- **Minimum safe price** = the lowest permitted price satisfying every configured absolute-profit, margin, and price-floor rule.

For a normalized model with fixed per-unit costs `F`, total price-proportional rate `R`, minimum profit `P`, and minimum margin fraction `M`, the candidate constraints are:

- absolute-profit candidate = `(F + P) / (1 - R)`;
- margin candidate = `F / (1 - R - M)`.

The minimum safe price is the maximum of all applicable candidates and configured price floors, rounded upward to the configured price increment. Invalid denominators, missing required costs, negative inputs where forbidden, or rates outside configured valid ranges must produce a validation error rather than a misleading result.

Money calculations must use decimal arithmetic and documented rounding. Intermediate values must retain sufficient precision; values used for decisions and values displayed to users must follow the same documented rounding policy.

#### 6.2.2 Pricing Recommendation Rules

The allowed recommendation labels are:

- keep price;
- consider increasing price;
- consider decreasing price;
- do not decrease below the safe threshold;
- insufficient data.

A recommendation must cite its calculated evidence and the policy rule that produced it. No recommended or user-tested price may be labeled safe when it falls below the minimum safe price or violates another configured profitability rule.

A price-increase or price-decrease opportunity must have explicit evidence, such as a configured target, a current profitability violation, or properly labeled mock/manual/imported comparison data. The system must not infer demand elasticity, future volume, conversion, or revenue response merely from a price change.

### 6.3 Pricing Decision / What-If Simulator

The manager must be able to enter one or more hypothetical prices for a selected SKU. For each scenario, the system must use the same deterministic unit-economics engine as the main analysis and return:

- tested price;
- profit per unit;
- contribution-margin percentage;
- applicable profitability constraints;
- distance from the minimum safe price;
- safe or unsafe status;
- a calculation-based explanation.

The simulator must not modify source data or execute a price change. It must clearly state that results assume the supplied cost and rate inputs remain constant unless a different supported cost scenario is explicitly provided. It must not predict future sales volume from price changes unless a separate validated elasticity model is added after the MVP.

### 6.4 Structured Alerts and Priority Action Center

Python analytics must generate structured alerts in at least these categories:

- critical stock risk;
- reorder required or due soon;
- profitability risk;
- unusual sales decline or increase;
- pricing opportunity;
- insufficient or invalid data.

Each alert must contain:

- stable alert identifier;
- SKU identifier and display name when available;
- category and deterministic severity;
- what happened;
- calculated evidence with units and periods;
- rule and threshold that triggered it;
- recommended next action;
- calculation-based reason;
- data timestamp and provenance;
- status or confidence descriptor where appropriate.

Priority ordering must be deterministic and testable. It must be based on configured severity, urgency, and stable tie-break rules—not on free-form LLM judgment. The UI must make the underlying evidence inspectable.

#### 6.4.1 Approved MVP Priority Policy

MVP priority is categorical. Severity is ordered `critical`, `high`, `warning`, then `info`; urgency is ordered `immediate`, `soon`, then `monitor`. Severity is the primary ordering factor and urgency is secondary. The MVP uses no weighted, normalized, forecast-based, or other numeric business score.

The following production recommendation rules have approved priority assignments:

| Rule ID | Severity | Urgency | Equal-level rule precedence |
|---|---|---|---:|
| `inventory.out_of_stock` | `critical` | `immediate` | 1 |
| `inventory.replenishment_already_late` | `critical` | `immediate` | 2 |
| `profitability.no_finite_safe_price` | `critical` | `immediate` | 3 |
| `profitability.loss_making` | `critical` | `immediate` | 4 |
| `inventory.replenishment_due_now` | `high` | `immediate` | 5 |
| `pricing.current_price_unsafe` | `high` | `immediate` | 6 |
| `inventory.replenishment_due_soon` | `high` | `soon` | 7 |
| `profitability.below_minimum_profit` | `high` | `soon` | 8 |
| `profitability.below_minimum_margin` | `high` | `soon` | 9 |
| `pricing.scenario_below_safe_price` | `high` | `soon` | 10 |
| `economics.insufficient_data` | `warning` | `soon` | 11 |
| `inventory.missing_lead_time` | `warning` | `soon` | 12 |
| `sales.material_decline` | `warning` | `soon` | 13 |
| `inventory.overstock_candidate` | `warning` | `monitor` | 14 |
| `inventory.insufficient_sales_history` | `warning` | `monitor` | 15 |
| `sales.insufficient_history` | `warning` | `monitor` | 16 |
| `pricing.scenario_insufficient_data` | `warning` | `monitor` | 17 |
| `sales.material_increase` | `info` | `monitor` | 18 |

Actions are ordered by severity, urgency, equal-level rule precedence, SKU ascending, and recommendation ID ascending, in that sequence. Rank is one-based. Input order must not affect output.

Every valid recommendation becomes one individual priority action. Prioritization does not group, merge, suppress, or remove recommendations, and the Action Center has no top-N cap. Duplicate recommendation IDs are invalid and must be rejected rather than deduplicated. All combined decision evaluations must share one analysis timestamp. An unmapped rule must fail closed instead of receiving a default priority.

All 18 production recommendation rules emitted by MVP-011 have approved priority assignments. Any future production rule not listed above remains unmapped and must fail closed until the product owner explicitly approves its severity, urgency, and equal-level precedence.

### 6.5 AI Daily Brief

Python analytics must first produce a structured, validated collection of facts, alerts, priorities, and metadata. Only that collection may be passed to the LLM to create a brief.

The AI Daily Brief may:

- summarize calculated conditions;
- explain why existing recommendations were generated;
- present already-ranked priorities in concise management language;
- call out missing data or uncertainty already identified by Python.

The AI Daily Brief must not:

- introduce a number, SKU fact, cause, priority, or recommendation absent from its structured input;
- perform or revise financial or operational calculations;
- change deterministic severity or eligibility decisions;
- claim that an action was executed;
- conceal an insufficient-data state;
- present competitor-data provenance inaccurately.

The brief must remain optional: deterministic analytics, alerts, and the UI must still work when the LLM is unavailable. Generated text must be visibly labeled as AI-generated explanation. The system should retain enough linkage between brief statements and source alert identifiers to support grounding checks and user inspection.

#### 6.5.1 Grounded Input Boundary

The only authoritative source for AI input is one completed `AnalysisSnapshot`. The grounded-input builder must not call providers, read source files, rerun analytics or decision rules, rerun the prioritizer, call a clock, access a network, or call an AI model.

The MVP brief is action-centric. `GroundedBriefInput` contains exactly:

- the source snapshot's timezone-aware `analysis_timestamp`;
- every authoritative `PriorityAction`, in its existing order, represented as an immutable grounded action;
- the deterministic evidence closure of those actions, represented as an immutable tuple of grounded facts ordered by authoritative fact ID.

The MVP contract does not include selected-but-actionless SKUs, arbitrary snapshot summaries or counts, unrelated analytical fields, or `AnalysisSnapshot` service issues. A healthy or empty snapshot therefore produces `actions = ()` and `facts = ()`; it does not produce a filler monitoring item or an unsupported health statement. All actions are retained, with no grouping, merging, suppression, or top-N cap.

The only allowed non-fact metadata is:

- `analysis_timestamp` at snapshot level;
- `sku` and `product_name` copied from the matching snapshot result, with `product_name = null` when unavailable;
- recommendation ID, rule ID, recommendation category and status;
- priority rank, severity, and urgency;
- referenced fact IDs and the source/evidence reference IDs already attached to those facts.

Provider implementation details, credentials, secret references or values, authorization data, file paths, raw provider payloads, raw spreadsheet/API rows, configuration secrets, and arbitrary service internals are forbidden.

#### 6.5.2 Grounded Actions and Facts

A grounded action has these fields and no additional business fields:

- `action_ref`;
- `recommendation_id`;
- `sku`;
- `product_name`;
- `rule_id`, copied from the authoritative recommendation rule code;
- recommendation `category` and `status`;
- priority `rank`, `severity`, and `urgency`;
- `fact_refs`.

`action_ref` is exactly equal to the source recommendation ID and no separate action ID is generated. One recommendation maps to one priority action, so UUIDs, hashes, ranks, timestamps, and synthetic values must not be used as action identity. Action `fact_refs` are the source recommendation's evidence references and must resolve only to facts in the same grounded input.

AI-visible facts are only authoritative `CalculatedFact` objects reachable through evidence references of the included priority actions. Each grounded fact reuses the authoritative `fact_id` and preserves the existing fact fields needed for meaning and traceability: `sku`, `name`, `value`, `value_type`, `unit`, `period`, `formula_or_rule_id`, and `source_refs`. No parallel metric taxonomy or synthetic grounding ID is introduced.

The stable `value_type` values are `decimal`, `integer`, `text`, `boolean`, `date`, and `datetime`, selected from the existing authoritative fact value type. A fact ID appears once in the grounded fact tuple. Repeated resolution of an identical fact is deduplicated; the same ID resolving to conflicting fact content is invalid. Fact order is ascending by `fact_id` and must not depend on set, dictionary, traversal, or input order.

Unavailable values remain unavailable. The builder does not create a fact for an absent metric and does not substitute zero, false, estimates, averages, or placeholder numbers. When a deterministic availability condition is itself evidence, its existing status fact is copied exactly. Thus missing COGS does not create profit, margin, or safe-price facts; insufficient sales does not create ADS, stockout, or replenishment facts; and an unavailable finite safe price is never replaced by a fabricated price.

#### 6.5.3 Canonical Serialization

Grounded input must have a deterministic, standard-library-compatible JSON representation. Serialization follows these rules without changing the immutable authoritative values held by the domain model:

- `Decimal` becomes a canonical fixed-point decimal string using its exact exponent and value, never a binary float; for example, `Decimal("1233.00")` becomes `"1233.00"` and `Decimal("-649.70")` becomes `"-649.70"`;
- an integer remains an exact JSON integer in base-10 form and a boolean remains a JSON boolean;
- `date` becomes `YYYY-MM-DD`;
- `datetime` becomes ISO 8601 with an explicit timezone offset; naive datetimes are invalid;
- enum-like values use their exact stable project-contract value rather than Python representation or a localized display label;
- absent optional values become JSON `null`.

The source snapshot timestamp is copied directly; the builder must not create a second time. Repeated construction and serialization from the same snapshot must be identical.

#### 6.5.4 Structured AI Draft, Validated Brief, and Result

The replaceable AI provider receives `GroundedBriefInput` or its canonical serialization, never `AnalysisSnapshot`, provider objects, or credentials. It returns an untrusted structured `AIBriefDraft` containing:

- `summary`: immutable `text`, `action_refs`, and `fact_refs`;
- `items`: an immutable ordered tuple where each item contains `action_ref`, `text`, and `fact_refs`.

The draft contains exactly one item for each grounded action, with no missing, duplicate, merged, suppressed, or invented item. Item order and `action_ref` must match `GroundedBriefInput.actions` position for position. An item's fact references must be a subset of that action's approved `fact_refs`. Summary action references may use any grounded action and summary fact references may use any grounded fact, but all references must exist in the same grounded input.

Free-form text is non-authoritative. The model draft contains no generation or validation status and cannot declare itself generated, valid, invalid, disabled, or unavailable.

`DailyBrief` is immutable, exists only after complete validation succeeds, and contains exactly:

- `analysis_timestamp`: the exact timezone-aware timestamp copied from `GroundedBriefInput`;
- `grounding_digest`: the lowercase hexadecimal SHA-256 digest of the exact canonical `GroundedBriefInput` JSON serialization encoded as UTF-8;
- `summary`: the successfully validated `AIBriefSummary` from the draft, unchanged;
- `items`: the successfully validated ordered immutable tuple of `AIBriefItem` values from the draft, unchanged.

The grounding digest has no random component. The same canonical grounded input produces the same digest. It binds the brief to the exact validated context but is not a fact, recommendation, action, or business value identifier. No UUID or other generated brief identity is required.

`DailyBrief` does not duplicate authoritative rank, severity, urgency, SKU business state, profit, margin, safe price, stock, or recommendation truth. Its summary and items retain their exact validated text and structured references to Python-owned grounded actions and facts. Python must not repair, rewrite, or enrich accepted prose.

`BriefGenerationResult` is immutable and contains exactly `status` and `brief`; it contains no provider response, raw error message, stack trace, or optional reason. The exact Python-owned generation status values and invariants are:

- `GENERATED`: `brief` is a validated `DailyBrief`;
- `DISABLED`: generation is intentionally disabled, the provider is not called, and `brief` is null;
- `UNAVAILABLE`: the configured provider was unavailable and `brief` is null;
- `INVALID`: returned output failed structural parsing or grounding validation and `brief` is null.

The model and provider never supply this status. Every non-`GENERATED` outcome leaves the deterministic `AnalysisSnapshot` fully usable.

#### 6.5.5 Minimum Grounding Validation

MVP-015 must, at minimum, validate all of the following before constructing a `DailyBrief`:

1. The strict output schema parses.
2. Every action reference exists in the grounded input.
3. Draft action references contain no duplicates.
4. No grounded action item is missing.
5. There is exactly one item per grounded action.
6. Item order matches authoritative action order.
7. Every fact reference exists in the grounded input.
8. Each action item's fact references belong to that grounded action.
9. Every summary reference exists in the grounded input.
10. No unknown SKU, recommendation, or action identity enters through structured fields.
11. Unsupported numeric claims are rejected.
12. Unsupported date/time claims are rejected.
13. Empty grounded input cannot produce action items.
14. A validated brief preserves the source analysis timestamp and grounding identity.
15. Provider/model-reported status is ignored and never trusted.

Any numeric or date/time value in a text block must be copied verbatim from the canonical serialized value of a fact referenced by that same block. Action-item text may use only values from that item's fact references; summary text may use only values from the summary's fact references. The model must not calculate, transform units, convert fractions to percentages, round, or derive new numbers. The validator rejects unsupported numeric and date/time claims.

#### 6.5.6 Concrete OpenAI Runtime Contract

MVP-015 uses one concrete adapter implementing the existing `BriefModelProvider`; no second AI-provider abstraction is introduced. The adapter uses:

- provider: OpenAI;
- API: Responses API;
- model: `gpt-5.6-terra`;
- official Python `openai` SDK, added only as an MVP-015 runtime dependency;
- Responses API Structured Outputs with a strict JSON Schema matching the existing `AIBriefDraft` shape;
- reasoning effort `low`;
- no tools, web search, file search, function calls, or background mode;
- `store = false` and `streaming = false`;
- zero application-level retries, no fallback model, and no second provider or deterministic prose fallback.

The model converts already-grounded deterministic actions and facts into concise Russian management prose only. It does not calculate business facts, retrieve external information, browse, call tools, modify priority, or perform marketplace writes. The model identifier may be application configuration, with the exact MVP default `gpt-5.6-terra`; it never comes from grounded business content. Changing it is an explicit runtime configuration decision, not model output.

The adapter receives only `GroundedBriefInput` or its exact canonical serialization. It never receives `AnalysisSnapshot`, provider objects, raw marketplace payloads, service issues, credentials, or secrets in grounded content. The grounded payload is supplied as untrusted data, not as system/developer instructions.

The approved secret is `OPENAI_API_KEY`. Its value is resolved and owned only at the bootstrap/concrete-adapter composition boundary and is passed to the concrete adapter through construction. `GroundedBriefInput`, `AIBriefDraft`, `DailyBrief`, `BriefGenerationResult`, safe business errors, and logs must never contain it. Mock-only operation and intentionally disabled AI require no secret. If AI is enabled while `OPENAI_API_KEY` is missing or invalid, bootstrap/provider configuration fails with a `ConfigurationError`; this is not `UNAVAILABLE`.

The adapter and generation service apply this narrow failure mapping:

- connection failure, request timeout, rate-limit response, or transient/server-side OpenAI 5xx failure becomes typed `AIUnavailableError`, which orchestration maps to `UNAVAILABLE` with `brief = null`;
- authentication or configuration failure caused by missing or invalid local configuration becomes `ConfigurationError` and propagates or fails setup;
- a successful provider response with schema failure, refusal/non-draft content, malformed completed output, or otherwise unusable returned content becomes `INVALID` with `brief = null`;
- grounding-validation failure becomes `INVALID` with `brief = null`;
- unexpected programming errors propagate.

Raw provider exception text is never exposed in `BriefGenerationResult`.

#### 6.5.7 Free-Form Claim Validation Grammar

The lexical validator examines only `summary.text` and each `item.text`; it never scans structured `action_ref` or `fact_ref` values as prose. Validation is deterministic, ASCII/canonical-oriented, and block-local. For each text block, Python must:

1. determine that block's locally allowed fact IDs;
2. collect canonical numeric, date, and datetime strings from those referenced facts;
3. identify and validate date/datetime claims;
4. identify and reject relative date/time claims;
5. identify and validate remaining numeric claims;
6. reject any unsupported claim.

Date and datetime spans are processed before numeric claims; their component digits are not scanned separately. Validation performs no arithmetic, semantic-equivalence comparison, normalization, rounding, unit conversion, percentage conversion, or date calculation.

For item text, only that item's `fact_refs` authorize claim values. For summary text, only the summary's `fact_refs` authorize claim values. A fact elsewhere in `GroundedBriefInput` is insufficient.

##### 6.5.7.1 Identifier and Digit Handling

A contiguous token is identifier-like, and therefore excluded from numeric-claim scanning, only when it contains at least one ASCII letter and one ASCII digit and otherwise contains only ASCII letters, digits, `_`, or `-`. Examples include `DEMO-015`, `SKU123`, `S24`, and `Model_X5`. In `Model 15`, `15` remains a standalone numeric claim.

Any non-ASCII Unicode decimal digit in generated prose makes the draft `INVALID`; it is not Unicode-normalized into an accepted number.

##### 6.5.7.2 Numeric Claims

After excluding recognized date/datetime spans and identifier-like tokens, the validator detects numeric-like tokens with an optional leading `+` or `-`, one or more ASCII digits, and any supported lexical extension: dot decimal, comma decimal, comma grouping, space grouping, scientific notation using `e` or `E` with optional sign, or a trailing `%`. It must therefore detect at least `1233`, `-649.70`, `+5`, `1233.0`, `1,233.00`, `1 233,00`, `0.20`, `20%`, `1e3`, and `1.2E-4` as complete numeric claims.

A numeric claim is supported only when its exact token text equals the canonical numeric value of a fact referenced by that same block. There is no normalization or mathematical equivalence. Thus canonical `1233.00` supports only `1233.00`, not `1233`, `1233.0`, or `1,233.00`; canonical `0.20` does not support `20%`; canonical `-649.70` supports `-649.70`. Canonical positive Decimal and integer values do not acquire a plus sign, so `+5` is invalid when the canonical value is `5`. Scientific notation is detected but normally invalid because canonical MVP Decimal and integer serialization is fixed-point. A percent sign is part of the complete claim; the validator never derives a percentage. A future explicit percent representation would require a separately approved value-type contract.

##### 6.5.7.3 Date and Time Claims

A calendar-date claim uses canonical `YYYY-MM-DD` syntax and is supported only when the same block references a fact whose canonical date value is that exact complete string.

A datetime claim uses the exact ISO 8601 form emitted by MVP-014 canonical serialization, including an explicit timezone and any canonical fractional seconds, for example `YYYY-MM-DDTHH:MM:SS+HH:MM`. `Z` is supported only when the canonical fact actually uses `Z`; `Z` and `+00:00` are never converted or treated as equivalent.

Numeric date-like forms with analogous digit groups separated by dots or slashes, including `11.09.2026` and `11/09/2026`, are detected as date claims and normally rejected because canonical MVP dates use ISO hyphens. The validator does not infer component order.

Standalone clock-time claims `HH:MM` and `HH:MM:SS`, with an optional timezone suffix, are detected. A time-only claim is valid only if an exact canonical referenced fact independently represents that time. A time must not be extracted from a referenced datetime and treated as separately grounded.

##### 6.5.7.4 Relative Date and Time Claims

The following standalone English words and phrases are rejected case-insensitively: `today`, `tomorrow`, `yesterday`, `tonight`; `next week`, `next month`, `next year`; `last week`, `last month`, `last year`; `in <ASCII integer> day(s)`, `week(s)`, `month(s)`, or `year(s)`; and `next Monday`, `next Tuesday`, `next Wednesday`, `next Thursday`, `next Friday`, `next Saturday`, or `next Sunday`.

The following standalone Russian words and phrases are also rejected case-insensitively:

- `сегодня`, `завтра`, `вчера`, `послезавтра`, `позавчера`;
- `на следующей неделе`, `в следующем месяце`, `в следующем году`;
- `через <ASCII integer> день/дня/дней`, `неделю/недели/недель`, `месяц/месяца/месяцев`, or `год/года/лет`;
- `следующий понедельник`, `следующий вторник`, `следующую среду`, `следующий четверг`, `следующую пятницу`, `следующую субботу`, or `следующее воскресенье`.

Any detected relative temporal phrase makes the draft `INVALID`. The validator never resolves it against `analysis_timestamp`, even when an equivalent canonical date is referenced; for example, `через 4 дня` is invalid even if `2026-09-11` is a referenced fact.

#### 6.5.8 Provider Instructions and Empty Input

The versioned static provider instructions must require concise Russian business prose; exactly one item per supplied action; original action order; only supplied action and fact references; strict schema-only output; and no invented facts/actions, calculation, rounding, number-format change, percentage or unit conversion, date reformatting, or relative date/time wording. Referenced numeric/date values must be copied verbatim. Structured enum, identifier, and reference values retain their stable machine values and are not translated. The validator recognizes the approved English relative phrases defensively even though generated prose is Russian.

When AI is enabled and grounded input has `actions = ()` and `facts = ()`, the provider may still be invoked. A valid draft then has `items = ()`; its summary may be empty or qualitative but cannot introduce an action or an unsupported numeric/date claim. MVP-015 must not add an undocumented empty-input skip optimization.

## 7. Data Requirements

### 7.1 Demo Dataset

A future demo dataset must contain approximately 30-50 SKUs and enough historical and configuration data to demonstrate:

- a healthy SKU;
- a near-stockout SKU;
- an already-late reorder SKU;
- an overstocked SKU;
- a low-margin SKU;
- an unsafe-price SKU;
- a material sales decline;
- a material sales increase;
- a possible price-increase opportunity.

The dataset is a later implementation deliverable and is not created as part of planning. It must be realistic enough for a portfolio walkthrough but must contain no secrets or falsely presented live business data.

### 7.2 Minimum Logical Data Domains

The MVP data model must support, without prescribing storage technology:

- SKU identity and descriptive metadata;
- dated sales quantities and, where relevant, revenue or advertising totals;
- current sellable inventory;
- confirmed inbound quantity and expected arrival date when available;
- production/procurement and delivery lead times;
- current price and unit cost components;
- profitability, replenishment, risk, and rounding configuration;
- optional competitor or comparison prices with explicit provenance;
- data-effective timestamps and validation status.

### 7.3 Data Quality and Provenance

- Required fields must be validated before calculation.
- Missing, stale, malformed, or contradictory values must be reported; they must not be silently replaced by LLM guesses.
- Demo, manual, imported, calculated, and future API-sourced values must have distinguishable provenance.
- Time windows, currencies, units, and timestamps must be explicit.
- RUB is the target display currency for the MVP demo, while calculation logic should avoid unnecessary marketplace coupling.

## 8. User Experience

### 8.1 Initial Interface

The first interface target is Streamlit. The primary page question is:

> What requires attention today?

The planned information architecture is:

1. Executive summary
2. Priority actions
3. Inventory risk
4. Unit economics
5. Pricing simulator
6. SKU detail
7. AI Daily Brief

This section defines product behavior only; no UI is implemented during planning.

### 8.2 UX Requirements

- Critical actions and profitability violations must be visually distinguishable from informational opportunities.
- Every recommendation must expose its evidence and “why.”
- Monetary values must show currency; rates must show units or percentages; sales changes must show their comparison period.
- Missing data must be presented as missing, not as zero unless zero is the validated source value.
- Mock/demo data and any AI-generated prose must be clearly labeled.
- The pricing simulator must visibly separate hypothetical scenarios from current values.
- The product must not suggest that a read-only recommendation has been executed.

## 9. Human-in-the-Loop Safety

The MVP is advisory and read-only. It has no real Ozon write operations.

Any future capability that changes a price, updates stock, creates a supply, modifies a product, sends an external instruction, or causes another operational side effect must:

1. present the proposed action and its calculated evidence;
2. identify the exact target and values to be changed;
3. require explicit human approval for that specific action;
4. validate the action again immediately before execution;
5. record approval and outcome in an audit trail;
6. fail safely without implying success when execution is unconfirmed.

Bulk, background, inferred, or LLM-initiated approval is not sufficient. Designing and implementing such write workflows is outside the first MVP.

## 10. Non-Functional Requirements

### 10.1 Modularity and Provider Independence

- Business logic must not depend directly on Streamlit, the mock provider, a future Ozon client, Google Sheets, or the LLM vendor.
- Provider implementations must expose a stable application-facing contract.
- The same analytics must run against mock data and later normalized real-provider data.

### 10.2 Determinism and Explainability

- Identical validated inputs and configuration must yield identical calculations, classifications, and priority ordering.
- Every business-critical output must be reproducible without invoking an LLM.
- Each result must make its source inputs, formula or rule, and relevant threshold inspectable.

### 10.3 Testability

- Calculation functions and rule evaluation must be independently unit-testable.
- Provider behavior must be testable through shared contract tests.
- Edge cases must cover zero sales, zero stock, missing data, invalid rates, price-boundary equality, rounding boundaries, late reorder dates, inbound-stock timing, and conflicting profitability constraints.
- LLM grounding must be testable with structured fixtures and checks that unsupported metrics are rejected or flagged.
- UI smoke tests must not be the only evidence that business logic is correct.

### 10.4 Reliability and Graceful Degradation

- The deterministic application must remain usable when the LLM is disabled, unavailable, or returns invalid output.
- A provider or validation failure must be visible and must not silently produce a “healthy” or “safe” result.
- The application must not require real Ozon credentials to start or complete the portfolio demo.

### 10.5 Portfolio Quality

- The project must be understandable from its documentation and demo flow.
- Example outputs must look business-realistic while remaining clearly identified as demo data.
- The implementation should use the simplest architecture that preserves safety, testability, and future provider replacement; unnecessary frameworks and microservices are out of scope.

## 11. Explicit Non-Goals for the First MVP

The first MVP will not:

- act as a full ERP;
- include production infrastructure, billing, or authentication;
- integrate with the real Ozon Seller API;
- perform live Ozon writes;
- present mock behavior as a real integration;
- integrate with Google Sheets, Telegram, or n8n;
- automatically reprice products;
- monitor competitors from external sources;
- include a technical product-card auditor;
- provide advanced sales forecasting;
- claim unsupported prediction accuracy;
- include an ML price-elasticity model;
- include a voice interface;
- delegate money, inventory, risk, or eligibility calculations to the LLM.

## 12. Acceptance and Success Criteria

The first MVP is successful only when all of the following are demonstrated:

1. Demo SKU data loads through a mock provider.
2. Replacing the provider does not require rewriting analytics or business logic, as verified by an explicit provider contract and tests.
3. Inventory calculations are deterministic and covered by automated tests.
4. Supply and reorder risk is calculated using lead time and safety buffer and is explained with evidence.
5. Replenishment quantities are generated by documented deterministic rules.
6. Unit economics are deterministic and covered by automated tests.
7. Break-even and minimum safe prices are calculated, with boundary and rounding tests.
8. What-if pricing scenarios use the same calculation engine and correctly label safe and unsafe prices.
9. The system makes no unsupported price-to-demand forecast.
10. Structured alerts and deterministic priorities are generated for the required categories.
11. The LLM turns only calculated, structured facts into a grounded Daily Brief.
12. The system detects or clearly flags unsupported LLM claims in its validation/test strategy.
13. Streamlit presents priority actions, business metrics, explanations, a pricing simulator, SKU details, and the brief.
14. The deterministic product remains usable when the LLM is unavailable.
15. The full automated test suite passes.
16. The application runs without real Ozon credentials.
17. Competitor data is absent or truthfully labeled as mock, manual, or imported.
18. No business-critical calculation is delegated to the LLM.
19. No external write occurs in the MVP.
20. A portfolio walkthrough can show at least the nine required demo-data scenarios and trace recommendations back to inputs and rules.

## 13. Future Scope, Not MVP Commitments

After the core MVP is correct and tested, separately approved phases may add:

- a real `OzonSellerApiProvider`;
- Google Sheets input;
- Telegram delivery;
- n8n automation;
- human-approved Ozon write workflows;
- external competitor monitoring;
- automatic repricing with explicit safety and approval controls;
- a technical product-card auditor;
- validated forecasting or price-elasticity models;
- a voice interface.

These items must not weaken deterministic ownership, provenance, explainability, provider independence, or human approval.

## 14. Portfolio Demonstration Narrative

The finished MVP should demonstrate that its author can:

- understand a marketplace business process and identify automation opportunities;
- model inventory, supply, sales, and unit economics;
- separate deterministic computation from generative interpretation;
- design safe, explainable recommendations;
- isolate replaceable external providers;
- handle unavailable or untrusted data honestly;
- test business rules and calculation boundaries;
- present a working, business-oriented decision product.

## 15. Definitions

- **As-of date:** The explicit reference date for an analysis run.
- **Average daily sales:** Units sold per observed day in a configured historical window.
- **Stock coverage:** Estimated number of days current sellable stock can support at the calculated average daily sales rate.
- **Supply lead time:** Production/procurement lead time plus delivery lead time.
- **Safety buffer:** Additional configured time included to reduce replenishment risk.
- **DRR:** Advertising spend divided by attributable revenue for a stated period, expressed as a percentage.
- **Profit/contribution per unit:** Selling price less the variable cost components included by the configured MVP model; it is not represented as full accounting net profit.
- **Minimum safe price:** Lowest price that satisfies all configured price-floor, minimum-profit, and minimum-margin constraints.
- **Structured fact:** A validated value, classification, or recommendation created by deterministic application logic and supplied to the LLM as grounded input.
- **Provider:** A component that retrieves source data and maps it into the application’s normalized contract.

## 16. Items to Resolve Before Implementation

The following values require explicit product/configuration decisions before their dependent features are implemented; they must not be silently assumed:

- sales averaging and sales-change comparison windows;
- risk severity and warning-window thresholds;
- overstock threshold and target post-arrival coverage;
- treatment and eligibility of confirmed inbound stock;
- default lead times and whether defaults are permitted when SKU values are missing;
- included cost components and handling of unavailable advertising data;
- minimum profit, minimum margin, and any absolute price floor;
- monetary, date, pack-size, and display rounding conventions;
- priority mapping for any future production recommendation rule not covered by Section 6.4.1;
- stale-data thresholds;

MVP-015 has no remaining provider/runtime blocker: its provider, schema, failure, prompt, and lexical-grounding decisions are approved in Sections 6.5.4 through 6.5.8 and ADR-004.

Until resolved, these are configuration requirements and open decisions—not permission to invent production values or imply knowledge of Ozon API behavior.
