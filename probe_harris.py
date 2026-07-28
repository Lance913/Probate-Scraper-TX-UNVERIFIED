"""
Probe v3 -- Harris County Court Search: case-detail page + pagination.

v1/v2 findings (offline-analyzed from v2's downloaded HTML artifact -- see
probe_out/search1_casenum_daterange.html from that run):
  - CourtSearch.aspx?CaseType=Probate, Search #1 (Case Number section, blank
    case#, Court=All, Status=-All, File Date range) -> navigates to
    CourtSearch_R.aspx?ID=<opaque token>, a real results page. No CAPTCHA/
    bot-check encountered (consistent with the working foreclosure scraper on
    this same domain).
  - Results grid is an ASP.NET ListView (id 'ListViewCases') inside a table
    id='itemPlaceholderContainer', paginated by a DataPager
    (id 'DataPagerLisViewCases1', 200 rows/page) driven by
    __doPostBack('ctl00$ContentPlaceHolder1$DataPagerLisViewCases1$ctl0N$ctl0N','').
    IMPORTANT DOM gotcha: naive `querySelectorAll('tr')` over-collects because
    of nested layout tables in the header; must use the table's native
    `.rows`/`.cells` (or bs4 direct-child tr search within the correct
    <tbody>, tbodies[8] in that sample) to get clean rows. Real data rows
    have 9 cells; a 1-cell spacer <tr> follows each data row.
  - Columns (9): [0] Events icon (javascript:void(0)), [1] Case Number
    (text + a __doPostBack link id styled
    'ctl00_ContentPlaceHolder1_ListViewCases_ctrlN_btnSelect'),
    [2] Court, [3] File Date, [4] Status, [5] Type Desc, [6] Subtype,
    [7] Style (e.g. "IN THE ESTATE OF: JOHN DOE, DECEASED" /
    "IN THE GUARDIANSHIP OF: JANE DOE, INCAPACITATED" / "X, TESTATOR" for
    pre-death will-safekeeping deposits / "..., TRUST"), [8] Parties (BLANK
    in the list view for every one of 200 sampled rows -- party/executor
    info is NOT in the results table, must come from the case detail).
  - Of 200 sampled real cases (01/08/2024-01/21/2024 window, ALL Harris
    Probate-Court case types, unfiltered): 167 "IN THE ESTATE OF: ...,
    DECEASED" (true decedent-estate leads), 22 "IN THE GUARDIANSHIP OF: ...,
    INCAPACITATED" (already excluded by base.py's NON_ESTATE_CASE_TYPE_
    KEYWORDS matching Type Desc "GUARDIANSHIP OF AN ADULT"/"GUARDIANSHIP NO
    FEES"), 7 living-testator will-safekeeping deposits (Type Desc "WILLS
    FOR SAFE KEEPING", Style ends ", TESTATOR" -- NOT a decedent, current
    keyword list would NOT catch these since "WILLS FOR SAFE KEEPING"
    doesn't match any NON_ESTATE_CASE_TYPE_KEYWORDS), a couple of TRUST-only
    matters (Style doesn't end ", DECEASED"). --> the Style suffix itself
    (", DECEASED" vs anything else) is a MORE reliable estate-vs-not signal
    than Type Desc keyword matching, and doubles as the decedent-name
    extraction. Plan: parse Style with regex, treat "^IN THE ESTATE OF:
    (.+), DECEASED$" as the primary include+name-extraction rule, AND still
    run is_estate_case(type_desc) as a defense-in-depth second check per
    SYSTEM_GUIDE Sec 6 step 3.

Open question this probe answers: what does the case DETAIL page look like
after clicking a case's Select link -- is the applicant/executor name +
mailing address already structured HTML (no OCR), or does it require going
deeper (e.g. a separate "Parties" tab, or a scanned document)? Also confirms
the pagination postback actually advances to new rows.
"""
import json
import logging
import os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logging.basicConfig(level=logging.INFO, format='%(asctime)s [probe] %(message)s')
log = logging.getLogger()

OUT_DIR = 'probe_out'
os.makedirs(OUT_DIR, exist_ok=True)

URL = 'https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate'
DATE_FROM = '01/08/2024'
DATE_TO = '01/21/2024'

TABLE_JS = """
() => {
  const t = document.getElementById('itemPlaceholderContainer');
  if (!t) return null;
  const out = [];
  for (const tb of t.tBodies) {
    for (const tr of tb.rows) {
      const cells = Array.from(tr.cells).map(c => ({
        text: (c.innerText || '').trim(),
        links: Array.from(c.querySelectorAll('a')).map(a => ({
          text: (a.textContent || '').trim(),
          href: a.getAttribute('href'),
        })),
      }));
      if (cells.length >= 8) out.push(cells);
    }
  }
  return out;
}
"""

BODY_TEXT_JS = "() => document.body ? document.body.innerText.slice(0, 6000) : ''"


