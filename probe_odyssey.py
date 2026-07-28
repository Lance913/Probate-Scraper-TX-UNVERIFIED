"""
Probe v3 — isolate the AWS WAF "Human Verification" wall found in v2 when
clicking into Collin's Search.aspx. v2 confirmed:
  - https://portal-txcollin.tylertech.cloud/PublicAccess/default.aspx loads
    clean (no challenge), and its "Probate Case Records" nav link is
    javascript:LaunchSearch('Search.aspx?ID=200', ...) — confirms ID=200
    survived the migration and still means Probate.
  - Clicking "Criminal Case Records" (ID=100, same LaunchSearch mechanism)
    landed on a "Human Verification" page: button id=amzn-captcha-verify-
    button, XHR to *.token.awswaf.com — AWS WAF Bot Control.

This run answers, cleanly (Collin-only, minimal other traffic to avoid
confounding IP/session reputation):
  1. Does the Probate node (ID=200) specifically also hit this wall (not just
     Criminal)?
  2. Is it a one-time per-session challenge (passes silently / after clicking
     Begin) or a hard interactive puzzle with no automation path?
  3. Does going straight to Search.aspx?ID=200 with NO prior landing-page
     visit behave differently than clicking through from the landing page?
  4. Does a second navigation in the SAME context (same cookies) still get
     challenged, once one challenge has already been through?
  5. Quick check of the /SecurePA path (an alternate entry point another
     researcher found) for comparison.
  6. Quick comparison: does Tarrant's tylertech.cloud instance ALSO wall its
     Search.aspx the same way, or is this Collin-specific? (cheap, same
     mechanism, helps scope whether this is a per-county WAF config choice.)
"""
import logging

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def new_page(pw):
    browser = pw.chromium.launch(headless=True,
                                  args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=UA, locale='en-US')
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30000)
    xhr_log = []
    def on_response(resp):
        try:
            ct = resp.headers.get('content-type', '')
            if 'json' in ct or 'awswaf' in resp.url.lower() or '/api/' in resp.url.lower():
                xhr_log.append((resp.request.method, resp.url, resp.status))
        except Exception:
            pass
    page.on('response', on_response)
    return browser, page, xhr_log


def dump_state(page, label, xhr_log=None):
    log.info(f"[{label}] title={page.title()!r} url={page.url!r}")
    try:
        body = page.inner_text('body')
        log.info(f"[{label}] body ({len(body)} chars): {body[:1500]!r}")
    except Exception as e:
        log.info(f"[{label}] could not read body: {e}")
    try:
        markers = page.evaluate("""() => ({
            captchaWord: /captcha/i.test(document.documentElement.outerHTML),
            awswaf: /awswaf/i.test(document.documentElement.outerHTML),
            beginBtn: !!document.querySelector('#amzn-captcha-verify-button'),
            viewstate: !!document.querySelector('input[name="__VIEWSTATE"]'),
            formCount: document.querySelectorAll('form').length,
            inputCount: document.querySelectorAll('input,select,textarea').length,
        })""")
        log.info(f"[{label}] markers: {markers}")
    except Exception as e:
        log.info(f"[{label}] could not eval markers: {e}")
    if xhr_log:
        log.info(f"[{label}] xhr/waf calls so far: {len(xhr_log)}")
        for m, u, s in xhr_log[-10:]:
            log.info(f"  {m} {s} {u}")


