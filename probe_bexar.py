"""
Probe v8 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Ground truth from v1-v7 (confirmed):
  * Real submit control(s): input#btnSSSubmit[type=submit] — v1's raw DOM
    dump showed TWO such elements (invalid duplicate id): one near the top
    next to the reCAPTCHA checkbox (basic search), one at the bottom of
    "Case Search Criteria" (after filling Advanced options). `.first` always
    grabs the TOP one. UNTESTED whether that matters.
  * 'SMITH*' in #caseCriteria_SearchCriteria + Location=County Clerk (set via
    JS widget scripting) + wide date range gets PAST client validation and
    fires a REAL classic HTML form POST to
    /Portal/SmartSearch/SmartSearch/SmartSearch -> 302 -> GET back to
    /Portal/Home/Dashboard/29 — but that landing page is a genuinely blank,
    freshly-reset Smart Search form (bodyLen=262, no Advanced panel, no
    results, no grid, zero further network activity even after 22s of
    polling). This is NOT a render-timing issue — nothing ever arrives.
  * ePortal.js contains jQuery Form Plugin's ajaxSubmit pattern
    ("preventDefault(); $(this).ajaxSubmit(...)"), meaning a properly
    intercepted submit should stay on-page via AJAX, not cause a real
    navigating POST. Seeing a real navigating POST suggests that JS
    interception did NOT engage for our interaction — possibly because
    scripting the Kendo Location widget via raw JS (.value()+trigger
    ('change')) skips some companion state a real user selection would also
    set, causing the handler to no-op/fall through to native submission.

This probe removes ALL JS widget-scripting and does the ENTIRE interaction
via realistic UI actions only (real clicks, real typing with delay, keyboard
ArrowDown+Enter to select the Kendo Location suggestion) to rule out whether
synthetic scripting itself is what breaks the intended AJAX flow. Also tries
BOTH #btnSSSubmit buttons (top vs bottom) as a separate dimension. Stops at
first sign of real results OR a bot challenge (logged loudly, not solved).
"""
import json
import logging
import os
import re
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [BEXAR-PROBE] %(message)s')
log = logging.getLogger()

BASE = "https://portal-txbexar.tylertech.cloud/Portal"
HOST = "portal-txbexar.tylertech.cloud"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe_out')
os.makedirs(OUT_DIR, exist_ok=True)

TODAY = date(2026, 7, 28)
WINDOW_START = TODAY - timedelta(days=730)

_net_hits = []
_saved_bodies = 0


def _safe_name(url: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '_', url)[-100:]


def dump_network(page):
    def on_response(resp):
        global _saved_bodies
        try:
            url = resp.url
            if 'tylertech' not in url:
                return
            ct = resp.headers.get('content-type', '')
            status = resp.status
            lower = url.lower()
            is_asset = any(x in lower for x in ('/content/', '/scripts/', '/fonts/', '/theme/', '/bundles/'))
            _net_hits.append((resp.request.method, url, status, ct))
            if not is_asset:
                loc_hdr = ''
                if status in (301, 302, 303, 307, 308):
                    try:
                        loc_hdr = resp.headers.get('location', '')
                    except Exception:
                        pass
                log.info(f"  [non-asset] {resp.request.method} {status} [{ct}] {url}"
                          + (f"  -> Location: {loc_hdr}" if loc_hdr else ""))
                if _saved_bodies < 80:
                    try:
                        body = resp.text()
                        if body and len(body) < 2_000_000:
                            fname = os.path.join(OUT_DIR, f'net_{_saved_bodies:03d}_{_safe_name(url)}.txt')
                            with open(fname, 'w') as f:
                                f.write(body)
                            _saved_bodies += 1
                    except Exception:
                        pass
        except Exception:
            pass
    page.on('response', on_response)


def snapshot(page, tag: str):
    try:
        page.screenshot(path=os.path.join(OUT_DIR, f'{tag}.png'), full_page=True)
    except Exception as e:
        log.warning(f"[{tag}] screenshot failed: {e}")
    try:
        html = page.content()
        with open(os.path.join(OUT_DIR, f'{tag}.html'), 'w') as f:
            f.write(html)
        log.info(f"[{tag}] saved ({len(html)} bytes) url={page.url}")
    except Exception as e:
        log.warning(f"[{tag}] html save failed: {e}")


CAPTCHA_PATTERNS = re.compile(
    r"verify you are human|complete the captcha|human verification required|"
    r"unusual traffic from your (network|computer)|"
    r"access to this page has been denied|are you a robot\?|"
    r"checking your browser before accessing",
    re.I)


