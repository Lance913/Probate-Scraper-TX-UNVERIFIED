"""
Tyler Odyssey Public Access scraper -- the CLASSIC on-prem-shaped product
(URL pattern ".../PublicAccess/default.aspx", ASP.NET WebForms, __VIEWSTATE
etc.), shared across Tarrant, Denton, and (pending resolution) Johnson
counties. This is a DIFFERENT product from the newer Tyler "Enterprise
Public Access" / "Odyssey Portal" (URL shape ".../Portal/Home/Dashboard/...")
that Bexar (and, per its own investigation, Collin) runs -- see that
county's own scraper module, do not conflate the two.

============================================================================
CONFIRMED VIA PROBE (probe_tyler_odyssey.py, run via GitHub Actions from a
US IP -- see PR description for run links/logs):
============================================================================

NAVIGATION MECHANICS (confirmed by reading the landing page's own inline JS,
after an initial wrong assumption cost one probe iteration -- see git log):
  Each "* Case Records" link on default.aspx has an href like
  javascript:LaunchSearch('Search.aspx?ID=200', false, true, sbxControlID2).
  LaunchSearch() does NOT just navigate to that URL -- it reads
  `sbxControlID2.value` (the location <select>'s CURRENTLY SELECTED value,
  e.g. "1101" for Denton's "Probate Court"), dynamically builds a <form
  method=post action="Search.aspx?ID=200">, adds hidden fields NodeID=<that
  value> and NodeDesc=<that option's text>, and submits it. A direct
  page.goto('Search.aspx?ID=200') skips all of this -- NodeID/NodeDesc stay
  blank in the rendered form, and submitting THAT search gets silently
  redirected back to default.aspx by the server (confirmed: this is exactly
  what happened on the first attempt). The correct flow is therefore:
  select the right <option> in #sbxControlID2 first, THEN click the real
  link (so its onclick really runs), never navigate to Search.aspx directly.

TARRANT -- BLOCKED, confirmed, NOT a scraper bug:
  https://odyssey.tarrantcounty.com/PublicAccess/default.aspx (200, redirects
  same-path to portal-txtarrant.tylertech.cloud -- Tyler-cloud-hosted, but
  still the classic PublicAccess UI/shape). Landing page's #sbxControlID2
  has an "All Probate Courts" option (value="210,211,212", i.e. 3 numbered
  probate courts) as its FIRST/default entry, and one generic entry link,
  "Case Records Search" -> Search.aspx?ID=200 (no separate probate-only
  link, unlike Collin). ANY navigation into Search.aspx on this
  tylertech.cloud-hosted instance is gated by an AWS WAF Bot Control
  interactive image CAPTCHA ("Human Verification" / "Choose all the
  curtains", id=amzn-captcha-verify-button, *.awswaf.com calls) -- a genuine
  visual puzzle, not a silent/auto-passing JS challenge (confirmed: title
  stays "Human Verification" after clicking Begin + 12s of polling; visual
  confirmation via screenshot shows a real 3x3 image-selection CAPTCHA).
  Independently cross-verified by a sibling agent working Collin County
  (same tylertech.cloud/AWS-fronted hosting family) across 3 browser
  engines, fresh sessions, and repeat navigation -- all ruled OUT as
  fixable via browser choice, session handling, or rate-limiting backoff.
  This looks like IP/traffic-pattern classification of the GitHub Actions/
  Azure datacenter range, not something evasive tuning fixes.

  PROJECT POLICY: do NOT attempt any CAPTCHA-solving/bypass technique. This
  scraper detects the block and logs it LOUDLY (ERROR, not a quiet []) so it
  is never mistaken for "zero filings today" (SYSTEM_GUIDE.md Sec 9 bug 1).
  Unblocking Tarrant needs a human decision (e.g. scraping from a
  residential/non-datacenter egress IP) -- flagged in the PR, not solved here.

DENTON -- reaches a real, correctly-scoped search form; WAF-free:
  https://justice1.dentoncounty.gov/PublicAccess/ (200, self-hosted on
  dentoncounty.gov, NOT tylertech.cloud -- this appears to be why it's not
  WAF-gated the way Tyler's own cloud hosting is). #sbxControlID2 has TWO
  separate probate courts, no combined option: value="1101" text="Probate
  Court", value="1108" text="Probate Court #2". Entry link: "JP & County
  Court: Civil, Family & Probate Case Records" -> Search.aspx?ID=200 (a
  SEPARATE "District Court Case Records & Calendar" link/portal also exists
  at ../PublicAccessDC/ -- not used here; TX probate without a dedicated
  statutory probate court runs through County Court, not District Court).
  Search.aspx form (confirmed via live DOM dump, AFTER properly setting
  NodeID -- see above): SearchBy radios Case(0)/Party(1, default)/
  Attorney(2)/DateFiled(6); Party mode requires a Last Name (red-asterisk
  required in the UI) which we don't have for a blind daily scrape, so we
  use DateFiled mode (id=DateFiled). Date range fields: #DateFiledOnAfter /
  #DateFiledOnBefore (text inputs, MM/DD/YYYY, present regardless of
  SearchBy selection). Case-category checkboxes exist in the DOM
  (chkCriminal/chkFamily/chkCivil/chkProbate + chkDtRange* twins, all
  default-checked) but are NOT interactive (Playwright: "Element is not
  visible" on uncheck) -- so category scoping happens ENTIRELY via which
  NodeID/location was selected before submit, not via these checkboxes.
  Submit button: #SearchSubmit.

  Extra confidence on the DateFiled search-mode mechanics specifically (read
  directly from the real downloaded page's own JS, not guessed): the
  #DateFiled radio's onclick="SwitchCaseSearch(this.value, true)" sets
  SearchType="CASE" and SearchMode="FILED" itself, the moment it's checked --
  independent of the submit-time ValidateSearchParameters() validator (whose
  SearchBy switch has no explicit "6" case, which looked like a bug at first
  read until tracing SwitchCaseSearch showed the real field-prep already
  happened earlier, at select-time). Playwright's page.check('#DateFiled')
  fires this onclick the same as a real user click, so the hidden fields
  this scraper depends on ARE set correctly before submit.

  *** NOT YET VERIFIED (blocked by an account-wide GitHub Actions billing
  issue -- see PR description -- before the corrected-NodeID search could
  actually be submitted and a results page captured): the RESULTS TABLE's
  real header names/column shape, pagination control, and the case-DETAIL
  page's Party/Applicant/Executor section shape. The parsing code below is
  written defensively (match headers by name, treat every column optional,
  try several label patterns on the detail page) per SYSTEM_GUIDE.md Sec
  6.2/6.3, informed by the confirmed "Sort By" options (Case Number / Filed
  Date / Filed Date Rev / Status), but has NOT been exercised against a real
  results page. Flagged clearly in the PR -- re-run the probe and adjust
  this parsing the moment Actions billing is restored, before trusting
  Denton's real (non-dry) output.

JOHNSON -- unresolved as of this module's authorship; see scrapers/counties.py
  and the PR description for the current state of the DistrictClerkPA vs
  CountyClerkPA investigation (both candidate URLs connection-reset from
  three different GitHub Actions runs; a raw `requests`-based cross-check
  was queued but not yet returned before the billing block hit).

============================================================================
Design notes:
============================================================================
- One shared class, parameterized per county (slug, display name, base_url),
  following this repo's "one class per PORTAL, not per county" rule
  (SYSTEM_GUIDE.md Sec 3) -- but every step that touches county-specific page
  structure is defensive (try/except, multiple selector strategies, log-and-
  continue) because Tarrant and Denton have already proven to differ in real
  ways (WAF gating, number/shape of probate-court dropdown options) despite
  sharing the same underlying product.
- No OCR needed here: unlike a scanned-document recorder index, Odyssey
  Public Access is a native searchable case-management UI -- party/case data
  is plain indexed HTML text, both in the results grid and the case detail
  page's Party section (assuming that holds once verified -- see above).
- Table parsing matches columns BY HEADER NAME, not fixed index, and treats
  every column as optional (SYSTEM_GUIDE.md Sec 6.2) since we've already
  observed real per-county schema differences on this exact platform family.
"""
import re
import time
from datetime import date, timedelta
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup
from .base import BaseScraper, launch_chromium, is_estate_case

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

_DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})')


def _extract_filed_date(row: Dict) -> str:
    """Denton's grid header for this column is literally "Filed / Location /
    Judicial Officer" -- one combined column, not three -- so
    _HEADER_ALIASES matches it to 'location' (which the alias list expects
    to hold just a court name) rather than 'filed_date' (whose aliases are
    all two/three-word "file date" phrases that don't appear as a
    substring of the real header). Confirmed live: the cell's actual text
    is date + location + judge run together, e.g. "8/24/2026 Probate Court
    Judge ...". Pull the date back out of whichever field it landed in
    rather than assuming the header aliases will ever cleanly separate it."""
    for key in ('filed_date', 'location'):
        m = _DATE_RE.search(row.get(key, '') or '')
        if m:
            mm, dd, yyyy = m.group(1).split('/')
            return f"{int(mm):02d}/{int(dd):02d}/{yyyy}"
    return ''


# Case-type category checkbox ids -- confirmed NOT interactive on Denton
# (present in the DOM but "not visible" to Playwright), kept here as a
# best-effort no-op-safe attempt in case another county renders them for real.
_NON_PROBATE_CHECKBOX_IDS = [
    'chkCriminal', 'chkFamily', 'chkCivil',
    'chkDtRangeCriminal', 'chkDtRangeFamily', 'chkDtRangeCivil',
]

