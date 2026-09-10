# Agent Working Agreement

## Scope

This file defines how coding agents collaborate on Ozon AI Decision Copilot while preserving the approved product specification, architecture, and Fast Track backlog.

## Current Phase

Planning only until the user explicitly starts an implementation task. Do not create application code, folders, or install dependencies merely because they appear in `TASKS.md`.

## Coordination Reality

Agents must not claim to communicate autonomously outside the available Codex environment. Coordination happens only through:

- repository documents and task status;
- visible file diffs and repository state;
- recorded test commands and output;
- explicit handoffs, reviews, and user decisions in the active environment.

If no reliable coordination channel is available, work sequentially. Never assume another agent has seen an unrecorded message or change.

## Sources of Truth

Use this precedence order:

1. `MASTER_SPEC.md` - product behavior, calculations, safety constraints, and MVP acceptance.
2. `ARCHITECTURE.md` - module boundaries, dependencies, data flow, and integration boundaries.
3. `DECISIONS.md` - explicit approved decisions that resolve open design choices without contradicting higher-level sources.
4. `TASKS.md` - ordered execution queue, dependencies, acceptance criteria, and required tests.
5. `AGENTS.md` - collaboration, ownership, handoff, and review protocol.

If these sources conflict, stop the affected work. The Lead records the conflict, marks the task `BLOCKED`, and requests an explicit decision. Agents may propose a change but may not silently reinterpret or overwrite an approved source.

## Lead-Agent Operating Model

The project uses one Lead / Orchestrator and bounded specialist roles. A role describes responsibility, not a separate long-lived process. One agent may hold more than one role when necessary, but the implementation and reviewer roles for a calculation-sensitive change should be performed as separate passes with an explicit review record.

`TASKS.md` is the execution queue. The Lead selects the next unblocked task, verifies its dependencies, assigns file ownership for that task, and controls task-state transitions.

## Roles

### 1. Lead / Orchestrator

The Lead owns coordination and acceptance, not unilateral redesign.

Responsibilities:

- read `MASTER_SPEC.md`, `ARCHITECTURE.md`, `TASKS.md`, `DECISIONS.md`, and this file before selecting work;
- select the next `TODO` task whose dependencies are `DONE`;
- confirm scope, expected files, test command, reviewer, and file ownership before implementation;
- assign implementation and review work to the appropriate role;
- prevent scope creep and keep Post-MVP work out of the Fast Track;
- inspect the final diff, acceptance criteria, test evidence, known limitations, and review result;
- require QA review for calculation-sensitive and safety-sensitive tasks;
- mark a task `DONE` only after verification succeeds;
- keep at most one implementation task `IN_PROGRESS` unless the parallel-work exception is proven;
- never silently change architecture, formulas, policy, or source-of-truth documents.

Primary files:

- task status and coordination notes in `TASKS.md`;
- approved decisions in `DECISIONS.md`;
- planning documents only when the user has authorized their update.

### 2. Foundation / Domain Engineer

Primary Fast Track area: `MVP-001` through `MVP-006`.

Responsibilities:

- create the project scaffold and minimal test setup;
- implement core configuration, Decimal/money utilities, clock, and essential errors;
- define source-independent domain and policy models;
- define provider contracts and source mapping;
- create provenance-rich demo data;
- implement `MockOzonProvider` and the local unit-economics provider;
- keep provider parsing separate from business calculations.

Primary files:

- `pyproject.toml` and foundation configuration when assigned;
- `app/core/`;
- `app/domain/`, except brief-specific changes assigned to the Application / AI Engineer;
- `app/providers/contracts.py`, `app/providers/mapping.py`, `app/providers/mock_ozon.py`, and `app/providers/local_unit_economics.py`;
- `data/demo/marketplace/` and `data/demo/unit_economics/`;
- corresponding unit/provider tests and fixtures.

### 3. Analytics / Decision Engineer

Primary Fast Track area: `MVP-007` through `MVP-012`.

Responsibilities:

- implement deterministic sales metrics;
- implement inventory coverage, stockout timing, safe-start timing, and replenishment quantity;
- implement Decimal-only unit economics, DRR handling, break-even, and minimum safe price;
- implement the pricing what-if engine without demand prediction;
- implement inventory, pricing, profitability, and sales-change rules;
- generate structured recommendations, evidence, deterministic explanations, and priority ordering;
- provide normal, edge, and exact-boundary tests for every rule.

Primary files:

