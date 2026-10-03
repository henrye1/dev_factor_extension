# LGD Tail Extension

A web app that automates the hazard-rate bucket extension framework. Upload several
risk-suite debug zips, define scenarios, run them, and view and export the extended LGD for
each zip.

It reproduces the hand-built workbooks (`Nutun_HazardRate_Bucket_Extension_Framework_VB44_1.xlsx`,
`..._VB22.xlsx`) to 13 decimal places. Only `lgd_recovery.csv` and `debug.json` are read from
a zip.

## Run it on this PC

The app only serves pages while it is running. If a browser says the page failed to load,
the app is not running.

- **From the folder:** double-click `start_app.cmd`. A window opens, the app starts, and the
  browser opens on <http://127.0.0.1:8000> by itself. Closing that window stops the app.
- **From VS Code:** Terminal > Run Task > *LGD Tail Extension: start*, then open
  <http://127.0.0.1:8000>.
- **To stop it** wherever it was started: double-click `stop_app.cmd`, or run the task
  *LGD Tail Extension: stop*.

From a terminal the equivalent is:

```powershell
pwsh .\run_local.ps1
```

Which database it uses depends on `.env`:

- **`.env` present** (the current state): the app uses the Supabase project named in it.
  Sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `.env`, then change the password.
- **No `.env`**: the app uses a SQLite file and the parsed zips under `.\data`. Sign in as
  `admin@local` with password `change-me-now`, then change the password.
 The script creates a Python 3.12 virtual environment in
`%USERPROFILE%\.venvs\dev_factor_extension` if it is missing; it lives outside OneDrive on purpose.

## How it is used

1. **Project.** Create a project, for example "Nutun July 2026". Add members under
   *Members and curves*. Roles: viewer (read and export), editor (upload, edit, run),
   owner (also manage members). An administrator creates user accounts under *Users*.
2. **Zips.** Drop the debug zips on the project page. Each becomes a zip named after its
   `Category1` (VB44, VBALL and so on).
3. **Scenario.** A scenario is a named set of the Config parameters. It applies to every zip.
   Any zip can be given its own value for any parameter under *Per-zip overrides*.
4. **Run.** *Save and run all zips* on the scenario, or *Run all scenarios* on the project.
   A failure on one zip is reported against that zip and does not stop the others.
5. **Results.** The project page shows exposure-weighted selected LGD for every zip and
   scenario. Select a figure to open that zip: headline LGD under each shape, six charts,
   the Results, LGD-to-Target and Tail-fit tables, and the downloads.
6. **Export.** Per zip and scenario: Excel with values, Excel with live formulas (same
   sheets and defined names as the hand-built workbooks), and CSV of each table. Per
   project: a summary workbook with one row per zip and scenario (headline, horizon and
   decay figures), LGD by TermStep in long and wide form, scenario deltas against the first
   scenario, cumulative recovery curves next to the reference and client applied curves,
   exposure and credibility by TermStep, a chart per zip, and one sheet per zip and scenario
   of marginal recoveries by TermStep for the first 120 remaining steps.

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| Target TermStep | 300 | The LGD table runs from TermStep 1 to this |
| MaxBucket | 420 | Buckets are extended to this index |
| Valuation horizon | 120 | LGD is also given within this many months |
| Short horizon | 12 | The 12-month basis |
| Method | 3 | 1 exponential, 2 power law, 3 reference curve shape |
| Reference curve | empty | Empty = the zip's own category |
| MinExposure | R100m | Credibility cut, as Rand or as % of TermStep 1 opening exposure |
| Window W | 12 | Last W credible buckets set the tail level |
| Hazard floor | 0 | Minimum RecoveryPct on the tail |
| Reference TermStep | 1 | Row whose tail is fitted for λ and γ |
| FitStart | 24 | Start of the regression window |
| λ, γ override | empty | Empty = fitted |
| Base TermStep | 1 | Row rolled forward beyond LastTS |
| LastTS | empty | Empty = last observed TermStep |
| EventType | Lifetime | Block of `lgd_recovery` used |
| Discount rate | empty | Empty = the rate implied by each file |

### Reference curves (method 3)

Method 3 scales each zip's tail to a reference curve. The six built-in curves are the prototype
log-normal cohort curves (11, 15, 22, 23, 25, 44; t = 1..553). A zip uses the curve whose label
equals its category. The ALL zip has none: give it a reference curve in an
override, upload a curve labelled `ALL`, or use method 1 or 2. Upload a `.csv` or `.xlsx`
whose first column is `t` = 1, 2, 3 … and whose other columns are headed with curve labels.
An uploaded label that matches a built-in curve replaces it within that project.