# Label patterns to look for on a case-detail page when hunting for the
# executor/administrator/applicant's name + mailing address. UNVERIFIED
# against a real case-detail page (see module docstring) -- ordered
# roughly most- to least-specific.
_EXECUTOR_LABEL_PATTERNS = [
    r'Independent\s+Executor', r'Executor', r'Independent\s+Administrator',
    r'Administrator', r'Applicant', r'Personal\s+Representative',
]


class TylerOdysseyScraper(BaseScraper):
    """Classic Tyler Odyssey Public Access (".../PublicAccess/default.aspx")."""

    # Overlap window for the daily search -- dedup is keyed on case_number
    # (sheets_writer.py), so re-scanning a wide window is safe and guards
    # against a missed/failed run (SYSTEM_GUIDE.md Sec 9 bug 8: make the
    # lookback configurable per source rather than assuming one size fits all).
    LOOKBACK_DAYS = 30

    def __init__(self, county_slug: str, county_name: str, base_url: str):
        super().__init__(county_name)
        self.slug = county_slug
        self.base_url = base_url.rstrip('/') + '/'

    # ── Orchestration ───────────────────────────────────────────────────────

    def scrape(self, target_date: date) -> List[Dict]:
        self.logger.info(f"Scraping {self.county} County (Tyler Odyssey Public Access) for {target_date}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.error(f"{self.county}: Playwright not installed")
            return []

        try:
            with sync_playwright() as pw:
                browser = launch_chromium(pw)
                ctx = browser.new_context(user_agent=UA)
                try:
                    records = self._scrape_all_probate_courts(browser, ctx, target_date)
                finally:
                    browser.close()
                self.logger.info(f"{self.county}: {len(records)} estate records")
                return records
        except Exception as exc:
            self.logger.error(f"{self.county}: fatal scrape error: {exc}", exc_info=True)
            return []

    def _new_page(self, ctx):
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)
        return page

    def _scrape_all_probate_courts(self, browser, ctx, target_date: date) -> List[Dict]:
        page = self._new_page(ctx)
        landing = self.base_url + 'default.aspx'
        self.logger.info(f"{self.county}: loading landing page {landing}")
        page.goto(landing, wait_until='networkidle', timeout=30_000)
        page.wait_for_timeout(1200)

        locations = self._find_probate_locations(page)
        if not locations:
            self.logger.warning(
                f"{self.county}: no location option with 'probate' in its label found in "
                f"#sbxControlID2 -- falling back to a single pass with whatever location is "
                f"pre-selected by default, relying on client-side is_estate_case() filtering.")
            locations = [(None, None)]  # sentinel: don't touch the dropdown

        all_records: List[Dict] = []
        seen_case_numbers = set()
        for value, text in locations:
            self.logger.info(f"{self.county}: searching location {text!r} (value={value!r})")
            try:
                records = self._search_one_location(page, landing, value, text, target_date)
                for r in records:
                    key = r.get('case_number') or f"{r.get('decedent_last_name')}|{r.get('filing_date')}"
                    if key in seen_case_numbers:
                        continue
                    seen_case_numbers.add(key)
                    all_records.append(r)
            except Exception as exc:
                self.logger.error(f"{self.county}: error searching location {text!r}: {exc}", exc_info=True)
        return all_records

    # ── Per-location search ─────────────────────────────────────────────────

    def _search_one_location(self, page, landing_url: str, node_value: Optional[str],
                              node_text: Optional[str], target_date: date) -> List[Dict]:
        page.goto(landing_url, wait_until='networkidle', timeout=30_000)
        page.wait_for_timeout(800)

        if node_value is not None:
            try:
                page.select_option('#sbxControlID2', value=node_value)
                page.wait_for_timeout(400)
            except Exception as e:
                self.logger.warning(f"{self.county}: could not select location {node_text!r} "
                                     f"(value={node_value!r}): {e} -- skipping this location.")
                return []

        entry_locator = self._find_entry_link(page)
        if not entry_locator:
            self.logger.error(
                f"{self.county}: could not find a case-records search entry link on the "
                f"landing page ({page.url}) -- portal structure may have changed since this "
                f"scraper was written. Returning 0 for this location, but this should be "
                f"investigated, not treated as 'no filings'.")
            return []

        entry_locator.click()
        page.wait_for_timeout(1200)
        try:
            page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        self.logger.info(f"{self.county}: post-nav title={page.title()!r} url={page.url!r}")

        if self._is_waf_blocked(page):
            self.logger.error(
                f"{self.county}: BLOCKED by an AWS WAF bot-verification wall (interactive "
                f"image CAPTCHA) at {page.url!r} (title={page.title()!r}). This is a CONFIRMED, "
                f"investigated platform-level block (see scrapers/tyler_odyssey.py module "
                f"docstring / PR description), NOT a code bug and NOT 'zero filings today'. "
                f"Per project policy we do not attempt to solve/bypass CAPTCHAs. Returning 0 "
                f"for this location -- needs a human decision (e.g. a non-datacenter egress "
                f"IP) to unblock.")
            return []

        if not self._fill_and_submit_date_search(page, target_date):
            self.logger.error(
                f"{self.county}: could not fill/submit the search form as expected at "
                f"{page.url!r} -- form structure may differ from what this scraper assumes. "
                f"Returning 0 for this location; needs investigation, not 'no filings'.")
            return []

        try:
            page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass

        if self._is_waf_blocked(page):
            self.logger.error(f"{self.county}: BLOCKED by WAF wall on the RESULTS page "
                               f"(passed the search-entry gate but hit it after submit).")
            return []

        self._wait_for_results_ready(page)
        return self._collect_all_pages(page, target_date)

    def _wait_for_results_ready(self, page, timeout_s: int = 25) -> None:
        """Poll until the results table actually has data rows, OR the page
        explicitly says there are no results -- never trust a fixed sleep
        here. A slow-rendering results page read too early looks exactly
        like "0 filings today" and silently loses the whole location/court
        for the day (SYSTEM_GUIDE.md Sec 9 bug 1). Logs which branch it took.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                state = page.evaluate("""() => {
                    const body = (document.body.innerText || '').toLowerCase();
                    const noRes = /no cases were found|no records found|0 cases found|no results|did not match/.test(body);
                    const t = document.querySelector('table');
                    const dataRows = t ? t.querySelectorAll('tr').length : 0;
                    return {noRes, dataRows};
                }""")
            except Exception:
                break
            if state.get('noRes'):
                self.logger.info(f"{self.county}: results page explicitly reports NO RESULTS "
                                  f"(not a render timeout).")
                return
            if state.get('dataRows', 0) > 1:
                self.logger.info(f"{self.county}: results table ready with "
                                  f"{state['dataRows']} rows after "
                                  f"{timeout_s - (deadline - time.time()):.1f}s.")
                return
            page.wait_for_timeout(1000)
        self.logger.warning(f"{self.county}: results page still shows no table rows and no "
                             f"explicit 'no results' message after {timeout_s}s -- proceeding "
                             f"to parse anyway, but this may be a slow-load 0, not a real one "
                             f"(SYSTEM_GUIDE.md Sec 9 bug 1).")

    # ── Navigation helpers ───────────────────────────────────────────────────

    def _find_probate_locations(self, page) -> List[Tuple[str, str]]:
        """Every <option> in #sbxControlID2 whose visible text mentions
        'probate' (case-insensitive) -- may be zero (fall back to default),
        one (e.g. Tarrant's combined "All Probate Courts"), or several (e.g.
        Denton's 2 numbered probate courts, no combined option)."""
        try:
            opts = page.evaluate("""() => {
                const sel = document.querySelector('#sbxControlID2');
                if (!sel) return [];
                return Array.from(sel.options).map(o => [o.value, (o.textContent||'').trim()]);
            }""")
        except Exception as e:
            self.logger.warning(f"{self.county}: could not read #sbxControlID2 options: {e}")
            return []
        matches = [(v, t) for v, t in opts if 'probate' in t.lower()]
        self.logger.info(f"{self.county}: probate-labelled location options: {matches}")
        return matches

    def _find_entry_link(self, page):
        """Return a Playwright locator for the right case-records search
        entry link, or None. Must be REALLY CLICKED (not href-resolved and
        navigated to directly) -- see module docstring on LaunchSearch()."""
        candidates = [
            # Prefer an entry link that is itself Probate-labelled (covers a
            # hypothetical county with a dedicated Probate-only entry, like
            # Collin's "Probate Case Records") before falling back to the
            # general civil/family/probate grouping both Tarrant and Denton use.
            'a:has-text("Probate Case Records")',
            'a:has-text("Civil, Family & Probate")',
            'a:has-text("Case Records Search")',
        ]
        for sel in candidates:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    return el
            except Exception:
                continue
        # Last-resort generic match, explicitly excluding Criminal/Calendar/Jail.
        try:
            links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
                .map((a, i) => ({i, text:(a.textContent||'').trim()}))""")
            for l in links:
                low = l['text'].lower()
                if 'case records' in low and 'criminal' not in low:
                    loc = page.locator('a').nth(l['i'])
                    if loc.count() > 0:
                        return loc
        except Exception:
            pass
        return None

    @staticmethod
    def _is_waf_blocked(page) -> bool:
        try:
            info = page.evaluate("""() => {
                const html = document.documentElement.outerHTML;
                return {
                    title: document.title,
                    awswaf: /awswaf|amzn-captcha/i.test(html),
                };
            }""")
        except Exception:
            return False
        title = (info.get('title') or '').lower()
        return bool(info.get('awswaf')) or 'human verification' in title

    # ── Search form ──────────────────────────────────────────────────────────

    def _fill_and_submit_date_search(self, page, target_date: date) -> bool:
        """Blind date-range search (no name needed) -- Party mode requires a
        Last Name we don't have for a daily "what's new" scrape, so we use
        the DateFiled search-by mode (confirmed present on Denton; best-
        effort elsewhere)."""
        for sel in ['#DateFiled', 'input[id="DateFiled"]', 'input[value="6"][name="SearchBy"]']:
            try:
                if page.locator(sel).count() > 0:
                    page.check(sel)
                    page.wait_for_timeout(1000)
                    self.logger.info(f"{self.county}: switched to Date Filed search mode via {sel!r}")
                    break
            except Exception as e:
                self.logger.info(f"{self.county}: {sel!r} not usable: {str(e)[:120]}")

        # Best-effort: narrow case categories to Probate only IF the checkboxes
        # are real interactive controls on this county's form (confirmed NOT
        # the case on Denton -- harmless no-op there; kept for other counties).
        for cb_id in _NON_PROBATE_CHECKBOX_IDS:
            try:
                el = page.locator(f'#{cb_id}')
                if el.count() > 0 and el.is_visible():
                    el.uncheck(force=True, timeout=2000)
                    self.logger.info(f"{self.county}: unchecked case-category #{cb_id}")
            except Exception:
                pass  # not interactive / not present -- rely on client-side filtering

        start_fmt = (target_date - timedelta(days=self.LOOKBACK_DAYS)).strftime('%m/%d/%Y')
        end_fmt = target_date.strftime('%m/%d/%Y')
        filled = False
        for s_sel, e_sel in [
            ('#DateFiledOnAfter', '#DateFiledOnBefore'),
            ('input[id*="DateFiledOnAfter" i]', 'input[id*="DateFiledOnBefore" i]'),
            ('input[id*="DateFrom" i]', 'input[id*="DateTo" i]'),
        ]:
            try:
                if page.locator(s_sel).count() > 0 and page.locator(e_sel).count() > 0:
                    page.fill(s_sel, start_fmt)
                    page.fill(e_sel, end_fmt)
                    self.logger.info(f"{self.county}: date range {start_fmt} .. {end_fmt} via {s_sel!r}/{e_sel!r}")
                    filled = True
                    break
            except Exception as e:
                self.logger.info(f"{self.county}: date fill {s_sel!r} failed: {str(e)[:120]}")

        if not filled:
            self.logger.error(f"{self.county}: could not find/fill a Date Filed range on the search form.")
            return False

        for sel in ['#SearchSubmit', 'input[value="Search" i]', 'button:has-text("Search")']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.click()
                    self.logger.info(f"{self.county}: submitted search via {sel!r}")
                    return True
            except Exception:
                continue
        self.logger.error(f"{self.county}: could not find a Search submit control.")
        return False

    # ── Results parsing ──────────────────────────────────────────────────────
    # *** UNVERIFIED against a real results page -- see module docstring.
    # Written defensively (header-name matching, optional columns) per
    # SYSTEM_GUIDE.md Sec 6.2 so it degrades to "0 parsed, loudly logged",
    # not silently-wrong data, if the real markup differs from this guess.

    # Header name variants we know to look for from the confirmed "Sort By"
    # dropdown options (Case Number / Filed Date / Filed Date Rev / Status)
    # plus standard Odyssey Public Access grid conventions.
    _HEADER_ALIASES = {
        'case_number': ['case number', 'case #', 'case no'],
        'style':       ['case style', 'style', 'style/defendant', 'party', 'name'],
        'case_type':   ['case type', 'type'],
        'filed_date':  ['file date', 'filed date', 'date filed'],
        'location':    ['location', 'court'],
        'status':      ['case status', 'status'],
    }

    def _collect_all_pages(self, page, target_date: date) -> List[Dict]:
        records: List[Dict] = []
        for page_num in range(1, 21):
            html = page.content()
            rows, headers_found = self._parse_results_table(html)
            self.logger.info(f"{self.county}: results page {page_num} -> {len(rows)} rows "
                              f"(headers matched: {headers_found})")
            if page_num == 1 and not rows:
                body_low = BeautifulSoup(html, 'lxml').get_text(' ', strip=True).lower()
                no_results = any(p in body_low for p in
                                  ('no cases were found', 'no records found', '0 cases found',
                                   'no results', 'did not match'))
                self.logger.info(f"{self.county}: 0 rows on first page -- portal says "
                                  f"'no results'={no_results} (if False, this may be a parse "
                                  f"problem, not genuinely zero filings -- investigate before "
                                  f"trusting a 0 here, per SYSTEM_GUIDE.md Sec 9 bug 1).")

            for row in rows:
                case_number = row.get('case_number', '')
                case_type = row.get('case_type', '')
                if case_type and not is_estate_case(case_type):
                    self.logger.info(f"{self.county}: skip non-estate case type "
                                      f"{case_type!r} ({case_number})")
                    continue

                first, last = self._parse_style_name(row.get('style', ''))
                if not last:
                    self.logger.info(f"{self.county}: row with no parseable decedent name "
                                      f"(case {case_number!r}, style={row.get('style')!r}) -- skipping.")
                    continue

                executor_name, executor_address = self._fetch_party_info(page, row)

                records.append(self.build_record(
                    decedent_first_name=first,
                    decedent_last_name=last,
                    case_number=case_number,
                    case_type=case_type,
                    filing_date=_extract_filed_date(row),
                    executor_name=executor_name,
                    executor_address=executor_address,
                ))

            if not self._go_to_next_page(page, rows):
                break
        return records

    def _parse_results_table(self, html: str) -> Tuple[List[Dict], List[str]]:
        soup = BeautifulSoup(html, 'lxml')
        rows_out: List[Dict] = []
        headers_found: List[str] = []

        target = None
        header_row_is_td = False
        # Pass 1: proper semantic <th> headers.
        for table in soup.find_all('table'):
            headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
            if any(h for h in headers if 'case' in h):
                target = table
                break
        # Pass 2 (fallback): classic ASP.NET GridViews often render the header
        # row as plain <td> cells (a bold/CSS-styled first <tr>, no real <th>
        # at all) rather than semantic <th> -- UNVERIFIED whether Odyssey does
        # this here, but it's common enough on this vintage of WebForms grid
        # that it's worth defending against rather than silently returning 0.
        if target is None:
            for table in soup.find_all('table'):
                first_tr = table.find('tr')
                if not first_tr:
                    continue
                first_cells = [c.get_text(strip=True).lower() for c in first_tr.find_all('td')]
                if any('case' in c for c in first_cells):
                    target = table
                    header_row_is_td = True
                    break
        if target is None:
            return rows_out, headers_found

        if header_row_is_td:
            raw_headers = [c.get_text(strip=True).lower() for c in target.find('tr').find_all('td')]
        else:
            raw_headers = [th.get_text(strip=True).lower() for th in target.find_all('th')]
        col_idx: Dict[str, int] = {}
        for field, aliases in self._HEADER_ALIASES.items():
            for i, h in enumerate(raw_headers):
                if any(a == h or a in h for a in aliases):
                    col_idx[field] = i
                    headers_found.append(f"{field}={raw_headers[i]!r}")
                    break

        data_trs = target.find_all('tr')
        if header_row_is_td:
            data_trs = data_trs[1:]  # first row was consumed as the header
        for tr in data_trs:
            tds = tr.find_all('td')
            if not tds:
                continue
            cells = [td.get_text(' ', strip=True) for td in tds]
            if not any(c.strip() for c in cells):
                continue

            def cell(field):
                i = col_idx.get(field)
                return cells[i].strip() if i is not None and 0 <= i < len(cells) else ''

            case_number = cell('case_number')
            style = cell('style')
            if not case_number and not style:
                continue  # likely a header/spacer row, not a data row

            # Try to capture a link to the case detail page alongside the row,
            # matched by case number text (used later by _fetch_party_info).
            link_href = ''
            for a in tr.find_all('a'):
                if case_number and case_number in a.get_text(strip=True):
                    link_href = a.get('href', '')
                    break

            rows_out.append({
                'case_number': case_number,
                'style': style,
                'case_type': cell('case_type'),
                'filed_date': cell('filed_date'),
                'status': cell('status'),
                'link_href': link_href,
            })
        return rows_out, headers_found

    def _parse_style_name(self, style: str) -> Tuple[str, str]:
        """Odyssey case 'style' for a probate matter is commonly 'ESTATE OF
        <NAME>' or 'IN THE ESTATE OF <NAME>, DECEASED'.

        Verified against a real Denton batch (140 records, iterated to 129
        after fixes). Failure modes found and fixed here:
          1. An A/K/A ("also known as") clause after the real name -- e.g.
             "WILLIAM MURRAY TIPTON A/K/A WILLIAM M. TIPTON" was parsed as
             one garbled name. Now stripped before splitting into parts.
          2. Non-estate styles that don't contain "ESTATE OF" at all (e.g.
             "AN ALLEGED INCAPACITATED PERSON IN RE: GUARDIANSHIP OF X") were
             falling through to a blind parse_name() on the whole raw style
             text, producing nonsense like first_name="An Alleged
             Incapacitated Person". These case types should already be
             excluded by is_estate_case() (base.py), but that's a keyword
             safety net, not a hard guarantee -- as a second line of
             defense, if the style doesn't match ESTATE OF at all, this now
             returns ('', '') (dropped by main.py's _useful(), which
             requires a decedent_last_name) instead of guessing a name from
             unrelated text.
          3. A trailing generational suffix after a comma -- e.g. "RAYMOND
             D. ROBERTS, SR" -- broke the "comma means LAST, FIRST" heuristic
             (6/129 records in the same batch): the comma here separates a
             FIRST-name-first name from its suffix, not last from first, so
             it was wrongly routed through parse_name_lf() and came out as
             first_name="Sr". Now stripped (and re-appended to the last
             name) before the LAST,FIRST-vs-FIRST-LAST decision is made."""
        if not style:
            return '', ''
        s = style.strip()
        m = re.search(r'ESTATE OF\s+(.+?)(?:,?\s*DECEASED)?$', s, re.I)
        if not m:
            return '', ''
        name = re.split(r'\s+A/?K/?A\b', m.group(1).strip(), flags=re.I)[0].strip()

        suffix = ''
        sm = re.search(r',\s*(JR|SR|II|III|IV)\.?\s*$', name, re.I)
        if sm:
            suffix = sm.group(1).title()
            name = name[:sm.start()].strip()

        first, last = self.parse_name(name) if ',' not in name else self.parse_name_lf(name)
        if suffix and last:
            last = f"{last} {suffix}"
        return first, last

    def _fetch_party_info(self, page, row: Dict) -> Tuple[str, str]:
        """Open the case detail page (if we have a link) and look for the
        executor/administrator/applicant's name + mailing address in the
        Party section. Always returns to the results page afterward.

        Verified against a real Denton batch: `page.inner_text('body')`
        renders adjacent table cells joined by tab characters WITHOUT a
        newline between them until the row actually ends -- the original
        character class here included \\s (which matches tabs), so a match
        ran straight through the next cell too, e.g. captured
        "Taylor, Richard Scott\\t\\t\\tJack T. Gannon" (applicant name +
        attorney name from two different cells) as one garbled "name".
        Fixed by excluding tabs from the captured class so a match stops at
        the real cell boundary, not just the row's newline."""
        href = row.get('link_href', '')
        if not href:
            return '', ''
        results_url = page.url
        try:
            full = href if href.startswith('http') else self.base_url + href.lstrip('/')
            page.goto(full, wait_until='networkidle', timeout=20_000)
            page.wait_for_timeout(800)
            text = page.inner_text('body')

            name, address = '', ''
            for label in _EXECUTOR_LABEL_PATTERNS:
                m = re.search(
                    rf'{label}[:\s]+([A-Z][A-Za-z.,\'\- ]+?)(?:\t|\n)', text)
                if m:
                    name = m.group(1).strip().rstrip(',')
                    # Look for an address-shaped line shortly after the name.
                    tail = text[m.end():m.end() + 300]
                    addr_m = re.search(
                        r'(\d+[^\n,]+),?\s*\n?\s*([A-Za-z\s]+),?\s*(TX|Texas)\s+(\d{5})',
                        tail)
                    if addr_m:
                        address = f"{addr_m.group(1).strip()}, {addr_m.group(2).strip()}, TX {addr_m.group(4)}"
                    break

            if not name:
                self.logger.info(f"{self.county}: case {row.get('case_number')!r} -- no "
                                  f"executor/administrator/applicant name pattern matched on "
                                  f"detail page (this parser is UNVERIFIED -- see module "
                                  f"docstring; needs a real sample to tune).")
            return name, address
        except Exception as e:
            self.logger.warning(f"{self.county}: could not fetch/parse case detail for "
                                 f"{row.get('case_number')!r}: {str(e)[:150]}")
            return '', ''
        finally:
            try:
                page.goto(results_url, wait_until='networkidle', timeout=20_000)
                page.wait_for_timeout(500)
            except Exception:
                pass

    def _go_to_next_page(self, page, current_rows: List[Dict]) -> bool:
        """Click 'Next' and WAIT for the first row's text to actually change
        before returning, rather than a fixed sleep -- a slow-rendering next
        page read too early looks like "0 rows" / end-of-results and silently
        truncates the scrape (SYSTEM_GUIDE.md Sec 9 bug 2)."""
        before_text = page.evaluate("""() => {
            const t = document.querySelector('table');
            const tr = t ? t.querySelectorAll('tr')[1] : null;
            return tr ? tr.innerText : '';
        }""")
        for sel in ['a:has-text("Next")', '[aria-label="Next"]', 'input[value="Next" i]']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible() and el.is_enabled():
                    el.click()
                    try:
                        page.wait_for_function(
                            """(before) => {
                                const t = document.querySelector('table');
                                const tr = t ? t.querySelectorAll('tr')[1] : null;
                                const now = tr ? tr.innerText : '';
                                return now !== before && now !== '';
                            }""",
                            arg=before_text, timeout=15_000)
                    except Exception:
                        self.logger.warning(
                            f"{self.county}: next-page row content didn't change within 15s "
                            f"of clicking Next -- re-checking once before treating this as "
                            f"end-of-results (SYSTEM_GUIDE.md Sec 9 bug 2).")
                        page.wait_for_timeout(3000)
                    try:
                        page.wait_for_load_state('networkidle', timeout=10_000)
                    except Exception:
                        pass
                    return True
            except Exception:
                continue
        return False
