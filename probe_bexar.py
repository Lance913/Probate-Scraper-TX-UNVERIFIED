"""
Probe v3 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Ground truth from v1+v2 (confirmed):
  * "Bexar County Justice Portal" (Tyler Odyssey Portal). Smart Search at
    /Portal/Home/Dashboard/29. Kendo UI (Telerik) widgets throughout, no
    native <select>. Real submit button: input#btnSSSubmit[type=submit].
  * Settings.CaptchaEnabled = 'True' (a visible reCAPTCHA v2 checkbox IS on
    the page) — but v2's submit attempt never even reached the server: it was
    blocked by pure CLIENT-SIDE validation ("One or more fields was completed
    incorrectly: Please enter a value for search criteria.") with ZERO new
    network requests fired. So captcha enforcement is still unverified — we
    haven't gotten far enough to test it.
  * caseCriteria_CaseType is a kendoComboBox (SINGLE-select, not multi) whose
    dataSource had exactly ONE placeholder option ("All Offices Case Search")
    while caseCriteria_CourtLocation = 'All Locations' (the default). This
    strongly suggests CaseType CASCADES from CourtLocation, whose only real
    options are: All Locations / County Clerk / District Clerk / Justice of
    the Peace (no explicit "Probate Court" location — Bexar's County Clerk is
    the documented custodian of probate case records per public research, so
    County Clerk is the best-guess Location for estates, but UNVERIFIED).

This probe:
  1. For each real Location value (County Clerk / District Clerk / Justice of
     the Peace), sets it via the Kendo widget API + fires 'change' (the
     documented way to trigger a Kendo cascade), waits, then dumps CaseType's
     dataSource again — to find the real case-type taxonomy and confirm/deny
     whether "Estates" (and its children, and whether Guardianship/Mental
     Health share the same bucket) lives under County Clerk.
  2. Tries to get a real search past the required-field client validation:
     first the cheapest universal wildcard ('*' alone) in the main search
     box, and if that's rejected, a single-letter wildcard ('A*') in the
     dedicated Last Name field instead — to learn the real minimum-length
     rule for this deployment (needed to decide the query strategy: one query
     per case-type with a universal wildcard, vs. an A-Z name sweep).
  3. Whichever gets past validation, sets File Date Start/End wide, sets
     CaseType to whatever estate-hinting option was found in step 1 (if any),
     submits for real, and — critically — checks whether a real server round
     trip happens and whether captcha blocks it.
  4. On any real result, dumps the Kendo Grid's dataSource.data() directly
     (structured JSON) and/or the results <table>, and opens a case detail
     page if a usable link/case number appears, dumping its Party Information
     section verbatim (the OCR-vs-structured-data question).
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
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe_out')
os.makedirs(OUT_DIR, exist_ok=True)

TODAY = date(2026, 7, 28)
WINDOW_START = TODAY - timedelta(days=730)

_net_hits = []
_saved_bodies = 0


def _safe_name(url: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '_', url)[-120:]


def dump_network(page):
    def on_response(resp):
        global _saved_bodies
        try:
            url = resp.url
            if 'tylertech' not in url:
                return
            ct = resp.headers.get('content-type', '')
            status = resp.status
            _net_hits.append((resp.request.method, url, status, ct))
            lower = url.lower()
            skip_static = any(x in ct.lower() for x in ('css', 'image', 'font')) and 'json' not in ct.lower()
            is_asset_path = any(x in lower for x in ('/content/', '/scripts/', '/fonts/', '/theme/'))
            if not skip_static and not is_asset_path and _saved_bodies < 60:
                try:
                    body = resp.text()
                    if body and len(body) < 2_000_000:
                        fname = os.path.join(OUT_DIR, f'net_{_saved_bodies:03d}_{_safe_name(url)}.txt')
                        with open(fname, 'w') as f:
                            f.write(body)
                        _saved_bodies += 1
                        log.info(f"  [captured non-static] {resp.request.method} {status} [{ct}] {url} "
                                  f"({len(body)} bytes, saved to {os.path.basename(fname)})")
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
        log.info(f"[{tag}] saved screenshot+html ({len(html)} bytes) url={page.url}")
    except Exception as e:
        log.warning(f"[{tag}] html save failed: {e}")


def fresh_smart_search(page):
    page.goto(f"{BASE}/Home/Dashboard/29", wait_until='networkidle')
    page.wait_for_timeout(1200)
    try:
        page.locator('a:has-text("Advanced")').first.click()
        page.wait_for_timeout(800)
    except Exception:
        pass


def set_kendo_value_and_cascade(page, el_id: str, value: str, label: str):
    """Set a Kendo widget's value via its JS API and fire 'change' — the
    documented way to trigger cascading dependents (e.g. CaseType depends on
    CourtLocation). Returns True if the widget was found."""
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
                return true;
            }
        }
        return false;
    }""", {'elId': el_id, 'value': value})
    log.info(f"[{label}] set_kendo_value_and_cascade({el_id!r}, {value!r}) -> found_widget={ok}")
    return ok


