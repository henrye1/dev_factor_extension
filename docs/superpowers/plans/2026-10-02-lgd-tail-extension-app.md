# LGD Tail Extension App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hosted multi-user web app that ingests risk-suite debug zips, runs the hazard-rate bucket extension under named scenarios, and shows and exports results per zip.

**Architecture:** One FastAPI service. A pure numpy engine (`hazard_ext/engine`) reproduces the example workbooks; the web layer (`hazard_ext/web`) adds sign-in, projects, datasets, scenarios, runs and exports over SQLAlchemy; a no-build front end is served as static files.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, psycopg 3, numpy, pandas, XlsxWriter, openpyxl (tests only), argon2-cffi, itsdangerous, httpx, pytest, Plotly (vendored JS).

**Spec:** `docs/superpowers/specs/2026-10-02-lgd-tail-extension-app-design.md`

## Global Constraints

- Only `lgd_recovery.csv` and `debug.json` are read from a zip.
- Engine output must equal the example workbooks' cached values to 1e-9.
- Postgres tables live in schema `hazard`; row-level security enabled, no policies.
- SQLite plus a local folder for development and tests; no cloud calls in tests.
- Built-in email and password sign-in (argon2id). No Supabase Auth.
- Every project route checks membership; viewers are read-only.
- Original zips are not stored; parsed data, `debug.json` and SHA-256 are.
- No git repository (not approved); tasks have no commit step.
- Virtual environment lives outside OneDrive at `C:\Users\APR\.venvs\dev_factor_extension`.
- Test command: `C:/Users/APR/.venvs/dev_factor_extension/Scripts/python -m pytest -q`.

## File Structure

```
requirements.txt, Dockerfile, .dockerignore, .env.example, README.md, run_local.ps1
migrations/001_init.sql              Postgres schema, RLS on
hazard_ext/engine/params.py          Params model, defaults, override merge, validation
hazard_ext/engine/parse.py           zip -> RecoveryData; RecoveryData <-> bytes
hazard_ext/engine/curves.py          built-in client curves; uploaded curve parsing
hazard_ext/engine/core.py            compute(data, params, curve) -> ExtensionResult
hazard_ext/engine/data/client_curves.csv
hazard_ext/export/values_xlsx.py     values workbook with charts
hazard_ext/export/formula_xlsx.py    live-formula workbook with cached values
hazard_ext/export/tables.py          CSV tables, project summary workbook
hazard_ext/web/config.py             settings from environment
hazard_ext/web/db.py                 engine, session, schema creation
hazard_ext/web/models.py             ORM models
hazard_ext/web/security.py           password hashing, session cookie
hazard_ext/web/storage.py            BlobStore: local folder, Supabase Storage
hazard_ext/web/deps.py               current user, project access
hazard_ext/web/runner.py             effective params, run, persist result
hazard_ext/web/routers/*.py          auth, admin, projects, datasets, curves, scenarios, results, exports
hazard_ext/web/main.py               app factory, static files, admin bootstrap
hazard_ext/web/static/               index.html, app.css, js modules, vendor/plotly
scripts/extract_golden.py            workbook -> tests/golden/*.npz
tests/                               golden, parse, params, api, export tests
```

---

### Task 1: Golden fixtures and zip parser

**Files:** `scripts/extract_golden.py`, `hazard_ext/engine/parse.py`, `tests/test_parse.py`, `tests/golden/vb44.npz`, `tests/golden/vb22.npz`

