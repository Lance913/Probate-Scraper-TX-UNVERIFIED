"""
Probe v4 -- Harris County Court Search: the "Parties" panel (executor/
applicant name + address -- the actual lead contact).

v3 findings: clicking a case's Select link (row postback) expands an
in-place case-detail view containing a case header row (Case/File Date/Type
Desc/Subtype/Style/Status/Judge/Court) PLUS a "Parties" link:
  javascript:__doPostBack('ctl00$ContentPlaceHolder1$gridViewCase','Parties$0')
(CommandArgument 'Parties$0' -- the trailing 0 is the row index, so this
should generalize to 'Parties$N' for the Nth row of gridViewCase, though we
only ever expand one case at a time in practice so it's always $0 once
selected). This probe clicks that link for TWO cases -- a Heirship
determination (522276, the case already reached in v3) AND a "PROBATE OF
WILL (INDEPENDENT ADMINISTRATION)" case (522272, 48% of all Probate-Court
volume in the sampled window, the single most common case type) -- to see
whether the applicant/executor's name + mailing address is plain structured
HTML (no OCR) and whether the party-role labels differ by case type.
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

BODY_TEXT_JS = "() => document.body ? document.body.innerText : ''"


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
            body_text = page.evaluate(BODY_TEXT_JS)
            with open(f'{OUT_DIR}/{slug}_body.txt', 'w') as f:
                f.write(body_text)
            log.info(f"{slug}: url={url} title={title!r} html={len(html)}b saved+screenshot+body.txt")
            log.info(f"{slug}: FULL BODY TEXT:\n{body_text}")
            return True
        except Exception as e:
            log.warning(f"{slug}: dump attempt {attempt} failed ({e}); retrying")
            page.wait_for_timeout(2000)
    log.error(f"{slug}: giving up after {tries} attempts")
    return False


def click_postback(page, target, arg=''):
    # f-string interpolation (not an evaluate() arg=) -- this is the form
    # that worked in probe v3; the arg-array-passing form
    # `page.evaluate("(a) => __doPostBack(a[0], a[1])", [target, arg])`
    # throws a Chromium strict-mode serialization TypeError on this page.
    # ASP.NET control-ID targets/CommandArguments are always plain
    # alphanumeric/$-safe strings, so naive interpolation is safe here.
    page.evaluate(f"__doPostBack('{target}','{arg}')")


def run_search(page):
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


def inspect_case(page, ctrl, slug_prefix):
    log.info(f"\n\n##### {slug_prefix}: opening case row {ctrl}")
    run_search(page)
    click_postback(page, f'ctl00$ContentPlaceHolder1$ListViewCases${ctrl}$btnSelect')
    safe_dump(page, f'{slug_prefix}_detail')

    # Find the Parties postback target fresh from THIS page's DOM (row index
    # inside gridViewCase may not always be 0, though v3 showed it was).
    parties_target = page.evaluate("""
        () => {
          const as = Array.from(document.querySelectorAll('a'));
          const a = as.find(x => (x.textContent||'').trim() === 'Parties'
                                  && (x.getAttribute('href')||'').includes('__doPostBack'));
          if (!a) return null;
          const m = /__doPostBack\\('([^']+)',\\s*'([^']*)'\\)/.exec(a.getAttribute('href'));
          return m ? [m[1], m[2]] : null;
        }
    """)
    log.info(f"{slug_prefix}: Parties postback = {parties_target!r}")
    if not parties_target:
        log.warning(f"{slug_prefix}: no 'Parties' link found on this case's detail view")
        return
    click_postback(page, parties_target[0], parties_target[1])
    safe_dump(page, f'{slug_prefix}_parties')

    # Also dump any table with 'Party'/'Role'/'Address'-ish headers precisely.
    try:
        tables = page.evaluate("""
            () => Array.from(document.querySelectorAll('table')).map((t,i) => ({
              idx: i, id: t.id,
              rows: Array.from(t.rows).slice(0, 15).map(r =>
                Array.from(r.cells).map(c => (c.innerText||'').trim()))
            })).filter(t => t.rows.length > 1)
        """)
        with open(f'{OUT_DIR}/{slug_prefix}_parties_tables.json', 'w') as f:
            json.dump(tables, f, indent=2)
        for t in tables:
            log.info(f"{slug_prefix}_parties: table[{t['idx']}] id={t['id']!r} rows={t['rows']}")
    except Exception as e:
        log.warning(f"{slug_prefix}_parties: table scan failed: {e}")


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

        # Case 1: Heirship determination (522276) -- already seen its docket in v3.
        try:
            inspect_case(page, 'ctrl0', 'heirship_522276')
        except Exception as e:
            log.error(f"heirship_522276 inspection failed: {e}", exc_info=True)

        # Case 2: Probate of Will (Independent Administration) -- 522272, the
        # single most common case type (48% of sampled volume). Highest value.
        try:
            inspect_case(page, 'ctrl1', 'probateofwill_522272')
        except Exception as e:
            log.error(f"probateofwill_522272 inspection failed: {e}", exc_info=True)

        browser.close()
    log.info("Probe v4 complete.")


if __name__ == '__main__':
    main()
