# Architecture Decision Log

## Decision Record Template

### ADR-XXX: Title

- Status: Proposed
- Date: YYYY-MM-DD
- Context:
- Decision:
- Consequences:
- Alternatives considered:

## Initial Constraints

- No assumption of real Ozon Seller API access.
- Mock and future real providers must be interchangeable behind a stable boundary.
- Python is authoritative for deterministic business calculations.
- LLM output is interpretive and explanatory, never the source of final calculated values.
- Future write actions require explicit human approval.

### ADR-001: Categorical MVP priority policy

- Status: Approved
- Date: 2026-09-04
- Context: MVP-012 requires deterministic product policy for converting every production MVP-011 recommendation into an ordered `PriorityAction`. The implemented decision layer emits 18 distinct production rule IDs, and the project owner has approved an explicit mapping for all 18.
- Decision: Use categorical priority only. Severity order is `critical`, `high`, `warning`, `info`; urgency order is `immediate`, `soon`, `monitor`. Sort first by severity, then urgency, then the approved rule precedence recorded in `MASTER_SPEC.md` Section 6.4.1, then SKU ascending, then recommendation ID ascending. Rank starts at one and input order is irrelevant. Each recommendation remains an individual action; there is no grouping, merging, suppression, or top-N cap. Duplicate recommendation IDs are rejected. Combined evaluations require one analysis timestamp. Unknown or unmapped rules fail closed. No weighted, normalized, forecast-based, or other numeric business score is permitted.
- Consequences: All 18 current production rule mappings are normative and MVP-012 is ready to start. Any future production rule absent from the approved table must fail closed until explicitly mapped; no default mapping may be inferred.
- Alternatives considered: Weighted scoring, arbitrary numeric business scores, normalization, forecast-based impact scoring, input-order ranking, silent defaults, grouping by SKU, suppression, deduplication, and output caps were rejected for the MVP.

### ADR-002: Analysis SKU-selection semantics

- Status: Approved
- Date: 2026-09-04
- Context: MVP-013 exposes `AnalysisService.analyze(...)`. Default operational analysis, an explicitly empty request, and explicit inspection of inactive catalog products require distinct and deterministic meanings.
- Decision: `analyze(None)` selects all and only catalog products whose existing `Product.active` field is `true`. `analyze(())` is valid and returns an empty `AnalysisSnapshot` without filler issues or recommendations. A non-empty explicit selection analyzes exactly the requested known SKUs, including inactive products. Unknown explicit SKUs and duplicate explicit SKUs are rejected. Active filtering belongs only to application-service selection and is not moved into providers, analytics, decisions, or prioritization.
- Consequences: The default dashboard excludes inactive products while an operator may still inspect an inactive product explicitly. Omitted and explicitly empty selections are not equivalent. Explicit selection remains deterministically sorted after validation.
- Alternatives considered: Analyzing inactive products by default, treating an empty selection as all products, silently filtering explicitly requested inactive products, ignoring unknown SKUs, and silently deduplicating requests were rejected.

### ADR-003: Action-centric AI grounding and structured brief contract

- Status: Approved
- Date: 2026-09-04
- Context: MVP-014 was blocked because the exact AI-visible allow-list, action identity, draft schema, serialization, status ownership, and minimum grounding validation were not yet normative. The completed `AnalysisSnapshot`, `CalculatedFact`, `Recommendation`, and `PriorityAction` models already provide deterministic identity and traceability.
- Decision: The only source for `GroundedBriefInput` is one completed `AnalysisSnapshot`. The input is action-centric: it contains the snapshot analysis timestamp, every priority action in authoritative order, and only the `CalculatedFact` evidence closure of those actions, sorted by fact ID. Service issues, unrelated analytics, selected-but-actionless SKU details, counts, raw provider data, paths, config secrets, and credentials are excluded. A grounded action carries only action/recommendation identity, SKU and optional source product name, rule/category/status, rank/severity/urgency, and evidence fact references. `action_ref` equals the recommendation ID; no new action ID exists. Grounded facts reuse `CalculatedFact.fact_id`, preserve exact typed values, units, periods, formula/rule identity, and source references, and introduce no parallel metric taxonomy. Duplicate identical fact resolution is collapsed; conflicting content under one fact ID is invalid. Absent business values remain absent, while an existing deterministic status fact is copied exactly. Canonical JSON uses exact fixed-point strings for `Decimal`, exact JSON integers, JSON booleans, ISO dates, timezone-explicit ISO datetimes, stable enum values, and JSON null for absent optional values. The provider receives only this input and returns an untrusted `AIBriefDraft` with a referenced summary and exactly one ordered referenced item per action. The draft has no status. Python owns `BriefGenerationResult` status: `GENERATED` contains a validated `DailyBrief`; `DISABLED`, `UNAVAILABLE`, and `INVALID` contain no brief. MVP-015 must enforce the fifteen minimum reference, cardinality, ordering, identity, numeric/date-claim, empty-input, timestamp, and status-trust checks in `MASTER_SPEC.md` Section 6.5.5. Numeric and date/time text claims must copy canonical values verbatim from facts referenced by the same text block.
- Consequences: MVP-014 is ready to implement without selecting an LLM vendor. The grounded context cannot expose healthy filler, service diagnostics, unrelated analytical state, or a truncated/reordered view. MVP-015 may choose a vendor separately but may not weaken this contract or permit the provider to own business truth or generation status.
- Alternatives considered: Passing the full `AnalysisSnapshot`, exposing service issues, adding synthetic action/fact IDs, including all SKU analytics, adding counts or top-N truncation, letting the model choose priorities or status, accepting merged/missing action items, converting Decimal through float, and permitting derived numeric/date claims were rejected.

