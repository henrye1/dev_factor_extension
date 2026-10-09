# LGD Tail Extension – Three tail methods (exponential, power law, log-normal) and vintage start date

Date: 9 October 2026 (revised the same day: the reference-curve shape is removed). Status: draft
for Henry's review, to be built with Claude Code.
Companion to `2026-10-02-lgd-tail-extension-app-design.md` (the original design).

## 0. How to use this spec (read first, Claude Code)

1. **Bring the branch up to date before anything else.** Local `main` is one commit behind
   `origin/main` (`1e550b4 Prepare for Render: uploaded curves only, method 1 default, Render
   blueprint`). That commit touches `curves.py`, `models.py`, `runner.py`, `scenarios.py`,
   `projects.py`, the JS and the tests. Run `git pull --ff-only`, then create a working branch
   `feature/three-methods-and-vintage-start`. Do not commit to `main`.
2. Run the full test suite once before changing anything (`pytest -q`) and note the baseline.
3. Build in the order of Section 7. Each milestone ends with green tests.
4. **Exponential and power-law numbers must not move.** With the vintage filter off, every
   exponential and power-law figure must come out bit-for-bit the same as today. That covers
   λ, γ, scales, extended triangles, LGD columns, the derived LGD columns, and LGD_selected under
   methods 1 and 2. The golden `.npz` files are not edited. Only the test code changes, to stop
   asserting the removed reference-shape columns (Section 2.7).
5. Keep the house style of the codebase: numpy-vectorised engine, 1-indexed triangles,
   warnings as plain English sentences, no new runtime dependencies.

Suggested opening prompt for Claude Code:

> Read `docs/superpowers/specs/2026-10-09-lognormal-shape-and-vintage-start-design.md` and the
> original design spec it references. Follow Section 0, then build Milestone 1 only and stop
> so I can review it.

---

## 1. What changes

| # | Change | One-line summary |
|---|---|---|
| A1 | **Remove the reference-curve shape** | Method 3 today scales the tail to an uploaded client curve. It was a mistake and is removed completely. The app keeps exactly three tail methods |
| A2 | **Add the log-normal shape as the new method 3** | Same functional form as the client's industry curves (cn × log-normal density), but fitted to the challenger's own data with the **same log-linear least-squares solver** as λ and γ |
| B | **Vintage start date** | The user chooses which default vintages (CohortDate) feed the recovery triangle, for example only vintages from the last 10 or 15 years, instead of always using all of them |

After the change the methods are:

| Method | Shape | Fitted parameters | Solver |
|---|---|---|---|
| 1 | Exponential e^(−λb) | λ | OLS of ln R on b |
| 2 | Power law b^(−γ) | γ | OLS of ln R on ln b |
| 3 | Log-normal (1/b)·exp(−(ln b − μ)²/(2σ²)) | μ, σ | OLS of ln R + ln b on ln b and (ln b)² |

All three are fitted to the same points (Section 2.2), and all three take their level per row
from the same anchor window. Nothing is borrowed from the client except the functional form
of method 3.

**What stays:** the client's **applied** curves (`kind = "applied"`, `engine/applied.py`, the
"Compare the client's curves with our fitted tails" panel and the applied-curve columns in the
curves export). They are comparison only and never feed the extension. Only the reference
**shape** curves (`kind = "shape"`) go.

### 1.1 Interpretation to confirm: "the client's logistic"

Henry asked for "the client's logistic". This spec reads that as **the client's log-normal
curve form**. Nutun's industry curves (`04_AC_Model_Code2.sas`, the prototype Curves sheet) are
`yield(t) = cn · φ(t)`, where `φ(t) = 1/(t·s·√(2π)) · exp(−(ln t − m)² / (2s²))`. Method 3 fits
that same form to the challenger's own data. That makes the fitted (μ, σ) directly comparable
to the client's (m, s), without taking the client's curve itself.

If a true logistic curve was meant instead, only Section 2.2 changes. For example, a
logistic-decay shape `1/(1+exp(k(b−b0)))` cannot be solved by one linear regression. It would need
a non-linear solver, which breaks the "same solver" requirement. **Henry to confirm before
Milestone 3.**

---

## 2. Change A – Three methods: remove the reference shape, add log-normal

### 2.1 Shape indices and numbering

