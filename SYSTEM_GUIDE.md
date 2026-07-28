# County Foreclosure Lead Scraper — System Design & Replication Guide

> **Purpose of this document:** hand this whole file to a fresh Claude Code
> session (new project, new counties) so it can build the same kind of system
> without re-discovering the ~10 hard-won bugs below the hard way. It assumes
> the new agent has NO memory of this conversation — everything it needs is
> here or inferable from the code it will write.
>
> This describes a system built for **Texas** pre-foreclosure (Notice of
> Trustee's Sale) lead generation across 6 counties, writing to Google Sheets,
> running daily and unattended on GitHub Actions. The same architecture
> generalizes to any "scrape a government records portal → Google Sheet" job.

---

## 0. ALREADY BUILT — do not re-do these counties

The following 6 Texas counties already have a working, production scraper in
a separate existing system. **This new build is for a different/additional
set of counties — cross-reference any incoming county list against this one
and skip/exclude any overlap:**

| County  | Portal platform |
|---------|------------------|
| Harris  | County-clerk ASP.NET site (own scraper module) |
| Bexar   | `publicsearch.us` / GovOS |
| Dallas  | `publicsearch.us` / GovOS |
| Tarrant | `publicsearch.us` / GovOS |
| Denton  | `publicsearch.us` / GovOS |
| Johnson | `publicsearch.us` / GovOS |

If the new county list includes any of these, flag it back to the user
instead of silently rebuilding it — it may mean they want it consolidated
into this new system too (a decision for them, not an assumption to make).
If the new list includes OTHER counties that also turn out to run on
`publicsearch.us`, that's fine and expected (see §7) — that platform is
common across Texas — just confirm via probe like any other county, don't
assume identical behavior.

---

## 1. What the system does

A daily job that:
1. Scrapes county clerk / record-search portals for upcoming foreclosure
   (Notice of Trustee's Sale) filings.
2. Extracts: owner first/last name, property address, city, state, zip,
   county, file (recorded) date, sale (auction) date, and a doc ID.
3. Filters out non-homeowner entities (builders, HOAs, LLCs, funds) — the
   business only wants **individual homeowners** facing foreclosure.
4. Writes new (deduplicated) leads to a Google Sheet, stamped with the date
   pulled, sorted by file date.
5. Updates a second "Daily Counts" tab: one row per day, new-lead count per
   county — a lightweight ops dashboard.
6. Runs fully unattended on a daily cron.

## 2. Hard constraints — read this before writing any code

1. **Scrapers can only run on GitHub Actions, never locally.** If the
   operator is outside the US (common for this kind of contractor setup),
   the target county portals geo-block non-US IPs — GitHub's hosted runners
   are US-based, so real runs must happen there. You can (and should) still
   develop and edit code locally; you just can't `python main.py` it
   yourself and expect it to work. Validate locally with `python3 -m
   py_compile <file>` only — `pip install` of scraping deps
   (playwright/bs4/selenium/gspread) will typically fail or be pointless in
   a throwaway dev sandbox anyway.
2. **Test via `gh` CLI, not by asking the user to click around.** If `gh
   auth status` shows a logged-in token with `repo`+`workflow` scope, you can
   dispatch workflows, watch runs, and read logs entirely yourself:
   ```
   gh workflow run <workflow>.yml --ref <branch> [-f key=value ...] -R <owner>/<repo>
   gh run list --workflow <workflow>.yml -R <owner>/<repo> -L 1 --json databaseId -q '.[0].databaseId'
   gh run watch <run_id> -R <owner>/<repo> --exit-status
   gh run view <run_id> -R <owner>/<repo> --log
   ```
   **GitHub will not return `--log` output until the ENTIRE run (all
   matrix jobs) completes** — not per-job. If you need a completed job's
   log while sibling jobs are still running, use
   `gh run view --job <job_id> -R <owner>/<repo> --log`, which *does* work
   as soon as that specific job finishes.
3. **You will likely be blocked from pushing/merging directly to `main`.**
   Treat every change as: commit to a branch → push → open a PR → **ask the
   user to merge**. Don't fight this; it's a deliberate safety rail on the
   default branch. Keep PRs small and self-contained so merging is a
   1-click action, not a research project. If a PR shows `CONFLICTING`
   because its branch history diverged, don't try to resolve conflicts —
   cut a **fresh branch off current `origin/main`**, cherry-pick or
   re-apply just the file changes, and open a new clean PR. Close the messy
   one.
4. **Output must always match the exact Google Sheet column order** the
   business expects — don't reorder or rename columns casually, downstream
   spreadsheet formulas / filters may depend on position.
5. **An address is what makes a lead actionable** (skip-tracing needs a
   street address). Depending on the business's preference, you may be
   asked to keep name-only/doc-id-only rows or drop them — confirm which,
   don't assume.

## 3. Repo layout (the pattern to replicate)

```
main.py                    # CLI entry + orchestrator; --output (matrix mode),
                            # --from-json (collate mode), --reset-sheet
sheets_writer.py            # All Google Sheets I/O: dedup, sort, tracker, retry
requirements.txt
scrapers/
  __init__.py               # exports scraper classes
  base.py                   # BaseScraper: session/HTTP helpers, name/address
                             # parse helpers, build_record(), launch_chromium()
  counties.py                # one-line wrapper classes per county sharing a
                              # portal-type scraper (see §5)
  <portal>.py                 # one scraper class per DISTINCT portal platform
  <portal>_extract.py          # OCR/text-parsing helpers for that portal
.github/workflows/
  daily_scrape.yml            # production: matrix (one job per county) + collate
  reset_sheet.yml             # one-click "wipe sheet + tracker" (type RESET)
  probe_<portal>.yml           # throwaway investigation workflow (see §6)
probe_<portal>.py               # throwaway investigation script (see §6), lives
                                # at repo root, safe to delete once a portal is
                                # understood and its real scraper is written
```

**Why one scraper class per PORTAL PLATFORM, not per county:** many county
clerks outsource their record search to the same handful of SaaS vendors
(e.g. `publicsearch.us` / GovOS is extremely common across Texas). If 5
counties run the same platform, they need **one** scraper class parameterized
by county slug/name — not 5 near-duplicate scraper files. Counties on a
different or custom portal (e.g. a legacy ASP.NET county-clerk site) need
their own scraper module.

## 4. Output schema (adjust to the actual business ask, but keep this shape)

Google Sheet columns, in order:
```
First Name | Last Name | Address | City | State | Zip Code | County |
Foreclosure File Date | Sale Date | Doc ID | Date Pulled
```
- **Date Pulled**: stamped once, the day a record is first written. Ignored
  by the dedup key, so it never causes a re-write.
- **Dedup key** (in `sheets_writer._record_key`), priority order:
  1. `docid|county|doc_id` if a doc ID exists (most reliable — unique per
     filing regardless of name/address changes)
  2. `first|last|county|file_date|address` if an address exists
  3. `first|last|county|file_date` as a last resort
- **Sort:** whole sheet sorted by File Date after every write (sale date is
  left however it lands — don't assume the business wants both sorted).
- **Second tab — "Daily Counts" tracker:** one row per calendar day, one
  column per county, holding the count of **genuinely new (post-dedup)**
  leads written that day, plus a Total column. If the job runs twice in one
  day, ADD to that day's existing row rather than overwrite it (idempotent
  under re-runs / retries).

## 5. GitHub Actions structure (matrix + collate)

Don't scrape all counties in one long job — split into a **matrix**, one job
per county, run in parallel, each writing its own JSON artifact
(`--output records-<county>.json`), then a final **collate** job downloads
all artifacts and does ONE write to the sheet (`--from-json`). This:
- Lets slow counties (large volume, heavy OCR) get their own ~45-min budget
  instead of sharing one shrinking window.
- Avoids concurrent-write races on the single Google Sheet.
- Makes a single county's failure isolated (`fail-fast: false`) instead of
  killing the whole run.

```yaml
jobs:
  setup:        # builds the county list from workflow_dispatch input (or all)
  scrape:       # matrix: one job per county, needs: setup
    strategy: { fail-fast: false, matrix: { county: ${{ fromJson(...) }} } }
    timeout-minutes: 55
  collate:      # needs: scrape, if: always() — writes even if some counties failed
```
Support `dry_run` (scrape + log, don't write) and a `counties` input
(space-separated subset) via `workflow_dispatch`, so you can test one county
at a time without burning 45 minutes on all of them.

**Cron caveat:** GitHub's `schedule` trigger is best-effort and commonly
fires **1–3 hours late**, especially at the busy top-of-hour slot. If the
business wants "7 AM", schedule earlier and off the exact hour (e.g.
`35 11 * * *`) — don't promise exact timing.

## 6. The investigation methodology (do this BEFORE writing the real scraper)

For any portal you haven't reverse-engineered yet, **do not guess the form
fields, search filters, or table schema.** Government record portals are
React/JS apps with non-obvious multi-step flows (landing page → pick a
"department"/record type from a dropdown → date range → submit → paginated
results, sometimes with per-row document viewers behind session-signed image
URLs). Guessing wastes runs and produces confidently-wrong scrapers.

Instead: write a **throwaway probe script** (`probe_<portal>.py`) + a minimal
GitHub Actions workflow (`workflow_dispatch` only) that runs it, and iterate:

1. **Dump the advanced-search form**: every input/select/button, with id/
   name/placeholder/aria-label, and any `<select>` options — especially a
   "Department" or "Record Type" selector. (In one real case, the correct
   record type was hidding behind a "Foreclosures" department option that
   wasn't the default — the default "Land Records" search silently returned
   almost none of the actual target documents.)
2. **Dump the results table's actual `<th>` headers and a few sample rows**,
   via `page.evaluate()` JS, not assumptions. **Different counties on the
   same platform can use different table schemas** — e.g. one county's table
   had `Doc Type | Recorded Date | Sale Date | Doc Number | Remarks |
   Property Address`, while another county on the identical platform used
   `Grantor | Sale Date | Filed Date | Property Address` (no doc-type
   column, a coarse "Jul 2026" sale date instead of mm/dd/yyyy, and the
   owner name available directly in the table — no OCR needed for that
   county). **Write your table parser to match columns by header name, not
   fixed index, and treat every column as optional.**
3. **Check whether required fields are already in the table** before
   assuming you need OCR. It's tempting to assume every field needs OCR from
   a scanned document — but often the results table already has the
   address/sale-date/file-date for free (dramatically faster + more
   reliable), and OCR is only truly needed for one field (commonly the
   owner name, since portals often don't index "parties" for this document
   type — check the document detail page for something like "Parties: No
   parties found.").
4. **If OCR is needed**: capture the network response for the document
   **image** (not the summary page) via `page.on('response', ...)`, filter
   for the image URL pattern, fetch it via
   `context.request.get(url).body()` (carries session cookies/signature —
   authenticated fetch without re-navigating), then OCR with
   `pytesseract.image_to_string(Image.open(...))`. Dump the **raw OCR text**
   of a few real documents in the probe log before writing any parsing
   regex — the label wording ("Grantor:", "Debtor(s):", "executed by...")
   varies by law firm / document template, and you need real examples to
   build a robust parser, not guesses.
5. **Prefer navigating directly to the results URL** (most of these SPAs are
   query-param driven, e.g. `?department=FC&recordedDateRange=YYYYMMDD,YYYYMMDD&searchType=advancedSearch`)
   over clicking through the form each time — faster and more reliable once
   you've reverse-engineered the query shape from one form submission.
6. **Run the probe via `gh workflow run` + `gh run watch` + `gh run view
   --log`**, read the actual output, and rewrite the probe based on what you
   learn — expect 5–15 iterations for a genuinely new portal. This is normal
   and fast (~2 min per iteration) — don't skip straight to writing the real
   scraper on assumptions.

## 7. Step-by-step: adding a new county

1. **Identify the portal.** Try `https://{county-slug}.tx.<platform>.com` (or
   whatever the known common vendor pattern is for the state) — many
   counties share a handful of vendors. If it resolves and looks like an
   already-supported platform, skip to step 2. If not, or you're unsure,
   visit the county's official "official records search" / county clerk
   page and look for the vendor's branding/URL pattern.
2. **If it's a platform you already support**: add a one-line wrapper class
   (slug + display name) — no new scraper code needed — **but still run the
   probe workflow once against this specific county** to confirm its table
   schema, department options, and OCR layout match what you expect. Do not
   assume every county on "the same platform" behaves identically — verify.
3. **If it's a new/unknown platform**: build a new `probe_<portal>.py` +
   `probe_<portal>.yml`, follow §6, then write `scrapers/<portal>.py` +
   `scrapers/<portal>_extract.py` following the structure of an existing
   scraper module (constructor takes county slug/name; `scrape(target_date)`
   returns a list of `build_record(...)` dicts).
4. **Wire it in**: add to `main.py`'s county list + scraper map, add a
   wrapper class in `scrapers/counties.py`, add the county to the
   `daily_scrape.yml` matrix default list.
5. **Test with `dry_run=true` and `counties=<just-this-one>`** first — read
   the log for: are date-filter params applied, is pagination reaching every
   page (log a per-page row count), are addresses/dates parsed correctly,
   are entity/builder names filtered out, does a sample of printed records
   look right.
6. **Then test a real (non-dry) write** for that one county only, and
   confirm in the actual sheet: correct columns, Date Pulled populated,
   sheet still sorted, tracker updated, no duplicate rows on a second run of
   the same window.
7. **Open a PR, ask the user to merge.** Don't merge yourself.

## 8. Auditing an existing list of counties/links (some may be stale)

When handed a list of county portal links to (re-)validate:
1. For each link, just try loading it (`page.goto`, check final URL / status
   / page title) — flag: **reachable as-is**, **redirects to a new domain**
   (platform migration), or **dead/404/unrecognizable**.
2. For reachable ones, fingerprint the platform (look for known markers —
   URL pattern, page title, a distinctive form-field id) so you can tell
   "this is the same platform we already support" from "this is something
   new."
3. Don't assume a previously-working county is still on the same platform —
   counties do migrate record-search vendors. Re-run the probe checklist
   (§6 steps 1–2 at minimum) for every county before trusting an old
   scraper module against it, even one that "should" still apply.
4. Report back a simple table: county → status → action needed (nothing /
   reuse existing scraper / new probe required / dead link, needs manual
   follow-up).

## 9. Hard-won bugs — build these defenses in from day one

These were each discovered in production, on a *working, previously-tested*
system, usually because they're intermittent (only trigger sometimes) rather
than always. Don't wait to rediscover them.

1. **A slow-loading results table silently drops the entire county.** If you
   parse the table right after navigating with only a fixed sleep, a slow
   portal response gets read as an empty table — the code sees "0 results"
   and moves on, silently losing that county for the day, indistinguishable
   from "genuinely no filings." **Fix:** poll until the table has data rows
   (or the page explicitly says "no results") with a real timeout (e.g.
   25s), and log which branch it took. Apply this on the FIRST results page
   too, not just when paginating.
2. **Pagination can silently truncate.** Clicking "next page" then waiting a
   fixed time before parsing can read a stale/still-rendering page as empty
   and stop early, silently returning far fewer records than exist. **Fix:**
   wait for the first row's content to actually change after clicking next
   (`page.wait_for_function` comparing before/after text), not a fixed
   sleep. If a page ever comes back empty mid-pagination, re-check it once
   before trusting "end of results."
3. **A stale/incompatible headless-browser cache silently breaks a whole
   county.** `BrowserType.launch: Executable doesn't exist` — a GitHub
   Actions cache step that "hits" with a browser build that's since been
   superseded by a `playwright` version bump leaves the binary missing.
   **Fix:** don't rely on conditional cache-skip logic for the browser
   install step; just always run `playwright install --with-deps chromium`
   fresh, and add a runtime self-heal (catch the "Executable doesn't exist"
   error, run the install, retry once) as a second line of defense.
4. **`apt-get update` can hang indefinitely on an unrelated pre-installed
   apt source** (e.g. a cloud-vendor CLI repo baked into the runner image
   that has nothing to do with your actual dependencies) and eat the
   entire job timeout, killing that county before the scraper ever starts.
   **Fix:** remove sources you don't need before updating, and wrap
   `apt-get update` in a `timeout` with retry/connect-timeout flags,
   falling through to `apt-get install` with the existing index if update
   still fails.
5. **A single transient write-API error can discard an entire day's
   already-scraped data.** If every county scrapes successfully but the
   final write to the destination (Sheets/DB/etc) throws once (e.g. HTTP
   503) with no retry, the whole write aborts and all that work is lost —
   even though nothing was actually wrong with the data. **Fix:** wrap every
   write-API call in retry-with-exponential-backoff for transient status
   codes (429/500/502/503/504) and connection/timeout errors. Design the
   write function to be safely re-callable (dedup-check-then-write, not
   blind-append) so a retry of the *whole* write operation can't create
   duplicates.
6. **A "how much did we pull" metric can quietly become meaningless.** If
   your lookback window is a rolling N days (re-scanning largely the same
   filings every run), a naive "records found this run" count will hover
   near the same big number every day regardless of real new intake — not
   useful, and looks like a bug to anyone reading it. **Count records that
   survive dedup (i.e., are genuinely new), not everything scanned.**
7. **A row-count computed from a column with many blank values silently
   truncates a range operation** (e.g. a "sort this whole sheet" or "read
   every row" call that determines row count from a column that's blank on
   many rows) — trailing blanks get trimmed by the API and you undercount,
   leaving part of the data untouched. **Use a row-count method that counts
   any row with content in ANY column**, not one specific column.
8. **One fixed lookback window doesn't fit every source.** If some sources
   file/update near-daily and others file in infrequent batches (e.g.
   monthly), a single short window makes the batch-filer source
   intermittently empty, while a single long window makes the daily
   sources noisy/slow/stale. **Make the lookback window configurable per
   source**, not global.
9. **Don't conflate "the venue/government/servicer address" with "the
   property address."** Any parser that greedily regex-matches "a street
   address followed by a Texas city/zip" in free text will eventually grab
   the courthouse steps, the county clerk's own address, or the law firm/
   loan-servicer's mailing address instead of the actual property. Build an
   explicit reject-list/context-check (keywords like "courthouse",
   "commissioner", "county clerk", "suite", "c/o", "attorney", "servicer")
   and prefer labeled anchors ("Property Address:", "Commonly known as:")
   over blind regex scanning.
10. **Verify claims against real data, not logs of intent.** When something
    looks off (e.g. "why did this appear/not appear"), pull the actual
    record(s) in question and check them — don't reason from what the code
    is *supposed* to do. More than once, a one-off "weird" record turned
    out to be entirely correct behavior once inspected (e.g. a filing from
    over a year ago with a sale date that keeps getting postponed forward —
    a real, valid record, not a bug) — and more than once, a real bug was
    hiding in a workflow env-var that silently overrode a code-level
    default, which only showed up by diffing the actual constructed request
    URL against what the code intended to send.

## 10. Testing/deployment checklist for a new county before calling it done

- [ ] Probe confirms department/record-type filter, table schema, and (if
      needed) OCR document-image capture and real sample OCR text
- [ ] `python3 -m py_compile` passes on every new/changed file
- [ ] `dry_run=true`, single county — log shows per-page row counts,
      reasonable "upcoming" filter count, entity names correctly excluded
- [ ] Non-dry run, single county — confirm in the real sheet: correct
      columns, Date Pulled populated, sheet re-sorted, tracker updated
- [ ] Re-run the same window a second time — confirm ZERO new rows (dedup
      working, no duplicates)
- [ ] Full matrix run (`dry_run=true`, all counties) — confirm no county
      silently returns 0 unless the log explicitly shows "portal reports no
      results," not a render-timeout/empty-table warning
- [ ] PR opened, small and reviewable; user merges (don't push to `main`
      yourself)

---

### Appendix: known-working reference implementation

This guide was extracted from a real system (`Lance913/Scraper_Python`) with
6 Texas counties on two portal types:
- **`publicsearch.us` / GovOS** (Bexar, Dallas, Tarrant, Denton, Johnson) —
  one scraper class, `department=FC` (Foreclosures) query param, per-county
  table-schema tolerance, OCR fallback for name/address via signed PNG
  document images.
- **A county-clerk ASP.NET site** (Harris) — scrapes by upcoming auction
  month, downloads a PDF per document via an in-session request, OCRs all
  pages with `pdf2image` + `pytesseract`.

Shared: `sheets_writer.py` (dedup/sort/tracker/retry), `scrapers/base.py`
(shared session/parse helpers + browser-launch self-heal), a matrix +
collate GitHub Actions workflow, and a `reset_sheet.yml` one-click sheet
wipe for clean re-populates. If the new project's list of counties overlaps
with `publicsearch.us`-platform counties, the fastest path is: confirm via
probe, then literally reuse the same `PublicSearchScraper` class with a new
slug/name — no new scraper code required.
