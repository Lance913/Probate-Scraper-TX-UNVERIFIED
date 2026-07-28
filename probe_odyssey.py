"""
Probe v4 — final check before concluding the AWS WAF wall is a hard blocker.

v3 conclusively showed (Collin AND Tarrant, both on tylertech.cloud):
  - default.aspx (landing) loads clean, no challenge.
  - ANY navigation to Search.aspx (even a fresh session, direct goto, no
    referrer) immediately serves a "Human Verification" page.
  - Clicking "Begin" reveals a genuine visual puzzle CAPTCHA ("Choose all the
    hats") — not a silent JS challenge that auto-resolves.
  - It re-challenges on a second navigation in the same session/cookies too.
  - /SecurePA is an unrelated attorney login page (User ID/Password), not a
    public path.

This is AWS WAF Bot Control's hardest tier (CAPTCHA action), which by design
has no silent pass-through for traffic it has already classified as bot-like.
Per instructions, we do NOT attempt to solve/bypass this (no CAPTCHA-solving
services, no purpose-built evasion). The ONE legitimate thing left to check:
does the classification itself depend on Playwright's headless-Chromium
automation fingerprint specifically? Testing a different real, unmodified
browser engine (Firefox / WebKit, both standard Playwright-supported browsers,
no spoofing beyond what's already used) is a fair, non-evasive test of
whether a normal browser session clears it organically — same spirit as the
`--disable-blink-features` / webdriver-property tweaks already standard in
this repo's base.py. If every engine hits the same wall, that confirms it's
IP/traffic-pattern based, not Chromium-specific, and the block is structural.
"""
import logging

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

TARGET = "https://portal-txcollin.tylertech.cloud/PublicAccess/Search.aspx?ID=200"


def dump_state(page, label):
    log.info(f"[{label}] title={page.title()!r} url={page.url!r}")
    try:
        body = page.inner_text('body')
        log.info(f"[{label}] body ({len(body)} chars): {body[:800]!r}")
    except Exception as e:
        log.info(f"[{label}] could not read body: {e}")
    try:
        markers = page.evaluate("""() => ({
            captchaWord: /captcha/i.test(document.documentElement.outerHTML),
            beginBtn: !!document.querySelector('#amzn-captcha-verify-button'),
            formCount: document.querySelectorAll('form').length,
        })""")
        log.info(f"[{label}] markers: {markers}")
    except Exception as e:
        log.info(f"[{label}] could not eval markers: {e}")


def test_engine(pw, engine_name):
    log.info("=" * 70)
    log.info(f"ENGINE TEST — {engine_name}")
    log.info("=" * 70)
    engine = getattr(pw, engine_name)
    try:
        browser = engine.launch(headless=True)
    except Exception as e:
        log.error(f"{engine_name}: launch failed (browser probably not installed): {str(e)[:300]}")
        return
    try:
        ctx = browser.new_context(locale='en-US')
        page = ctx.new_page()
        page.set_default_timeout(25000)
        log.info(f"{engine_name}: goto {TARGET}")
        page.goto(TARGET, wait_until='networkidle', timeout=25000)
        page.wait_for_timeout(2000)
        dump_state(page, engine_name)
        browser.close()
    except Exception as exc:
        log.error(f"{engine_name}: error: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


def main():
    with sync_playwright() as pw:
        test_engine(pw, 'chromium')  # baseline re-confirm
        test_engine(pw, 'firefox')
        test_engine(pw, 'webkit')


if __name__ == '__main__':
    main()
