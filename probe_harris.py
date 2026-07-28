"""
Probe v2 -- Harris County Court Search: run a real date-range search and dump
the results table schema.

v1 findings (see probe_out/courtsearch_form.json / .png from that run):
  - CourtSearch.aspx?CaseType=Probate has TWO independent search forms:
    (1) Case Number [optional] + Court (ddlCourt) + Status (DropDownListStatus)
        + File Date From/To (txtFrom/txtTo) -> btnSearchCase
        This is the one we want: Case Number can stay BLANK, so this is a
        pure "all cases filed in date range" search -- no name required.
    (2) Party/Attorney/Company (rblPartyType) + Last/First/Middle Name +
        Bar Card + File Date From/To (txtFrom2/txtTo2) -> btnSearch
        Requires (probably) a name; tested here for comparison only.
  - NO case-type filter/checkboxes anywhere on this page -- CaseType=Probate
    is just the court-department context (Probate Courts vs Civil/Family/
    etc), not a sub-type filter. Case type (Independent Administration vs
    Guardianship vs Heirship etc) must come from the RESULTS TABLE and be
    filtered client-side via base.py's is_estate_case().
  - CourtSettingsTyler.aspx is a DIFFERENT tool ("finding the court docket
    date for a particular case or reviewing the docket schedule") -- a
    hearing-calendar search, not a filing index. Not used further.

This probe: fill Search #1 with a blank case number + a real historical
File Date range, submit, and dump every table's full row content (headers +
data rows + any anchor hrefs/onclick in cells, to catch case-detail links and
GridView pagination postback targets). Then reload fresh and try Search #2
with a blank name + date range for comparison.
"""
import json
import logging
import os
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [probe] %(message)s')
log = logging.getLogger()

OUT_DIR = 'probe_out'
os.makedirs(OUT_DIR, exist_ok=True)

URL = 'https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate'

# Safely-historical fixed window (real portal data, not dependent on sandbox
# "today"). Harris is the most populous TX county -- 2 weeks should yield a
# real, non-trivial, but not overwhelming sample.
DATE_FROM = '01/08/2024'
DATE_TO = '01/21/2024'

TABLE_DUMP_JS = """
() => {
  const tables = Array.from(document.querySelectorAll('table'));
  return tables.map((t, idx) => {
    const rows = Array.from(t.querySelectorAll('tr'));
    return {
      idx, id: t.id, className: t.className, rowCount: rows.length,
      rows: rows.slice(0, 200).map(tr =>
        Array.from(tr.querySelectorAll('th,td')).map(cell => ({
          text: (cell.innerText || '').trim(),
          links: Array.from(cell.querySelectorAll('a')).map(a => ({
            text: (a.textContent || '').trim(),
            href: a.getAttribute('href'),
            onclick: a.getAttribute('onclick'),
          })),
        }))
      ),
    };
  });
}
"""

BODY_TEXT_JS = "() => document.body ? document.body.innerText.slice(0, 4000) : ''"


def dump_tables(page, slug):
    try:
        tables = page.evaluate(TABLE_DUMP_JS)
    except Exception as e:
        log.warning(f"{slug}: table dump failed: {e}")
        return
    with open(f'{OUT_DIR}/{slug}_tables.json', 'w') as f:
        json.dump(tables, f, indent=2)
    log.info(f"{slug}: {len(tables)} <table> elements found")
    for t in tables:
        log.info(f"  table[{t['idx']}] id={t['id']!r} class={t['className']!r} rowCount={t['rowCount']}")
    # Log full content only for tables that look like real data grids.
    for t in tables:
        if t['rowCount'] < 2 or t['rowCount'] > 200:
            continue
        # Skip tiny layout tables (e.g. 1-2 cols, all rows identical length 1).
        widths = {len(r) for r in t['rows']}
        if widths == {1} and t['rowCount'] < 5:
            continue
        log.info(f"  ==== FULL DUMP table[{t['idx']}] id={t['id']!r} rowCount={t['rowCount']} ====")
        for ri, row in enumerate(t['rows']):
            cell_texts = [c['text'] for c in row]
            log.info(f"    row[{ri}]: {cell_texts}")
            for ci, c in enumerate(row):
                for lk in c['links']:
                    log.info(f"      row[{ri}] cell[{ci}] LINK text={lk['text']!r} "
                              f"href={lk['href']!r} onclick={lk['onclick']!r}")


def dump_common(page, slug):
    try:
        page.wait_for_load_state('networkidle', timeout=20000)
    except Exception as e:
        log.warning(f"{slug}: networkidle wait: {e}")
    page.wait_for_timeout(2000)
    log.info(f"{slug}: final URL = {page.url}")
    html = page.content()
    with open(f'{OUT_DIR}/{slug}.html', 'w') as f:
        f.write(html)
    log.info(f"{slug}: saved HTML ({len(html)} bytes)")
    try:
        page.screenshot(path=f'{OUT_DIR}/{slug}.png', full_page=True)
    except Exception as e:
        log.warning(f"{slug}: screenshot failed: {e}")
    try:
        body_text = page.evaluate(BODY_TEXT_JS)
        log.info(f"{slug}: body text (first 4000 chars):\n{body_text}")
    except Exception as e:
        log.warning(f"{slug}: body text failed: {e}")
    dump_tables(page, slug)


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

        # ---- Search #1: Case Number section (blank case#) = pure date-range search ----
        log.info("\n\n##### SEARCH #1: Case Number section, blank case#, date range only")
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle', timeout=15000)
        page.wait_for_timeout(1000)
        try:
            page.select_option('select[name="ctl00$ContentPlaceHolder1$ddlCourt"]', value='All')
            page.select_option('select[name="ctl00$ContentPlaceHolder1$DropDownListStatus"]', value='-All')
            page.fill('#ctl00_ContentPlaceHolder1_txtFrom', DATE_FROM)
            page.fill('#ctl00_ContentPlaceHolder1_txtTo', DATE_TO)
            log.info(f"Search #1: filled File Date {DATE_FROM} - {DATE_TO}, Court=All, Status=-All")
            page.click('#ctl00_ContentPlaceHolder1_btnSearchCase')
        except Exception as e:
            log.error(f"Search #1: fill/click failed: {e}", exc_info=True)
        dump_common(page, 'search1_casenum_daterange')

        # ---- Search #2: Party section, blank name, date range only (fresh reload) ----
        log.info("\n\n##### SEARCH #2: Party section, blank name, date range only")
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle', timeout=15000)
        page.wait_for_timeout(1000)
        try:
            page.fill('#ctl00_ContentPlaceHolder1_txtFrom2', DATE_FROM)
            page.fill('#ctl00_ContentPlaceHolder1_txtTo2', DATE_TO)
            log.info(f"Search #2: filled File Date2 {DATE_FROM} - {DATE_TO}, blank name")
            page.click('#ctl00_ContentPlaceHolder1_btnSearch')
        except Exception as e:
            log.error(f"Search #2: fill/click failed: {e}", exc_info=True)
        dump_common(page, 'search2_party_daterange_blank')

        browser.close()
    log.info("Probe v2 complete.")


if __name__ == '__main__':
    main()