Today: `SHAPES = ("exp", "power", "client")`, methods 1, 2, 3.
After: `SHAPES = ("exp", "power", "logn")`, methods 1, 2, 3.
`SHAPE_LABELS = {"exp": "Exponential", "power": "Power law", "logn": "Log-normal"}`.

Index 2 changes meaning from the reference curve to log-normal. **Every `shapes[2]`, `ext[2]`,
`scale[2]`, `lgd_client`, `derived_client`, `scale_client`, `"client"` key and `Ext_Ref` sheet
must be found and either removed or renamed to the log-normal equivalent** (`lgd_logn`,
`derived_logn`, `scale_logn`, `"logn"`, `Ext_LogN`). Do not leave a renamed key that still
carries reference-curve logic. Search with
`git grep -nE "client_cohort|curve_label|_client|\"client\"|shapes\[2|scale\[2|Ext_Ref|RefCurve|reference|kind == \"shape\"|project_curves|choose_curve"`.
The hit counts by file on `origin/main` are in 2.5.

Change the engine default `method` from 3 to **1** (it is already 1 for new scenarios in
`scenarios.py`). Saved scenarios on method 3 keep it and become log-normal (Section 2.4).

### 2.2 Log-normal shape and fit: same solver as λ and γ

Shape by bucket b ≥ 1. The constant `1/(σ√(2π))` and the client's `cn` drop out, because the
per-row scale absorbs any constant:

    s_logn(b) = (1/b) · exp( −(ln b − μ)² / (2σ²) )

Fit on exactly the **same points** as λ and γ: the reference row `ref_ts`, buckets
`[FitStart, ref row's last credible bucket]`, where RecoveryPct > 0 (the existing `fx`, `fy`
arrays in `core._compute`). Taking logs makes it linear in x = ln b:

    y = ln R + ln b = a0 + a1·x + a2·x²         (ordinary least squares, x = ln b)
    σ² = −1 / (2·a2)        μ = a1 · σ²          valid only when a2 < 0

This is the same log-linear OLS as `_slope` with one extra regressor. Implement it with
`np.linalg.lstsq` on `[1, x, x²]`. **No new solver dependency.** In Excel it is
`LINEST(y, x^{1,2})`.

Overrides, matching `lambda_override` and `gamma_override`. Each case stays a single-regressor
`SLOPE`:

| μ override | σ override | Fit |
|---|---|---|
| blank | blank | Full fit above |
| blank | given σ | y + x²/(2σ²) = c + (μ/σ²)·x → μ = SLOPE · σ² |
| given μ | blank | y = c + a2·(x² − 2μx) → a2 = SLOPE on z = x² − 2μx; σ² = −1/(2a2), needs a2 < 0 |
| given | given | No fit |

Undefined fit (fewer than 3 points, or a2 ≥ 0, meaning the tail is not concave in ln b):
μ and σ are nan, the shape is nan, and the logn columns stay blank exactly as the other shapes
do when undefined. If method 3 is the selected method, raise the existing
`EngineError("The selected method cannot be computed because …")`, naming μ/σ and suggesting a
lower FitStart, overrides or another method. Add a warning like the λ/γ one when `fx.size < 3`.
Add a separate warning when a2 ≥ 0.

**Reference values for a sanity check.** These are at default parameters (FitStart 24, RefTS 1,
MinExposure R100m) on the 2 October zips, computed while writing this spec:

| Zip | Fit window | μ | σ | (λ, γ for comparison) |
|---|---|---|---|---|
| VB44 | b 24–89 | ≈ 3.043 | ≈ 0.703 | 0.0528, 2.71 |
| VB22 | b 24–151 | ≈ 3.769 | ≈ 0.792 | 0.0233, 1.70 |
| ALL | b 24–328 | ≈ 3.722 | ≈ 0.943 | 0.0157, 2.11 |

The client's own VB44 fit has m pinned at 3.25, so the challenger's μ ≈ 3.04 is in the same
region. Report these figures; they are useful in the report.

### 2.3 Engine changes (`hazard_ext/engine/core.py`)

