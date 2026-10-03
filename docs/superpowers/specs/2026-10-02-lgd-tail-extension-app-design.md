# LGD Tail Extension App – Design

Date: 2 October 2026. Status: approved by Henry on 2 October 2026 and built. Section 12 lists what changed during the build.

## 1. Purpose

Automate the hazard-rate bucket extension framework that is currently built by hand as one
formula-driven workbook per cohort (`Nutun_HazardRate_Bucket_Extension_Framework_VB44_1.xlsx`,
`..._VB22.xlsx`). Users upload several risk-suite debug zips, define scenarios, run them, and
view and export the results for each zip.

Only `lgd_recovery.csv` and `debug.json` are read from each zip. The other files
(`runoff_triangle`, `pd_scored`, `lgd_defaults`, the `.json` twins) are ignored.

## 2. Decisions taken with Henry

| Topic | Decision |
|---|---|
| Platform | FastAPI back end with a web front end |
| Scenario | Named global parameter set, run against every zip in the project, with per-zip overrides |
| Client curve (shape 3) | Six prototype curves embedded, auto-matched on `Category1`; replacement curves can be uploaded; the ALL zip picks a cohort curve or uses shape 1 or 2 |
| Export | Values-only Excel by default, formula-driven Excel on request, CSV of every table |
| Horizon | Explicit per scenario: Target TermStep and MaxBucket. No auto rule |
| Users | Hosted, multi-user |
| Hosting | Supabase (Postgres and Storage) plus a separate host for the FastAPI container |
| Sign-in | Built-in email and password. Not Supabase Auth |
| Visibility | Projects with members |
| MinExposure | Absolute Rand, or percentage of the TermStep 1 opening exposure |
| LastTS default | Last observed TermStep, overridable |

## 3. Approach

Chosen: **one FastAPI service** that holds the calculation engine, the JSON API and serves a
no-build front end (ES modules, Plotly vendored locally). One container, one deploy.

Rejected:
- React/Vite single-page app plus API. Adds a Node build chain, CORS and a second deploy for
  no functional gain at this size.
- Browser talking straight to Supabase with row-level security, FastAPI as compute only.
  Conflicts with built-in sign-in and spreads authorisation across two places.

## 4. Calculation engine

A pure numpy module with no web or database dependency. It reproduces the example workbooks
exactly; the workbook sheet each step mirrors is shown in brackets.

Inputs: observed triangles `R[ts,b]` (RecoveryPct) and `E[ts,b]` (ExposureBucket) for the chosen
EventType, zero where `b < ts`; the file LGD per TermStep; the client curve; the parameters.
`N` is the highest TermStep in the file.

1. **Rate.** Scenario rate, or if blank the rate implied by the file,
   `(1 / DiscountFactor at DiscountIndex 1)^12 − 1`. `v = (1+r)^(−1/12)`.
2. **Credibility (Tail_Fit).** Per TermStep: last observed bucket (largest `b` with `E > 0`);
   last credible bucket (largest `b` with `E ≥ MinExposure`, falling back to last observed);
   window = last `W` credible buckets, not starting before `ts`.
3. **Shapes (Tail_Fit).** Exponential `e^(−λb)`, power `b^(−γ)`, client curve for the chosen
   cohort. λ and γ are minus the least-squares slope of `ln R[RefTS,b]` on `b` and on `ln b`
   over `[FitStart, last credible bucket of RefTS]`, skipping non-positive points. Either can
   be overridden.
4. **Scale (Tail_Fit).** Per TermStep and shape: `Σ observed over window ÷ Σ shape over window`.
5. **Extended triangles (Ext_Exp, Ext_Power, Ext_Client).** `R[ts,b]` up to the last credible
   bucket, then `max(floor, scale × shape[b])` to MaxBucket.
6. **Results per observed TermStep (Results).** Exposure at ts, original LGD from the file,
   replica LGD from observed buckets only, tie-out, LGD under each shape, selected LGD, PV
   recoveries original and selected, uplift, LGD within the short horizon, undiscounted
   recoveries observed and selected, last credible bucket, buckets added. Exposure-weighted
   and simple averages. PV = `Σ row × v^b ÷ v^(ts−1)`.