### Client applied recovery curves

Under *Members and curves* a second upload takes the curves the client actually applies, in the
same layout (a month column, then one column per cohort label). On upload you say whether the
monthly rates are a share of the balance at default (face value) or of the balance still
outstanding each month; the app converts the latter to the face basis. Each zip is matched to the
curve whose label equals its category. The results charts then show a dashed "Client applied"
line: on the RecoveryPct chart the curve rolled forward to the chosen TermStep, and on the LGD
charts the LGD it implies at the file's discount rate. Applied curves never enter the calculation
and never mark results out of date. The same page has a comparison section: pick a cohort, scenario
and TermStep to see the observed data, our three fitted tails and the client's curve on one chart,
on either the face-value or the outstanding-balance basis, with a cumulative recovery table.
Below it, "Download curves" produces one workbook per scenario with final LGD by TermStep for
every cohort, each cohort's marginal recovery curve on both bases next to its reference curve and
the client's applied curve, cumulative recovery, and a sheet per cohort of marginal recoveries by
TermStep for the first 120 remaining steps.

### The assistant

With `ANTHROPIC_API_KEY` set in `.env`, an "Assistant" button appears on the project and results
pages. Type an instruction such as "set MaxBucket to 60 on the Exponential scenario" or "give VB44
a LastTS of 77", or a question such as "what is the LGD for VB44 at TermStep 60 under Power law".
The assistant reads the project's state and results through the app's own API and, for any
change, shows a proposal with the old and new values. Nothing changes until you confirm; the
confirmation goes through the ordinary scenario and override endpoints with your own role, and
then reruns. Viewers can ask but not change. Every instruction, reply and proposal is kept in the
project's assistant log (`GET /api/projects/{id}/agent/log`). The model is `claude-opus-5-5` by
default (`AGENT_MODEL`); an instruction costs about a cent or two.

### Two things the app reports that the workbooks did not

- **File LGD floor.** The risk suite floors each TermStep's LGD at the previous TermStep's
  LGD. Where that bites, the file's LGD is not `1 − CumulativeSumPV`. The app shows the file
  value as "Original LGD", reports the gap in its own column, and measures the tie-out
  against `1 − CumulativeSumPV`, which always ties to zero. The extended LGD is not floored.
- **Warnings** when MaxBucket runs past the end of the reference curve, the Target is below the
  observed range, the balance factor reaches zero, or the λ/γ regression has too few points.

## Hosting: Supabase and a container host

The app is one container. Supabase supplies Postgres and, optionally, Storage. Sign-in is
built into the app; Supabase Auth is not used.

### 1. Supabase

The Supabase project `dev_factor_extension` (ref `eqknozafzvnrxecuxirr`, London) was set up on
2 October 2026 with the script below, and `.env` in this folder points at it.

```powershell
supabase link --project-ref <ref>          # once per folder; the CLI must be signed in
python scripts/supabase_setup.py --admin-email you@example.com
```

The script uses the Supabase CLI, so it needs no database password from you. It:

1. applies `migrations/001_init.sql`: the private schema `hazard`, nine tables, row-level
   security on every table;
2. creates the database role `hazard_app` with a generated password. Only a SCRAM verifier is
   sent to the database; the password is written to `.env` and nowhere else;
3. applies `migrations/002_app_role.sql`: `hazard_app` may read and write rows in `hazard`
   and nothing else. It cannot change tables and has no rights on any other schema. One
   policy per table admits `hazard_app`; the API roles `anon` and `authenticated` have none;
4. writes `.env`: the session-pooler connection string, a session secret, and the first
   administrator. The administrator's first password is `ADMIN_PASSWORD` in `.env`.

Run it again with `--rotate` to replace the database password. Later schema changes go in a
new file under `migrations/` and are applied with
`supabase db query --linked -f migrations/<file>.sql`. `AUTO_CREATE_SCHEMA` is `false` because
the app role is not allowed to create tables.

**Do not add `hazard` to the exposed schemas** of the Data API (dashboard: API settings).

Parsed zips are kept in the table `hazard.blobs` (`STORAGE_BACKEND=db`), about 0.2 to 2 MB
per zip. To use Supabase Storage instead, create a **private** bucket (default name
`hazard-data`) and set `STORAGE_BACKEND=supabase`, `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`
(a secret key or the legacy service_role key; it stays on the server).