### ADR-004: OpenAI runtime and strict lexical grounding for MVP-015

- Status: Approved
- Date: 2026-09-07
- Context: MVP-014 established the vendor-neutral action-centric grounding contract, but MVP-015 still required an explicit provider/API/model/dependency choice, secret ownership, failure classification, exact validated result models, and deterministic lexical rules for prose claims. Without these choices, implementation would have to invent runtime and grounding policy.
- Decision: Implement one concrete adapter behind the existing `BriefModelProvider` using OpenAI, the Responses API, model `gpt-5.6-terra`, and the official Python `openai` SDK. Use strict Responses API Structured Outputs matching `AIBriefDraft`, reasoning effort `low`, no tools or retrieval, `store = false`, no streaming/background mode, zero application retries, and no fallback model/provider/prose. The model is an interpretation-only formatter of `GroundedBriefInput` into concise Russian prose; it never calculates, reprioritizes, retrieves, browses, invokes tools, or writes. The grounded payload is data, not system/developer instructions. `OPENAI_API_KEY` is resolved only at bootstrap/concrete-adapter composition when AI is enabled. Missing or invalid local secret/auth configuration is `ConfigurationError`; connection, timeout, rate-limit, and transient/server-side 5xx errors map through typed `AIUnavailableError` to `UNAVAILABLE`; returned schema failure, refusal/non-draft content, malformed/unusable completed output, or grounding failure maps to `INVALID`; unexpected programming errors propagate. Raw provider responses and exceptions are excluded from business results.
- Decision: `DailyBrief` is immutable and contains exactly the copied aware `analysis_timestamp`, a lowercase SHA-256 hex digest of the exact canonical `GroundedBriefInput` UTF-8 serialization, the validated summary, and the ordered validated item tuple. It exists only after complete validation, retains exact text/references without repair, and does not duplicate Python-owned business truth. `BriefGenerationResult` is immutable and contains exactly `status` and `brief`; the exact status values are `GENERATED`, `DISABLED`, `UNAVAILABLE`, and `INVALID`, with a brief only for `GENERATED`.
- Decision: Apply the strict block-local claim grammar in `MASTER_SPEC.md` Section 6.5.7 to free-form summary/item text only. Values are authorized only by the same block's fact references and must match canonical numeric/date/datetime strings verbatim. Date/datetime spans precede numeric scanning. Identifier-like ASCII letter/digit tokens are excluded; standalone and grouped/signed/decimal/scientific/percent forms are detected; non-ASCII decimal digits are invalid; ISO date/time forms require exact canonical matches; dot/slash dates, time-only claims without an independent exact fact, and the approved English/Russian relative-time phrases are rejected. There is no normalization, semantic equivalence, arithmetic, date resolution, unit or percentage conversion, rounding, or reference laundering. Empty grounded input may still be sent and can validate only with zero items and a claim-safe empty/qualitative summary.
- Consequences: All product/runtime decisions needed by MVP-015 are normative in `MASTER_SPEC.md` Sections 6.5.4 through 6.5.8. MVP-015 is ready to start while remaining optional to deterministic operation. Its implementation may add only the approved SDK dependency and must use fakes/mocks for the default test suite; it may not weaken the existing provider protocol or grounding boundary.
- Alternatives considered: Prompt-only JSON, Chat Completions, additional AI abstractions, tools/retrieval, retries, automatic model fallback, a second provider, deterministic prose fallback, hidden secret resolution, treating misconfiguration or invalid output as availability failure, returning provider diagnostics in business results, random brief IDs, duplicated business fields, global fact authorization, numeric/date normalization, relative-date resolution, and prose repair were rejected for the MVP.
