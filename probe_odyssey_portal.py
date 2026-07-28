"""
probe_odyssey_portal.py -- throwaway investigation script for the Tyler
Technologies Odyssey "Portal" product family, hypothesized for Dallas +
Travis County TX probate case search (both self-hosted on the county's own
domain, unlike Bexar's portal-txbexar.tylertech.cloud). See SYSTEM_GUIDE.md
S6 for the methodology this follows; safe to delete once both counties'
real scrapers are written and confirmed working.

ALWAYS run via GitHub Actions (gh workflow run probe_odyssey_portal.yml) --
these portals geo-block non-US IPs, so a local run will hang/fail/time out.
Local use is limited to `python3 -m py_compile probe_odyssey_portal.py`.

What it does, in order:
  1. A plain `requests` HTTP check of --base-url (fast signal: reachable?
     redirected? WAF/geo-block page? plain HTML vs JS shell?).
  2. Loads --base-url in headless Chromium, dismisses common disclaimer/
     cookie modals, dumps: title, all <form> fields (incl. <select> options),
     all clickable nav text, any <table> headers+sample rows, and a body
     text sample -- plus every non-static network response (method/status/
     url, and a truncated body for anything JSON-ish or url-matching
     api/search), which is usually the fastest way to reverse-engineer an
     Angular/React portal's real query shape.
  3. Optionally clicks through a pipe-separated sequence of link/button text
     (--click "Case Records|Probate"), re-dumping state after each click.
  4. Optionally also loads extra relative paths (--extra-paths) and/or full
     URLs (--direct-urls) -- e.g. to test a hypothesized results query URL
     directly, once the query shape is known, per SYSTEM_GUIDE.md S6 item 5.

Usage:
  python probe_odyssey_portal.py --base-url <url> --county <name> \
      [--click "text1|text2"] [--extra-paths "/p1|/p2"] [--direct-urls "u1|u2"]
"""
import argparse
import json
import logging
import sys
import time
from urllib.parse import urljoin

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [probe] %(levelname)s: %(message)s',
    stream=sys.stdout,
)
log = logging.getLogger('probe')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

STATIC_EXT = ('.js', '.css', '.woff', '.woff2', '.ttf', '.eot', '.ico',
              '.svg', '.jpg', '.jpeg', '.gif', '.map')

DUMP_JS = r"""() => {
    const forms = Array.from(document.querySelectorAll('form')).map(f => ({
        id: f.id, action: f.action, method: f.method,
        fields: Array.from(f.querySelectorAll('input,select,textarea,button')).map(el => ({
            tag: el.tagName, type: el.type || '', id: el.id, name: el.name,
            placeholder: el.placeholder || '', aria: el.getAttribute('aria-label') || '',
            text: (el.textContent || '').trim().slice(0, 60),
            options: el.tagName === 'SELECT'
                ? Array.from(el.options).map(o => o.textContent.trim()).slice(0, 40)
                : undefined,
        })),
    }));
    const clickableRaw = Array.from(document.querySelectorAll('a,button,[role="button"],li,span'))
        .map(el => ({
            tag: el.tagName, text: (el.textContent || '').trim().slice(0, 60),
            href: el.getAttribute('href') || '', id: el.id,
            cls: (el.className || '').toString().slice(0, 60),
        }))
        .filter(x => x.text && x.text.length < 60);
    const seen = new Set(); const nav = [];
    for (const c of clickableRaw) {
        const k = c.tag + '|' + c.text;
        if (seen.has(k)) continue;
        seen.add(k); nav.push(c);
        if (nav.length >= 100) break;
    }
    const tables = Array.from(document.querySelectorAll('table')).map(t => ({
        headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent || '').trim()),
        rowCount: t.querySelectorAll('tr').length,
        sampleRows: Array.from(t.querySelectorAll('tr')).slice(1, 6).map(
            tr => Array.from(tr.querySelectorAll('td')).map(td => (td.textContent || '').trim())
        ),
    }));
    return {
        title: document.title,
        url: location.href,
        forms: forms,
        nav: nav,
        tables: tables,
        bodyTextSample: (document.body.innerText || '').slice(0, 2500),
    };
}"""

MODAL_DISMISS_JS = r"""() => {
    const phrases = ['i agree', 'agree', 'accept', 'continue', 'acknowledge',
                      'ok', 'got it', 'i understand', 'close', 'enter site'];
    const els = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]'));
    for (const el of els) {
        const t = (el.textContent || el.value || '').trim().toLowerCase();
        if (!t) continue;
        for (const p of phrases) {
            if (t === p || t.includes(p)) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) { el.click(); return t; }
            }
        }
    }
    return null;
}"""

