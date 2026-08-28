"""
Probe v5 -- Harris County Court Search: two remaining architecture questions.

v4 findings (see probe_out/*_parties*.png|json from that run -- these are the
KEY findings, already understood, not re-tested here):
  - Case detail is reached via Select postback
    (__doPostBack('ctl00$ContentPlaceHolder1$ListViewCases$ctrlN$btnSelect',''))
    which swaps the view to a single-row 'gridViewCase' table (cols: CaseID,
    Case, File Date, Type Desc, Subtype, Style, Status, Judge, Court, View
    All) -- NOTE 'Case' (e.g. 522272) is the public case number; 'CaseID'
    (e.g. 2117108) is an internal DB id, NOT to be used as case_number.
  - Clicking that row's 'Parties' link
    (__doPostBack('ctl00$ContentPlaceHolder1$gridViewCase','Parties$0'))
    reveals table id='GridViewParties', cols: Case, Role, Party, Attorney.
    Party cell = newline-separated "Name\\nStreet\\n[optional N/A line]\\n
    City<nbsp>State<nbsp>Zip". 100% plain structured HTML text -- NO OCR
    needed anywhere in this flow.
  - Role vocabulary seen so far: 'Applicant', 'Independent Executor',
    'Deceased', 'Attorney Ad Litem (Participant)'. Both samples used
    'Deceased' (not 'Decedent') consistently. The 'Deceased' row SOMETIMES
    carries an address (occasionally matching the applicant's address) --
    opportunistic bonus property-address signal, not guaranteed (matches
    the assignment's expectation that property address is usually blank).

Open questions THIS probe answers:
  1. Can the search-by-Case-Number field (txtFileNo, left of the Court/
     Status dropdowns, section 1) jump directly to a single case -- i.e. is
     there a clean "fresh page load -> fill case number only -> Search ->
     land on (or one click away from) that case's detail" path? This would
     let the real scraper do: (a) one date-range sweep to collect case
     numbers + list-view fields across all pages, (b) a separate, simple,
     stateless fetch per case number for Parties -- instead of having to
     keep one page's ViewState alive across many sequential row-selects.
  2. After viewing case A's Parties, does the ORIGINAL 200-row ListViewCases
     grid still exist/react in the DOM (so case B could be selected without
     re-running the date-range search), or does the detail view fully
     replace it? Tests both so we know which scraper architecture to build.
"""
import logging
import os
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [probe] %(message)s')
log = logging.getLogger()

OUT_DIR = 'probe_out'
os.makedirs(OUT_DIR, exist_ok=True)

URL = 'https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate'
DATE_FROM = '01/08/2024'
DATE_TO = '01/21/2024'


def safe_dump(page, slug, tries=3):
    for attempt in range(tries):
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception as e:
            log.warning(f"{slug}: networkidle wait attempt {attempt}: {e}")
        page.wait_for_timeout(1500)
        try:
            url = page.url
            title = page.title()
            html = page.content()
            with open(f'{OUT_DIR}/{slug}.html', 'w') as f:
                f.write(html)
            page.screenshot(path=f'{OUT_DIR}/{slug}.png', full_page=True)
            body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
            log.info(f"{slug}: url={url} title={title!r} html={len(html)}b")
            log.info(f"{slug}: BODY TEXT (first 2500 chars):\n{body_text[:2500]}")
            return True
        except Exception as e:
            log.warning(f"{slug}: dump attempt {attempt} failed ({e}); retrying")
            page.wait_for_timeout(2000)
    log.error(f"{slug}: giving up after {tries} attempts")
    return False


def click_postback(page, target, arg=''):
    page.evaluate(f"__doPostBack('{target}','{arg}')")


def dump_parties_table(page, slug):
    rows = page.evaluate("""
        () => {
          const t = document.getElementById('ctl00_ContentPlaceHolder1_GridViewParties');
          if (!t) return null;
          return Array.from(t.rows).map(r => Array.from(r.cells).map(c => (c.innerText||'').trim()));
        }
    """)
    log.info(f"{slug}: GridViewParties = {rows!r}")


def dump_case_grid(page, slug):
    rows = page.evaluate("""
        () => {
          const t = document.getElementById('ctl00_ContentPlaceHolder1_gridViewCase');
          if (!t) return null;
          return Array.from(t.rows).map(r => Array.from(r.cells).map(c => (c.innerText||'').trim()));
        }
    """)
    log.info(f"{slug}: gridViewCase = {rows!r}")
    list_rows = page.evaluate("""
        () => {
          const t = document.getElementById('itemPlaceholderContainer');
          if (!t) return -1;
          let n = 0;
          for (const tb of t.tBodies) for (const tr of tb.rows) if (tr.cells.length >= 8) n++;
          return n;
        }
    """)
    log.info(f"{slug}: ListViewCases (itemPlaceholderContainer) 9-cell row count = {list_rows}")


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

        # ---- Q1: search by Case Number alone (fresh page, no date range) ----
        log.info("##### Q1: fresh page, Case Number field only = 522272, no dates")
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle', timeout=15000)
        page.wait_for_timeout(1000)
        page.fill('#ctl00_ContentPlaceHolder1_txtFileNo', '522272')
        page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        safe_dump(page, 'q1_by_casenumber_only')
        dump_case_grid(page, 'q1_by_casenumber_only')
        dump_parties_table(page, 'q1_by_casenumber_only')

        # ---- Q2: after viewing case A's parties, can we still select case B
        #      from the ORIGINAL list without re-searching? ----
        log.info("\n\n##### Q2: date-range search, open case ctrl0's Parties, then try ctrl1 Select")
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle', timeout=15000)
        page.wait_for_timeout(1000)
        page.select_option('select[name="ctl00$ContentPlaceHolder1$ddlCourt"]', value='All')
        page.select_option('select[name="ctl00$ContentPlaceHolder1$DropDownListStatus"]', value='-All')
        page.fill('#ctl00_ContentPlaceHolder1_txtFrom', DATE_FROM)
        page.fill('#ctl00_ContentPlaceHolder1_txtTo', DATE_TO)
        page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        page.wait_for_load_state('networkidle', timeout=20000)
        page.wait_for_timeout(1500)
        dump_case_grid(page, 'q2_before_any_select')

        click_postback(page, 'ctl00$ContentPlaceHolder1$ListViewCases$ctrl0$btnSelect')
        page.wait_for_load_state('networkidle', timeout=20000)
        page.wait_for_timeout(1500)
        dump_case_grid(page, 'q2_after_select_ctrl0')
        click_postback(page, 'ctl00$ContentPlaceHolder1$gridViewCase', 'Parties$0')
        page.wait_for_load_state('networkidle', timeout=20000)
        page.wait_for_timeout(1500)
        dump_parties_table(page, 'q2_after_parties_ctrl0')

        log.info("Now attempting ctrl1 Select WITHOUT re-running the search...")
        try:
            click_postback(page, 'ctl00$ContentPlaceHolder1$ListViewCases$ctrl1$btnSelect')
            page.wait_for_load_state('networkidle', timeout=20000)
            page.wait_for_timeout(1500)
            dump_case_grid(page, 'q2_after_select_ctrl1_no_research')
            safe_dump(page, 'q2_after_select_ctrl1_no_research')
        except Exception as e:
            log.error(f"Q2: ctrl1 select without re-search FAILED: {e}", exc_info=True)

        browser.close()
    log.info("Probe v5 complete.")


if __name__ == '__main__':
    main()
