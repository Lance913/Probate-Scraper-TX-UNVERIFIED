"""
Harris County Probate Scraper.

Portal: https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate
A standalone Harris County Clerk ASP.NET WebForms case-search system (NOT
Tyler/Odyssey like most other TX counties in this project) -- confirmed via
an official Harris County Probate Courts informational site
(probate.harriscountytx.gov), which links a near-identical
CourtSettingsTyler.aspx?CaseType=Probate path on the same domain. That
second path was investigated and is a DIFFERENT tool ("finding the court
docket date for a particular case or reviewing the docket schedule for a
court" -- a hearing-calendar search), not a filing index, so it is not used
here. This is also the EXACT SAME DOMAIN as the existing, working
foreclosure scraper (Lance913/Scraper_Python, scrapers/harris.py ->
FRCL_R.aspx) but a completely different application path (case-management/
docket search vs recorded-document search) -- its Playwright anti-bot
posture (webdriver flag override, AutomationControlled disabled) was reused
here, but the form fields/table schema/flow are entirely different and were
independently reverse-engineered via probe_harris.py (see that file's git
history for the full v1-v5 investigation trail). No CAPTCHA/bot-check was
encountered anywhere in that investigation.

FLOW (2 phases, deliberately never reuses one page's postback state across
different cases -- see PHASE 2 docstring for why):

Phase 1 -- list scan (self._search_date_range):
  CourtSearch.aspx?CaseType=Probate has TWO independent search forms. We use
  the "Case Number" section (txtFileNo left BLANK, ddlCourt=All,
  DropDownListStatus=-All, txtFrom/txtTo = File Date range) -> btnSearchCase.
  This returns ALL cases (any type -- Harris's "Probate Courts" docket also
  carries Guardianship/Heirship/Trust/pre-death Will-safekeeping matters,
  not just decedent estates) filed in that date range, across a
  ListViewCases + DataPager grid (200 rows/page, no case-type filter exists
  at search time).

  Columns (9 cells/row): Events(icon) | Case | Court | File Date | Status |
  Type Desc | Subtype | Style | Parties (ALWAYS blank in the list view --
  party/executor info is never in the list, only reachable via Phase 2).
  "Style" reads like "IN THE ESTATE OF: JOHN DOE, DECEASED" for real
  decedent-estate cases, vs "IN THE GUARDIANSHIP OF: X, INCAPACITATED" /
  "X, TESTATOR" (pre-death will-safekeeping deposits -- the "testator" is
  alive) / no "DECEASED" suffix at all for trust-only matters. In a 200-case
  sample this Style suffix was a MORE reliable estate-vs-not signal than
  Type Desc keywords alone -- e.g. Type Desc "WILLS FOR SAFE KEEPING"
  doesn't match any of base.py's NON_ESTATE_CASE_TYPE_KEYWORDS but is a
  LIVING testator, not a decedent -- so we gate primarily on the Style
  regex (which doubles as the decedent-name source), AND still layer
  base.py's is_estate_case(type_desc) as a second check per
  SYSTEM_GUIDE.md Sec 6 step 3.

Phase 2 -- per-case Parties fetch (self._fetch_parties):
  For each case that passed the Phase-1 filter, do a COMPLETELY FRESH
  page.goto() + search BY CASE NUMBER ALONE (txtFileNo=<num>, dates blank)
  -> one-row result -> click that row's Select postback -> click its
  "Parties" postback -> parse table id=GridViewParties (cols: Case, Role,
  Party, Attorney). Party/Attorney cells are newline-separated
  "Name\\nStreet\\n[optional N/A placeholder line]\\nCity<nbsp>State<nbsp>Zip".
  100% structured HTML text -- NO OCR anywhere in this flow.

  IMPORTANT hard-won bug (found via probe v5): you CANNOT select a second
  case (ListViewCases$ctrl1$btnSelect) from the SAME results page after
  having already opened another case's Parties view in that same page
  instance -- the server redirects to a generic Maintenance.aspx error page
  (stale/invalid ViewState once the view has moved from "list" mode to
  "detail" mode). Each case's Parties data MUST be fetched from its own
  fresh navigation (goto resets everything cleanly). This costs a few extra
  requests per case but is reliable; daily estate-case volume in Harris
  (~20-30/day observed in a 2-week sample) makes this a non-issue for the
  job's time budget.

Role vocabulary observed in GridViewParties (2 case types sampled: Heirship
and Probate of Will/Independent Administration, together >55% of volume):
  'Deceased' (the decedent -- row occasionally also carries an address,
  used here as an opportunistic bonus property-address signal, NOT
  guaranteed -- per the assignment brief, a probate case index is
  person-centric, not property-centric, so this is expected to be blank
  often), 'Applicant', 'Independent Executor', 'Attorney Ad Litem
  (Participant)' (a court-appointed attorney representing an unknown/
  incapacitated party's interests -- NOT the estate's actionable contact,
  must never be picked as executor).
"""
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .base import BaseScraper, launch_chromium, is_estate_case