- `compute(data, params)`: drop the `curve` argument and every branch that handles it ("Method 3
  needs a reference curve", `shapes[2, :] = nan` when there is no curve, the `curve_end` warning).
- `shapes`, `scale` and `ext` keep 3 rows; row 2 is now log-normal:
  `shapes[2, 1:] = (1/bb) * exp(-(ln bb − μ)² / (2σ²))`, computed inside the existing errstate.
- Rename the keys: `results.lgd_client → lgd_logn`, `lgd_ts.derived_client → derived_logn`,
  `tail_fit.scale_client → scale_logn`, `averages.lgd_client → lgd_logn` (still `wavg_strict`).
  In `curve()`, `"client" → "logn"`, and **delete `client_curve`**.
- `config`: remove `client_cohort`. Add `mu_fit`, `sigma_fit`, `mu`, `sigma`,
  `logn_mode = exp(μ − σ²)`, `logn_median = exp(μ)` and `logn_points` (nan or 0 when undefined).
- The undefined-method error: `what = "λ" | "γ" | "μ / σ"`.

### 2.4 Parameters (`engine/params.py`)

- `method: Literal[1, 2, 3] = Field(1, description="1 exponential, 2 power law, 3 log-normal")`.
- Remove `client_cohort`.
- Add `mu_override: Optional[float] = Field(None, ge=-10, le=20, description="Log-normal μ (on ln b); blank = fitted")`.
- Add `sigma_override: Optional[float] = Field(None, gt=0, le=20, description="Log-normal σ; blank = fitted")`.
- **Saved data.** `Params` has `extra="forbid"`, and stored scenarios, overrides and results
  carry `client_cohort`. `merge_params` and every `Params(**stored)` call (`runner.recompute`)
  must drop the retired key `client_cohort` before validating. Put the list of retired keys in
  one place, for example `RETIRED = {"client_cohort"}`.
- **Stored method 3 moves to log-normal (decided by Henry, 9 Oct 2026).** In existing
  scenarios and overrides, method 3 means the old reference shape. They keep `method = 3`, which
  now means log-normal; this is intended. The results they hold were computed with the
  reference curve, so they are no longer valid. Add a one-off migration
  (`migrations/005_three_methods.sql`, plus the SQLite equivalent wherever the app bootstraps
  its schema) that:
  - leaves `method` unchanged (stored 3 becomes log-normal);
  - removes `client_cohort` from scenario and override params;
  - marks every result whose effective method was 3 as stale, so the old reference-curve
    figures are never shown as current.

  The project page shows a one-time notice: "The reference-curve method has been replaced by
  the log-normal method; scenarios that used it now run log-normal and need running again."
  If the log-normal fit is undefined for a zip (Section 2.2), that zip's run fails with the
  usual clear error rather than falling back to another method.

### 2.5 Everything that touches the reference shape or hard-codes the three shapes

Hit counts from the search in 2.1 on `origin/main`:

| File | Hits | What to change |
|---|---|---|
| `export/formula_xlsx.py` | 34 | See 2.6 |
| `engine/core.py` | 20 | 2.3 |
| `tests/test_api.py` | 18 | Remove the shape-curve upload and method 3 reference tests. Add method 3 log-normal and retired-key tests |
| `tests/test_engine_golden.py` | 14 | See 2.7 |
| `web/runner.py` | 14 | Delete `project_curves` and `choose_curve`. `run_one`/`recompute` call `compute(data, params)`. `result.curve_label` is always "" (keep the DB column). `summarise`: `lgd_client → lgd_logn`, add `mu`, `sigma` |
| `web/routers/projects.py` | 11 | The curve upload accepts `kind = "applied"` only, and `kind = "shape"` is refused with a clear message. Existing shape rows are left in the database, hidden and unused (no destructive delete) |
| `web/routers/scenarios.py` | 10 | Comparison payload: the `"reference"` series becomes `"logn"`, selection map `{1: "exp", 2: "power", 3: "logn"}`. Remove `reference_label` |
| `export/tables.py` | 10 | Column lists, Config labels (drop "Reference curve", add μ/σ rows), summary headings |
| `static/js/zip.js` | 9 | SERIES method 3 → `lgd_logn` "Log-normal". Charts: remove the reference-curve line on the TermStep chart. Table columns |
| `static/js/settings.js` | 9 | Remove the "Reference curves" upload section. Keep "Client applied curves" and the comparison chart, with series exp / power / logn |
| `static/js/help.js` | 9 | Rewrite `METHOD_HELP[3]` as log-normal. Remove the reference-curve parameter help. Add μ/σ override help. Keep "three tail methods" wording |
| `export/summary_xlsx.py` | 9 | Headline columns (drop the reference curve; add LGD log-normal, μ, σ fitted/used). Remove the "Reference curve {label}" cumulative column |
| `export/curves_xlsx.py` | 7 | Remove the reference-curve column (it reads `shapes[2]`, which would now silently be log-normal). Keep the applied-curve columns |
| `export/values_xlsx.py` | 6 | Curve sheet heads/keys, chart series, `Ext_Ref → Ext_LogN` |
| `web/models.py` | 4 | `CURVE_KINDS` stays `("shape", "applied")` so old rows still load. Fix the docstrings |
| `tests/test_agent.py`, `web/agent.py`, `web/routers/agent.py` | 4 / 3 / 2 | Glossary: method 3 = log-normal, μ/σ overrides. Remove `client_cohort` from the config subset; add `mu`, `sigma` |
| `tests/conftest.py` | 3 | `CONFIG_KEYS["client_cohort"]` is still needed to read the golden config and pick the category, but it must not be passed into `Params` |
| `web/main.py`, `web/config.py`, `static/js/project.js`, `params.js`, `app.css`, `scripts/smoke_test.py`, `tests/test_applied.py` | 1–3 each | Remove the reference-curve option and labels. `METHODS = {1: "1 – Exponential", 2: "2 – Power law", 3: "3 – Log-normal"}`. Two new fields in the "Decay fit" group (μ override, σ override, blank allowed) |
| `static/js/chart.js` | – | `COLORS.logn` takes the old `client` slot (`#1baf7a`) |
| `README.md` | – | Parameters table and method list: remove "Reference curve", add μ/σ |
| `docs/LGD tail extension - what each scenario method does.docx` | – | Out of scope for code. Flag to Henry that its Method 3 section describes the removed reference shape and needs rewriting for log-normal |

The front end must tolerate stored results that predate this change (they have `lgd_client`,
not `lgd_logn`): show blank, never throw, and treat them as stale.

### 2.6 Formula workbook (`export/formula_xlsx.py`)

The live-formula workbook must still recalculate to the engine's numbers.

- Config: remove the "Reference curve" input row and the `RefCurve` name. Add rows for μ
  override, σ override, μ fitted, σ fitted, μ used, σ used, as defined names `MuOvr`, `SigOvr`,
  `Mu`, `Sig`. **The Config cell addresses are hard-coded in the name map** (`"Lam": "$B$27"`,
  …). The cleanest route is to reuse the removed row for one of the new inputs and append the
  rest after the existing block, so no other address moves. If anything moves, update the map
  and every reference together.
- Method label: "Method (1 = exponential, 2 = power law, 3 = log-normal)".
- Fitted μ, σ: `LINEST` over the same ln-b / ln-R helper rows that `Tail_Fit` already holds for
  λ and γ (rows 5–11). Then σ = SQRT(−1/(2·a2)) and μ = a1·σ², wrapped in IF(a2<0, …, NA()).
- Tail_Fit: shape row 3 becomes `=EXP(-((LN(b)-Mu)^2)/(2*Sig^2))/b`, in place of the reference
  curve lookup. `Scale – reference curve` becomes `Scale – log-normal`.
- `Ext_Ref` becomes `Ext_LogN`, built the same way as `Ext_Exp`. Any client-curve data sheet
  feeding the old shape row is removed.
- `CHOOSE(Method, x1, x2, x3)` keeps three arguments; the third is now the log-normal one.
  Charts and README text: "reference curve shape" becomes "log-normal".
- Verify with `scripts/compare_recalc.py` (LibreOffice recalculation compared with the engine)
  on VB44 and VB22 under all three methods.

### 2.7 Golden tests

The golden `.npz` files were extracted from the hand-built workbooks, whose third shape was the
client curve and whose Config method may be 3. **Do not regenerate or edit them.**

- Assert only the columns that still exist with the same meaning: everything except
  `lgd_client`, `derived_client`, `scale_client` and anything selected under method 3.
- Run the golden comparisons with `method = 1` and with `method = 2`. Check `lgd_selected` and
  `derived_selected` against the golden `lgd_exp` / `derived_exp` and `lgd_power` /
  `derived_power` columns respectively. Check `pv_selected`, `uplift` and `lgd_horizon` likewise
  where the golden file holds them for the selected method. Otherwise drop those assertions and
  say why in a comment.
- `prototype_curves()` stays only if `test_applied.py` still uses it as applied-curve test data.

---

## 3. Change B – Vintage start date

### 3.1 The data is already in the zip

Today only `lgd_recovery.csv` and `debug.json` are read. `lgd_recovery` is an aggregate over
all default vintages, so it cannot be filtered. The same zip also carries
**`runoff_triangle.csv`**:

| Column | Example | Meaning |
|---|---|---|
| EventType | Lifetime | Same blocks as `lgd_recovery` (Lifetime, LifetimeSingle, TwelveMonthSingle) |
| CohortDate | 2018-06-30 | The default vintage, month-end |
| Bucket | 0, 1, 2, … | Months since default (0 = at default) |
| ExposureAmount | 97,584,658.58 | Outstanding balance of that vintage at that bucket |
| AccountCount | 1 | Accounts in the vintage |

Vintage coverage in the 2 October zips: VB44 has 25 cohorts (2018-06 to 2026-05), VB22 has 71
(2013-12 to 2025-09), VB15 has 93 (1999-03 to 2026-07), VB11 has 59 (2001-06 to 2026-07) and ALL
has 189 (1999-03 to 2026-07). In ALL, vintages from 2016-10 onwards hold R51.8bn of the R71.1bn
at-default exposure.

### 3.2 Rebuild rule (verified exactly)

For one EventType, let `X[c, k]` be the vintage-c exposure at bucket k (0 where there is no row),
`obs[c, k]` be True where a runoff row exists for (c, k), and `F` be the set of vintages kept by
the filter. For TermStep ts ≥ 1 and bucket b ≥ ts:

    I(ts, b)        = { c ∈ F : obs[c, b] and X[c, ts−1] > 0 }
    ExposureBucket  = Σ_{c∈I} X[c, ts−1]
    PrevColSum      = Σ_{c∈I} X[c, b−1]
    ThisColSum      = Σ_{c∈I} X[c, b]
    RecoveryPct     = (PrevColSum − ThisColSum) / ExposureBucket    (0 when ExposureBucket = 0)

Verification while writing this spec, with F = all vintages: VB22 matches every row of
`lgd_recovery` for all three EventTypes (11,628 rows each; max relative error on
ExposureBucket 8.6e-16, max absolute error on RecoveryPct 1.1e-15). VB44 matches on a 3,000-row
sample (RecoveryPct error 4e-16). The file's row set is ts = 1…B+1 and b = ts…B+1, where B is
the highest runoff bucket. The last bucket column is empty (exposure 0).

The other inputs the engine needs:

- `lgd_unfloored[ts] = 1 − Σ_b RecoveryPct·DF` along row ts, with
  `DF = (1+r)^(−(b−ts+1)/12)` (DiscountIndex = b − ts + 1).
- `lgd_file[ts] = running maximum over ts' ≤ ts of lgd_unfloored[ts']`. This matches the risk
  suite's floor exactly on VB44, VB22 and ALL.
- `implied_rate` stays the rate implied by the **full** file's DiscountFactor. The filter does
  not change the discount rate.
- `last_obs_file` is the largest b with ExposureBucket > 0 in the rebuilt triangle.

**Vectorise it as matrix products**, not loops. Let `P[c, ts] = X[c, ts−1] > 0` and `O = obs`:

    E[ts, b]    = (P ∘ X[:, ts−1])ᵀ @ O
    Prev[ts, b] = Pᵀ @ (X[:, b−1] ∘ O)
    This[ts, b] = Pᵀ @ (X[:, b]   ∘ O)

Zero the entries where b < ts. ALL (189 × ~330) runs in milliseconds. Keep a slow loop version
in the tests as the reference implementation.

### 3.3 Parse and storage (`engine/parse.py`)

- `parse_zip` also reads `runoff_triangle.csv` when it is present. It stays optional: a zip
  without it still uploads, with the filter unavailable.
- `RecoveryData` gains `runoff: dict[event] -> RunoffBlock | None`. A `RunoffBlock` holds
  `cohort: int32[C]` (yyyymmdd), `X: float64[C, K]` and `obs: bool[C, K]`. If the blocks are
  identical across EventTypes, store one copy (the same pattern as `identical_events`).
- `to_bytes`/`from_bytes`: add the arrays to the npz, keeping `allow_pickle=False`, so dates are
  stored as integers. Old blobs without these keys load with `runoff = None`.
- **Self-check at upload.** Rebuild the unfiltered triangles from runoff and compare them with
  `lgd_recovery` (RecoveryPct to 1e-9 absolute, ExposureBucket to 1e-9 relative). If the check
  fails, keep the dataset, set `profile["vintage_filter"] = False` and record the reason. Never
  let a filtered run use a runoff that does not reproduce the file.
- `profile()` adds `has_runoff`, `vintage_filter`, `cohort_first`, `cohort_last`,
  `cohort_count`.
- Size limits: apply the same guards as for the CSV (an uncompressed size cap, finite numbers,
  Bucket a whole number ≥ 0, a parseable CohortDate).

**Datasets uploaded before this change have no runoff stored**, because only the parsed npz is
kept, not the zip. They need the zip uploaded again. Today a second upload is refused as a
duplicate (`uq_dataset_project_sha`). Change `upload_datasets` so that if the duplicate's blob
has no runoff, the new parse **replaces that dataset's blob in place**. The dataset id, name,
overrides and results are kept, and the results are marked stale. The response says
"Updated with vintage data". Otherwise the duplicate is still refused.

### 3.4 Parameters

Two mutually exclusive ways to choose vintages, so a scenario can stay dynamic across zips:

- `vintage_start: Optional[date] = None`: include vintages with CohortDate ≥ this date. Month
  granularity, compared at month-end.
- `vintage_years: Optional[int] = Field(None, ge=1, le=50)`: include vintages whose CohortDate
  falls within the last N years **of that zip's own latest vintage**. For example, if the latest
  vintage is 2026-07-31 and N = 10, cohorts after 2016-07-31 are included (2016-08-31 onwards).

A validator rejects both being set. Both blank means all vintages, which is today's behaviour,
so the golden values are unchanged. Per-zip overrides work automatically through `Params`.

### 3.5 Engine

- `RecoveryData.triangles(event, vintage_start=None)` resolves the filter and returns
  `Triangles` from the runoff rebuild when a filter is active, or from `lgd_recovery` as now when
  it is not. The cache key becomes `(event, resolved_start)`.
- `compute()` resolves `vintage_years` against the zip's latest cohort for that event.
- Errors (`EngineError`): the filter is requested but the dataset has no usable runoff ("Upload
  the zip again to enable the vintage filter"); the start is after the last vintage; the filter
  leaves no exposure.
- New `config` keys: `vintage_start_effective`, `vintage_first`, `vintage_last`,
  `cohorts_included`, `cohorts_total`, `exposure_share` (share of at-default exposure kept) and
  `vintage_filter` (bool).
- Always add an info warning when the filter is active, for example: "Vintages from 2016-08:
  120 of 189 cohorts, 73% of the at-default exposure."
- **Labelling.** With a filter, "Original LGD" is no longer the file's figure. It is the LGD of
  the rebuilt subset, and the tie-out is 0 by construction. Label the column "LGD (vintages from
  YYYY-MM)" in the UI and exports, and do not show the file-floor warning wording as if it came
  from the file.
- Warnings worth adding, because a shorter vintage window changes what is observed:
  - The observed triangle is now at most (months since the start date) buckets deep, so more of
    each row is fitted tail. Report the new last observed bucket next to the unfiltered one.
  - Absolute MinExposure (R100m) cuts credibility much earlier on a smaller book. When
    `min_exposure_mode == "abs"` and fewer than `window` credible buckets remain on RefTS, warn
    and suggest percentage mode.
  - The opening exposure (E[1, 1]) changes, so a percentage MinExposure is relative to the
    subset.

### 3.6 Front end

- Scenario form, a new "Data" group: **Vintages** select with All vintages / Last N years / From
  date. Show the matching input (number, or month picker) and store the right parameter. Add
  help text in `help.js`.
- Zip page header: "Vintages: 2016-08 to 2026-07 (120 of 189, 73% of exposure)", or "All
  vintages".
- Project results grid: a small marker on cells whose run used a vintage filter.
- Dataset list: show the vintage range. If `has_runoff` is false, show "Re-upload the zip to
  enable the vintage filter".

### 3.7 Exports

- Config sheet (values and formula workbooks): rows for the vintage parameters and the
  effective window, cohorts included and exposure share.
- Formula workbook: with a filter, the raw-data sheet holds **the rebuilt rows** (all 13
  columns: DiscountIndex = b − ts + 1, DF = (1+r)^(−idx/12), Contribution, a running
  CumulativeSumPV per ts, and LGD with the running-max floor of 3.2). The README states that the
  rows were rebuilt from `runoff_triangle.csv` for vintages from X. Check how the file fills the
  per-row LGD column on rows before the last bucket, and match it.
- Summary workbook: a "Vintages" column on the headline sheet.

---

## 4. Tests to add

Engine, three methods:
1. Synthetic row R(b) = k·s_logn(b; μ=3.2, σ=0.8): the fit recovers μ and σ to 1e-10, and each
   single-override path recovers the other parameter.
2. A row with a2 ≥ 0: μ and σ are nan, there is a warning, the logn columns are blank,
   exponential and power are unchanged, and method 3 raises `EngineError`.
3. VB44 and VB22 at defaults: μ and σ within 1e-3 of the table in 2.2. The golden suite passes
   as described in 2.7.
4. No result, payload, export or chart contains a reference-curve key or label (scan
   `to_json()` and each export's sheet names and headers for "client" and "reference").
5. The formula workbook under each of methods 1, 2 and 3 recalculates to the engine.

Engine, vintages:
6. For every fixture zip and every EventType: rebuild with no filter equals `lgd_recovery`
   (R to 1e-12, E relative 1e-12, lgd_file and lgd_unfloored to 1e-12).
7. The vectorised rebuild equals the slow loop reference on a random vintage subset.
8. A start before the first vintage gives the same output as no filter, for the whole
   `to_json()`.
9. `vintage_years` resolves per zip (VB44 and VB15 get different start dates from the same
   scenario).
10. Both parameters set fails validation. A start after the last vintage raises `EngineError`.
11. Storage round trip with runoff. An old blob without runoff runs unfiltered and gives a clear
    error when filtered.

API and migration:
12. Stored params containing `client_cohort` still load (the retired key is dropped). After the
    migration, a scenario that stored method 3 is still method 3 (log-normal), has no
    `client_cohort`, and its results are stale. A run then produces log-normal figures.
13. Uploading a curve with `kind = "shape"` is refused; `kind = "applied"` still works.
14. Re-upload of a duplicate zip whose stored blob lacks runoff replaces the blob, keeps the
    dataset id and marks results stale. A second re-upload is refused as before.
15. Scenario create/update with the new parameters.

Front end: run `scripts/browser_walkthrough.py` (or extend it) to select method 3 log-normal
and "Last 10 years", run, and open the zip page and the settings comparison panel without
console errors.

---

## 5. Out of scope

- A vintage **end** date or excluding single vintages. The parameter design leaves room for
  `vintage_end` later.
- Weighting vintages differently.
- Deleting the stored reference-shape curve rows. They stay in the database, unused.
- Rewriting the methods note (.docx). Henry will rewrite its Method 3 section and add a paragraph
  on vintage windows.

## 6. Decisions for Henry before the build

1. "Logistic" means the client's log-normal form (Section 1.1). Needed before Milestone 3.
2. ~~Where scenarios saved on the old method 3 go~~. **Decided 9 Oct 2026: they move to
   log-normal** (they keep method 3; Section 2.4).

## 7. Build order (milestones)

1. **Sync and baseline.** `git pull --ff-only`, branch, run `pytest`. Stop for review.
2. **Remove the reference shape.** Engine, params, retired-key handling, migration, runner,
   routers, curve upload, JS, exports, golden-test adjustment (2.7), tests 4, 12 and 13. At the
   end the app runs with methods 1 and 2 only, and their numbers are unchanged.
3. **Log-normal engine.** Method 3 in `params.py` and `core.py`, tests 1–3.
4. **Log-normal through the app.** Runner, routers, JS, values/summary/tables/curves exports,
   agent glossary, README.
5. **Formula workbook.** `formula_xlsx.py` with the reference shape removed and log-normal
   added, test 5.
6. **Runoff parse and storage.** `parse.py`, self-check, profile, re-upload path, tests 6, 7, 11
   and 14.
7. **Vintage filter in the engine.** Parameters, `triangles()`, `compute()`, warnings, labels,
   tests 8–10 and 15.
8. **Vintage filter UI and exports.** 3.6 and 3.7, plus the browser walkthrough.
9. **Final check.** Full test suite, `scripts/smoke_test.py`, and a manual run on ALL with
   method 3 and "Last 10 years". Write a short "what changed" section at the end of this file,
   as the original spec does in its Section 12.

## 8. Definition of done

- The app offers exactly three tail methods (exponential, power law, log-normal). No reference
  curve shape remains in the engine, UI, exports or API. Client applied curves still work for
  comparison.
- With the vintage filter off, every exponential and power-law number is identical to today.
- Method 3 (log-normal) runs end to end: engine, UI, all three exports, and a formula workbook
  that recalculates to the engine.
- Saved scenarios that used the old method 3 load as log-normal and are flagged to run
  again.
- A scenario on "Last 10 years" runs on every zip whose runoff passes the self-check, shows the
  vintage window everywhere a result is shown, and fails with a clear message on zips that need
  re-uploading.

## 9. What changed during the build (9 October 2026)

Built in one pass on `feature/three-methods-and-vintage-start` (Henry asked for every milestone
at once after reviewing Milestone 1). Everything in Sections 2 and 3 is in, with these notes:

- **"Logistic" was read as the client's log-normal form** (Section 1.1). Henry has not yet
  confirmed this; only Section 2.2 would change if a true logistic was meant.
- **`vintage_start` is a string "YYYY-MM"**, not a `date`, so it serialises unchanged into the
  JSON parameter columns and the month picker. "YYYY-MM-DD" is accepted and normalised.
- **A start at or before the first vintage is treated as no filter**, so the whole output
  (except the stored parameters) is identical to an unfiltered run, as test 8 requires. Test 8
  therefore compares `to_json()` without the `params` key.
- **The retired key is dropped on input too.** A scenario sent with `client_cohort` from a
  stale browser is accepted with the key removed, rather than refused.
- **The migration runs in Python at every start** (`runner.migrate_three_methods`): it strips
  the retired key from scenarios and overrides and marks every result stored before this
  change as out of date, including methods 1 and 2, because their stored tables lack the
  log-normal columns. `migrations/005_three_methods.sql` is kept for the record and is
  optional. Legacy results are flagged in the matrix, on the zip page and in a project notice
  (the notice counts the method 3 ones).
- **The formula workbook fits the log-normal with SLOPE/INTERCEPT helper rows** (Tail_Fit
  rows 12–17, the two-stage form of the quadratic fit) instead of LINEST, so the fit honours
  the same blank-point rule as λ and γ and needs no array formulas. The per-TermStep table on
  Tail_Fit therefore starts on row 20 instead of 15. Config keeps every address up to B30;
  B12 is the μ override, B20 the σ override, B31–B37 the log-normal block, and the vintage
  rows follow. Excel recalculation matched the engine to 1e-13 on VB44 and VB22 under methods
  1, 2 and 3, with each override path and with a vintage filter (Excel through COM, not
  LibreOffice).
- **Result payloads are now loaded only by the routes that need them**, and the final LGD
  series is kept in the summary. The project page with seven zips took about 25 seconds over
  the Supabase link before this; it takes about two now.
- **The golden tests** assert only the surviving columns. The "selected" columns are checked
  under methods 1 and 2 against the golden exponential and power-law columns; pv_selected,
  uplift and lgd_horizon are checked for consistency with the per-shape columns instead,
  because the golden files hold them for the removed method only. The golden λ and γ differ
  slightly from the Section 2.2 table because the workbooks' Raw_Debug is an older cut of the
  data than the 2 October zips; the μ/σ check (test 3) runs on the zips.
- **Out of scope, still to do by Henry:** rewrite Method 3 of the methods note (.docx) and add
  a vintage paragraph; re-upload the seven zips in the existing projects so the vintage
  filter is available (they were stored without runoff data); push the branch.