To check a running app end to end against whatever `.env` points at:

```powershell
python scripts/smoke_test.py http://127.0.0.1:8000
```

### 2. App host

Any host that runs a Docker container (Azure Container Apps, Render, Fly.io, Railway).
Requirements: request bodies up to about 100 MB (the largest zip is 44 MB), about 1 GB of
memory, HTTPS, and **one instance** (the sign-in throttle and the dataset cache are held in
the process).

```bash
docker build -t lgd-tail-extension .
docker run -p 8000:8000 --env-file .env lgd-tail-extension
```

Environment variables are listed in `.env.example`. For a hosted deployment set at least
`DATABASE_URL`, `STORAGE_BACKEND`, `SESSION_SECRET`, `COOKIE_SECURE=true`, `ADMIN_EMAIL` and
`ADMIN_PASSWORD`. The first administrator is created once, when no user exists. On Postgres
the app refuses to start unless `SESSION_SECRET` is at least 32 characters.

### Security behaviour

- Passwords are hashed with argon2id. The session cookie is signed, HttpOnly and SameSite=Lax.
  Signing out, changing a password, an administrator's reset and deactivation each end every
  session of that user.
- Sign-in is limited to 8 failures per account and 40 per address in 15 minutes.
- A user who is not a member of a project gets "not found" for everything in it.
- Uploads are refused before the body is read when the user is not signed in or the request
  is over `MAX_UPLOAD_MB`. A zip's `lgd_recovery.csv` may be at most 600 MB uncompressed and
  TermStep and BucketIndex must be whole numbers from 1 to 2000.
- Names typed by users are written to Excel as text, never as formulas.
- Excel exports of a result that is out of date are refused until the scenario is run again.

### What has and has not been tested

Tested: the engine against both workbooks; all seven zips; the API on SQLite; both Excel
exports; the formula export recalculated in Excel; the front end in Edge; and the app running
from this PC against the Supabase project (sign-in, upload, run, every export, delete).

Not tested: Supabase Storage against a real bucket (its requests are tested against a stub)
and the Docker build, because Docker was not running. From South Africa each request to the
London database takes one to two seconds; hosting the app in or near London removes that.

## Development

```powershell
$py = "$env:USERPROFILE\.venvs\dev_factor_extension\Scripts\python.exe"
& $py -m pip install -r requirements-dev.txt
& $py -m pytest -q                      # 189 tests, about 35 seconds
```

| Path | Purpose |
|---|---|
| `hazard_ext/engine/` | Zip parser, parameters, reference curves, the calculation (`core.py`). No web or database code |
| `hazard_ext/export/` | Values workbook, formula workbook, CSV and summary |
| `hazard_ext/web/` | FastAPI app: settings, models, security, storage, routers, static front end |
| `migrations/001_init.sql` | Postgres schema, generated by `scripts/make_migration.py` |
| `migrations/002_app_role.sql` | Rights and policies for the app's database role |
| `migrations/003_applied_curves.sql` | Adds the kind and basis columns for applied curves (applied 3 October 2026) |
| `migrations/004_agent_log.sql` | The assistant's log table (applied 3 October 2026) |
| `tests/golden/` | Inputs and expected outputs extracted from the two example workbooks |
| `scripts/extract_golden.py` | Rebuilds the golden fixtures from the workbooks |
| `scripts/excel_recalc.ps1`, `compare_recalc.py` | Recalculate a formula export in Excel and compare with the engine |
| `scripts/browser_walkthrough.py` | Drives the running app in Edge through the whole flow with the seven zips |
| `scripts/supabase_setup.py` | Sets up the linked Supabase project and writes `.env` |
| `scripts/smoke_test.py` | Checks a running app end to end through its API, then cleans up |
| `docs/superpowers/` | Design spec and implementation plan |

The formula workbook differs from the hand-built ones in two ways. `Hazard_Obs` reads
`Raw_Debug` by position through a `Raw_Index` sheet instead of one `SUMIFS` per cell, which
would be unusable on the 329-TermStep zips. `Results` has two extra columns for the file LGD
floor. A block pasted into `Raw_Debug` must keep the delivered row order; `Raw_Index` column D
flags any TermStep that does not.

Fonts: IBM Plex Sans, SIL Open Font License (`hazard_ext/web/static/fonts/OFL.txt`).
