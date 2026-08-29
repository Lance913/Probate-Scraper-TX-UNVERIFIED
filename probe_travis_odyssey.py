"""
Probe -- Travis County's own modern "Odyssey Portal" (a different, newer
Tyler product from the classic Tyler Odyssey Public Access used by
Tarrant/Denton -- URL shape ".../Portal/Home/Dashboard/..." like Bexar,
not ".../PublicAccess/default.aspx"). Covers County Clerk + District Clerk
+ Probate Court in one portal via "Smart Search". Per public search results,
registration is NOT required to search here (unlike Bexar/Dallas).

Local sandbox connectivity to odysseyweb.traviscountytx.gov timed out/was
refused -- testing from a GitHub Actions (US) IP instead before concluding
anything, per SYSTEM_GUIDE.md S6 (don't guess reachability, verify).
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
log = logging.getLogger('PROBE')

ART_DIR = 'probe_artifacts'
BASE_URL = 'https://odysseyweb.traviscountytx.gov/Portal/'


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


def dump_body_text(page, label, n=80):
    txt = page.evaluate("() => document.body.innerText || ''")
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    log.info(f"=== {label}: body text ({len(lines)} lines, showing first {n}) ===")
    for ln in lines[:n]:
        log.info(f"  | {ln}")
    return lines


def recaptcha_visible(page):
    return page.evaluate(
        "() => { const el = document.querySelector('.g-recaptcha, iframe[src*=\"recaptcha\"]'); "
        "return !!el && el.offsetWidth > 0 && el.offsetHeight > 0; }")


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from scrapers.base import launch_chromium

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        context = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ))
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30_000)

        log.info(f"Loading {BASE_URL} ...")
        try:
            resp = page.goto(BASE_URL, wait_until='domcontentloaded', timeout=30_000)
            log.info(f"status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        except Exception as e:
            log.error(f"goto failed: {str(e)[:300]}")
            browser.close()
            return
        page.wait_for_timeout(2000)
        shot(page, '01_landing')
        dump_body_text(page, 'landing')
        log.info(f"reCAPTCHA visible: {recaptcha_visible(page)}")

        # Try to reach Smart Search directly.
        try:
            loc = page.get_by_text('Smart Search', exact=False)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=8000)
                page.wait_for_timeout(2000)
                log.info(f"After Smart Search click: url={page.url!r} title={page.title()!r}")
                shot(page, '02_smart_search')
                dump_body_text(page, 'smart_search')
                log.info(f"reCAPTCHA visible on Smart Search: {recaptcha_visible(page)}")
        except Exception as e:
            log.warning(f"Smart Search click failed: {str(e)[:200]}")

        browser.close()

    log.info("Probe complete.")


if __name__ == '__main__':
    main()
