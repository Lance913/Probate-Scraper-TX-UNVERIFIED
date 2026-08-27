"""
Probe -- log into Dallas's Tyler Odyssey Portal and retry the search flow.
Dallas's own form settings say Settings.CaptchaDisabledForAuthenticated =
'True' (Bexar's says 'False') -- so login may genuinely waive the captcha
here. Screenshot-heavy: the Bexar login probe wasted a round-trip guessing
selectors blindly instead of looking at what's actually on screen.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [DALLAS-LOGIN] %(message)s')
log = logging.getLogger()

ART_DIR = 'probe_artifacts'
BASE = "https://courtsportal.dallascounty.org/DALLASPROD"

EMAIL = os.environ['ACCOUNT_EMAIL']
PASSWORD = os.environ['ACCOUNT_PASSWORD']

_shot_ctr = 0


def shot(page, name):
    global _shot_ctr
    _shot_ctr += 1
    tag = f"{_shot_ctr:02d}_{name}"
    try:
        page.screenshot(path=f'{ART_DIR}/{tag}.png', full_page=True)
        log.info(f"[{tag}] screenshot saved, url={page.url}")
    except Exception as e:
        log.warning(f"screenshot {tag} failed: {e}")


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
        shot(page, 'landing')
        log.info(f"reCAPTCHA visible on landing search form: {recaptcha_visible(page)}")

        loc = page.get_by_text('Register / Sign In', exact=False)
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000)
                break
        page.wait_for_timeout(1000)
        shot(page, 'dropdown_open')

        loc2 = page.get_by_text('Sign In', exact=True)
        clicked = False
        for i in range(loc2.count()):
            el = loc2.nth(i)
            if el.is_visible():
                log.info(f"Clicking 'Sign In' match [{i}]")
                el.click(timeout=8000)
                clicked = True
                break
        if not clicked:
            log.error("No visible 'Sign In' text match found.")
            browser.close()
            return
        page.wait_for_timeout(1500)
        shot(page, 'after_signin_click')

        fields = page.evaluate("""
            () => Array.from(document.querySelectorAll('input')).map(el => ({
                type: el.type||'', id: el.id||'', name: el.name||'',
                visible: !!(el.offsetWidth || el.offsetHeight)
            })).filter(f => f.visible)
        """)
        log.info(f"Visible input fields: {fields}")

        email_filled = False
        for sel in ['#UserName', '#Username', '#Email', 'input[name="Email"]', 'input[name="UserName"]',
                    'input[type="email"]']:
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
            log.error("No visible email field found -- see screenshots for manual inspection.")
            browser.close()
            return

        for sel in ['#Password', 'input[name="Password"]', 'input[type="password"]']:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.fill(PASSWORD, timeout=3000)
                    log.info(f"Filled password via {sel!r}")
                    break
            except Exception:
                continue

        shot(page, 'login_filled')

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
        shot(page, 'after_login')
        body = page.evaluate("() => document.body.innerText || ''")
        logged_in = 'Sign Out' in body or 'Log Out' in body or 'Logout' in body
        log.info(f"Login appears successful: {logged_in}")
        log.info(f"reCAPTCHA visible after login: {recaptcha_visible(page)}")

        if not logged_in:
            log.error("Not logged in -- stopping (see screenshots).")
            browser.close()
            return

        # Real search: CaseType=Probate (confirmed real-value-resolving via
        # typing in the earlier probe), wide date range, submit.
        page.locator('a:has-text("Advanced")').first.click(timeout=8000)
        page.wait_for_timeout(800)

        ct_input = page.locator('input[name="caseCriteria.CaseType_input"]').first
        ct_input.click()
        ct_input.fill('')
        ct_input.type('Probate', delay=70)
        page.wait_for_timeout(1000)
        page.keyboard.press('ArrowDown')
        page.wait_for_timeout(300)
        page.keyboard.press('Enter')
        page.wait_for_timeout(800)
        ct_value = page.evaluate('document.getElementById("caseCriteria_CaseType")?.value')
        log.info(f"CaseType real value after typing+Enter: {ct_value!r}")

        from datetime import date, timedelta
        today = date.today()
        start = today - timedelta(days=365)
        page.locator('input[name*="FileDateStart" i]').first.fill(start.strftime('%m/%d/%Y'))
        page.keyboard.press('Escape')
        page.locator('input[name*="FileDateEnd" i]').first.fill(today.strftime('%m/%d/%Y'))
        page.keyboard.press('Escape')
        shot(page, 'before_submit')
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
        log.info(f"Post-submit: url={page.url!r} title={page.title()!r}")
        shot(page, 'results')

        result_body = page.evaluate("() => document.body.innerText || ''")
        log.info(f"Results body ({len(result_body)} chars) first 2000:\n{result_body[:2000]}")

        grids = page.evaluate("""
            () => [...document.querySelectorAll('table')].map(t => ({
                id: t.id, headers: [...t.querySelectorAll('th')].map(h => (h.textContent||'').trim()),
                rowCount: t.querySelectorAll('tr').length
            }))
        """)
        log.info(f"Tables on results page: {grids}")

        browser.close()

    log.info("Dallas login probe complete.")


if __name__ == '__main__':
    main()
