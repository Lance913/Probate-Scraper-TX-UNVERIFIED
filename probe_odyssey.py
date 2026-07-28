"""
Probe v2 — Collin County has MIGRATED off cijspub.co.collin.tx.us (that URL
now 503s "This site has been moved permanently") to a new Tyler-hosted
domain: https://portal-txcollin.tylertech.cloud/Publicaccess (discovered from
a link on the dead page's own error screen). That's the SAME "tylertech.cloud"
domain family this repo's README already notes for Bexar
(portal-txbexar.tylertech.cloud), and v1 of this probe showed Tarrant's
classic URL 302-redirects to portal-txtarrant.tylertech.cloud too. So this
version:

Part 1 — re-fingerprint the same county candidates (now with a settle-wait so
we don't miss a client-side redirect), PLUS peek at Bexar's tylertech.cloud
URL purely for platform-shape comparison (read-only; not building Bexar).

Part 2 — deep-dive the REAL current Collin URL. Also capture network
responses (esp. JSON/XHR) while interacting, since SYSTEM_GUIDE §6 notes
modern portals are often React/JS apps with a query-param-driven results API
worth reverse-engineering directly.
"""
import logging
import re
import sys

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

COLLIN_CANDIDATES = [
    "https://portal-txcollin.tylertech.cloud/Publicaccess",
    "https://portal-txcollin.tylertech.cloud/",
]
BEXAR_REFERENCE = "https://portal-txbexar.tylertech.cloud/Publicaccess"

CANDIDATES = {
    "dallas":  "https://obpublicaccess24.dallascounty.org/PublicAccess/",
    "tarrant": "https://odyssey.tarrantcounty.com/PublicAccess/default.aspx",
    "denton":  "https://justice1.dentoncounty.gov/PublicAccess/",
    "johnson": "https://pa.johnsoncountytx.org/publicaccess/",
    "travis":  "https://odysseyweb.traviscountytx.gov/Portal",
    "harris":  "https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate",
    "ellis":   "https://public.lgsonlinesolutions.com/ors.html",
}


def dump_form(page, label):
    info = page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('input, select, textarea, button'));
        return els.map(el => {
            const o = {tag: el.tagName.toLowerCase(), type: el.type||'', id: el.id||'',
                       name: el.name||'', placeholder: el.placeholder||'',
                       text: (el.tagName==='BUTTON'? (el.textContent||'').trim() : ''),
                       visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)};
            if (el.tagName === 'SELECT') {
                o.options = Array.from(el.options).map(op => `${op.value}=${op.text}`);
            }
            return o;
        });
    }""")
    log.info(f"[{label}] form elements ({len(info)}):")
    for el in info:
        if el['tag'] == 'select':
            log.info(f"  SELECT id={el['id']!r} name={el['name']!r} visible={el['visible']} "
                      f"options={el.get('options')}")
        elif el['tag'] == 'button' or el['type'] in ('submit', 'button'):
            log.info(f"  BUTTON/SUBMIT id={el['id']!r} name={el['name']!r} text={el['text']!r} "
                      f"visible={el['visible']}")
        else:
            log.info(f"  {el['tag'].upper()} type={el['type']!r} id={el['id']!r} "
                      f"name={el['name']!r} placeholder={el['placeholder']!r} visible={el['visible']}")


def dump_nav_links(page, label, limit=60):
    links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
        .map(a => ({text:(a.textContent||'').replace(/\\s+/g,' ').trim(), href:a.getAttribute('href')||''}))
        .filter(l => l.text || l.href);""")
    log.info(f"[{label}] links ({len(links)}):")
    for l in links[:limit]:
        log.info(f"  LINK text={l['text']!r} href={l['href']!r}")


def check_captcha(page, label):
    info = page.evaluate("""() => {
        const html = document.documentElement.outerHTML;
        return {
            recaptcha: /recaptcha/i.test(html),
            hcaptcha: /hcaptcha/i.test(html),
            captchaWord: /captcha/i.test(html),
            viewstate: !!document.querySelector('input[name="__VIEWSTATE"]'),
        };
    }""")
    log.info(f"[{label}] captcha/markers: {info}")
    return info


def click_first_matching(page, selectors, label):
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                log.info(f"[{label}] clicking control matching {sel!r} (text={el.inner_text()[:60]!r})")
                el.click()
                page.wait_for_timeout(2000)
                try:
                    page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