- `app/analytics/`;
- `app/decisions/`;
- narrowly required result/policy model updates coordinated with the Foundation / Domain Engineer;
- `tests/unit/analytics/` and `tests/unit/decisions/`.

### 4. Application / AI Engineer

Primary Fast Track area: `MVP-013` through `MVP-015`.

Responsibilities:

- implement application-service orchestration and the composition root;
- preserve partial-data behavior without duplicating calculations;
- create the allow-listed `DailyBriefInput` and replaceable AI-provider contract;
- implement the approved LLM adapter and versioned interpretation-only prompt;
- validate grounding against structured facts and source references;
- ensure disabled, unavailable, malformed, or ungrounded LLM output affects only the natural-language brief;
- keep the deterministic product fully usable without an LLM.

Primary files:

- `app/services/`;
- `app/bootstrap.py`;
- `app/ai/`;
- `app/domain/briefs.py`;
- corresponding service, integration, and AI-grounding tests.

### 5. UI Engineer

Primary Fast Track area: `MVP-016` through `MVP-018`.

Responsibilities:

- implement the Streamlit entry point, presenters, and reusable components;
- present the executive summary and “What requires attention today?” view;
- present the Priority Action Center without reranking service output;
- present inventory, unit-economics, pricing-scenario, SKU-detail, and Daily Brief views;
- label mock/demo provenance, hypothetical values, unavailable data, and AI-generated prose;
- keep formulas, decision rules, provider construction, and action execution out of the UI.

Primary files:

- `streamlit_app.py`;
- `app/ui/`;
- Streamlit dependency metadata only while its task owns `pyproject.toml`;
- focused presenter and UI tests.

### 6. QA / Reviewer

Primary Fast Track area: continuous review plus `MVP-019` and `MVP-020`.

Responsibilities:

- review diffs for scope, architecture, and dependency-direction violations;
- independently challenge business formulas and rule precedence;
- design and run normal, edge, boundary, invalid-input, and degradation tests;
- verify every acceptance criterion against code and test evidence;
- run the relevant regression suite and report the exact result;
- verify that the LLM has not taken ownership of calculations, classification, recommendation, or ranking;
- verify that UI/services do not duplicate calculations;
- verify replaceable provider boundaries and mock/demo provenance;
- reject unsupported Ozon or competitor-data claims;
- verify no secrets, real writes, or unapproved infrastructure were added;
- review employer-demo documentation and screenshots for accuracy.

Primary files:

- tests and fixtures explicitly assigned for the review task;
- review/handoff records;
- implementation files only in a separately assigned correction task, never concurrently with their author.

QA does not mark a task `DONE`; it returns a review result to the Lead.

## File Ownership Guidance

Ownership is temporary and task-scoped. It prevents simultaneous edits; it does not grant permission to exceed the assigned task.

