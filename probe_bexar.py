"""
Probe v4 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Ground truth from v1-v3 (confirmed):
  * Kendo UI (Telerik) widgets, ASP.NET MVC model binding under `caseCriteria.*`.
    Real submit: input#btnSSSubmit[type=submit]. Settings.CaptchaEnabled='True'
    but never actually tested server-side yet (every attempt so far died in
    pure CLIENT-SIDE validation before any network request fired).
  * caseCriteria_CourtLocation (kendoComboBox) real options: All Locations /
    County Clerk / District Clerk / Justice of the Peace.
  * caseCriteria_CaseType (kendoComboBox) DOES cascade from CourtLocation
    (confirmed: its dataSource's "ParentItem" field changes to match the
    selected location) but each location's *default* dataSource is just ONE
    generic placeholder item, e.g. {'ParentItem':'County Clerk','Id':-1,
    'Text':'County Clerk Case Search','Value':'County Clerk Case Search'}.
    This is very likely a server-filtered autocomplete (only returns real
    case-type matches once you TYPE a query) rather than a fully preloaded
    list — v3 never tried typing into it. That's this probe's top priority.
  * The main caseCriteria_SearchCriteria box rejects a bare '*' — client-side
    "Please enter a value for search criteria" with ZERO network requests
    (so '*' alone fails some client regex/length check, not a server rule).
  * v3 crashed on a later .fill() (30s actionability timeout) — almost
    certainly a lingering Kendo popup/overlay from scripting the Location
    widget right before trying to interact with another field. This probe
    fixes that: JS-based value-setting (bypasses Playwright actionability
    waits entirely) with try/except around every step, and Escape presses
    to close popups after every Kendo interaction.

This probe:
  1. PRIORITY: with Location=County Clerk, TYPE into the real CaseType input
     (triggers Kendo's remote filter) several probe terms — 'estate',
     'administ', 'probate', 'muniment', 'heirship', 'will', 'guardian',
     'mental' — and after each, dump both the widget's dataSource AND the
     raw Kendo popup DOM list, plus save any network request the typing
     fires (the real autocomplete endpoint + param shape). This should
     finally reveal the real estate case-type taxonomy and whether
     guardianship/mental-health share it.
  2. Repeats step 1 for Location=District Clerk too (probate could
     conceivably sit there instead/also — cheap to check, removes doubt).
  3. Tests the minimum viable value for caseCriteria_NameLast: 'A*', 'AB*',
     and '%' (in case this deployment uses SQL-style wildcards instead of
     '*'), each on a fresh page load, watching for either a real network
     POST (success) or a specific client-side length/format complaint.
  4. If any submission gets past client validation, dumps whatever comes
     back — including explicitly checking for a captcha/human-verification
     challenge at that point (per coordinator guidance: if seen, log loudly,
     do not attempt to solve/bypass it, do not theorize fingerprinting).
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
            if not is_asset and _saved_bodies < 80:
                try:
                    body = resp.text()
                    if body and len(body) < 2_000_000:
                        fname = os.path.join(OUT_DIR, f'net_{_saved_bodies:03d}_{_safe_name(url)}.txt')
                        with open(fname, 'w') as f:
                            f.write(body)
                        _saved_bodies += 1
                        log.info(f"  [non-asset] {resp.request.method} {status} [{ct}] {url} ({len(body)}b)")
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
    """Set a Kendo widget's value via JS + fire 'change' (triggers cascades).
    Bypasses Playwright's actionability waits entirely — robust against
    overlays. Also presses Escape afterward to close any popup left open."""
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


def js_fill_text(page, el_id: str, value: str, label: str) -> bool:
    """Set a plain text input's value via JS (dispatch input+change), skipping
    Playwright's visibility/actionability checks — robust against overlays."""
    try:
        ok = page.evaluate("""(args) => {
            const el = document.getElementById(args.elId);
            if (!el) return false;
            el.value = args.value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", {'elId': el_id, 'value': value})
        log.info(f"[{label}] js_fill_text({el_id!r}, {value!r}) -> {ok}")
        return ok
    except Exception as e:
        log.warning(f"[{label}] js_fill_text error: {e}")
        return False


def probe_casetype_autocomplete(page, location: str, terms):
    """Type each term into the real CaseType input and see what the Kendo
    remote filter returns — this is how we find the actual case-type
    taxonomy (v3 showed the preloaded dataSource is just a placeholder)."""
    log.info(f"=== CaseType autocomplete probe under Location={location!r} ===")
    fresh_smart_search(page)
    js_set_kendo_value(page, 'caseCriteria_CourtLocation', location, f'{location}-loc')
    page.wait_for_timeout(1500)

    input_sel = 'input[name="caseCriteria.CaseType_input"]'
    all_found = {}
    for term in terms:
        try:
            el = page.locator(input_sel).first
            el.click(timeout=5000)
            el.fill('')
            el.type(term, delay=60)
            page.wait_for_timeout(1800)  # let the debounced remote filter fire
            popup_items = page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('.k-list-container .k-item, .k-popup .k-item, ul[id*="CaseType"] li'));
                return items.map(li => (li.textContent||'').trim()).filter(Boolean);
            }""")
            ds_info = page.evaluate("""(elId) => {
                const el = document.getElementById(elId);
                if (!el) return null;
                const $el = jQuery(el);
                const data = $el.data();
                for (const key of Object.keys(data)) {
                    if (!key.startsWith('kendo')) continue;
                    const widget = data[key];
                    if (!widget || !widget.dataSource) continue;
                    try {
                        return widget.dataSource.data().map(it => it.toJSON ? it.toJSON() : it);
                    } catch (e) { return {error: String(e)}; }
                }
                return null;
            }""", 'caseCriteria_CaseType')
            log.info(f"  term={term!r}: popup_items={popup_items} dataSource={ds_info}")
            if ds_info and isinstance(ds_info, list):
                for item in ds_info:
                    key = json.dumps(item, sort_keys=True)
                    all_found[key] = item
            page.keyboard.press('Escape')
        except Exception as e:
            log.warning(f"  term={term!r}: error {e}")
    log.info(f"=== {location}: {len(all_found)} DISTINCT case-type items found across all terms ===")
    for item in all_found.values():
        log.info(f"    ITEM: {item}")
    return all_found


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

        terms = ['estate', 'admin', 'probate', 'muniment', 'heirship',
                 'will', 'guardian', 'mental', 'decedent']

        all_estate_options = {}
        try:
            found = probe_casetype_autocomplete(page, 'County Clerk', terms)
            all_estate_options['County Clerk'] = found
        except Exception as ex:
            log.error(f"County Clerk autocomplete probe error: {ex}", exc_info=True)

        try:
            found = probe_casetype_autocomplete(page, 'District Clerk', terms)
            all_estate_options['District Clerk'] = found
        except Exception as ex:
            log.error(f"District Clerk autocomplete probe error: {ex}", exc_info=True)

        # ── Phase B: minimum wildcard length for NameLast (defensive, isolated) ──
        wildcard_candidates = ['A*', 'AB*', '%', 'A%']
        b_success = False
        success_body = ''
        for wc in wildcard_candidates:
            try:
                log.info(f"=== PHASE B: testing NameLast={wc!r} ===")
                fresh_smart_search(page)
                js_set_kendo_value(page, 'caseCriteria_CourtLocation', 'County Clerk', 'phaseB-loc')
                page.wait_for_timeout(1000)
                js_fill_text(page, 'caseCriteria.FileDateStart', WINDOW_START.strftime('%m/%d/%Y'), 'phaseB-start')
                js_fill_text(page, 'caseCriteria.FileDateEnd', TODAY.strftime('%m/%d/%Y'), 'phaseB-end')
                js_fill_text(page, 'caseCriteria_NameLast', wc, 'phaseB-lastname')
                page.wait_for_timeout(400)
                snapshot(page, f'phaseB_{wc.replace("*","STAR").replace("%","PCT")}_before')

                pre = len(_net_hits)
                page.locator('#btnSSSubmit').first.click(timeout=10000)
                page.wait_for_timeout(4500)
                post = len(_net_hits)
                body = page.evaluate("() => document.body.innerText || ''")
                err_lines = [l for l in body.split('\n') if
                             'incorrectly' in l.lower() or 'please enter' in l.lower()
                             or 'must be' in l.lower() or 'at least' in l.lower()
                             or 'captcha' in l.lower() or 'robot' in l.lower()
                             or 'verify' in l.lower()]
                log.info(f"[NameLast={wc!r}] new_requests={post - pre} err_lines={err_lines}")
                snapshot(page, f'phaseB_{wc.replace("*","STAR").replace("%","PCT")}_after')
                if post > pre and not err_lines:
                    log.info(f"*** SUCCESS with NameLast={wc!r} — got a real server round trip! ***")
                    b_success = True
                    success_body = body
                    break
                elif err_lines:
                    log.info(f"NameLast={wc!r} rejected: {err_lines}")
            except Exception as ex:
                log.error(f"PHASE B ({wc}) error: {ex}", exc_info=True)

        if b_success:
            log.info("=== SUCCESS BODY (first 300 lines) ===")
            for ln in [l for l in success_body.split('\n') if l.strip()][:300]:
                log.info(f"  | {ln}")
            try:
                grid_info = page.evaluate("""() => {
                    if (typeof jQuery === 'undefined') return {error: 'no jQuery'};
                    const g = jQuery('[data-role="grid"]').data('kendoGrid');
                    if (!g) return {error: 'no kendoGrid'};
                    let rows = [];
                    try { rows = g.dataSource.data().map(it => it.toJSON ? it.toJSON() : it); } catch (e) {}
                    return {total: g.dataSource.total(), rows: rows.slice(0, 20)};
                }""")
                log.info(f"KENDO GRID: {json.dumps(grid_info, default=str)[:6000]}")
            except Exception as e:
                log.warning(f"grid dump error: {e}")
            snapshot(page, '20_phaseB_success')
        else:
            log.info("=== PHASE B: no wildcard candidate got past client validation ===")

        log.info(f"=== NETWORK: {len(_net_hits)} total responses touching tylertech ===")
        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _net_hits], f, indent=2)
        with open(os.path.join(OUT_DIR, 'estate_options_found.json'), 'w') as f:
            json.dump({loc: list(items.values()) for loc, items in all_estate_options.items()},
                       f, indent=2, default=str)

        browser.close()
        log.info("Probe v4 complete.")


if __name__ == '__main__':
    main()