def safe_dump(page, slug, full=True, tries=3):
    """Wait for the page to settle then grab url/html/screenshot, retrying
    if Playwright reports an in-flight navigation (seen in probe v2)."""
    for attempt in range(tries):
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception as e:
            log.warning(f"{slug}: networkidle wait attempt {attempt}: {e}")
        page.wait_for_timeout(1500)
        try:
            url = page.url
            title = page.title()
            log.info(f"{slug}: url={url} title={title!r}")
            if full:
                html = page.content()
                with open(f'{OUT_DIR}/{slug}.html', 'w') as f:
                    f.write(html)
                log.info(f"{slug}: saved HTML ({len(html)} bytes)")
                page.screenshot(path=f'{OUT_DIR}/{slug}.png', full_page=True)
                log.info(f"{slug}: saved screenshot")
                body_text = page.evaluate(BODY_TEXT_JS)
                log.info(f"{slug}: body text (first 6000 chars):\n{body_text}")
            return True
        except Exception as e:
            log.warning(f"{slug}: dump attempt {attempt} failed ({e}); retrying after settle")
            page.wait_for_timeout(2000)
    log.error(f"{slug}: giving up after {tries} attempts")
    return False


def dump_results_table(page, slug, max_rows=6):
    try:
        rows = page.evaluate(TABLE_JS)
    except Exception as e:
        log.warning(f"{slug}: table JS eval failed: {e}")
        return
    if rows is None:
        log.warning(f"{slug}: itemPlaceholderContainer not found on this page")
        return
    with open(f'{OUT_DIR}/{slug}_rows.json', 'w') as f:
        json.dump(rows, f, indent=2)
    log.info(f"{slug}: {len(rows)} data rows (9-cell) captured")
    for i, row in enumerate(rows[:max_rows]):
        texts = [c['text'] for c in row]
        log.info(f"  row[{i}]: {texts}")


def click_postback(page, target):
    """Fire an ASP.NET __doPostBack the same way clicking its <a> would."""
    page.evaluate(f"__doPostBack('{target}','')")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled'],
        )
        ctx = browser.new_context(
            accept_downloads=True,
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        )
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30000)

        # ---- Run Search #1 to reach the results grid ----
        log.info("##### Search #1: Case Number section, blank case#, date range")
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle', timeout=15000)
        page.wait_for_timeout(1000)
        page.select_option('select[name="ctl00$ContentPlaceHolder1$ddlCourt"]', value='All')
        page.select_option('select[name="ctl00$ContentPlaceHolder1$DropDownListStatus"]', value='-All')
        page.fill('#ctl00_ContentPlaceHolder1_txtFrom', DATE_FROM)
        page.fill('#ctl00_ContentPlaceHolder1_txtTo', DATE_TO)
        page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        safe_dump(page, 'results_page1', full=False)
        dump_results_table(page, 'results_page1')

        # ---- Click into the first case's detail (row ctrl0) ----
        log.info("\n\n##### Clicking case row 0 detail (Select postback)")
        new_page = None
        try:
            with ctx.expect_page(timeout=4000) as pi:
                click_postback(page, 'ctl00$ContentPlaceHolder1$ListViewCases$ctrl0$btnSelect')
            new_page = pi.value
            log.info("A NEW browser tab/page opened for the case detail.")
        except PWTimeout:
            log.info("No new tab opened -- assuming in-place postback on the same page.")
        target = new_page or page
        if new_page:
            target.wait_for_load_state('networkidle', timeout=20000)
        safe_dump(target, 'case_detail_ctrl0', full=True)
        # Search this page's text for party/executor-ish keywords as a quick signal.
        try:
            txt = target.evaluate(BODY_TEXT_JS)
            for kw in ['Applicant', 'Executor', 'Administrator', 'Attorney', 'Parties',
                       'Address', 'Independent Executor', 'Personal Representative']:
                if kw.lower() in txt.lower():
                    log.info(f"case_detail_ctrl0: contains keyword {kw!r}")
        except Exception as e:
            log.warning(f"case_detail keyword scan failed: {e}")
        if new_page:
            new_page.close()

        # ---- Fresh search again, then test pagination (page 2) ----
        log.info("\n\n##### Fresh Search #1 again, then test DataPager page 2")
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle', timeout=15000)
        page.wait_for_timeout(1000)
        page.select_option('select[name="ctl00$ContentPlaceHolder1$ddlCourt"]', value='All')
        page.select_option('select[name="ctl00$ContentPlaceHolder1$DropDownListStatus"]', value='-All')
        page.fill('#ctl00_ContentPlaceHolder1_txtFrom', DATE_FROM)
        page.fill('#ctl00_ContentPlaceHolder1_txtTo', DATE_TO)
        page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        safe_dump(page, 'results_page1_again', full=False)
        dump_results_table(page, 'results_page1_again', max_rows=2)

        try:
            # Locate the real page-2 postback target from the actual DOM (id may
            # shift), rather than hardcoding the exact ctl0N guessed from v2.
            page2_target = page.evaluate("""
                () => {
                  const as = Array.from(document.querySelectorAll('a'));
                  const a = as.find(x => (x.textContent||'').trim() === '2'
                                          && (x.getAttribute('href')||'').includes('__doPostBack'));
                  if (!a) return null;
                  const m = /__doPostBack\\('([^']+)'/.exec(a.getAttribute('href'));
                  return m ? m[1] : null;
                }
            """)
            log.info(f"Page-2 postback target resolved as: {page2_target!r}")
            if page2_target:
                click_postback(page, page2_target)
                safe_dump(page, 'results_page2', full=False)
                dump_results_table(page, 'results_page2', max_rows=4)
            else:
                log.warning("Could not resolve a page-2 postback target anchor.")
        except Exception as e:
            log.error(f"Pagination test failed: {e}", exc_info=True)

        browser.close()
    log.info("Probe v3 complete.")


if __name__ == '__main__':
    main()