def check_for_bot_challenge(page, label: str) -> bool:
    try:
        title = page.title()
        body = page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
        hit = CAPTCHA_PATTERNS.search(title) or CAPTCHA_PATTERNS.search(body)
        if hit:
            log.error(f"*** [{label}] POSSIBLE BOT-CHECK/CAPTCHA CHALLENGE DETECTED *** "
                      f"title={title!r} matched={hit.group(0)!r}")
            snapshot(page, f'BOT_CHALLENGE_{label}')
            return True
    except Exception as e:
        log.warning(f"[{label}] check_for_bot_challenge error: {e}")
    return False


def wait_settle(page, label, timeout_ms=22000):
    deadline = time.monotonic() + timeout_ms / 1000
    last_state = None
    while time.monotonic() < deadline:
        try:
            state = page.evaluate("""() => {
                if (!document.body) return {notReady: true};
                let gridRows = -1;
                try {
                    if (typeof jQuery !== 'undefined') {
                        const g = jQuery('[data-role="grid"]').data('kendoGrid');
                        if (g) gridRows = g.dataSource.data().length;
                    }
                } catch (e) {}
                const body = (document.body.innerText || '');
                const noRes = /no results|no records|0 results|did not match|no matches|no cases found/i.test(body);
                const realTableRows = Array.from(document.querySelectorAll('table')).map(
                    t => t.querySelectorAll('tr').length).filter(n => n > 1);
                return {gridRows, noRes, realTableRows, bodyLen: body.length};
            }""")
        except Exception as e:
            state = {'evalError': str(e)}
        last_state = state
        if state.get('notReady') or state.get('evalError'):
            page.wait_for_timeout(500)
            continue
        if state['gridRows'] > 0 or any(n > 5 for n in state['realTableRows']):
            log.info(f"[{label}] wait_settle -> has_rows {state}")
            return 'has_rows', state
        if state['noRes']:
            log.info(f"[{label}] wait_settle -> no_results_message {state}")
            return 'no_results_message', state
        page.wait_for_timeout(700)
    log.info(f"[{label}] wait_settle -> timeout {last_state}")
    return 'timeout', last_state