| Area | Default implementation owner | Review owner | Boundary |
|---|---|---|---|
| `MASTER_SPEC.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `TASKS.md`, `AGENTS.md` | Lead, only with authorization | QA / architecture review | No specialist silently edits sources of truth. |
| `pyproject.toml`, `.gitignore` | Agent owning the task that requires the change | Lead / QA | Shared files must be explicitly reserved before editing. |
| `app/core/` | Foundation / Domain Engineer | QA | Utilities only; no business formulas except shared money/rounding primitives. |
| `app/domain/` | Foundation / Domain Engineer | Analytics or QA | Source/framework independent; coordinate brief models with Application / AI. |
| `app/providers/`, `data/demo/` | Foundation / Domain Engineer | QA | Parsing/mapping/provenance only; no analytics or decision rules. |
| `app/analytics/` | Analytics / Decision Engineer | QA | Pure deterministic calculations; no I/O, UI, services, or LLM. |
| `app/decisions/` | Analytics / Decision Engineer | QA | Deterministic rules and priority only; no LLM. |
| `app/services/`, `app/bootstrap.py` | Application / AI Engineer | QA / Lead | Orchestration and wiring only; no duplicated formulas. |
| `app/ai/` | Application / AI Engineer | QA / grounding review | Structured facts in, explanatory prose out. |
| `streamlit_app.py`, `app/ui/` | UI Engineer | QA | Presentation only; no calculations, reranking, or provider construction. |
| `app/api/` | Post-MVP API owner | QA | Thin transport adapter; not Fast Track. |
| `tests/unit/` | Owner of tested module | QA | QA may add independent cases after implementation handoff. |
| `tests/integration/`, portfolio docs | QA / Reviewer or explicitly assigned owner | Lead | Must reflect real behavior and mock provenance. |

Before editing, an agent must inspect the current working tree and declare the exact files it intends to own. If another active task owns any file, do not edit it. The Lead resolves ownership first.

Never allow two agents to edit the same file simultaneously. This includes shared files such as `pyproject.toml`, `app/bootstrap.py`, common domain modules, test fixtures, and planning documents.

## Concurrency Rules

Only one implementation task may be `IN_PROGRESS` by default.

The Lead may allow two implementation tasks in parallel only after documenting that both tasks:

1. have no direct or transitive dependency relationship;
2. modify non-overlapping files, including fixtures and shared configuration;
3. cannot affect the same formula, business rule, domain invariant, output contract, or acceptance criterion.

If any condition is uncertain, run the tasks sequentially. The Lead must name each task, its owner, its exact file set, and the proof of independence in the assignment/handoff record.

Parallel agents may always perform read-only work such as:

- diff review;
- test-case design;
- documentation review;
- threat or safety review;
- architecture consistency review;
- inspection of test output;

provided they do not edit overlapping implementation files or mutate shared state. Running tests in parallel is allowed only when the test commands cannot alter the same generated files, caches, fixtures, or external state.

## Task Start Protocol

Before implementation, the assigned agent must inspect the relevant source documents and working tree, then report:

```text
TASK START
Task: <Task ID and title>
Goal: <one sentence>
Dependencies satisfied: <IDs and evidence, or None>
Expected files: <exact paths/patterns>
Reserved files: <exact files this agent will edit>
Test command: <command(s) planned>
Architecture/spec questions: <None, or blocking issue>
```

The Lead verifies the report, file ownership, and dependency status before implementation begins. The task then becomes `IN_PROGRESS`.

## Implementation Rules

- Implement only the assigned task and its smallest necessary supporting changes.
- Preserve user and unrelated agent changes already present in the working tree.
- Use provider interfaces rather than importing concrete adapters into business modules.
- Keep I/O at provider/UI/adapter boundaries and make analytics pure where possible.
- Pass the as-of date and policy explicitly; do not read time or hidden configuration inside calculations.
- Use `Decimal` and the shared rounding rules for money.
- Treat unavailable data as unavailable, never as zero or an LLM guess.
- Add tests in the same task as the behavior they verify.
- Do not weaken or delete an existing test merely to make a task pass.
- Do not add a dependency, external call, or shared-file change not declared at task start.

## Required Handoff Format

After implementation and self-test, the implementing agent provides:

```text
HANDOFF
Task: <Task ID and title>
Status: IMPLEMENTED / NEEDS_REVIEW
Files changed:
- <path>
What was implemented:
- <concise behavior summary>
Tests run:
- <exact command>
Exact test result:
- <passed/failed counts and relevant output>
Business rules implemented:
- <rule/formula IDs or None>
Edge cases covered:
- <cases or None>
Known limitations:
- <limitations or None>
Architecture/spec decision required:
- <No, or decision/blocker reference>
Open questions:
- <questions or None>
Next recommended action:
- QA REVIEW / FIX REQUIRED / LEAD ACCEPTANCE
```

Do not report a task as complete when tests were not run. If a test could not run, state the exact reason and use `BLOCKED` or `NEEDS_REVIEW`; do not substitute “should pass.”

## Review Protocol

The normal task lifecycle is:

```text
IMPLEMENT
  -> SELF TEST
  -> QA REVIEW
  -> FULL RELEVANT TESTS
  -> LEAD ACCEPTANCE
  -> TASK DONE
  -> NEXT TASK