LOCATE_JS = r"""(needle) => {
    const n = needle.toLowerCase();
    const els = Array.from(document.querySelectorAll('a,button,[role="button"],li,span,div'));
    for (const el of els) {
        const t = (el.textContent || '').trim();
        if (t && t.length < 80 && t.toLowerCase().includes(n)) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                el.scrollIntoView({block: 'center'});
                return {found: true, tag: el.tagName, text: t};
            }
        }
    }
    return {found: false};
}"""

JS_CLICK_FALLBACK_JS = r"""(needle) => {
    const n = needle.toLowerCase();
    const els = Array.from(document.querySelectorAll('a,button,[role="button"],li,span,div'));
    for (const el of els) {
        const t = (el.textContent || '').trim();
        if (t && t.length < 80 && t.toLowerCase().includes(n)) { el.click(); return true; }
    }
    return false;
}"""


def quick_http_check(url):
    """Cheap pre-flight with `requests` -- fast signal before paying for a
    browser boot: reachable? redirected? WAF/geo-block page? plain HTML?"""
    try:
        import requests
    except ImportError:
        log.warning("requests not importable -- skipping HTTP pre-check")
        return
    headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    log.info(f"===== quick_http_check: {url} =====")
    try:
        t0 = time.monotonic()
        r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        dt = time.monotonic() - t0
        log.info(f"status={r.status_code} final_url={r.url} elapsed={dt:.2f}s "
                  f"content-length={len(r.content)} content-type={r.headers.get('content-type')}")
        if r.history:
            log.info(f"redirect chain: {[h.status_code for h in r.history]} -> {[h.url for h in r.history]}")
        sample = ' '.join(r.text.split())[:800]
        log.info(f"body sample: {sample}")
    except Exception as exc:
        log.error(f"quick_http_check FAILED: {type(exc).__name__}: {exc}")


def make_network_logger(bucket, county):
    def on_response(resp):
        url = resp.url
        low = url.lower().split('?')[0]
        if any(low.endswith(ext) for ext in STATIC_EXT):
            return
        ctype = ''
        try:
            ctype = resp.headers.get('content-type', '')
        except Exception:
            pass
        try:
            method = resp.request.method
        except Exception:
            method = '?'
        entry = {'status': resp.status, 'url': url, 'ctype': ctype, 'method': method}
        if 'json' in ctype or '/api' in low or 'search' in low.lower():
            try:
                body = resp.text()
                entry['body'] = body[:2500]
            except Exception as e:
                entry['body_err'] = str(e)
        bucket.append(entry)
        log.info(f"[{county}] NET {method} {resp.status} {url} ctype={ctype}")
    return on_response


def dump_state(page, label):
    try:
        state = page.evaluate(DUMP_JS)
    except Exception as exc:
        log.warning(f"dump_state({label}) failed: {exc}")
        return
    log.info(f"===== STATE: {label} =====")
    log.info(f"title={state['title']!r} url={state['url']}")
    log.info(f"forms ({len(state['forms'])}): {json.dumps(state['forms'])[:4000]}")
    log.info(f"nav/clickable ({len(state['nav'])}): {json.dumps(state['nav'])[:4000]}")
    log.info(f"tables ({len(state['tables'])}): {json.dumps(state['tables'])[:4000]}")
    log.info(f"bodyTextSample:\n{state['bodyTextSample']}")


def try_dismiss_modal(page):
    try:
        clicked = page.evaluate(MODAL_DISMISS_JS)
        if clicked:
            log.info(f"Dismissed modal/banner via button text: {clicked!r}")
            page.wait_for_timeout(1000)
            return True
    except Exception as exc:
        log.info(f"modal dismiss attempt failed (ok, may just not exist): {exc}")
    return False


def settle(page, timeout_ms=20000):
    try:
        page.wait_for_load_state('networkidle', timeout=timeout_ms)
    except Exception:
        log.info("networkidle wait timed out (app may long-poll) -- continuing anyway")
    page.wait_for_timeout(1000)


