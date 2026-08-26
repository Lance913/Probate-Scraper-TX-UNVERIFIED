# Probate Scraper — 9 TX Counties

Pulls decedent-estate (probate) case filings from Texas county probate/county
clerk record portals daily and appends them to a Google Sheet. Sister system
to [`Lance913/Scraper_Python`](https://github.com/Lance913/Scraper_Python) and
`Foreclosure-Scraper-Collin-Ellis-Travis` — same matrix+collate architecture,
a different data domain, separate Google Sheet tabs so lead types never mix.
See `SYSTEM_GUIDE.md` in this repo for the full design rationale, the
probe-first investigation methodology, and the hard-won bugs this codebase
defends against (it was written for the foreclosure systems but the
architecture, GitHub Actions patterns, and lessons apply directly here).

| County  | Probate portal (as of scaffold time — verify via probe) |
|---------|-----------------------------------------------------------|
| Bexar   | portal-txbexar.tylertech.cloud |
| Collin  | cijspub.co.collin.tx.us/PublicAccess (Tyler Odyssey) |
| Harris  | cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate (standalone Harris County Clerk ASP.NET case-search system — same domain as the existing foreclosure scraper, but a different, custom application, not Tyler/Odyssey) |
| Dallas, Ellis, Tarrant, Denton, Johnson, Travis | not yet identified — needs discovery (SYSTEM_GUIDE.md §7 step 1) before probing |

**Fields captured:** Decedent First Name · Decedent Last Name · Property
Address (best-effort — often unavailable; a probate case index is
person-centric, not property-centric, unlike a foreclosure filing) · City ·
State · Zip Code · County · Case Number · Case Type · Filing Date · Executor
Name · Executor Address · Date Pulled.

The **actionable contact is the Executor/Administrator**, not the decedent —
Texas probate filings generally list the applicant/administrator's name and
mailing address for notice purposes, so that's the person you'd actually
reach out to about the property.

---

## One-Time Setup

### Step 1 — Enable Google Sheets API
1. [console.cloud.google.com](https://console.cloud.google.com) → new or existing project
2. Enable **Google Sheets API** and **Google Drive API**

### Step 2 — Create a Service Account
1. IAM & Admin → Service Accounts → Create Service Account (any name, no roles needed)
2. Keys tab → Add Key → JSON → download it

> **Reusing a service account from Scraper_Python or
> Foreclosure-Scraper-Collin-Ellis-Travis is fine and saves a step** — since
> all three write to Google Sheets under the same Google account, one service
> account can be shared across all of them. Just make sure its `client_email`
> is shared on the sheet (Step 3) and its JSON is added as *this* repo's
> secret too (Step 4) — GitHub secrets don't carry over between repos
> automatically, even for the same service account.

### Step 3 — Share the target Google Sheet
Open the JSON key → copy `client_email` → open the target Google Sheet
(the one this system writes to) → **Share** → paste that email → **Editor**.

### Step 4 — Add the GitHub secret
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `GOOGLE_CREDENTIALS`
- Value: the entire JSON key file contents

### Step 5 — Test it
**Actions tab → "Daily Probate Scraper (9 TX counties)" → Run workflow**
- `dry_run = true` first, to verify scraping without writing
- Check the run logs for output

---

## Schedule
Runs daily at ~7:45 AM CST (`45 13 * * *` UTC — offset from the exact hour
and from the sister foreclosure repo's run time, since GitHub's `schedule`
trigger is best-effort and commonly fires 1-3h late at :00). Edit
`.github/workflows/daily_scrape.yml` to change it.

## Manual / Backfill Run
Actions UI → Run workflow, with `date` / `counties` / `dry_run` inputs. Or
locally (editing only — see `SYSTEM_GUIDE.md` on why real runs must happen on
GitHub Actions, not locally):
```bash
pip install -r requirements.txt
export GOOGLE_CREDENTIALS='{ ... paste JSON ... }'
python main.py --dry-run
python main.py --counties collin
```

## Notes on Data
- **Case type matters more here than in foreclosure.** TX probate courts
  also docket guardianships, mental-health commitments, and trust matters —
  those are NOT decedent-estate leads. Filter at the search-form level
  (case-type checkboxes) first; `scrapers/base.py`'s `is_estate_case()` /
  `NON_ESTATE_CASE_TYPE_KEYWORDS` is a permissive second line of defense.
- **Property address will often be blank.** Unlike a Notice of Trustee's
  Sale, a probate case filing is about the estate/person, not one specific
  property — that's expected, not a bug (see `main.py`'s `_useful()`).
- **Duplicates** are filtered by case number (preferred — always assigned,
  never depends on OCR) or decedent name+county+filing date+address before
  writing — see `sheets_writer.py`.

## File Structure
```
Probate-Scraper-TX/
├── .github/workflows/
│   ├── daily_scrape.yml   # production cron — matrix (1 job/county) + collate
│   └── reset_sheet.yml    # one-click wipe (type RESET), for a clean re-populate
├── scrapers/
│   ├── __init__.py        # scraper class registry
│   ├── base.py             # shared utilities: name/address parsing, HTTP
│   │                        # retry, estate-case filtering, build_record()
│   └── <county>.py         # one module per county/portal (+ <county>_extract.py
│                            # if OCR is needed)
├── main.py                 # CLI orchestrator
├── sheets_writer.py         # Google Sheets I/O (dedup, sort, tracker, retry)
├── requirements.txt
├── SYSTEM_GUIDE.md          # full design guide — read this before changing anything
└── README.md
```
