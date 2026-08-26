"""
Ellis County Probate Scraper — LGS Online Solutions (Online Records Search).

Portal: https://public.lgsonlinesolutions.com/ors.html

Confirmed via probe (SYSTEM_GUIDE.md §6/§8 — see probe_lgs.py + PR notes for
the full iteration-by-iteration trail):

  * This is a classic HTML <frameset> app (frames: heart / menu / update),
    backed by a stateful CGI "webshell" gateway (LANSA/IBM-i-style; hidden
    WEBIOHANDLE session token, XEVENT-driven postbacks). Nearly every
    meaningful UI transition is a full server round trip / frame reload,
    not client-only JS — so this scraper drives the real UI with Playwright
    rather than replaying raw HTTP requests, and every wait below polls a
    real DOM condition rather than sleeping a fixed amount (SYSTEM_GUIDE.md
    §9 bug #1, applied to frame transitions specifically).
  * Ellis County's OFFICIAL site (elliscountytx.gov/1397/Online-Record-Search)
    names this URL as its "County Court Record Search", explicitly distinct
    from https://ellisccktxpublicsearch.us/AcclaimWeb/ ("Property Search" —
    a decoy for this system: real-property recording, not case search).
  * A free "Guest Login" (button label; internally posts OPERCODE=PASSWD=
    "orguest") gives full INDEX search access with no registration —
    registration/subscription is only required to purchase document images,
    which this scraper never needs (confirmed on-portal message: "You can
    not purchase images without an account login" — index search is fine).
  * The office/county picker (`select[name="P_1"]`) lists ~60 small-county
    LGS-client offices sharing this one multi-tenant portal. Ellis County
    Clerk = "CC070" (there's also "DC070" Ellis District Clerk, not used
    here — probate is a County Court at Law / County Clerk matter per this
    repo's README, confirmed again by the real sample data below: every
    record's Court field read "COUNTY COURT AT LAW NO 1").
  * Selecting an office relabels a set of placeholder buttons (literally
    spelling "LOADING": WTKCB_1.."L", WTKCB_2.."O", WTKCB_3.."A"...) into
    the search categories THAT OFFICE offers. For CC070 exactly three
    appear: Criminal / Civil / Probate (no separate Property/Vitals/Trustee
    search for Ellis via this system — it uses AcclaimWeb for property).
    Click by visible text, not a fixed WTKCB index, since the mapping is
    office-dependent (confirmed by diffing pre/post-selection HTML).
  * The Probate Search panel's results grid has columns (matched by header
    text, not fixed index, per SYSTEM_GUIDE §6):
        Cause Number | Name | Cause Type | File Date | Address
    "Name" is the DECEDENT, format "LAST, FIRST MIDDLE" (use
    parse_name_lf) — confirmed against 29 real records, e.g.
    "TEKELL, WILLIAM JAY". "Cause Type" values seen (all legitimate estate
    types, matching base.py's ESTATE_CASE_TYPE_HINTS): SMALL ESTATE,
    LETTERS TESTAMENTARY, MUNIMENT OF TITLE, ADMINISTRATION,
    ADMINISTRATION - DEPENDANT. "Address" is best-effort/frequently blank
    (bare street only, no city/zip) — confirmed empirically: ~45% populated
    in the real sample. Cell values live in `<input readonly>` elements, so
    they must be read via `.value`, NOT `.textContent` (which is blank for
    them — this tripped up naive probing early on).
  * Each row has a "More Information" action opening a "Probate Pop-Up
    (Case Detail)" panel with confirmed field labels: Cause / Date Filed /
    Case Status / Court / Order Date / Oath Date / Qualified Date /
    Case Type / Type / Name / Address / City State Zip (P_97..P_108), plus
    a "Representative Information" grid (P_109/P_110/P_111 — 3 columns,
    widths 30/70/60 chars respectively) that has NO static header labels in
    the template — almost certainly the executor/administrator/attorney
    list (the section title is literally "Representative Information",
    positioned right after the case's own Name/Address/City-State-Zip),
    but the exact per-column semantics (which of P_110/P_109/P_111 is
    role vs name vs address) could NOT be empirically confirmed: GitHub
    Actions hit an account-level billing/spending-limit block (unrelated to
    this portal or this code) right as this was about to be exercised live.
    See the PR description for exactly what's confirmed vs inferred.
    `_fetch_case_detail()` below is written defensively — enrichment
    self-disables after its first failure so a wrong assumption here can't
    eat the job's time budget, and it never blocks the core record (case
    number / decedent name / case type / filing date / best-effort address)
    from shipping, which IS fully verified end-to-end against 29 real
    filings.
"""