def load_and_dump(page, url, label, screenshot_path=None):
    log.info(f"--- goto {label}: {url}")
    try:
        resp = page.goto(url, wait_until='domcontentloaded', timeout=45000)
        log.info(f"HTTP status: {resp.status if resp else '(no response obj)'}")
    except Exception as exc:
        log.error(f"goto FAILED for {label} ({url}): {exc}")
        return False
    settle(page)
    try_dismiss_modal(page)
    settle(page, timeout_ms=8000)
    dump_state(page, label)
    if screenshot_path:
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            log.info(f"Screenshot saved: {screenshot_path}")
        except Exception as exc:
            log.warning(f"screenshot failed: {exc}")
    return True


def click_text(page, text, label):
    log.info(f"--- click: {text!r}")
    try:
        info = page.evaluate(LOCATE_JS, text)
    except Exception as exc:
        log.warning(f"click_text locate failed: {exc}")
        info = {'found': False}
    if not info.get('found'):
        log.warning(f"No visible clickable element found containing {text!r}")
        return False
    log.info(f"located: tag={info.get('tag')} text={info.get('text')!r}")
    try:
        loc = page.get_by_text(text, exact=False).first
        loc.click(timeout=8000)
    except Exception as exc:
        log.warning(f"click via get_by_text failed ({exc}); trying JS click")
        try:
            clicked = page.evaluate(JS_CLICK_FALLBACK_JS, text)
            if not clicked:
                log.error("JS click fallback found nothing to click")
                return False
        except Exception as exc2:
            log.error(f"JS click fallback also failed: {exc2}")
            return False
    settle(page)
    try_dismiss_modal(page)
    settle(page, timeout_ms=8000)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-url', required=True)
    ap.add_argument('--county', required=True)
    ap.add_argument('--click', default='', help='pipe-separated click text sequence')
    ap.add_argument('--extra-paths', default='', help='pipe-separated relative paths')
    ap.add_argument('--direct-urls', default='', help='pipe-separated full URLs')
    args = ap.parse_args()

    click_list = [s.strip() for s in args.click.split('|') if s.strip()]
    extra_paths = [s.strip() for s in args.extra_paths.split('|') if s.strip()]
    direct_urls = [s.strip() for s in args.direct_urls.split('|') if s.strip()]

    quick_http_check(args.base_url)

    from playwright.sync_api import sync_playwright
    sys.path.insert(0, '.')
    try:
        from scrapers.base import launch_chromium
    except Exception as exc:
        log.info(f"could not import scrapers.base.launch_chromium ({exc}); using plain launch")
        launch_chromium = None

    network_log = []
    slug = args.county.lower().replace(' ', '_')

    with sync_playwright() as pw:
        if launch_chromium:
            browser = launch_chromium(pw)
        else:
            browser = pw.chromium.launch(headless=True,
                                          args=['--disable-blink-features=AutomationControlled'])
        try:
            context = browser.new_context(user_agent=UA, viewport={'width': 1440, 'height': 1000},
                                           ignore_https_errors=True)
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = context.new_page()
            page.set_default_timeout(45000)
            page.on('response', make_network_logger(network_log, args.county))

            log.info(f"===== PROBE START: {args.county} @ {args.base_url} =====")
            ok = load_and_dump(page, args.base_url, 'base',
                                screenshot_path=f'probe_{slug}_00_base.png')

            if ok:
                for i, text in enumerate(click_list, start=1):
                    if click_text(page, text, f'click:{text}'):
                        dump_state(page, f'after-click:{text}')
                        try:
                            page.screenshot(path=f'probe_{slug}_{i:02d}_click.png', full_page=True)
                        except Exception:
                            pass

            base_for_join = args.base_url if args.base_url.endswith('/') else args.base_url + '/'
            for i, path in enumerate(extra_paths, start=1):
                url = urljoin(base_for_join, path.lstrip('/'))
                load_and_dump(page, url, f'extra-path:{path}',
                              screenshot_path=f'probe_{slug}_path{i:02d}.png')

            for i, url in enumerate(direct_urls, start=1):
                load_and_dump(page, url, f'direct-url:{url}',
                              screenshot_path=f'probe_{slug}_direct{i:02d}.png')
        finally:
            browser.close()

    log.info(f"===== NETWORK SUMMARY ({len(network_log)} non-static responses) =====")
    for e in network_log:
        line = f"{e['method']} {e['status']} {e['url']} ctype={e.get('ctype', '')}"
        log.info(line)
        if 'body' in e:
            log.info(f"    BODY: {e['body'][:1500]}")

    out_name = f'probe_{slug}_network.json'
    with open(out_name, 'w') as f:
        json.dump(network_log, f, indent=2, default=str)
    log.info(f"Wrote {out_name}")

    log.info("===== PROBE END =====")


if __name__ == '__main__':
    main()