def test_probate_via_click(pw):
    log.info("=" * 70)
    log.info("TEST 1 — land on default.aspx, click 'Probate Case Records' (ID=200)")
    log.info("=" * 70)
    browser, page, xhr = new_page(pw)
    try:
        page.goto("https://portal-txcollin.tylertech.cloud/Publicaccess", wait_until='networkidle')
        page.wait_for_timeout(1000)
        dump_state(page, 'T1 landing', xhr)

        link = page.locator('a:has-text("Probate Case Records")').first
        if link.count() == 0:
            log.error("T1: no 'Probate Case Records' link found on landing page!")
            browser.close()
            return
        log.info("T1: clicking 'Probate Case Records'...")
        link.click()
        page.wait_for_timeout(3000)
        try:
            page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        dump_state(page, 'T1 after-click', xhr)

        # If a "Begin" verification button showed up, click it and see what happens.
        begin = page.locator('#amzn-captcha-verify-button')
        if begin.count() > 0:
            log.info("T1: 'Begin' verification button present — clicking it.")
            begin.click()
            page.wait_for_timeout(4000)
            dump_state(page, 'T1 after-Begin-click', xhr)
            # Wait a bit more in case it's a delayed automatic pass-through.
            page.wait_for_timeout(5000)
            dump_state(page, 'T1 after-Begin-click+5s', xhr)

        # Try navigating to the SAME URL again in this same context/session —
        # does the challenge persist or was a pass-cookie set?
        log.info("T1: re-navigating to Search.aspx?ID=200 in the SAME session...")
        page.goto("https://portal-txcollin.tylertech.cloud/PublicAccess/Search.aspx?ID=200",
                   wait_until='networkidle')
        page.wait_for_timeout(2000)
        dump_state(page, 'T1 second-visit-same-session', xhr)

        browser.close()
    except Exception as exc:
        log.error(f"T1: fatal error: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


def test_direct_goto(pw):
    log.info("=" * 70)
    log.info("TEST 2 — fresh session, goto Search.aspx?ID=200 DIRECTLY (no landing visit)")
    log.info("=" * 70)
    browser, page, xhr = new_page(pw)
    try:
        page.goto("https://portal-txcollin.tylertech.cloud/PublicAccess/Search.aspx?ID=200",
                   wait_until='networkidle')
        page.wait_for_timeout(2000)
        dump_state(page, 'T2 direct-goto', xhr)
        browser.close()
    except Exception as exc:
        log.error(f"T2: fatal error: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


def test_securepa(pw):
    log.info("=" * 70)
    log.info("TEST 3 — /SecurePA alternate entry point")
    log.info("=" * 70)
    browser, page, xhr = new_page(pw)
    try:
        page.goto("https://portal-txcollin.tylertech.cloud/SecurePA", wait_until='networkidle')
        page.wait_for_timeout(1500)
        dump_state(page, 'T3 securepa', xhr)
        browser.close()
    except Exception as exc:
        log.error(f"T3: error: {str(exc)[:300]}")
        try:
            browser.close()
        except Exception:
            pass


def test_tarrant_comparison(pw):
    log.info("=" * 70)
    log.info("TEST 4 — comparison: does Tarrant's tylertech.cloud Search.aspx also wall?")
    log.info("=" * 70)
    browser, page, xhr = new_page(pw)
    try:
        page.goto("https://odyssey.tarrantcounty.com/PublicAccess/default.aspx", wait_until='networkidle')
        page.wait_for_timeout(1000)
        dump_state(page, 'T4 tarrant landing', xhr)
        # Tarrant's landing page structure differs (location dropdown, not
        # LaunchSearch links) per v1/v2 — try the most generic path: find any
        # link/button that leads into an actual case search.
        clicked = False
        for sel in ['a:has-text("Case Records Search")', 'a:has-text("Probate")',
                    'button:has-text("Search")', 'a:has-text("Search")']:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                log.info(f"T4: clicking {sel!r} ({el.inner_text()[:60]!r})")
                el.click()
                page.wait_for_timeout(3000)
                try:
                    page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    pass
                clicked = True
                break
        if clicked:
            dump_state(page, 'T4 tarrant after-click', xhr)
        else:
            log.info("T4: no obvious search-entry control found on Tarrant landing page.")
        browser.close()
    except Exception as exc:
        log.error(f"T4: error: {str(exc)[:300]}")
        try:
            browser.close()
        except Exception:
            pass


def main():
    with sync_playwright() as pw:
        test_probate_via_click(pw)
        test_direct_goto(pw)
        test_securepa(pw)
        test_tarrant_comparison(pw)


if __name__ == '__main__':
    main()