import os
import re
import time
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .base import BaseScraper, launch_chromium

ORS_URL = "https://public.lgsonlinesolutions.com/ors.html"
OFFICE_CODE = "CC070"        # Ellis County Clerk (NOT "DC070" Ellis District Clerk)
CATEGORY_TEXT = "Probate"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# Rolling lookback window for the daily job. Ellis files roughly ~1 probate
# case/day (confirmed: 29 cases in a real 30-day sample window), so a short
# window with a safety margin is plenty day to day — env-overridable for
# manual backfills, matching the publicsearch.py convention in this codebase.
WINDOW_DAYS = int(os.environ.get('ELLIS_WINDOW_DAYS', '14'))
MAX_PAGES = int(os.environ.get('ELLIS_MAX_PAGES', '20'))

# Best-effort executor/administrator enrichment budget (see module docstring
# — the "More Information" click-through is UNVERIFIED end to end). Keeps a
# broken/slow enrichment path from eating the whole job's time budget; core
# records ship regardless of whether this succeeds.
DETAIL_ENRICHMENT = os.environ.get('ELLIS_DETAIL_ENRICHMENT', 'true').lower() != 'false'
DETAIL_MAX = int(os.environ.get('ELLIS_DETAIL_MAX', '40'))
DETAIL_BUDGET_SEC = int(os.environ.get('ELLIS_DETAIL_BUDGET_SEC', '1200'))

_PAGE_INFO_RE = re.compile(r'Page\s+(\d+)\s+of\s+(\d+)\s*-\s*Total:\s*(\d+)', re.I)

# Reads the results grid generically: finds the table containing a row with
# both "Cause Number" and "Name" text, treats that as the header row (plus a
# second header-ish row immediately after it, if that row has no <input>
# cells of its own — this portal splits data-column headers and action-
# column headers ["More Information", "Filings"] across two rows). Data
# cells are read via their <input readonly>.value, falling back to
# textContent for any plain-text cell — matches columns by header name per
# SYSTEM_GUIDE §6, not fixed index.
_GRID_JS = """() => {
    const cellText = (cell) => {
        const inp = cell.querySelector('input, textarea');
        if (inp) return (inp.value || '').trim();
        return (cell.textContent || '').trim();
    };
    const tables = Array.from(document.querySelectorAll('table'));
    let target = null, hdrIdx = -1;
    for (const t of tables) {
        const rows = Array.from(t.rows);
        for (let i = 0; i < rows.length; i++) {
            const txt = (rows[i].textContent || '');
            if (txt.includes('Cause Number') && txt.includes('Name')) { target = t; hdrIdx = i; break; }
        }
        if (target) break;
    }
    if (!target) return {headers: [], rows: [], pageInfo: (document.body.innerText || '')};

    const row0 = Array.from(target.rows[hdrIdx].cells).map(c => (c.textContent || '').trim());
    let headers = row0.slice();
    let dataStart = hdrIdx + 1;
    if (hdrIdx + 1 < target.rows.length) {
        const nextRow = target.rows[hdrIdx + 1];
        const nextHasInputs = Array.from(nextRow.cells).some(c => c.querySelector('input,textarea'));
        if (!nextHasInputs) {
            const row1 = Array.from(nextRow.cells).map(c => (c.textContent || '').trim());
            headers = headers.map((h, i) => h || row1[i] || '');
            dataStart = hdrIdx + 2;
        }
    }

    const out = [];
    for (let i = dataStart; i < target.rows.length; i++) {
        const cells = Array.from(target.rows[i].cells);
        if (!cells.length) continue;
        out.push(cells.map(cellText));
    }
    return {headers, rows: out, pageInfo: (document.body.innerText || '')};
}"""