**Interfaces — Produces:**
- `RecoveryData` dataclass: `category: str`, `meta: dict` (debug.json), `event_types: list[str]`, `identical_events: bool`, `raw: dict[str, np.ndarray]` (event type -> array of shape (rows, 12): the 12 numeric columns in file order), methods `triangles(event) -> (R, E, lgd_file, n)` where `R`, `E` are `(n+1, n+1)` 1-indexed arrays and `lgd_file[ts]` is the LGD at the file's last observed bucket (nan if absent), `implied_rate(event) -> float`, `profile() -> dict`, `to_bytes() -> bytes`, `from_bytes(b) -> RecoveryData`.
- `parse_zip(path_or_file) -> RecoveryData`; raises `ParseError` (missing file, missing columns, non-contiguous buckets).
- `from_raw_frame(df, category, meta) -> RecoveryData` (used by golden tests to load a workbook's Raw_Debug).

- [x] `extract_golden.py` reads each example workbook with `data_only=True` and saves Raw_Debug, Config inputs, Config derived values, Results rows 10.., Results averages, LGD_300 rows, Tail_Fit columns A–J, Client_Curve.
- [x] Tests: all seven zips parse; three EventTypes identical; `implied_rate` equals `debug.json` InterestRate to 1e-6; round trip through `to_bytes`; a zip without `lgd_recovery.csv` raises `ParseError`.

### Task 2: Parameters and client curves

**Files:** `hazard_ext/engine/params.py`, `hazard_ext/engine/curves.py`, `hazard_ext/engine/data/client_curves.csv`, `tests/test_params.py`

**Interfaces — Produces:**
- `Params` (pydantic): `event_type='Lifetime'`, `rate: float|None`, `target_ts: int=300`, `max_bucket: int=420`, `min_exposure_mode: 'abs'|'pct'='abs'`, `min_exposure: float=1e8`, `window: int=12`, `fit_start: int=24`, `ref_ts: int=1`, `method: 1|2|3=3`, `client_cohort: str|None`, `horizon: int=12`, `horizon2: int=120`, `lambda_override: float|None`, `gamma_override: float|None`, `floor: float=0`, `base_ts: int=1`, `last_ts: int|None`.
- `merge_params(base: dict, override: dict|None) -> Params` (override keys win; unknown keys rejected).
- `builtin_curves() -> dict[str, np.ndarray]` (cohort label -> values for t=1..553); `parse_curve_file(bytes, filename) -> dict[str, np.ndarray]` (CSV or xlsx with a `t` column and one column per cohort).

- [x] Tests: defaults equal the workbook Config; override precedence; unknown key rejected; built-in curves equal the golden Client_Curve to 1e-15.

### Task 3: Engine core

**Files:** `hazard_ext/engine/core.py`, `tests/test_engine_golden.py`

**Interfaces — Consumes:** `RecoveryData.triangles`, `Params`, curve array.
**Produces:** `compute(data: RecoveryData, params: Params, curve: np.ndarray|None) -> ExtensionResult` with fields `config` (effective and derived values: rate, v, last_obs_file, lam_fit, gam_fit, lam, gam, ref_last_cred, half_life, min_exposure_abs, last_ts, cohort), `tail_fit` (dict of arrays by ts), `shapes` (3 × max_bucket), `ext` (3 × n × max_bucket), `R`, `E`, `results` (dict of column arrays by ts), `averages`, `lgd_ts` (dict of column arrays for ts 1..target), `lgd_ts_summary`, `warnings: list[str]`; `ExtensionResult.to_json() -> dict` (tables only, nan -> None).

- [x] Golden tests for VB44 and VB22: λ, γ, every Results column, weighted averages, every LGD_300 column, tie-out below 1e-9.
- [x] Tests on the seven zips: tie-out below 1e-9 with default params (cohort from Category1; method 1 for ALL).
- [x] Warnings: max_bucket beyond curve length, target below n, balance factor at or below zero, fewer than three regression points, method 3 without a curve (error).

### Task 4: Database, storage, security

**Files:** `hazard_ext/web/{config,db,models,security,storage,deps}.py`, `migrations/001_init.sql`, `tests/conftest.py`, `tests/test_security.py`

**Interfaces — Produces:** `Settings` (env: `DATABASE_URL`, `STORAGE_BACKEND=local|supabase`, `LOCAL_DATA_DIR`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET`, `SESSION_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `COOKIE_SECURE`); models `User, Project, ProjectMember, Dataset, ClientCurve, Scenario, ScenarioOverride, Result`; `hash_password`, `verify_password`, `make_session`, `read_session`; `BlobStore.put/get/delete`; dependencies `current_user`, `require_admin`, `project_access(min_role)`.

- [x] Tests: password hash round trip; tampered cookie rejected; local blob store round trip.

### Task 5: API

**Files:** `hazard_ext/web/routers/*.py`, `hazard_ext/web/runner.py`, `hazard_ext/web/main.py`, `tests/test_api.py`

Routes (all under `/api`): `POST auth/login`, `POST auth/logout`, `GET auth/me`, `POST auth/password`; `GET/POST admin/users`, `PATCH admin/users/{id}`; `GET/POST projects`, `GET/PATCH/DELETE projects/{pid}`, `GET/POST/DELETE projects/{pid}/members`; `GET/POST projects/{pid}/datasets` (multi-file), `PATCH/DELETE .../datasets/{did}`; `GET/POST/DELETE projects/{pid}/curves`; `GET/POST projects/{pid}/scenarios`, `GET/PUT/DELETE .../scenarios/{sid}`, `POST .../scenarios/{sid}/clone`, `PUT .../scenarios/{sid}/overrides/{did}`; `POST .../scenarios/{sid}/run` (optional `dataset_id`); `GET projects/{pid}/matrix`; `GET .../results/{sid}/{did}` (tables), `GET .../results/{sid}/{did}/curve?ts=`; exports in Task 7.

- [x] Tests: sign-in and wrong password; non-member gets 404; viewer cannot upload, edit or run; upload of two zips; duplicate rejected; override precedence in effective params; editing a scenario marks results stale; run all records per-zip errors without failing the others; mutating request without the `X-Requested-With` header is rejected.

### Task 6: Front end

**Files:** `hazard_ext/web/static/index.html`, `app.css`, `js/{api,main,views-*.js}`, `vendor/plotly.min.js`

Hash-routed single page: sign in, projects, project home (zips, scenarios, matrix), zip view (headline figures, tables, five charts, scenario comparison), scenario editor with override grid, project settings (members, curves), admin users.

- [x] Verified by running the app locally and exercising each page against the seven zips.

### Task 7: Exports

**Files:** `hazard_ext/export/{values_xlsx,formula_xlsx,tables}.py`, `hazard_ext/web/routers/exports.py`, `tests/test_exports.py`

Routes: `GET .../results/{sid}/{did}/export?kind=values|formula|csv&table=`, `GET projects/{pid}/export/summary`.

Formula workbook: sheets README, Config, Results, LGD_TS, Charts, Hazard_Obs, Tail_Fit, Ext_Exp, Ext_Power, Ext_Client, Client_Curve, Raw_Debug with the examples' defined names and formulas, generalised to n, target and max_bucket. Hazard_Obs uses a per-TermStep start row and INDEX instead of SUMIFS, because SUMIFS over 160k rows for 216k cells would make Excel unusable. Every formula cell carries the engine's value as its cached result.

- [x] Tests: values workbook cells equal engine output; formula workbook cached values equal engine output and defined names exist; CSV shape.

### Task 8: Packaging

**Files:** `requirements.txt`, `Dockerfile`, `.dockerignore`, `.env.example`, `README.md`, `run_local.ps1`

- [x] README covers local run, Supabase setup (schema, pooler connection string, private bucket, service key), deployment and environment variables.
- [x] Full test suite passes; app starts locally; end-to-end check with all seven zips.
