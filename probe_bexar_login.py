"""
Probe -- log into Bexar's Tyler Odyssey Portal with the now-real account and
retry the full human-like search flow. NOTE: Bexar's own form settings show
Settings.CaptchaDisabledForAuthenticated = 'False' (unlike Dallas, which is
'True') -- so login may NOT bypass captcha here. Testing anyway since the
earlier "blank reset form" bug never actually showed a captcha challenge in
the first place, so it may be a different, unrelated AJAX issue that login
could still resolve.
"""
import logging
import os
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s [BEXAR-LOGIN] %(message)s')
log = logging.getLogger()

ART_DIR = 'probe_artifacts'
BASE = "https://portal-txbexar.tylertech.cloud/Portal"

EMAIL = os.environ['ACCOUNT_EMAIL']
PASSWORD = os.environ['ACCOUNT_PASSWORD']


def shot(page, name):
    try:
        page.screenshot(path=f'{ART_DIR}/{name}.png', full_page=True)
    except Exception as e:
        log.warning(f"screenshot {name} failed: {e}")


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

        page.goto(f"{BASE}/Home/Dashboard/29", wait_until='networkidle', timeout=30_000)
        page.wait_for_timeout(1000)

        # Open the Register/Sign In dropdown, find the login form fields.
        loc = page.get_by_text('Register / Sign In', exact=False)
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000)
                break
        page.wait_for_timeout(1000)
        shot(page, '01_signin_dropdown')

        fields = page.evaluate("""
            () => Array.from(document.querySelectorAll('input')).map(el => ({
                type: el.type||'', id: el.id||'', name: el.name||'',
                visible: !!(el.offsetWidth || el.offsetHeight)
            }))
        """)
        log.info(f"Visible input fields in sign-in state: {[f for f in fields if f['visible']]}")

        # Try common Tyler login field id/name patterns.
        email_filled = False
        for sel in ['#UserName', '#Username', '#Email', 'input[name="Email"]', 'input[name="UserName"]']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.fill(EMAIL, timeout=3000)
                    log.info(f"Filled email via {sel!r}")
                    email_filled = True
                    break
            except Exception:
                continue
        if not email_filled:
            log.error("Could not find a visible email/username field -- check field dump above.")
            browser.close()
            return

        for sel in ['#Password', 'input[name="Password"]']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.fill(PASSWORD, timeout=3000)
                    log.info(f"Filled password via {sel!r}")
                    break
            except Exception:
                continue

        shot(page, '02_login_filled')
        log.info(f"reCAPTCHA visible before login submit: {recaptcha_visible(page)}")

        for sel in ['button:has-text("Sign In")', 'button:has-text("Log In")',
                    'input[type="submit"][value*="Sign In" i]', 'input[type="submit"][value*="Log In" i]',
                    'button[type="submit"]']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.click(timeout=8000)
                    log.info(f"Clicked submit via {sel!r}")
                    break
            except Exception:
                continue

        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        shot(page, '03_after_login')
        body = page.evaluate("() => document.body.innerText || ''")
        log.info(f"Post-login body ({len(body)} chars) first 1500:\n{body[:1500]}")
        logged_in = 'Sign Out' in body or 'Log Out' in body or 'Logout' in body
        log.info(f"Login appears successful: {logged_in}")

        if not logged_in:
            browser.close()
            return

        # Now retry the real search: Location=County Clerk, wide date range, SMITH*.
        page.locator('a:has-text("Advanced")').first.click(timeout=8000)
        page.wait_for_timeout(800)
        loc_input = page.locator('input[name="caseCriteria.CourtLocation_input"]').first
        loc_input.click()
        loc_input.fill('')
        loc_input.type('County Clerk', delay=70)
        page.wait_for_timeout(1000)
        page.keyboard.press('ArrowDown')
        page.wait_for_timeout(300)
        page.keyboard.press('Enter')
        page.wait_for_timeout(800)

        from datetime import date, timedelta
        today = date.today()
        start = today - timedelta(days=730)
        page.locator('input[name*="FileDateStart" i]').first.fill(start.strftime('%m/%d/%Y'))
        page.keyboard.press('Escape')
        page.locator('input[name*="FileDateEnd" i]').first.fill(today.strftime('%m/%d/%Y'))
        page.keyboard.press('Escape')

        main_box = page.locator('#caseCriteria_SearchCriteria').first
        try:
            main_box.click(timeout=5000)
        except Exception:
            main_box.click(force=True, timeout=5000)
        main_box.fill('')
        main_box.type('SMITH*', delay=60)
        page.wait_for_timeout(400)
        shot(page, '04_before_submit_loggedin')
        log.info(f"reCAPTCHA visible before search submit (logged in): {recaptcha_visible(page)}")

        submit_btn = page.locator('#btnSSSubmit').last
        try:
            submit_btn.click(timeout=10000)
        except Exception:
            submit_btn.click(force=True, timeout=10000)
        page.wait_for_timeout(3000)
        try:
            page.wait_for_load_state('networkidle', timeout=18000)
        except Exception:
            pass
        log.info(f"Post-submit (logged in): url={page.url!r} title={page.title()!r}")
        shot(page, '05_results_loggedin')
        result_body = page.evaluate("() => document.body.innerText || ''")
        log.info(f"Results body ({len(result_body)} chars) first 2000:\n{result_body[:2000]}")

        grids = page.evaluate("""
            () => [...document.querySelectorAll('table')].map(t => ({
                id: t.id, headers: [...t.querySelectorAll('th')].map(h => (h.textContent||'').trim()),
                rowCount: t.querySelectorAll('tr').length
            }))
        """)
        log.info(f"Tables on results page (logged in): {grids}")

        browser.close()

    log.info("Bexar login probe complete.")


if __name__ == '__main__':
    main()
