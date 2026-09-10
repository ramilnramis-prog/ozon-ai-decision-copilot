# Employer Demo Script

Target length: **5–8 minutes**. Use the deterministic, AI-disabled mode unless a live AI call has been separately approved and configured.

## Before the interview

1. Start from the repository root in Windows PowerShell:

   ```powershell
   $env:OZON_COPILOT_AI_ENABLED = "false"
   .\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
   ```

2. Open the URL printed by Streamlit and use a clean browser window at a readable zoom.
3. Confirm the header visibly says `Ozon AI Decision Copilot` and the disclosure says the data is demo/read-only with automatic actions disabled.
4. Confirm the executive summary reads:
   - 37 analyzed active SKUs;
   - 82 Priority Actions;
   - 17 `CRITICAL`;
   - 53 `HIGH`.
5. Do not expose a terminal, local filesystem path, username, notification, API key, or browser-private data during the demo.

## Walkthrough

### 0:00–0:40 — Frame the product

**Show:** The dashboard header and demo/read-only disclosure.

**Say:**

> “This is a portfolio decision-support project modeled around an Ozon seller workflow. It uses deterministic synthetic data; it is not connected to the Ozon Seller API and performs no marketplace writes.”

Then establish the main boundary:

> “Python calculates the business facts and priorities. AI can only explain an allow-listed set of those verified results. Any future action would still require explicit human approval.”

### 0:40–1:30 — Executive summary and Priority Action Center

**Show:** `Overview / Priority Actions`, the four summary metrics, and the first action cards.

**Point out:**

- all 82 recommendations remain individual actions—there is no hidden top-N cap or suppression;
- `CRITICAL` and `HIGH` counts come directly from authoritative severity values;
- ranking is deterministic by severity, urgency, approved rule precedence, SKU, and recommendation ID;
- each card exposes the proposed read-only action, deterministic explanation, rule ID, recommendation ID, and expandable facts.

**Say:**

> “The LLM does not choose what is important. Python applies explicit rules and stable tie-breaks, then the UI preserves that order exactly.”

### 1:30–2:35 — Inventory timing: `DEMO-002`

**Show:** `Детали SKU`, select `DEMO-002 — Brass Ball Valve 1/2 inch`, then show sales, inventory, actions, and evidence.

**Verify on screen:**

- sellable stock: `35` units;
- average daily sales: `12` units/day;
- stock coverage: `2.9` days;
- projected stockout date: `2026-09-05`;
- latest safe start date: `2026-08-23`;
- recommended replenishment: `481` units;
- `inventory.replenishment_already_late` is a `CRITICAL / IMMEDIATE` action.

**Say:**

> “This goes beyond a low-stock flag. The engine combines observed demand with production, delivery, and safety time to show that the latest safe start date is already past. The evidence is traceable to exact calculated facts and source records.”

### 2:35–3:25 — Overlap and partial interpretation: `DEMO-006`

**Show:** Select `DEMO-006 — Flexible Water Hose 1 m` in SKU Detail.

**Verify:**

- average daily sales: `3`;
- period change: `-0.75` (a 75% decline);
- sellable stock: `900`;
- coverage: `300` days;
- recommended replenishment: `0`;
- both sales-decline and overstock-candidate actions remain visible.

**Say:**

> “Valid overlapping findings are preserved. The system does not merge or suppress them, and it does not claim a cause for the observed sales change.”

### 3:25–4:15 — Unit economics: `DEMO-015`

**Show:** SKU Detail or `Юнит-экономика`, then locate `DEMO-015 — Stainless Kitchen Faucet`.

**Verify:**

- selling price: `3290 RUB`;
- profit/contribution per unit: `-649.7 RUB`;
- contribution margin: `-0.197`;
- minimum safe price: `6825 RUB`;
- loss-making and unsafe-price actions are visible.

**Say:**

> “These values use Decimal rather than binary float. The result is per-unit contribution economics—not full accounting net profit—and the same safe-price engine is used everywhere.”

If useful, briefly contrast `DEMO-018`: its supplied proportional cost structure produces no finite safe price. Contrast `DEMO-021`: missing COGS remains unavailable rather than being guessed or treated as zero.

### 4:15–5:20 — Price what-if boundary

**Show:** `Price Simulator`, select `DEMO-001 — Smart LED Bulb Set`.