```

QA review must be independent of the implementation pass for calculation-sensitive changes. At minimum, QA checks:

1. the diff contains only the assigned scope and declared files;
2. dependencies and module import direction match `ARCHITECTURE.md`;
3. formulas and rule boundaries match `MASTER_SPEC.md` and approved decisions;
4. normal, zero, missing, invalid, exact-boundary, and rounding cases are covered where relevant;
5. results retain evidence, units, periods, thresholds, and provenance;
6. provider adapters contain no business calculations;
7. services, Streamlit, and future FastAPI adapters do not duplicate calculations;
8. AI input is allow-listed and generated prose cannot replace or alter deterministic facts;
9. the product still works in no-LLM mode;
10. no real write, unsupported API claim, secret, or unapproved dependency/infrastructure appears.

QA returns one of:

- `APPROVED` - acceptance criteria and review checks pass;
- `CHANGES_REQUIRED` - concrete corrections or tests are listed; task remains `IN_PROGRESS`;
- `BLOCKED` - an unresolved source-of-truth or prerequisite issue prevents safe completion.

The reviewer reports exact commands and results. The Lead reruns or inspects the relevant evidence, confirms no unresolved findings remain, and alone changes the task status to `DONE`.

## Mandatory QA Gates

QA review is required before completion of:

- the Decimal and safe-rounding portion of `MVP-002`;
- deterministic calculations in `MVP-007` through `MVP-010`;
- decisions, evidence, and priority rules in `MVP-011` and `MVP-012`;
- partial-data and orchestration behavior in `MVP-013`;
- AI grounding and graceful degradation in `MVP-015`;
- end-to-end verification and portfolio claims in `MVP-019` and `MVP-020`.

The Lead may require QA for any other task. A mandatory QA gate cannot be waived merely to meet the Fast Track schedule.

## Business-Calculation Review Checklist

For money, inventory, pricing, and decision logic, tests must include normal cases and relevant edge/boundary cases.

### Money and Unit Economics

- Decimal conversion and rejection of unsafe float input;
- commission and other fixed/proportional costs;
- DRR available, missing, and invalid-denominator cases;
- positive, zero, and negative profit;
- minimum-profit, margin, and price-floor constraints separately and together;
- equality at the safe boundary and upward price-increment rounding;
- invalid rates, denominators, currency mismatch, and reconciliation.

### Inventory and Supply

- healthy stock, zero stock, and invalid negative stock;
- positive demand, zero sales, and insufficient history;
- missing lead time and safety-buffer boundaries;
- stockout/safe-start dates before, on, and after the as-of date;
- confirmed timely, late, and unconfirmed inbound stock;
- zero recommendation, fractional demand, MOQ, and pack-size rounding.

### Pricing and Decisions

- hypothetical prices below, equal to, and above the minimum safe price;
- missing economics and invalid scenario values;
- deterministic rule precedence and exact thresholds;
- stable priority order under shuffled input and ties;
- no price-to-demand prediction, unsupported opportunity, or execution claim.

Expected values in these tests must be calculated independently during review from the documented formulas, not copied blindly from implementation output.

## Design-Change and Blocking Protocol

An agent may identify and propose a design change but may not implement it when it conflicts with or materially extends the approved documents.

The agent must:

1. stop the affected implementation;
2. avoid speculative code or dependency changes;
3. mark the task `BLOCKED` through the Lead;
4. record the conflicting source sections and concrete issue;
5. describe the smallest viable options, impact, and recommended choice;
6. request an explicit user/Lead decision;
7. record an approved durable choice in `DECISIONS.md` and update other source documents only when authorized;
8. resume only after the task and acceptance criteria are consistent with the decision.

Open product-policy values in `MASTER_SPEC.md` are not permission to invent production assumptions. Demo defaults must be explicit, documented, provenance-aware, and confined to mock/demo configuration.

## Non-Negotiable Boundaries

- Do not assume or invent real Ozon Seller API access, endpoints, fields, permissions, or competitor data.
- Preserve replaceable marketplace, unit-economics, competitor, and AI-provider boundaries.
- Keep deterministic financial and operational calculations in Python.
- Limit the LLM to interpreting structured facts, explaining recommendations, and generating grounded briefs.
- Never allow the LLM to create authoritative final values, eligibility, severity, action, or priority.
- Keep the MVP read-only with no real Ozon write operations.
- Require explicit, action-specific human approval, revalidation, and audit before any future external write.
- Do not introduce fake live integrations, committed secrets, automatic marketplace writes, microservices, or unnecessary infrastructure.
- Do not expand the Fast Track or start Post-MVP work without explicit approval.

## Definition of Task Completion

A task is `DONE` only when:

- all declared acceptance criteria are demonstrably satisfied;
- required tests pass with exact results recorded;
- mandatory QA review is `APPROVED`;
- relevant regression tests pass after review fixes;
- the final diff contains only approved scope and no overlapping/unowned edits;
- documentation/provenance accurately describes mock, calculated, hypothetical, AI-generated, and unavailable states;
- no known blocker or architecture/spec conflict remains;
- the Lead records acceptance and updates `TASKS.md`.

Passing tests alone is not sufficient when the implementation contradicts the specification, architecture, safety model, or assigned scope.