SEARCH_URL = 'https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate'

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)

# How many days back (inclusive of target_date) to search each run. A small
# safety margin against late-indexed filings -- case_number-based dedup in
# sheets_writer.py makes re-scanning the overlap harmless (SYSTEM_GUIDE.md
# Sec 9 bug 8: tune the lookback window per source rather than using one
# fixed global window).
LOOKBACK_DAYS = 3

# Safety cap on DataPager pages per date-range search (200 rows/page ->
# 30 pages = 6000 cases, far beyond any realistic LOOKBACK_DAYS window even
# for Harris) so a pagination bug can never spin forever.
MAX_PAGES = 30

# "IN THE ESTATE OF: <NAME>, DECEASED" -- also the decedent-name source.
# Deliberately does NOT match "IN THE GUARDIANSHIP OF: ..., INCAPACITATED",
# "..., TESTATOR" (pre-death will-safekeeping deposits), or trust-only
# styles that don't end in ", DECEASED".
RE_ESTATE_STYLE = re.compile(r'^\s*IN\s+THE\s+ESTATE\s+OF:\s*(.+?),?\s*DECEASED\.?\s*$', re.I)

# Priority order for picking the single best "executor" contact out of
# possibly several Parties rows (most specific/authoritative role first).
# 'APPLICANT' is the fallback for case types (e.g. Heirship) that never
# name a formal executor/administrator role at all.
EXECUTOR_ROLE_PRIORITY = [
    'INDEPENDENT EXECUTOR', 'EXECUTOR', 'DEPENDENT ADMINISTRATOR',
    'TEMPORARY ADMINISTRATOR', 'ADMINISTRATOR', 'PERSONAL REPRESENTATIVE',
    'APPLICANT',
]
DECEDENT_ROLES = ('DECEASED', 'DECEDENT')

# City/State/Zip line, e.g. "Houston TX 77096" or "THE WOODLANDS TX 77381"
# (nbsp already normalised to space by the time this runs).
RE_CITY_STATE_ZIP = re.compile(r'^(.*?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$')


def _clean(s: Optional[str]) -> str:
    return (s or '').replace('\xa0', ' ').strip()


def _parse_party_cell(text: str) -> Tuple[str, str, str, str, str]:
    """'Name\\nStreet\\n[N/A]\\nCity ST Zip' -> (name, street, city, state, zip)."""
    lines = [_clean(l) for l in (text or '').split('\n')]
    lines = [l for l in lines if l and l.upper() != 'N/A']
    if not lines:
        return '', '', '', '', ''
    name = lines[0]
    street_lines = lines[1:]
    city = state = zipc = ''
    if street_lines:
        m = RE_CITY_STATE_ZIP.match(street_lines[-1])
        if m:
            city, state, zipc = m.group(1).strip(), m.group(2), m.group(3)
            street_lines = street_lines[:-1]
    street = ', '.join(street_lines)
    return name, street, city, state, zipc


def _format_address_line(street: str, city: str, state: str, zipc: str) -> str:
    parts = [p for p in [street.strip(), (f"{city} {state} {zipc}".strip())] if p and p.strip()]
    return ', '.join(parts)


def _pick_executor(party_rows: List[Tuple[str, str, str]]) -> Tuple[str, str, str, str, str]:
    """party_rows: list of (role, party_text, attorney_text). Returns the
    (name, street, city, state, zip) of the highest-priority role match, or
    all-blank if no recognized executor-ish role is present."""
    best_rank = None
    best = ('', '', '', '', '')
    for role, party_text, _attorney_text in party_rows:
        up = _clean(role).upper()
        for rank, key in enumerate(EXECUTOR_ROLE_PRIORITY):
            if key in up:
                if best_rank is None or rank < best_rank:
                    best_rank = rank
                    best = _parse_party_cell(party_text)
                break
    return best