1. Enter `1189.00`, click `Рассчитать сценарий`, and show `UNSAFE` with safe-price distance `-1 RUB`.
2. Enter `1190.00`, submit again, and show the exact inclusive `SAFE` boundary with distance `0 RUB` and profit per unit `238 RUB`.
3. Optionally enter `1191.00` to show `SAFE`, distance `1 RUB`, and profit per unit `238.7 RUB`.

**Say:**

> “The simulator changes only the hypothetical price and recalculates per-unit economics using the same Python engine. It does not forecast demand, sales, conversion, revenue, or future total profit, and it never writes a price back to a marketplace.”

### 5:20–6:15 — Evidence and provenance

**Show:** Return to a priority card or SKU Detail. Expand the evidence and provenance sections.

**Point out:** Stable fact IDs, formula/rule IDs, periods, source references, source type `demo`, and the explicit analysis timestamp.

**Say:**

> “Recommendations are explainable without an AI call. Provenance distinguishes source values from calculated facts, and missing data is shown as missing rather than silently becoming zero.”

### 6:15–7:15 — Optional AI Daily Brief and graceful states

**Show:** `AI Daily Brief` in the default AI-disabled run.

**Say:**

> “The deterministic dashboard works with no OpenAI key. When enabled, the service sends only the evidence closure of the ranked actions, requests structured output, and validates references plus numeric and date claims before any prose is displayed.”

Explain the four outcomes:

- `GENERATED`: a fully validated `DailyBrief` is displayed and labeled AI-generated;
- `DISABLED`: no provider is constructed or called;
- `UNAVAILABLE`: a typed provider availability failure affects only the brief;
- `INVALID`: malformed, refused, or ungrounded prose is rejected and not shown.

Do not spend API credits or present deterministic fake prose as a live model response. If a live call was separately approved, use the configured environment and clearly identify it as a live optional demonstration.

### 7:15–7:40 — Close

**Say:**

> “The project demonstrates a replaceable provider architecture and a deliberate trust boundary: Python calculates, AI explains, and a human approves. A future verified read-only Ozon adapter could replace the mock marketplace provider without rewriting analytics or decision rules.”

## Short version if time is tight

Use `DEMO-002` for inventory timing, `DEMO-015` for loss-making economics, `DEMO-001` at `1190.00` for the safe-price boundary, then show the AI-disabled brief state. This covers the core story in roughly five minutes.

## Screenshot capture plan

Capture genuine screenshots from the running Streamlit application; never fabricate or generate dashboard images.

The accepted MVP-020 capture set is:

1. `docs/screenshots/overview.png` — header/disclosure, summary metrics, and the first Priority Actions.
2. `docs/screenshots/inventory.png` — header/disclosure plus the inventory table showing `DEMO-002` timing and readable numeric formatting.
3. `docs/screenshots/pricing-simulator.png` — header/disclosure plus the submitted `DEMO-001` / `1190.00` safe-boundary result.

An AI Daily Brief screenshot is intentionally omitted. The brief remains an optional feature, and no paid/live model call is required solely for portfolio capture. Never label fake prose as a live response.

Before saving each image:

- use a clean browser window and readable zoom;
- keep the product title and demo/read-only disclosure visible where feasible;
- crop out desktop chrome that contains unrelated or private information;
- verify there is no key, terminal, local path, username, notification, or client data;
- save a real non-empty PNG with the stable filename above;
- open the saved file and inspect it before linking it from `README.md`.

The README embeds only these three accepted files after their visual safety review.

## Video checklist

- [ ] Keep the recording between 5 and 8 minutes.
- [ ] Use a clean screen and readable zoom.
- [ ] Keep the demo/mock and read-only label visible early.
- [ ] Hide terminals, local paths, usernames, notifications, and secrets.
- [ ] Show the executive summary and Priority Action Center.
- [ ] Show an inventory timing case with evidence.
- [ ] Show contribution economics and minimum safe price.
- [ ] Submit a pricing scenario and state that it is not a demand forecast.
- [ ] Show provenance and the optional AI state.
- [ ] Explain that rejected/unavailable AI never changes deterministic facts.
- [ ] State “Python calculates → AI explains → human approves.”
- [ ] Avoid claims of real Ozon access, live competitor monitoring, production use, or automatic execution.
- [ ] End with the GitHub URL only after the user has created and published the repository.

## Repeatability checklist

Run the walkthrough twice before an interview or recording. On both runs confirm the same summary values, deterministic action order, price-boundary result, AI-disabled behavior, and absence of stale pricing or brief output.