def investigate_collin(pw):
    log.info("=" * 70)
    log.info("PART 2 — Collin County deep dive (NEW tylertech.cloud URL)")
    log.info("=" * 70)
    browser = pw.chromium.launch(headless=True,
                                  args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ))
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30000)

    xhr_log = []
    def on_response(resp):
        try:
            ct = resp.headers.get('content-type', '')
            if 'json' in ct or '/api/' in resp.url.lower():
                xhr_log.append((resp.request.method, resp.url, resp.status))
        except Exception:
            pass
    page.on('response', on_response)

    try:
        landed = False
        for url in COLLIN_CANDIDATES:
            try:
                log.info(f"Collin: goto {url}")
                resp = page.goto(url, wait_until='networkidle', timeout=25000)
                status = resp.status if resp else None
                log.info(f"Collin: HTTP status={status} final_url={page.url!r} title={page.title()!r}")
                if status and status < 400:
                    landed = True
                    break
            except Exception as e:
                log.info(f"Collin: {url} failed: {str(e)[:200]}")
        if not landed:
            log.error("Collin: could not land on any candidate URL — stopping deep dive.")
            browser.close()
            return

        page.wait_for_timeout(2000)
        check_captcha(page, 'collin')
        dump_nav_links(page, 'collin')
        try:
            body_text = page.inner_text('body')
            log.info(f"Collin: full body text ({len(body_text)} chars):\n{body_text[:5000]}")
        except Exception as e:
            log.info(f"Collin: could not read body text: {e}")
        dump_form(page, 'collin landing')

        # Look for a "Smart Search" / "Search" / "Case Records" entry point (the
        # modern Odyssey Portal product gates the actual search behind a button,
        # per what Travis's landing page described).
        clicked = click_first_matching(page, [
            'a:has-text("Smart Search")', 'button:has-text("Smart Search")',
            'a:has-text("Case Records")', 'a:has-text("Search")',
            'button:has-text("Search")', 'a:has-text("Begin")',
        ], 'collin')
        log.info(f"Collin: clicked into a search entry point: {clicked}")
        if clicked:
            log.info(f"Collin: post-click title={page.title()!r} url={page.url!r}")
            check_captcha(page, 'collin search page')
            dump_nav_links(page, 'collin search page')
            try:
                body_text = page.inner_text('body')
                log.info(f"Collin: search-page body text ({len(body_text)} chars):\n{body_text[:5000]}")
            except Exception as e:
                log.info(f"Collin: could not read search-page body text: {e}")
            dump_form(page, 'collin search page')

            # Try to open "Advanced Filtering Options" if present (Travis
            # mentioned this as where case-category/court selection lives).
            clicked2 = click_first_matching(page, [
                'a:has-text("Advanced Filtering")', 'button:has-text("Advanced Filtering")',
                'a:has-text("Advanced")', 'button:has-text("Advanced")',
                'a:has-text("Filter")', 'button:has-text("Filter")',
            ], 'collin')
            log.info(f"Collin: clicked into advanced filtering: {clicked2}")
            if clicked2:
                log.info(f"Collin: post-filter-click title={page.title()!r} url={page.url!r}")
                dump_form(page, 'collin advanced filter panel')
                try:
                    body_text = page.inner_text('body')
                    log.info(f"Collin: advanced-filter body text ({len(body_text)} chars):\n{body_text[:5000]}")
                except Exception as e:
                    log.info(f"Collin: could not read advanced-filter body text: {e}")

        log.info(f"Collin: XHR/JSON responses observed so far ({len(xhr_log)}):")
        for m, u, s in xhr_log[:60]:
            log.info(f"  {m} {s} {u}")

        browser.close()
    except Exception as exc:
        log.error(f"Collin: fatal error: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


def peek_bexar_reference(pw):
    """Read-only platform-shape comparison. Collin's new home is the same
    tylertech.cloud domain family as Bexar's — this is NOT building/probing
    Bexar's scraper (another agent owns that), just confirming whether the
    two counties' portals present the same UI, which affects whether Collin's
    scraper design can/should mirror it."""
    log.info("=" * 70)
    log.info("PART 3 — Bexar tylertech.cloud reference peek (comparison only)")
    log.info("=" * 70)
    browser = pw.chromium.launch(headless=True)
    try:
        page = browser.new_context().new_page()
        page.set_default_timeout(20000)
        resp = page.goto(BEXAR_REFERENCE, wait_until='networkidle', timeout=20000)
        page.wait_for_timeout(1500)
        log.info(f"bexar: status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        body = page.inner_text('body')[:1500]
        log.info(f"bexar: body snippet: {body!r}")
    except Exception as e:
        log.info(f"bexar: FAILED: {str(e)[:300]}")
    finally:
        browser.close()


def fingerprint_others(pw):
    log.info("=" * 70)
    log.info("PART 1 — fingerprint sweep of other TX counties (candidate Odyssey URLs)")
    log.info("=" * 70)
    for county, url in CANDIDATES.items():
        browser = None
        try:
            browser = pw.chromium.launch(headless=True,
                                          args=['--disable-blink-features=AutomationControlled'])
            ctx = browser.new_context(user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ))
            page = ctx.new_page()
            page.set_default_timeout(20000)
            log.info(f"--- {county} : {url}")
            resp = page.goto(url, wait_until='networkidle', timeout=20000)
            page.wait_for_timeout(1500)  # catch a late client-side redirect
            status = resp.status if resp else None
            final_url = page.url
            title = page.title()
            markers = check_captcha(page, county)
            body_snip = ''
            try:
                body_snip = page.inner_text('body')[:500]
            except Exception:
                pass
            is_publicaccess_shape = '/publicaccess' in final_url.lower()
            is_tylertech_cloud = 'tylertech.cloud' in final_url.lower()
            log.info(f"{county}: status={status} final_url={final_url!r} title={title!r} "
                     f"publicaccess_url_shape={is_publicaccess_shape} tylertech_cloud={is_tylertech_cloud} "
                     f"viewstate={markers.get('viewstate')}")
            log.info(f"{county}: body snippet: {body_snip!r}")
        except Exception as exc:
            log.info(f"{county}: FAILED to load: {str(exc)[:300]}")
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


def main():
    with sync_playwright() as pw:
        fingerprint_others(pw)
        peek_bexar_reference(pw)
        investigate_collin(pw)


if __name__ == '__main__':
    main()