def _find_decedent_address(party_rows: List[Tuple[str, str, str]]) -> Tuple[str, str, str]:
    """Opportunistic bonus: the 'Deceased' party row sometimes (not always)
    carries an address. Returns (street, city, zip) or blanks. Substring
    match (not exact-equality) for the same reason as EXECUTOR_ROLE_PRIORITY
    below -- only 2 case types were sampled during investigation, so we
    stay permissive against label variants like "Decedent" or "Deceased
    Party" rather than requiring an exact "Deceased" string."""
    for role, party_text, _attorney_text in party_rows:
        up = _clean(role).upper()
        if any(d in up for d in DECEDENT_ROLES):
            _name, street, city, _state, zipc = _parse_party_cell(party_text)
            return street, city, zipc
    return '', '', ''


class HarrisCountyScraper(BaseScraper):

    def __init__(self):
        super().__init__('Harris')

    def scrape(self, target_date: date) -> List[Dict]:
        date_from = target_date - timedelta(days=LOOKBACK_DAYS - 1)
        date_to = target_date
        self.logger.info(f"Harris: scraping File Date {date_from} - {date_to}")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.error("Harris: Playwright not installed")
            return []

        records: List[Dict] = []
        try:
            with sync_playwright() as pw:
                browser = launch_chromium(pw)
                ctx = browser.new_context(accept_downloads=True, user_agent=USER_AGENT)
                page = ctx.new_page()
                page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                page.set_default_timeout(30_000)

                try:
                    listed = self._search_date_range(page, date_from, date_to)
                except Exception as exc:
                    self.logger.error(f"Harris: date-range search failed: {exc}", exc_info=True)
                    listed = []
                self.logger.info(f"Harris: {len(listed)} total cases in date range (all case types)")

                estate_cases = [c for c in listed if self._decedent_name(c) and
                                 is_estate_case(c['type_desc'])]
                self.logger.info(
                    f"Harris: {len(estate_cases)} pass estate-case filter "
                    f"({len(listed) - len(estate_cases)} excluded as non-estate)")

                for c in estate_cases:
                    party_rows: List[Tuple[str, str, str]] = []
                    try:
                        party_rows = self._fetch_parties(page, c['case_number'])
                    except Exception as exc:
                        self.logger.warning(
                            f"Harris: case {c['case_number']}: Parties fetch failed: {exc}")
                    records.append(self._build_record(c, party_rows))

                browser.close()
        except Exception as exc:
            self.logger.error(f"Harris: scrape crashed: {exc}", exc_info=True)

        self.logger.info(f"Harris: {len(records)} estate records built")
        return records

    # ── Phase 1: date-range list scan ───────────────────────────────────────

    def _search_date_range(self, page, date_from: date, date_to: date) -> List[Dict]:
        page.goto(SEARCH_URL, wait_until='domcontentloaded')
        self._wait_settled(page)
        page.select_option('select[name="ctl00$ContentPlaceHolder1$ddlCourt"]', value='All')
        page.select_option('select[name="ctl00$ContentPlaceHolder1$DropDownListStatus"]', value='-All')
        page.fill('#ctl00_ContentPlaceHolder1_txtFrom', date_from.strftime('%m/%d/%Y'))
        page.fill('#ctl00_ContentPlaceHolder1_txtTo', date_to.strftime('%m/%d/%Y'))
        page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        self._wait_settled(page)
        self._check_maintenance(page, 'date-range search')

        all_dicts: List[Dict] = []
        prev_first_case = None
        for page_num in range(1, MAX_PAGES + 1):
            reported = self._result_count_from_page(page)
            rows = self._extract_list_rows(page)
            if rows is None:
                if reported == 0:
                    self.logger.info(f"Harris: page {page_num}: portal reports 0 records — done")
                else:
                    self.logger.error(
                        f"Harris: page {page_num}: results grid not found "
                        f"(reported count={reported}) — possible slow load, layout "
                        f"change, or block; NOT treating as a genuine zero")
                    self._check_maintenance(page, f'page {page_num} grid-missing')
                break

            first_case = rows[0][1] if rows else None
            self.logger.info(
                f"Harris: page {page_num}: {len(rows)} rows (first case {first_case}, "
                f"reported total={reported})")
            all_dicts.extend(self._row_to_dict(r) for r in rows)

            if first_case is not None and first_case == prev_first_case:
                # Pagination bug 2 defense: a click that didn't actually advance.
                self.logger.warning(
                    f"Harris: page {page_num} first row unchanged from previous page "
                    f"— stopping to avoid an infinite loop / duplicate scan")
                break
            prev_first_case = first_case

            next_target = self._find_next_postback(page)
            if not next_target:
                self.logger.info(f"Harris: page {page_num}: no further pages")
                break
            self._click_postback(page, next_target)
            self._wait_settled(page)
            new_first = self._peek_first_case(page)
            if new_first == first_case:
                # Stale render — wait longer once before trusting it (SYSTEM_GUIDE
                # Sec 9 bug 2: don't trust a fixed sleep after "next page").
                self.logger.warning(
                    f"Harris: page {page_num + 1} looked unchanged right after Next "
                    f"— waiting longer once and re-checking")
                page.wait_for_timeout(3000)

        if reported is not None and reported != len(all_dicts):
            self.logger.warning(
                f"Harris: portal reported {reported} total records but we collected "
                f"{len(all_dicts)} — possible pagination gap, investigate if large")

        return all_dicts

    def _row_to_dict(self, cells: List[str]) -> Dict:
        # cells: ['', case_number, court, file_date, status, type_desc, subtype, style, '']
        return {
            'case_number': cells[1].strip(),
            'court':       cells[2].strip(),
            'file_date':   cells[3].strip(),
            'status':      cells[4].strip(),
            'type_desc':   cells[5].strip(),
            'subtype':     cells[6].strip(),
            'style':       cells[7].strip(),
        }

    def _decedent_name(self, c: Dict) -> str:
        m = RE_ESTATE_STYLE.match(c.get('style', '') or '')
        return m.group(1).strip() if m else ''

    def _extract_list_rows(self, page) -> Optional[List[List[str]]]:
        """Returns None if the results grid never appeared (caller decides
        whether that means genuine zero results or a real problem, via
        _result_count_from_page). Uses the table's native tBodies/rows/cells
        (NOT a blanket querySelectorAll('tr')) because this grid's header
        contains nested per-column sort-icon tables that a naive descendant
        selector would also match, corrupting row alignment."""
        try:
            page.wait_for_selector('#itemPlaceholderContainer', timeout=25_000)
        except Exception:
            return None
        return page.evaluate("""
            () => {
              const t = document.getElementById('itemPlaceholderContainer');
              if (!t) return null;
              const out = [];
              for (const tb of t.tBodies) {
                for (const tr of tb.rows) {
                  if (tr.cells.length !== 9) continue;
                  out.push(Array.from(tr.cells).map(c => c.innerText.trim()));
                }
              }
              return out;
            }
        """)

    def _peek_first_case(self, page) -> Optional[str]:
        rows = self._extract_list_rows(page)
        return rows[0][1] if rows else None

    def _find_next_postback(self, page) -> Optional[str]:
        return page.evaluate(r"""
            () => {
              const as = Array.from(document.querySelectorAll('a'));
              let a = as.find(x => (x.textContent||'').trim() === 'Next'
                                    && (x.getAttribute('href')||'').includes('__doPostBack'));
              if (!a) {
                a = as.find(x => (x.textContent||'').trim() === '>>'
                                  && (x.getAttribute('href')||'').includes('__doPostBack'));
              }
              if (!a) return null;
              const m = /__doPostBack\('([^']+)'/.exec(a.getAttribute('href'));
              return m ? m[1] : null;
            }
        """)

    def _result_count_from_page(self, page) -> Optional[int]:
        try:
            txt = page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return None
        m = re.search(r'(\d+)\s+Record\(s\)\s+Found', txt, re.I)
        return int(m.group(1)) if m else None

    # ── Phase 2: per-case Parties fetch ─────────────────────────────────────

    def _fetch_parties(self, page, case_number: str) -> List[Tuple[str, str, str]]:
        page.goto(SEARCH_URL, wait_until='domcontentloaded')
        self._wait_settled(page)
        page.fill('#ctl00_ContentPlaceHolder1_txtFileNo', case_number)
        page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        self._wait_settled(page)
        self._check_maintenance(page, f'case {case_number} lookup')

        select_target = page.evaluate("""
            () => {
              const as = Array.from(document.querySelectorAll("a[id*='ListViewCases'][id*='btnSelect']"));
              if (!as.length) return null;
              const m = /__doPostBack\\('([^']+)'/.exec(as[0].getAttribute('href')||'');
              return m ? m[1] : null;
            }
        """)
        if not select_target:
            self.logger.warning(f"Harris: case {case_number}: no selectable row found by case-number search")
            return []
        self._click_postback(page, select_target)
        self._wait_settled(page)
        self._check_maintenance(page, f'case {case_number} select')

        # Diagnostic-only sanity check: txtFileNo was assumed exact-match
        # from a single probe observation. If it ever turns out to be a
        # partial/fuzzy match, this makes a case/party mismatch visible in
        # the log instead of silently mis-attributing another case's
        # executor -- it does not block the fetch either way.
        selected_case = page.evaluate("""
            () => {
              const t = document.getElementById('ctl00_ContentPlaceHolder1_gridViewCase');
              if (!t || t.rows.length < 2) return null;
              const c = t.rows[1].cells[1];
              return c ? c.innerText.trim() : null;
            }
        """)
        if selected_case and selected_case != case_number:
            self.logger.warning(
                f"Harris: case {case_number}: case-number search selected a "
                f"different case ({selected_case}) -- txtFileNo may not be "
                f"exact-match; Parties data below belongs to {selected_case}")

        parties_target = page.evaluate(r"""
            () => {
              const as = Array.from(document.querySelectorAll('a'));
              const a = as.find(x => (x.textContent||'').trim() === 'Parties'
                                      && (x.getAttribute('href')||'').includes('__doPostBack'));
              if (!a) return null;
              const m = /__doPostBack\('([^']+)',\s*'([^']*)'\)/.exec(a.getAttribute('href'));
              return m ? [m[1], m[2]] : null;
            }
        """)
        if not parties_target:
            self.logger.info(f"Harris: case {case_number}: no 'Parties' link on its detail view")
            return []
        self._click_postback(page, parties_target[0], parties_target[1])
        self._wait_settled(page)
        self._check_maintenance(page, f'case {case_number} parties')

        rows = page.evaluate("""
            () => {
              const t = document.getElementById('ctl00_ContentPlaceHolder1_GridViewParties');
              if (!t) return null;
              return Array.from(t.rows).slice(1).map(r => Array.from(r.cells).map(c => c.innerText));
            }
        """)
        if not rows:
            return []
        return [(r[1], r[2], r[3] if len(r) > 3 else '') for r in rows if len(r) >= 3]

    def _build_record(self, c: Dict, party_rows: List[Tuple[str, str, str]]) -> Dict:
        decedent_full = re.sub(r',', '', self._decedent_name(c))
        dec_first, dec_last = self.parse_name(decedent_full)
        dec_street, dec_city, dec_zip = _find_decedent_address(party_rows)
        ex_name, ex_street, ex_city, ex_state, ex_zip = _pick_executor(party_rows)
        executor_address = _format_address_line(ex_street, ex_city, ex_state, ex_zip)

        return self.build_record(
            decedent_first_name=dec_first,
            decedent_last_name=dec_last,
            address=dec_street,
            city=dec_city,
            zip_code=dec_zip,
            case_number=c['case_number'],
            case_type=c['type_desc'] or c['subtype'],
            filing_date=c['file_date'],
            executor_name=ex_name,
            executor_address=executor_address,
        )

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _wait_settled(self, page, timeout: int = 20_000, extra_wait: int = 1200):
        try:
            page.wait_for_load_state('networkidle', timeout=timeout)
        except Exception as exc:
            self.logger.warning(f"Harris: networkidle wait: {exc}")
        page.wait_for_timeout(extra_wait)

    def _click_postback(self, page, target: str, arg: str = ''):
        # f-string interpolation, not evaluate(expr, arg=...): the arg-array
        # passing form throws a Chromium strict-mode serialization TypeError
        # on this page for reasons never fully isolated. Targets/args here
        # only ever come from our own regex-extracted ASP.NET control
        # id/CommandArgument strings (alphanumeric + '$'), never raw external
        # input, so naive interpolation is safe.
        page.evaluate(f"__doPostBack('{target}','{arg}')")

    def _check_maintenance(self, page, context: str):
        """Harris's ASP.NET app redirects to a generic Maintenance.aspx page
        on an invalid/stale postback (observed when re-using a case-detail
        page's ViewState for a second, unrelated case — see module
        docstring). If we ever land here, or anywhere unexpected, log it
        loudly rather than silently proceeding as if nothing was found."""
        url = page.url
        if 'maintenance' in url.lower() or 'error' in url.lower():
            self.logger.error(
                f"Harris: unexpected redirect during {context}: {url} — "
                f"treating this as a real failure, not a silent zero-results page")