def dump_results(page, tag):
    body = page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
    lines = [l for l in body.split('\n') if l.strip()]
    log.info(f"=== [{tag}] BODY TEXT ({len(lines)} lines) ===")
    for ln in lines[:400]:
        log.info(f"  | {ln}")
    snapshot(page, tag)
    grid_info = page.evaluate("""() => {
        if (typeof jQuery === 'undefined') return {error: 'no jQuery'};
        const grids = [];
        jQuery('[data-role="grid"]').each(function() {
            const g = jQuery(this).data('kendoGrid');
            if (!g) return;
            let rows = [];
            try { rows = g.dataSource.data().map(it => it.toJSON ? it.toJSON() : it); } catch (e) {}
            grids.push({id: this.id, total: g.dataSource.total(), rows: rows.slice(0, 30)});
        });
        return {count: grids.length, grids};
    }""")
    log.info(f"[{tag}] KENDO GRIDS: {json.dumps(grid_info, default=str)[:8000]}")
    tables = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('table')).map(t => ({
            id: t.id, headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
            rowCount: t.querySelectorAll('tr').length,
            sample: Array.from(t.querySelectorAll('tr')).slice(1,20).map(
                tr => Array.from(tr.querySelectorAll('td')).map(td => (td.textContent||'').trim())
            ),
        }));
    }""")
    for i, t in enumerate(tables):
        if t['rowCount'] > 1:
            log.info(f"[{tag}] TABLE {i}: id={t['id']!r} headers={t['headers']} rowCount={t['rowCount']}")
            for r in t['sample']:
                log.info(f"  ROW: {r}")


def human_like_attempt(page, submit_which: str):
    """submit_which: 'first' or 'last' — which #btnSSSubmit to click.
    Does the whole flow via realistic UI actions (no JS widget scripting)."""
    log.info(f"=== Human-like attempt, submit_which={submit_which!r} ===")
    page.goto(f"{BASE}/Home/Dashboard/29", wait_until='networkidle')
    page.wait_for_timeout(1000)
    page.locator('a:has-text("Advanced")').first.click()
    page.wait_for_timeout(800)

    # Real typing + keyboard select for the Location Kendo combo box.
    loc_input = page.locator('input[name="caseCriteria.CourtLocation_input"]').first
    loc_input.click()
    loc_input.fill('')
    loc_input.type('County Clerk', delay=70)
    page.wait_for_timeout(1000)
    page.keyboard.press('ArrowDown')
    page.wait_for_timeout(300)
    page.keyboard.press('Enter')
    page.wait_for_timeout(800)
    loc_value = page.evaluate('document.getElementById("caseCriteria_CourtLocation").value')
    log.info(f"Location input value after real typing+Enter: {loc_value!r}")

    # v8b: v8 hung here (20s timeout, no further log line) with no
    # diagnosis of why -- log visibility/count for both date fields BEFORE
    # attempting a normal fill, then fall back to force=True (bypassing the
    # actionability check) if a normal fill can't proceed. This mirrors the
    # exact class of bug hit on Ellis's DocTypesList and Travis's date-range
    # control this session: a native input hidden behind a widget overlay.
    start_loc = page.locator('input[name*="FileDateStart" i]').first
    end_loc = page.locator('input[name*="FileDateEnd" i]').first
    log.info(f"FileDateStart: count={page.locator('input[name*=\"FileDateStart\" i]').count()} "
             f"visible={start_loc.is_visible() if start_loc.count() else 'N/A'}")
    log.info(f"FileDateEnd: count={page.locator('input[name*=\"FileDateEnd\" i]').count()} "
             f"visible={end_loc.is_visible() if end_loc.count() else 'N/A'}")
    try:
        start_loc.fill(WINDOW_START.strftime('%m/%d/%Y'), timeout=5000)
    except Exception as e:
        log.warning(f"FileDateStart normal fill failed ({str(e)[:150]}) -- retrying with force=True")
        start_loc.fill(WINDOW_START.strftime('%m/%d/%Y'), force=True, timeout=5000)
    page.keyboard.press('Escape')
    try:
        end_loc.fill(TODAY.strftime('%m/%d/%Y'), timeout=5000)
    except Exception as e:
        log.warning(f"FileDateEnd normal fill failed ({str(e)[:150]}) -- retrying with force=True")
        end_loc.fill(TODAY.strftime('%m/%d/%Y'), force=True, timeout=5000)
    page.keyboard.press('Escape')

    main_box = page.locator('#caseCriteria_SearchCriteria').first
    main_box.click()
    main_box.fill('')
    main_box.type('SMITH*', delay=60)
    page.wait_for_timeout(400)
    snapshot(page, f'v8_{submit_which}_before_submit')

    if check_for_bot_challenge(page, f'{submit_which}_before_submit'):
        return 'bot_challenge', None

    submit_btn = page.locator('#btnSSSubmit')
    count = submit_btn.count()
    log.info(f"#btnSSSubmit matched elements: {count}")
    target = submit_btn.first if submit_which == 'first' else submit_btn.last
    pre = len(_net_hits)
    target.click(timeout=10000)
    try:
        page.wait_for_load_state('domcontentloaded', timeout=10000)
    except Exception as e:
        log.info(f"post-click wait_for_load_state: {e}")
    post = len(_net_hits)
    log.info(f"[{submit_which}] new network requests immediately after click: {post - pre}")

    status, state = wait_settle(page, submit_which, timeout_ms=18000)

    if check_for_bot_challenge(page, f'{submit_which}_after_submit'):
        return 'bot_challenge', None

    body = page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
    err = 'incorrectly' in body.lower() or 'please enter' in body.lower()
    log.info(f"[{submit_which}] final status={status} err_validation={err} url={page.url} bodyLen={len(body)}")
    dump_results(page, f'v8_{submit_which}_result')
    if status == 'has_rows':
        return 'success', state
    return status, state


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ), viewport={'width': 1500, 'height': 1600})
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(20000)
        dump_network(page)

        result = None
        for which in ['last', 'first']:
            try:
                result, state = human_like_attempt(page, which)
                if result == 'success':
                    log.info(f"*** SUCCESS with submit_which={which!r} ***")
                    break
                if result == 'bot_challenge':
                    log.error("Stopping — bot challenge encountered, not attempting to solve/bypass.")
                    break
            except Exception as ex:
                log.error(f"human_like_attempt({which}) error: {ex}", exc_info=True)

        log.info(f"=== NETWORK: {len(_net_hits)} total responses touching tylertech ===")
        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _net_hits], f, indent=2)

        browser.close()
        log.info("Probe v8 complete.")


if __name__ == '__main__':
    main()
