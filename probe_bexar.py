"""
Probe v5 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Ground truth from v1-v4 (confirmed):
  * Kendo UI widgets, submit = input#btnSSSubmit[type=submit].
  * caseCriteria_CourtLocation cascades to caseCriteria_CaseType ENTIRELY
    client-side (0 network requests on change) — the "cascade" only ever
    yields a single generic placeholder item per location though (e.g.
    'County Clerk Case Search'). Typing into the CaseType box ALSO fired
    zero network requests across 18 attempts (9 terms x 2 locations) — so
    it is not a server-filtered autocomplete either. This dropdown's real
    per-type taxonomy (if reachable here at all) needs a different approach
    than anything tried so far — DEPRIORITIZED for this probe.
  * The SEPARATE caseCriteria_NameLast field (and First/Middle) is NOT what
    client validation checks — filling it with 'A*', 'AB*', '%', 'A%' (v4)
    ALL produced the exact same "Please enter a value for search criteria"
    error, proving that field is inert/hidden in the current UI mode.
  * The REAL required field is the single combined text input
    #caseCriteria_SearchCriteria (plain <input type=text>, no Kendo widget
    attached). v3 only ever tried it with a bare '*' (rejected). NEVER
    tried with a realistic value like 'A*' or 'SMITH*' — that is the
    priority gap this probe closes.

This probe, in order, stopping at first success:
  1. Fresh page, expand Advanced, set Location=County Clerk (client-side
     cascade, confirmed harmless), fill File Date Start/End wide.
  2. Try #caseCriteria_SearchCriteria = 'A*', then 'SMITH*', then 'SMITH, *',
     then 'A*, *' — one per fresh page load — clicking the REAL submit
     button each time and checking for (a) a real network POST/GET beyond
     the static page load, (b) absence of the "please enter/incorrectly"
     client validation text, (c) — per explicit instruction from the
     coordinator — an active CAPTCHA/human-verification CHALLENGE page
     (not just the passive checkbox we already know exists). If seen: log
     it loudly and stop trying that path; do NOT attempt to solve/bypass
     it, do NOT theorize browser-fingerprinting causes.
  3. On first real success: dump EVERYTHING about the results — Kendo Grid
     dataSource (structured JSON), any <table>, real case-number links, and
     the actual 'Case Type' values seen in real rows (the most reliable way
     left to learn the true estate-case-type taxonomy, since the dropdown
     approach has failed twice). Opens the first case detail link and dumps
     its Party Information section verbatim (decedent/executor structured
     data vs OCR-needed — the other big open question).
"""
import json
import logging
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [BEXAR-PROBE] %(message)s')
log = logging.getLogger()

BASE = "https://portal-txbexar.tylertech.cloud/Portal"
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
                log.info(f"  [non-asset] {resp.request.method} {status} [{ct}] {url}")
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
    except Exception as e:
        log.warning(f"[{tag}] html save failed: {e}")


def fresh_smart_search(page):
    page.goto(f"{BASE}/Home/Dashboard/29", wait_until='networkidle')
    page.wait_for_timeout(1000)
    try:
        page.locator('a:has-text("Advanced")').first.click()
        page.wait_for_timeout(700)
    except Exception:
        pass


def js_set_kendo_value(page, el_id: str, value: str, label: str) -> bool:
    ok = False
    try:
        ok = page.evaluate("""(args) => {
            const el = document.getElementById(args.elId);
            if (!el) return false;
            const $el = jQuery(el);
            const data = $el.data();
            for (const key of Object.keys(data)) {
                if (!key.startsWith('kendo')) continue;
                const widget = data[key];
                if (widget && widget.value) {
                    widget.value(args.value);
                    widget.trigger('change');
                    if (widget.close) widget.close();
                    return true;
                }
            }
            return false;
        }""", {'elId': el_id, 'value': value})
    except Exception as e:
        log.warning(f"[{label}] js_set_kendo_value error: {e}")
    try:
        page.keyboard.press('Escape')
    except Exception:
        pass
    log.info(f"[{label}] js_set_kendo_value({el_id!r}, {value!r}) -> {ok}")
    return ok


CAPTCHA_PATTERNS = re.compile(
    r"verify you are human|complete the captcha|human verification|"
    r"unusual traffic|access denied|are you a robot|challenge",
    re.I)


def check_for_bot_challenge(page, label: str) -> bool:
    """Explicit, isolated check for an ACTIVE bot-check/CAPTCHA challenge
    page (distinct from the passive reCAPTCHA checkbox we already know is
    embedded in the form). Per explicit guidance: if this ever fires, log
    loudly, do not attempt to solve/bypass, do not theorize fingerprinting."""
    try:
        title = page.title()
        body = page.evaluate("() => document.body.innerText || ''")
        hit = CAPTCHA_PATTERNS.search(title) or CAPTCHA_PATTERNS.search(body)
        if hit:
            log.error(f"*** [{label}] POSSIBLE BOT-CHECK/CAPTCHA CHALLENGE DETECTED *** "
                      f"title={title!r} matched={hit.group(0)!r}")
            snapshot(page, f'BOT_CHALLENGE_{label}')
            return True
    except Exception as e:
        log.warning(f"[{label}] check_for_bot_challenge error: {e}")
    return False