def dump_one_widget_datasource(page, el_id: str, label: str):
    info = page.evaluate("""(elId) => {
        const el = document.getElementById(elId);
        if (!el) return {error: 'no element'};
        const $el = jQuery(el);
        const data = $el.data();
        for (const key of Object.keys(data)) {
            if (!key.startsWith('kendo')) continue;
            const widget = data[key];
            if (!widget) continue;
            let items = [];
            try { items = widget.dataSource.data().map(it => it.toJSON ? it.toJSON() : it); } catch (e) {}
            let value = null;
            try { value = widget.value(); } catch (e) {}
            return {widgetType: key, value, optionCount: items.length, options: items.slice(0, 200)};
        }
        return {error: 'no kendo widget'};
    }""", el_id)
    if info.get('error'):
        log.warning(f"[{label}] {el_id}: {info['error']}")
        return info
    log.info(f"[{label}] {el_id}: widgetType={info['widgetType']} value={info['value']!r} "
              f"optionCount={info['optionCount']}")
    for opt in info['options']:
        log.info(f"    OPT: {opt}")
    return info


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
        page.set_default_timeout(30000)
        dump_network(page)

        # ── Phase A: cascade test — does CaseType depend on CourtLocation? ────
        estate_hits = {}   # location -> [ (Text, Value) ... ] that look estate-related
        for loc_value in ['County Clerk', 'District Clerk', 'Justice of the Peace']:
            try:
                log.info(f"=== PHASE A: Location={loc_value!r} ===")
                fresh_smart_search(page)
                set_kendo_value_and_cascade(page, 'caseCriteria_CourtLocation', loc_value, loc_value)
                page.wait_for_timeout(2000)  # let any cascade AJAX resolve
                info = dump_one_widget_datasource(page, 'caseCriteria_CaseType', f'CaseType under {loc_value}')
                opts = info.get('options') or []
                hints = ['ESTATE', 'ADMIN', 'MUNIMENT', 'HEIRSHIP', 'PROBATE', 'WILL',
                         'GUARDIAN', 'MENTAL', 'DECEDENT', 'TESTAMENTARY']
                hits = [o for o in opts if any(h in json.dumps(o).upper() for h in hints)]
                if hits:
                    estate_hits[loc_value] = hits
                    log.info(f"  >>> {loc_value}: {len(hits)} probate/estate/guardianship-hinting case types: {hits}")
            except Exception as ex:
                log.error(f"PHASE A ({loc_value}) error: {ex}", exc_info=True)
        log.info(f"=== PHASE A SUMMARY: estate-hinting case types by location: "
                  f"{ {k: len(v) for k, v in estate_hits.items()} } ===")

        # ── Phase B: find the minimum viable "search criteria" input ──────────
        # Fresh page, try the cheapest universal wildcard first.
        try:
            log.info("=== PHASE B: minimum search-criteria wildcard test ===")
            fresh_smart_search(page)
            best_location = next(iter(estate_hits), 'County Clerk')
            set_kendo_value_and_cascade(page, 'caseCriteria_CourtLocation', best_location, 'phaseB-location')
            page.wait_for_timeout(1500)

            # Pick a case type to test with, if we found one; else leave default.
            chosen_case_type = None
            if estate_hits.get(best_location):
                chosen_case_type = estate_hits[best_location][0].get('Text') or estate_hits[best_location][0].get('Value')
                set_kendo_value_and_cascade(page, 'caseCriteria_CaseType', chosen_case_type, 'phaseB-casetype')
                page.wait_for_timeout(500)
            log.info(f"Phase B using location={best_location!r} case_type={chosen_case_type!r}")

            start_el = page.locator('input[name*="FileDateStart" i]').first
            end_el = page.locator('input[name*="FileDateEnd" i]').first
            if start_el.count() > 0:
                start_el.fill(WINDOW_START.strftime('%m/%d/%Y'))
                page.keyboard.press('Escape')
            if end_el.count() > 0:
                end_el.fill(TODAY.strftime('%m/%d/%Y'))
                page.keyboard.press('Escape')
            page.wait_for_timeout(300)

            def try_submit(criteria_value: str, field: str):
                """field: 'main' (combined SearchCriteria box) or 'lastname'."""
                if field == 'main':
                    page.locator('#caseCriteria_SearchCriteria').fill(criteria_value)
                else:
                    page.locator('#caseCriteria_NameLast').fill(criteria_value)
                snapshot(page, f'phaseB_before_{field}_{criteria_value.replace("*","STAR")}')
                pre_count = len(_net_hits)
                page.locator('#btnSSSubmit').first.click()
                page.wait_for_timeout(4000)
                post_count = len(_net_hits)
                body = page.evaluate("() => document.body.innerText || ''")
                err_lines = [l for l in body.split('\n') if 'incorrectly' in l.lower()
                             or 'please enter' in l.lower() or 'must be' in l.lower()
                             or 'at least' in l.lower() or 'captcha' in l.lower()
                             or 'robot' in l.lower()]
                log.info(f"[try_submit field={field} value={criteria_value!r}] "
                          f"new_network_requests={post_count - pre_count} error_lines={err_lines}")
                return post_count - pre_count, err_lines, body

            # Attempt 1: universal wildcard in the main box.
            delta, errs, body = try_submit('*', 'main')
            success = delta > 0 and not errs
            if not success:
                log.info("Universal '*' in main box did not clearly pass — trying 'A*' in Last Name field.")
                fresh_smart_search(page)
                set_kendo_value_and_cascade(page, 'caseCriteria_CourtLocation', best_location, 'phaseB2-location')
                page.wait_for_timeout(1500)
                if chosen_case_type:
                    set_kendo_value_and_cascade(page, 'caseCriteria_CaseType', chosen_case_type, 'phaseB2-casetype')
                    page.wait_for_timeout(500)
                start_el = page.locator('input[name*="FileDateStart" i]').first
                end_el = page.locator('input[name*="FileDateEnd" i]').first
                if start_el.count() > 0:
                    start_el.fill(WINDOW_START.strftime('%m/%d/%Y')); page.keyboard.press('Escape')
                if end_el.count() > 0:
                    end_el.fill(TODAY.strftime('%m/%d/%Y')); page.keyboard.press('Escape')
                page.wait_for_timeout(300)
                delta, errs, body = try_submit('A*', 'lastname')
                success = delta > 0 and not errs

            log.info(f"=== PHASE B RESULT: success={success} last_delta={delta} last_errors={errs} ===")
            snapshot(page, '10_phaseB_final')
            log.info("=== PHASE B FINAL BODY TEXT (first 300 lines) ===")
            for ln in [l for l in body.split('\n') if l.strip()][:300]:
                log.info(f"  | {ln}")

            # ── Phase C: if we got real results, dump them thoroughly ─────────
            if success:
                grid_info = page.evaluate("""() => {
                    if (typeof jQuery === 'undefined') return {error: 'no jQuery'};
                    const g = jQuery('[data-role="grid"]').data('kendoGrid');
                    if (!g) return {error: 'no kendoGrid found'};
                    let rows = [];
                    try { rows = g.dataSource.data().map(it => it.toJSON ? it.toJSON() : it); } catch (e) {}
                    return {total: g.dataSource.total(), rowCount: rows.length, rows: rows.slice(0, 25)};
                }""")
                log.info(f"KENDO GRID: {json.dumps(grid_info, default=str)[:8000]}")

                tables = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('table')).map(t => ({
                        headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
                        rowCount: t.querySelectorAll('tr').length,
                        sample: Array.from(t.querySelectorAll('tr')).slice(1,15).map(
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
                        log.info(f"PHASE D: opening case detail -> {detail_url}")
                        page.goto(detail_url, wait_until='networkidle')
                        page.wait_for_timeout(2500)
                        snapshot(page, '11_case_detail')
                        detail_body = page.evaluate("() => document.body.innerText || ''")
                        log.info("=== CASE DETAIL BODY TEXT (first 300 lines) ===")
                        for ln in [l for l in detail_body.split('\n') if l.strip()][:300]:
                            log.info(f"  | {ln}")

        except Exception as ex:
            log.error(f"PHASE B/C error: {ex}", exc_info=True)

        log.info(f"=== NETWORK: {len(_net_hits)} total responses touching tylertech ===")
        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _net_hits], f, indent=2)

        browser.close()
        log.info("Probe v3 complete.")


if __name__ == '__main__':
    main()