class EllisCountyScraper(BaseScraper):

    def __init__(self):
        super().__init__('Ellis')

    # ── Entry point ──────────────────────────────────────────────────────

    def scrape(self, target_date: date) -> List[Dict]:
        self.logger.info(f"Scraping Ellis County for {target_date}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.error("Playwright not installed")
            return []

        begin_str = (target_date - timedelta(days=WINDOW_DAYS)).strftime('%m/%d/%Y')
        end_str = target_date.strftime('%m/%d/%Y')

        records: List[Dict] = []
        try:
            with sync_playwright() as pw:
                browser = launch_chromium(pw)
                ctx = browser.new_context(user_agent=UA, viewport={'width': 1440, 'height': 1000})
                page = ctx.new_page()
                page.set_default_timeout(20_000)

                frame = self._open_probate_search(page, begin_str, end_str)
                if not frame:
                    self.logger.error("Ellis: could not reach Probate Search results — aborting")
                    browser.close()
                    return records

                rows, total_pages = self._scrape_all_pages(page, frame)
                self.logger.info(
                    f"Ellis: {len(rows)} row(s) parsed across {total_pages} page(s)")

                records = self._rows_to_records(rows)

                if DETAIL_ENRICHMENT and records:
                    self._enrich_with_details(page, records, begin_str, end_str)
                else:
                    self.logger.info(
                        "Ellis: detail (executor) enrichment skipped "
                        "(ELLIS_DETAIL_ENRICHMENT=false or no records)")

                browser.close()
        except Exception as exc:
            self.logger.error(f"Ellis: scrape failed: {exc}", exc_info=True)

        self.logger.info(f"Ellis: {len(records)} total records")
        return records

    # ── Navigation: fresh load -> guest login -> Probate Search results ────

    def _open_probate_search(self, page, begin_str: str, end_str: str):
        """Fresh navigate -> guest login -> select CC070 -> click 'Probate'
        category -> fill date range -> submit. Returns the ready 'update'
        frame, or None on failure. Every step polls a real DOM condition on
        a freshly-resolved frame reference (not a held-over handle) since
        this WTK app frequently does full server-driven frame reloads
        rather than in-place DOM patches — holding a stale Frame object
        across a wait is unreliable."""
        try:
            page.goto(ORS_URL, wait_until='load', timeout=30_000)
        except Exception as e:
            self.logger.error(f"Ellis: goto {ORS_URL} failed: {str(e)[:200]}")
            return None

        menu = self._wait_frame_condition(
            page, 'menu', "() => !!document.getElementById('GuestLogIn')", timeout=15)
        if not menu:
            self.logger.error("Ellis: login form ('menu' frame / #GuestLogIn) never appeared")
            return None

        try:
            menu.locator('#GuestLogIn').click(timeout=10_000)
        except Exception as e:
            self.logger.warning(
                f"Ellis: #GuestLogIn click failed ({str(e)[:150]}) — trying GuestSubmit() directly")
            try:
                menu.evaluate("GuestSubmit()")
            except Exception as e2:
                self.logger.error(f"Ellis: GuestSubmit() fallback also failed: {str(e2)[:200]}")
                return None

        update = self._wait_frame_condition(
            page, 'update', "() => !!document.getElementById('actionButton3')", timeout=15)
        if not update:
            self.logger.error("Ellis: post-guest-login Search button (#actionButton3) never appeared")
            return None

        # Dismiss the "Guest Login Message" dialog if present. Best-effort —
        # confirmed non-fatal if this fails or times out; clicking Search
        # (#actionButton3) below works regardless of whether Continue was
        # clicked first.
        try:
            cont = update.locator('#WTKCB_10')
            if cont.count() > 0 and cont.is_visible():
                cont.click(timeout=3_000)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        try:
            update.locator('#actionButton3').click(timeout=10_000)
        except Exception as e:
            self.logger.error(f"Ellis: click Search(#actionButton3) failed: {str(e)[:200]}")
            return None

        office_form = self._wait_frame_condition(
            page, 'update', "() => !!document.querySelector('select[name=\"P_1\"]')", timeout=15)
        if not office_form:
            self.logger.error("Ellis: office picker (select[name=P_1]) never appeared")
            return None

        try:
            office_form.locator('select[name="P_1"]').select_option(value=OFFICE_CODE, timeout=10_000)
        except Exception as e:
            self.logger.error(f"Ellis: selecting office {OFFICE_CODE} failed: {str(e)[:200]}")
            return None

        # Selecting the office relabels WTKCB_1..7 into that office's
        # categories via a server postback. Poll for the exact-text
        # "Probate" button to appear (it exists from page-load as a hidden
        # placeholder labeled "A" — checking existence alone would false-
        # positive; the TEXT change is the real signal).
        probate_btn_frame = self._wait_frame_condition(
            page, 'update',
            "() => Array.from(document.querySelectorAll('button'))"
            f".some(b => (b.textContent||'').trim() === {CATEGORY_TEXT!r})",
            timeout=15)
        if not probate_btn_frame:
            self.logger.error(
                f"Ellis: {CATEGORY_TEXT!r} category button never appeared for office {OFFICE_CODE} "
                f"(office may not offer probate search, or the portal changed)")
            return None

        try:
            probate_btn_frame.locator(f'button:text-is("{CATEGORY_TEXT}")').click(timeout=10_000)
        except Exception as e:
            self.logger.error(f"Ellis: click {CATEGORY_TEXT!r} category failed: {str(e)[:200]}")
            return None

        probate_form = self._wait_frame_condition(
            page, 'update',
            "() => { const el = document.getElementById('layer25'); "
            "return !!el && window.getComputedStyle(el).visibility === 'visible'; }",
            timeout=15)
        if not probate_form:
            self.logger.error("Ellis: Probate Search panel (layer25) never became visible")
            return None

        try:
            probate_form.locator('input[name="P_38"]').fill(begin_str, timeout=8_000)
            probate_form.locator('input[name="P_190"]').fill(end_str, timeout=8_000)
            probate_form.locator('button[name="WTKCB_12"]').click(timeout=10_000)
        except Exception as e:
            self.logger.error(f"Ellis: submitting Probate Search failed: {str(e)[:200]}")
            return None

        results = self._wait_frame_condition(
            page, 'update',
            "() => /Page\\s+\\d+\\s+of\\s+\\d+\\s*-\\s*Total:\\s*\\d+/i"
            ".test(document.body.innerText||document.body.textContent||'')",
            timeout=25)
        if not results:
            # SYSTEM_GUIDE §9 bug #1: a slow/late results render must not be
            # silently read as "0 results". Log loudly and still try to
            # parse whatever is currently there rather than giving up.
            self.logger.warning(
                "Ellis: results-ready signal ('Page X of Y - Total: N') never appeared within "
                "timeout — proceeding with a best-effort parse anyway (may under-report)")
            results = next((f for f in page.frames if f.name == 'update'), None)
        return results

    # ── Frame/condition polling (SYSTEM_GUIDE §9 bug #1: poll, don't sleep) ─

    @staticmethod
    def _wait_frame_condition(page, frame_name: str, predicate_js: str,
                               timeout: float = 15.0, interval: float = 0.4):
        """Poll for a JS condition to become true on the CURRENT frame named
        `frame_name`, re-resolving the frame fresh from page.frames every
        iteration. Returns the ready frame, or None on timeout."""
        end = time.time() + timeout
        while time.time() < end:
            fr = next((f for f in page.frames if f.name == frame_name), None)
            if fr:
                try:
                    if fr.evaluate(predicate_js):
                        return fr
                except Exception:
                    pass  # frame mid-navigation/detached -- retry with a fresh lookup
            time.sleep(interval)
        return None

    @staticmethod
    def _poll(predicate, timeout: float, interval: float = 0.5) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if predicate():
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    # ── Grid parsing + pagination ────────────────────────────────────────

    def _parse_grid(self, frame) -> Tuple[List[Dict[str, str]], Optional[int], Optional[int]]:
        """Returns (rows as {case_number,name,case_type,filing_date,address}
        dicts for non-blank rows, current_page, total_pages). Matches
        columns by header text and treats every column as optional, per
        SYSTEM_GUIDE §6 (different result sets on this same multi-tenant
        portal could plausibly vary column presence/order by office)."""
        try:
            data = frame.evaluate(_GRID_JS)
        except Exception as e:
            self.logger.error(f"Ellis: grid evaluate failed: {str(e)[:200]}")
            return [], None, None

        headers = [h.strip().lower() for h in data.get('headers', [])]
        raw_rows = data.get('rows', [])
        page_info = data.get('pageInfo', '')

        def col(fragment: str) -> Optional[int]:
            for i, h in enumerate(headers):
                if fragment in h:
                    return i
            return None

        idx_case = col('cause number')
        idx_name = col('name')
        idx_type = col('cause type')
        if idx_type is None:
            idx_type = col('case type')
        idx_date = col('file date')
        idx_addr = col('address')

        def cell(cells: List[str], idx: Optional[int]) -> str:
            if idx is None or idx >= len(cells):
                return ''
            return cells[idx].strip()

        out = []
        for cells in raw_rows:
            case_no = cell(cells, idx_case)
            if not case_no:
                continue  # unused template row -- WTK grids pre-render a fixed max row count
            out.append({
                'case_number': case_no,
                'name': cell(cells, idx_name),
                'case_type': cell(cells, idx_type),
                'filing_date': cell(cells, idx_date),
                'address': cell(cells, idx_addr),
            })

        m = _PAGE_INFO_RE.search(page_info)
        cur_page = int(m.group(1)) if m else None
        total_pages = int(m.group(2)) if m else None
        return out, cur_page, total_pages

    def _scrape_all_pages(self, page, frame) -> Tuple[List[Dict[str, str]], int]:
        all_rows: List[Dict[str, str]] = []
        rows, cur_page, total_pages = self._parse_grid(frame)
        self.logger.info(f"Ellis: page {cur_page or 1} of {total_pages or 1} -> {len(rows)} row(s)")
        all_rows.extend(rows)

        # Pagination logic below could NOT be live-tested (the confirmed
        # 30-day sample fit on one page: "Page 1 of 1 - Total: 29"). Written
        # defensively per SYSTEM_GUIDE §9 bug #2 (wait for real content
        # change, re-check once before trusting "end of results"), but
        # flagged here as an outstanding verification item for whenever a
        # long enough window/backfill actually spans >1 page.
        seen_pages = 1
        while total_pages and cur_page and cur_page < total_pages and seen_pages < MAX_PAGES:
            before = rows[0]['case_number'] if rows else ''
            new_frame = self._click_next_results(page, frame, before)
            if not new_frame:
                self.logger.warning(f"Ellis: could not advance past page {cur_page} — stopping pagination")
                break
            frame = new_frame
            rows, cur_page, total_pages = self._parse_grid(frame)
            if not rows:
                page.wait_for_timeout(2000)  # re-check once before trusting "end of results"
                rows, cur_page, total_pages = self._parse_grid(frame)
            seen_pages += 1
            self.logger.info(f"Ellis: page {cur_page} of {total_pages} -> {len(rows)} row(s)")
            all_rows.extend(rows)

        return all_rows, (total_pages or 1)

    def _click_next_results(self, page, frame, before_case_no: str):
        """Click 'Next Results' and wait for the first row to actually
        change before trusting the new page. Returns the frame to keep
        using (may be the same object), or None if the click itself failed."""
        try:
            btn = frame.locator(':text("Next Results")').first
            if btn.count() == 0:
                return None
            btn.click(timeout=8_000)
        except Exception as e:
            self.logger.warning(f"Ellis: click 'Next Results' failed: {str(e)[:150]}")
            return None

        end = time.time() + 12
        while time.time() < end:
            fr = next((f for f in page.frames if f.name == 'update'), None)
            if fr:
                try:
                    rows, _, _ = self._parse_grid(fr)
                    if rows and rows[0]['case_number'] != before_case_no:
                        return fr
                except Exception:
                    pass
            time.sleep(0.5)
        # Fall back to a short settle wait rather than silently dropping the
        # rest of pagination (matches the sister publicsearch.py pattern).
        page.wait_for_timeout(2000)
        return next((f for f in page.frames if f.name == 'update'), frame)

    # ── Record building ──────────────────────────────────────────────────

    def _rows_to_records(self, rows: List[Dict[str, str]]) -> List[Dict]:
        records = []
        skipped_non_estate = 0
        for r in rows:
            if not self.is_estate_case(r['case_type']):
                skipped_non_estate += 1
                continue
            first, last = self.parse_name_lf(r['name']) if r['name'] else ('', '')
            if r['address']:
                address, city, zip_code = self.parse_address(r['address'])
            else:
                address, city, zip_code = '', '', ''
            records.append(self.build_record(
                decedent_first_name=first,
                decedent_last_name=last,
                address=address,
                city=city,
                zip_code=zip_code,
                case_number=r['case_number'],
                case_type=r['case_type'],
                filing_date=r['filing_date'],
            ))
        if skipped_non_estate:
            self.logger.info(
                f"Ellis: filtered out {skipped_non_estate} non-estate case(s) "
                f"(guardianship/mental health/etc — none seen in initial sample, "
                f"kept as a defensive second line per SYSTEM_GUIDE)")
        return records

    # ── Executor/administrator enrichment (best-effort, UNVERIFIED) ───────
    # See module docstring: a GitHub Actions account-level billing block hit
    # mid-investigation before this could be exercised end to end. Written
    # defensively: self-disables after the first failure so a wrong
    # assumption here can't eat the job's time budget or block core records.

    def _enrich_with_details(self, page, records: List[Dict], begin_str: str, end_str: str):
        budget_end = time.time() + DETAIL_BUDGET_SEC
        attempted = 0
        broken = False
        for rec in records:
            if broken or attempted >= DETAIL_MAX or time.time() > budget_end:
                break
            case_no = rec.get('case_number', '')
            if not case_no:
                continue
            attempted += 1
            try:
                detail = self._fetch_case_detail(page, begin_str, end_str, case_no)
            except Exception as e:
                self.logger.warning(
                    f"Ellis: detail enrichment broke on case {case_no} ({str(e)[:150]}) "
                    f"— disabling enrichment for the rest of this run")
                broken = True
                continue
            if not detail:
                continue
            if detail.get('executor_name'):
                rec['executor_name'] = detail['executor_name']
            if detail.get('executor_address'):
                rec['executor_address'] = detail['executor_address']
            # Upgrade address/city/zip only if the results grid didn't
            # already have one -- the grid value is confirmed/verified, this
            # popup-sourced one is not, so never let it clobber a known-good
            # value.
            if detail.get('address') and not rec.get('address'):
                rec['address'] = detail['address']
            if detail.get('city') and not rec.get('city'):
                rec['city'] = detail['city']
            if detail.get('zip_code') and not rec.get('zip_code'):
                rec['zip_code'] = detail['zip_code']

        note = " (self-disabled after a failure)" if broken else ""
        self.logger.info(
            f"Ellis: detail enrichment attempted for {attempted} of {len(records)} record(s){note}")

    def _fetch_case_detail(self, page, begin_str: str, end_str: str,
                            case_number: str) -> Optional[Dict]:
        """Re-runs the full known-good search flow (cheap relative to the
        job's time budget at this county's ~1-record/day volume) rather than
        guessing at how to close/reuse the 'More Information' popup without
        live verification, then clicks that specific case's row and reads
        the Case Detail panel.

        NOTE: only looks at the page the case appears on during THIS fresh
        search (does not re-paginate to hunt for it), so if a case has
        drifted to page 2+ by the time enrichment runs, it will be skipped
        (logged, non-fatal). Given the confirmed ~1/day volume this is not
        expected to matter for Ellis in practice, but is a known limitation.
        """
        frame = self._open_probate_search(page, begin_str, end_str)
        if not frame:
            return None

        try:
            clicked = frame.evaluate("""(caseNo) => {
                const inputs = Array.from(document.querySelectorAll('input[readonly]'));
                const target = inputs.find(i => (i.value||'').trim() === caseNo);
                if (!target) return false;
                const row = target.closest('tr');
                if (!row) return false;
                const btn = Array.from(row.querySelectorAll('button')).find(
                    b => (b.textContent||'').includes('More Information'));
                if (!btn) return false;
                btn.click();
                return true;
            }""", case_number)
        except Exception as e:
            self.logger.warning(f"Ellis: 'More Information' click-finder failed for {case_number}: "
                                 f"{str(e)[:200]}")
            return None

        if not clicked:
            self.logger.warning(
                f"Ellis: could not locate 'More Information' button for case {case_number} "
                f"(may be on a later results page not re-checked during enrichment)")
            return None

        populated = self._poll(
            lambda: frame.evaluate(
                "() => { const el = document.querySelector('[name=\"P_97\"]'); "
                "return !!el && el.value.trim().length > 0; }"),
            timeout=12)
        if not populated:
            self.logger.warning(f"Ellis: case detail for {case_number} never populated (P_97 empty)")
            return None

        try:
            fields = frame.evaluate("""() => {
                const val = (name) => { const el = document.querySelector(`[name="${name}"]`); return el ? el.value.trim() : ''; };
                const repRows = [];
                const tbl = document.getElementById('GRIDTBL_22B');
                if (tbl) {
                    for (const row of Array.from(tbl.rows)) {
                        const inputs = Array.from(row.querySelectorAll('input[name="GD"]'));
                        if (!inputs.length) continue;
                        const vals = inputs.map(i => (i.value||'').trim());
                        if (vals.some(v => v)) repRows.push(vals);
                    }
                }
                return {address: val('P_107'), city_state_zip: val('P_108'), representatives: repRows};
            }""")
        except Exception as e:
            self.logger.warning(
                f"Ellis: reading case detail fields for {case_number} failed: {str(e)[:200]}")
            return None

        result: Dict[str, str] = {}
        if fields.get('address'):
            result['address'] = fields['address']
        csz = fields.get('city_state_zip', '') or ''
        if csz:
            m = re.match(r'^\s*([A-Za-z\s]+?)\s*,?\s*TX\s*(\d{5})?\s*$', csz, re.I)
            if m:
                result['city'] = m.group(1).strip().title()
                if m.group(2):
                    result['zip_code'] = m.group(2)

        reps = fields.get('representatives') or []
        if reps:
            # UNVERIFIED column order (see module docstring) -- the static
            # template's field widths were P_110(30) / P_109(70) / P_111(60)
            # in that left-to-right DOM order, which is our best structural
            # guess at [role/type, name, address] (short role word, long
            # name, medium address). Prefer a row whose first column looks
            # like an executor/administrator role; else take the first row
            # in this "Representative Information" grid.
            role_kw = ('EXECUT', 'ADMINISTRAT', 'APPLICANT', 'REPRESENTATIVE')
            chosen = None
            for row in reps:
                role = (row[0] if len(row) > 0 else '').upper()
                if any(k in role for k in role_kw):
                    chosen = row
                    break
            if not chosen:
                chosen = reps[0]
            name_val = chosen[1] if len(chosen) > 1 else (chosen[0] if chosen else '')
            addr_val = chosen[2] if len(chosen) > 2 else ''
            if name_val:
                result['executor_name'] = name_val
            if addr_val:
                result['executor_address'] = addr_val

        return result