def try_main_box(page, value: str):
    fresh_smart_search(page)
    js_set_kendo_value(page, 'caseCriteria_CourtLocation', 'County Clerk', 'loc')
    page.wait_for_timeout(800)
    page.locator('input[name*="FileDateStart" i]').first.fill(WINDOW_START.strftime('%m/%d/%Y'))
    page.keyboard.press('Escape')
    page.locator('input[name*="FileDateEnd" i]').first.fill(TODAY.strftime('%m/%d/%Y'))
    page.keyboard.press('Escape')
    page.locator('#caseCriteria_SearchCriteria').fill(value)
    page.wait_for_timeout(300)
    snapshot(page, f'v5_before_{_safe_name(value)}')

    if check_for_bot_challenge(page, f'before_submit_{value}'):
        return False, ['BOT_CHALLENGE'], ''

    pre = len(_net_hits)
    page.locator('#btnSSSubmit').first.click(timeout=10000)
    page.wait_for_timeout(5000)
    post = len(_net_hits)

    if check_for_bot_challenge(page, f'after_submit_{value}'):
        return False, ['BOT_CHALLENGE'], ''

    body = page.evaluate("() => document.body.innerText || ''")
    err_lines = [l for l in body.split('\n') if
                 'incorrectly' in l.lower() or 'please enter' in l.lower()
                 or 'must be' in l.lower() or 'at least' in l.lower()]
    new_reqs = post - pre
    log.info(f"[try_main_box value={value!r}] new_requests={new_reqs} err_lines={err_lines}")
    snapshot(page, f'v5_after_{_safe_name(value)}')
    success = new_reqs > 0 and not err_lines
    return success, err_lines, body


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

        candidates = ['A*', 'SMITH*', 'SMITH, *', 'A*, *']
        success = False
        success_body = ''
        winning_value = None
        for val in candidates:
            try:
                log.info(f"=== Trying main SearchCriteria = {val!r} ===")
                ok, errs, body = try_main_box(page, val)
                if ok:
                    log.info(f"*** SUCCESS with {val!r} ***")
                    success = True
                    success_body = body
                    winning_value = val
                    break
                elif errs == ['BOT_CHALLENGE']:
                    log.error("Stopping wildcard sweep — bot challenge encountered, not a validation issue.")
                    break
                else:
                    log.info(f"{val!r} rejected: {errs}")
            except Exception as ex:
                log.error(f"try_main_box({val!r}) error: {ex}", exc_info=True)

        if success:
            log.info(f"=== RESULTS after winning query (SearchCriteria={winning_value!r}) ===")
            log.info(f"Post-submit URL: {page.url}")
            log.info("=== BODY TEXT (first 300 lines) ===")
            for ln in [l for l in success_body.split('\n') if l.strip()][:300]:
                log.info(f"  | {ln}")

            try:
                grid_info = page.evaluate("""() => {
                    if (typeof jQuery === 'undefined') return {error: 'no jQuery'};
                    const g = jQuery('[data-role="grid"]').data('kendoGrid');
                    if (!g) return {error: 'no kendoGrid'};
                    let rows = [];
                    try { rows = g.dataSource.data().map(it => it.toJSON ? it.toJSON() : it); } catch (e) {}
                    return {total: g.dataSource.total(), rowCount: rows.length, rows: rows.slice(0, 30)};
                }""")
                log.info(f"KENDO GRID: {json.dumps(grid_info, default=str)[:10000]}")
            except Exception as e:
                log.warning(f"grid dump error: {e}")

            try:
                tables = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('table')).map(t => ({
                        headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
                        rowCount: t.querySelectorAll('tr').length,
                        sample: Array.from(t.querySelectorAll('tr')).slice(1,20).map(
                            tr => Array.from(tr.querySelectorAll('td')).map(td => (td.textContent||'').trim())
                        ),
                    }));
                }""")
                for i, t in enumerate(tables):
                    if t['rowCount'] <= 1:
                        continue
                    log.info(f"TABLE {i}: headers={t['headers']} rowCount={t['rowCount']}")
                    for r in t['sample']:
                        log.info(f"  ROW: {r}")
            except Exception as e:
                log.warning(f"table dump error: {e}")

            try:
                case_links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({href: a.getAttribute('href'), text:(a.textContent||'').trim()}))
                        .filter(a => a.href && a.href !== '#' && !a.href.startsWith('javascript'))
                        .slice(0, 30);
                }""")
                log.info(f"Real links on results page ({len(case_links)}): {case_links}")
                if case_links:
                    href = case_links[0]['href']
                    detail_url = href if href.startswith('http') else (
                        f"https://portal-txbexar.tylertech.cloud{href}" if href.startswith('/') else None)
                    if detail_url:
                        log.info(f"Opening case detail -> {detail_url}")
                        page.goto(detail_url, wait_until='networkidle')
                        page.wait_for_timeout(2500)
                        if not check_for_bot_challenge(page, 'case_detail'):
                            snapshot(page, '30_case_detail')
                            detail_body = page.evaluate("() => document.body.innerText || ''")
                            log.info("=== CASE DETAIL BODY TEXT (first 350 lines) ===")
                            for ln in [l for l in detail_body.split('\n') if l.strip()][:350]:
                                log.info(f"  | {ln}")
            except Exception as e:
                log.warning(f"case detail phase error: {e}")
        else:
            log.info("=== No candidate got past validation / a bot challenge was hit — see logs above ===")

        log.info(f"=== NETWORK: {len(_net_hits)} total responses touching tylertech ===")
        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _net_hits], f, indent=2)

        browser.close()
        log.info("Probe v5 complete.")


if __name__ == '__main__':
    main()