7. **LGD to Target TermStep (LGD_300).** TermSteps up to LastTS use their own row. Beyond it
   the BaseTS row's extended cash curve `c(b)` is rolled forward: balance factor
   `1 − Σ c(BaseTS..ts−1)`, `RecoveryPct(ts,b) = c(b) ÷ factor`, LGD capped at MaxBucket.
   Derived LGD under all three shapes for every TermStep, validation (own row minus derived),
   LGD within the valuation horizon and the short horizon, undiscounted remaining recovery,
   source flag, maximum absolute validation, balance factor at Target.

Generalisations beyond the examples: `N`, Target TermStep and MaxBucket are not fixed at
98/152, 300 and 420. Warnings, not failures, are raised when MaxBucket exceeds the client
curve length (553, shorter for cohorts 23, 25 and 44), when Target is below `N`, when the
balance factor reaches zero, or when the regression window has fewer than three points.

### Parameters (scenario, each overridable per zip)

EventType (Lifetime), rate (blank = implied), Target TermStep, MaxBucket, MinExposure
(mode Rand or percent, value; default R100m), Window W (12), FitStart (24), RefTS (1),
Method 1/2/3 (3), client cohort (blank = the zip's Category1), short horizon (12), valuation
horizon (120), λ override, γ override, floor (0), BaseTS (1), LastTS (blank = last observed
TermStep with data).

## 5. Data model

Postgres on Supabase in a dedicated schema `hazard` that is not exposed through the Supabase
Data API. Row-level security is enabled on every table with no policies, so the anon and
authenticated API roles can read nothing. Only the FastAPI service connects, with the database
connection string. SQLite and a local folder are used for development and tests.

- `users`: email, name, password hash (argon2id), is_admin, is_active.
- `projects`, `project_members` (role owner, editor or viewer).
- `datasets`: one per uploaded zip. Project, display name, Category1, original file name,
  SHA-256, uploader, timestamp, metadata from `debug.json`, profile (TermStep range, last
  observed bucket, implied rate, EventTypes and whether they are identical), storage key.
- `client_curves`: built-in (six cohorts, t = 1..553) or uploaded per project.
- `scenarios`: project, name, description, parameters (JSON).
- `scenario_overrides`: scenario, dataset, parameters (JSON, partial).
- `results`: scenario, dataset, effective parameters, summary (JSON), per-TermStep tables
  (JSON), warnings, computed by, computed at, stale flag.

Storage: the parsed `lgd_recovery` table is stored per dataset as one compressed array file
(a few megabytes at most) in a private Supabase Storage bucket. The original zip is not kept;
its hash and file name are. Identical EventType blocks are stored once.

## 6. API and behaviour

- **Auth.** Email and password, argon2id, HttpOnly SameSite session cookie, custom header
  required on mutating requests. First admin is created from environment variables. Admin
  creates and deactivates users and resets passwords. No email flows.
- **Projects.** Create, rename, add and remove members. Every project route checks membership.
  Viewers cannot upload, edit or run.
- **Upload.** Several zips at once. Each is streamed to a temporary file; `debug.json` and
  `lgd_recovery.csv` are read; the 13 expected columns are validated; a duplicate hash in the
  project is rejected with a clear message. A zip that fails does not block the others.
- **Scenarios.** Create, clone, edit, delete. An override grid shows one row per zip.
  Editing parameters marks affected results stale.
- **Run.** Runs one scenario for one zip or for all zips, synchronously (a run is well under
  a second per zip once parsed). A failure on one zip is recorded against that zip only.
- **Export.** Per zip and scenario: values workbook, formula-driven workbook, CSV per table.
  Per project: a summary workbook across zips and scenarios.

## 7. Front end

No build step. Pages:

1. Sign in. Project list.
2. **Project home**: zips (upload, profile, delete), scenarios, a matrix of zips by scenarios
   showing exposure-weighted selected LGD and uplift with stale and error markers.
3. **Zip view** (the main results page, one zip at a time, scenario selector):
   headline figures (exposure-weighted original, exponential, power, client, selected LGD;
   uplift; λ, γ, half-life; tie-out; warnings); Results by TermStep table; LGD to Target
   table; tail-fit table; charts matching the example workbooks (observed against extended
   RecoveryPct on a log scale for a chosen TermStep with the client curve; LGD by TermStep
   original against the three shapes; uplift by TermStep; LGD to Target, final and derived;
   valuation-horizon against lifetime LGD); scenario comparison for this zip.
4. **Scenario editor** with the per-zip override grid.
5. Admin: users. Project settings: members, client curves.

## 8. Excel exports

- **Values workbook**: Config (effective parameters and derived values), Results, LGD by
  TermStep, Tail_Fit, the three extended triangles, Hazard_Obs, native charts.
- **Formula workbook**: the same sheet structure, names and formulas as the examples,
  generalised to the zip's `N`, Target and MaxBucket, written with engine values cached in
  every formula cell so it opens populated. Config cells stay live inputs.

## 9. Deployment

One Dockerfile. Configuration by environment variables: database URL, Supabase URL and
service key, bucket name, session secret, first admin. The host must accept request bodies of
about 100 MB (the largest zip is 44 MB) and give the container about 1 GB of memory. Schema
creation is a SQL migration file. Provisioning Supabase and the app host is done by Henry;
the Supabase connector is not authorised in this session.

## 10. Testing

- **Engine golden tests**: feed the Raw_Debug sheet of each example workbook through the
  engine with that workbook's Config and compare every Results and LGD_300 column, λ, γ and
  the weighted averages against the workbook's cached values to 1e-9.
- **Parser tests** on all seven zips: tie-out of replica to file LGD below 1e-9.
- **API tests** on SQLite: sign-in, membership isolation, viewer restrictions, upload,
  duplicate rejection, override precedence, stale marking, run, export.
- **Formula export test**: structure and cached values checked against the engine. A full
  recalculation check needs Excel or LibreOffice, which is not installed here; the user
  verifies by opening one export in Excel.

## 11. Out of scope

PD outputs, run-off triangle and defaults files; email password reset; single sign-on;
background job queue; provisioning cloud resources; keeping original zips.

## 12. Changes made during the build

- **File LGD floor.** The new zips showed that the risk suite floors each TermStep's LGD at
  the previous TermStep's final LGD (verified on all seven zips: file LGD equals the running
  maximum of `1 - CumulativeSumPV` to 3e-16). The example workbooks did not hit this. The
  engine keeps the file value as Original LGD, adds a floor-gap column, measures the tie-out
  against `1 - CumulativeSumPV`, and raises a warning naming the TermSteps. The extended LGD
  is not floored.
- **Formula workbook reads Raw_Debug by position.** One `SUMIFS` per Hazard_Obs cell would
  mean about 200,000 scans of 55,000 rows on the 329-TermStep zips. A `Raw_Index` sheet holds
  the first row and row count per TermStep and Hazard_Obs uses `INDEX`. Excel recalculates
  the largest workbook in 16 seconds. `Results` gained columns R and S for the floor.
- **Identical EventType blocks are written once** to Raw_Debug.
- **Third storage option.** `STORAGE_BACKEND=db` keeps parsed zips in a database table, so a
  deployment needs only the connection string. Supabase Storage remains available.
- **Charts are drawn by a small SVG module**, not Plotly. Fonts are served locally.
- **Git.** No repository was initialised; Henry did not approve it.
- **Supabase set up and verified (later on 2 October 2026).** Project `dev_factor_extension`
  (`eqknozafzvnrxecuxirr`, London). The app connects as a dedicated role `hazard_app` that has
  row rights on schema `hazard` only; `postgres` owns the tables and migrations run through
  the Supabase CLI. The app was run from this PC against the project and passed the
  end-to-end smoke test.
- **Not verified:** Supabase Storage against a real bucket, and the Docker build (Docker was
  not running).
