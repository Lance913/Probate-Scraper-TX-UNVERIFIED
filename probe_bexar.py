"""
Probe v6 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Ground truth from v1-v5 (confirmed):
  * Real submit: input#btnSSSubmit[type=submit]. Required field: the plain
    #caseCriteria_SearchCriteria text input — needs a REALISTIC wildcard
    ('SMITH*' works; bare '*' and separate NameLast field do NOT — that
    field is inert in this UI mode).
  * 'SMITH*' + Location=County Clerk (client-side cascade) + wide File Date
    range got PAST client validation and fired a REAL network round trip:
        POST https://portal-txbexar.tylertech.cloud/Portal/SmartSearch/SmartSearch/SmartSearch
        -> 302 -> GET .../Portal/Home/Dashboard/29
    This is the real search endpoint (Post-Redirect-Get pattern) — a major
    unblock. NO captcha/bot-challenge was encountered on this real POST.
  * BUT the redirect landing page showed no visible results and no kendoGrid
    — body text looked like a freshly-reset search FORM shell (Advanced
    Filtering Options collapsed again), not a results view. Most likely
    explanation: results render via a follow-up AJAX call after the redirect
    lands, and v5 only waited 5s total before checking — not necessarily a
    real "0 results" answer. This probe waits much longer and polls.
  * v5 had two bugs fixed here: (1) the "case link" filter followed unrelated
    footer badge links (Chrome/Firefox/Safari download badges, generic
    Register/Sign-In), which is how it ended up on a Google marketing page;
    now restricted to hrefs actually on portal-txbexar.tylertech.cloud.
    (2) the bot-challenge regex included the bare word "challenge", which
    false-positived on that Google page's ad copy ("browser challenges") —
    tightened to specific human-verification phrases only.

This probe: re-runs the known-working 'SMITH*' + County Clerk query, but
(a) logs the 302's Location header explicitly, (b) waits/polls up to 20s for
a real results signal (kendoGrid with rows, a real <table>, or explicit
no-results text) instead of one fixed 5s wait, (c) saves the FULL raw HTML
of the landing page (not just innerText) so a results container can be
spotted even if still empty, (d) only follows same-host result links, and
(e) uses a tightened bot-challenge check.
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


# Tightened: specific human-verification phrasing only (v5's bare "challenge"
# false-positived on an unrelated Google Chrome marketing page).
CAPTCHA_PATTERNS = re.compile(
    r"verify you are human|complete the captcha|human verification required|"
    r"unusual traffic from your (network|computer)|"
    r"access to this page has been denied|are you a robot\?|"
    r"checking your browser before accessing",
    re.I)


def check_for_bot_challenge(page, label: str) -> bool:
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


def wait_for_results(page, timeout_ms=22000):
    """Poll for a real results signal after submit, per SYSTEM_GUIDE.md bug#1
    (a slow render must never be read as '0 results'). Defensive against the
    brief null-document window during a real full-page POST-redirect-GET
    navigation (v6 crashed on document.body being null mid-navigation)."""
    deadline = time.monotonic() + timeout_ms / 1000
    last_state = None
    while time.monotonic() < deadline:
        try:
            state = page.evaluate("""() => {
                if (!document.body) return {notReady: true};
                let gridRows = -1, gridTotal = -1;
                try {
                    if (typeof jQuery !== 'undefined') {
                        const g = jQuery('[data-role="grid"]').data('kendoGrid');
                        if (g) { gridRows = g.dataSource.data().length; gridTotal = g.dataSource.total(); }
                    }
                } catch (e) {}
                const body = (document.body.innerText || '');
                const noRes = /no results|no records|0 results|did not match|no matches|no cases found/i.test(body);
                const realTableRows = Array.from(document.querySelectorAll('table')).map(
                    t => t.querySelectorAll('tr').length).filter(n => n > 1);
                return {gridRows, gridTotal, noRes, realTableRows, bodyLen: body.length};
            }""")
        except Exception as e:
            state = {'evalError': str(e)}
        last_state = state
        if state.get('notReady') or state.get('evalError'):
            page.wait_for_timeout(500)
            continue
        if state['gridRows'] > 0 or any(n > 5 for n in state['realTableRows']):
            return 'has_rows', state
        if state['noRes']:
            return 'no_results_message', state
        page.wait_for_timeout(700)
    return 'timeout', last_state


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

        try:
            log.info("=== Submitting known-working query: SMITH* / County Clerk / wide date range ===")
            fresh_smart_search(page)
            js_set_kendo_value(page, 'caseCriteria_CourtLocation', 'County Clerk', 'loc')
            page.wait_for_timeout(800)
            page.locator('input[name*="FileDateStart" i]').first.fill(WINDOW_START.strftime('%m/%d/%Y'))
            page.keyboard.press('Escape')
            page.locator('input[name*="FileDateEnd" i]').first.fill(TODAY.strftime('%m/%d/%Y'))
            page.keyboard.press('Escape')
            page.locator('#caseCriteria_SearchCriteria').fill('SMITH*')
            page.wait_for_timeout(300)

            if check_for_bot_challenge(page, 'before_submit'):
                raise SystemExit("bot challenge before submit")

            page.locator('#btnSSSubmit').first.click(timeout=10000)
            try:
                page.wait_for_load_state('domcontentloaded', timeout=10000)
            except Exception as e:
                log.info(f"wait_for_load_state(domcontentloaded) after submit: {e}")
            log.info("Clicked submit — waiting/polling for real results (up to 22s)...")
            status, state = wait_for_results(page, timeout_ms=22000)
            log.info(f"wait_for_results -> status={status} state={state}")

            if check_for_bot_challenge(page, 'after_submit'):
                raise SystemExit("bot challenge after submit")

            log.info(f"Landing URL: {page.url}")
            snapshot(page, '01_after_submit_full')

            body = page.evaluate("() => document.body.innerText || ''")
            log.info(f"=== BODY TEXT ({len([l for l in body.split(chr(10)) if l.strip()])} lines) ===")
            for ln in [l for l in body.split('\n') if l.strip()][:400]:
                log.info(f"  | {ln}")

            # Grep the raw HTML for likely results-container ids/classes even
            # if currently empty, to guide the next probe if this one is dry.
            html = page.content()
            container_hits = re.findall(
                r'id="([^"]*(?:[Rr]esult|[Ss]earch|[Gg]rid)[^"]*)"', html)
            log.info(f"HTML ids containing Result/Search/Grid ({len(set(container_hits))} distinct): "
                      f"{sorted(set(container_hits))[:60]}")

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
            log.info(f"KENDO GRIDS: {json.dumps(grid_info, default=str)[:10000]}")

            tables = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('table')).map(t => ({
                    id: t.id, cls: (t.className||'').toString(),
                    headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
                    rowCount: t.querySelectorAll('tr').length,
                    sample: Array.from(t.querySelectorAll('tr')).slice(1,20).map(
                        tr => Array.from(tr.querySelectorAll('td')).map(td => (td.textContent||'').trim())
                    ),
                }));
            }""")
            for i, t in enumerate(tables):
                log.info(f"TABLE {i}: id={t['id']!r} class={t['cls']!r} headers={t['headers']} rowCount={t['rowCount']}")
                if t['rowCount'] > 1:
                    for r in t['sample']:
                        log.info(f"  ROW: {r}")

            same_host_links = page.evaluate("""(host) => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({href: a.getAttribute('href'), text:(a.textContent||'').trim()}))
                    .filter(a => a.href && a.href !== '#' && !a.href.startsWith('javascript')
                                 && !a.href.startsWith('http') || (a.href.includes(host)))
                    .filter(a => !/getfirefox|microsoft\\.com|apple\\.com|google\\.com/i.test(a.href))
                    .slice(0, 40);
            }""", HOST)
            log.info(f"Same-host / relative links ({len(same_host_links)}): {same_host_links}")

            case_like = [l for l in same_host_links if
                         re.search(r'/case/|casedetail|smartsearch/case|/cases/', l['href'], re.I)]
            log.info(f"Case-detail-looking links: {case_like}")

            if status == 'has_rows' and case_like:
                href = case_like[0]['href']
                detail_url = href if href.startswith('http') else f"https://{HOST}{href}"
                log.info(f"Opening case detail -> {detail_url}")
                page.goto(detail_url, wait_until='networkidle')
                page.wait_for_timeout(2500)
                if not check_for_bot_challenge(page, 'case_detail'):
                    snapshot(page, '02_case_detail')
                    detail_body = page.evaluate("() => document.body.innerText || ''")
                    log.info("=== CASE DETAIL BODY TEXT (first 350 lines) ===")
                    for ln in [l for l in detail_body.split('\n') if l.strip()][:350]:
                        log.info(f"  | {ln}")

        except SystemExit as se:
            log.error(f"STOPPED: {se}")
        except Exception as ex:
            log.error(f"main flow error: {ex}", exc_info=True)

        log.info(f"=== NETWORK: {len(_net_hits)} total responses touching tylertech ===")
        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _net_hits], f, indent=2)

        browser.close()
        log.info("Probe v6 complete.")


if __name__ == '__main__':
    main()
