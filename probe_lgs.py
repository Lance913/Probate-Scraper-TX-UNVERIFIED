"""
Probe — Ellis County, TX probate case search via LGS Online Solutions
(public.lgsonlinesolutions.com/ors.html).

Why this URL, not a guess (SYSTEM_GUIDE.md §6/§8 fingerprinting done first):
- Ellis County's OFFICIAL site (elliscountytx.gov/1397/Online-Record-Search)
  explicitly separates two DIFFERENT systems:
    * "Property Search"       -> https://ellisccktxpublicsearch.us/AcclaimWeb/
      (a decoy — real-property/document recording, NOT court case search)
    * "County Court Record Search" -> https://public.lgsonlinesolutions.com/ors.html
      (this is the one we want)
- us-lgs.com/products/county-clerk (the vendor's own product page) confirms
  their DataPoint system covers "Probate/Guardianship Case Management" with
  "Public Inquiry for all systems" — probate should be in scope.
- Third-party summaries describe ors.html as a free, no-subscription-required
  INDEX search (registration/subscription is only needed to purchase document
  IMAGES) — i.e. exactly what we need (index data: name, case #, case type,
  filing date), no paywall expected for the scrape itself.
- A prior sweep (sibling agent's probe on add-collin-probate-scraper branch,
  run 30384956632, part of a multi-county fingerprint sweep — NOT built for
  Ellis specifically) already hit this exact URL from a real US GitHub
  Actions IP and got: HTTP 200, title="Online Records Search", no
  recaptcha/hcaptcha/viewstate markers — i.e. REACHABLE, not geo-blocked for
  US traffic. But `page.inner_text('body')` came back empty after hanging
  for exactly its 20s default timeout. That specific symptom (goto succeeds،
  title renders, but a `body` selector wait times out) is the classic
  signature of a page with NO <body> element Playwright can match — i.e. a
  classic HTML <frameset> page (old-school county-vendor systems still do
  this; the literal filename "ors.html", not an SPA route, is consistent
  with that). This probe is built to confirm/refute that hypothesis and,
  either way, get past it to the real search form.
- Both my own local WebFetch attempts at this URL got connect ECONNREFUSED
  (same symptom the human-supplied discovery notes reported) — consistent
  with geo-blocking non-US traffic. Real investigation has to happen on a
  GitHub Actions US runner, per SYSTEM_GUIDE.md §2.1 — this script is meant
  to be run there, not locally.

This probe is defensive and multi-hypothesis on purpose: it dumps many
diagnostics in one run (structure, frames, screenshot, raw HTML, console
errors, network calls) so a single ~2min iteration yields maximum signal.
"""
import logging

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ORS_URL = "https://public.lgsonlinesolutions.com/ors.html"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def check_structure(target, label):
    try:
        info = target.evaluate("""() => ({
            hasBody: !!document.body,
            hasFrameset: !!document.querySelector('frameset'),
            frameCount: document.querySelectorAll('frame').length,
            iframeCount: document.querySelectorAll('iframe').length,
            frameSrcs: Array.from(document.querySelectorAll('frame,iframe')).map(
                f => (f.getAttribute('src')||'') + ' name=' + (f.getAttribute('name')||'')),
            scripts: Array.from(document.querySelectorAll('script[src]')).map(
                s => s.getAttribute('src')).slice(0,25),
            title: document.title,
            htmlLen: document.documentElement ? document.documentElement.outerHTML.length : 0,
            bodyChildCount: document.body ? document.body.children.length : -1,
        })""")
        log.info(f"[{label}] structure: {info}")
        return info
    except Exception as e:
        log.info(f"[{label}] check_structure FAILED: {str(e)[:300]}")
        return {}


def safe_body_text(target, label, limit=3000):
    try:
        txt = target.inner_text('body', timeout=8000)
        log.info(f"[{label}] body text ({len(txt)} chars): {txt[:limit]!r}")
        return txt
    except Exception as e:
        log.info(f"[{label}] inner_text('body') FAILED/timeout: {str(e)[:200]}")
        try:
            txt2 = target.evaluate(
                "() => (document.documentElement && "
                "(document.documentElement.innerText || document.documentElement.textContent)) "
                "|| 'NO DOCUMENT ELEMENT TEXT'")
            log.info(f"[{label}] documentElement text fallback ({len(txt2)} chars): {txt2[:limit]!r}")
            return txt2
        except Exception as e2:
            log.info(f"[{label}] documentElement fallback ALSO failed: {str(e2)[:200]}")
            return ''


def dump_form(target, label):
    try:
        info = target.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('input, select, textarea, button, a'));
            return els.slice(0, 250).map(el => {
                const o = {tag: el.tagName.toLowerCase(), type: el.type||'', id: el.id||'',
                           name: el.name||'', placeholder: el.placeholder||'',
                           text: (el.textContent||'').trim().slice(0,80),
                           href: (el.getAttribute && el.getAttribute('href')) || '',
                           visible: !!(el.offsetWidth || el.offsetHeight ||
                                       (el.getClientRects && el.getClientRects().length))};
                if (el.tagName === 'SELECT') {
                    o.options = Array.from(el.options).map(op => `${op.value}=${op.text}`);
                }
                return o;
            });
        }""")
    except Exception as e:
        log.info(f"[{label}] dump_form FAILED: {str(e)[:300]}")
        return
    log.info(f"[{label}] form/nav elements ({len(info)}):")
    for el in info:
        if el['tag'] == 'select':
            log.info(f"  SELECT id={el['id']!r} name={el['name']!r} visible={el['visible']} "
                      f"options={el.get('options')}")
        elif el['tag'] in ('button', 'a') or el['type'] in ('submit', 'button'):
            log.info(f"  {el['tag'].upper()} id={el['id']!r} name={el['name']!r} text={el['text']!r} "
                      f"href={el.get('href','')!r} visible={el['visible']}")
        else:
            log.info(f"  {el['tag'].upper()} type={el['type']!r} id={el['id']!r} name={el['name']!r} "
                      f"placeholder={el['placeholder']!r} visible={el['visible']}")


def dump_frames(page):
    frames = page.frames
    log.info(f"Frames on page ({len(frames)} total, including main):")
    for i, fr in enumerate(frames):
        try:
            log.info(f"  frame[{i}]: name={fr.name!r} url={fr.url!r}")
        except Exception as e:
            log.info(f"  frame[{i}]: FAILED to read: {e}")
    return frames


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=UA, viewport={'width': 1440, 'height': 1000})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        console_msgs = []
        page.on('console', lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
        page.on('pageerror', lambda e: console_msgs.append(f"PAGEERROR: {str(e)[:200]}"))

        xhr_log = []

        def on_response(resp):
            try:
                ct = resp.headers.get('content-type', '')
                if 'json' in ct or '/api/' in resp.url.lower() or resp.request.method == 'POST':
                    xhr_log.append((resp.request.method, resp.status, resp.url))
            except Exception:
                pass
        page.on('response', on_response)

        log.info(f"Navigating to {ORS_URL} (wait_until='load')")
        try:
            resp = page.goto(ORS_URL, wait_until='load', timeout=30000)
            log.info(f"goto returned status={resp.status if resp else None} ok={resp.ok if resp else None}")
        except Exception as e:
            log.error(f"goto FAILED even with wait_until='load': {str(e)[:400]}")
            resp = None

        page.wait_for_timeout(4000)  # let any late JS/redirect settle
        log.info(f"Final URL: {page.url!r} | Title: {page.title()!r}")

        try:
            page.screenshot(path='ors_landing.png', full_page=True)
            log.info("Saved screenshot -> ors_landing.png")
        except Exception as e:
            log.info(f"Screenshot FAILED: {str(e)[:200]}")

        try:
            html = page.content()
            with open('ors_landing.html', 'w') as f:
                f.write(html)
            log.info(f"Saved raw HTML ({len(html)} chars) -> ors_landing.html")
        except Exception as e:
            log.info(f"page.content() FAILED: {str(e)[:200]}")

        check_structure(page, 'main-doc')
        safe_body_text(page, 'main-doc')
        dump_form(page, 'main-doc')
        frames = dump_frames(page)

        # Drill into every non-main frame (handles classic <frameset> pages,
        # where the real content/form lives inside a child frame, not <body>).
        for i, fr in enumerate(frames):
            if fr == page.main_frame:
                continue
            label = f"frame[{i}]:{fr.url}"
            try:
                fr.wait_for_load_state('load', timeout=8000)
            except Exception:
                pass
            check_structure(fr, label)
            safe_body_text(fr, label)
            dump_form(fr, label)
            try:
                fr_html = fr.content()
                fname = f'ors_frame_{i}.html'
                with open(fname, 'w') as f:
                    f.write(fr_html)
                log.info(f"[{label}] saved raw HTML ({len(fr_html)} chars) -> {fname}")
            except Exception as e:
                log.info(f"[{label}] frame.content() FAILED: {str(e)[:200]}")

        log.info(f"Console/page errors captured ({len(console_msgs)}):")
        for m in console_msgs[:60]:
            log.info(f"  {m}")

        # Detect (don't yet act on) common guest/search entry points, in main
        # doc and every frame, so the NEXT probe iteration can target exact
        # selectors instead of guessing.
        targets = [('main', page)] + [(f'frame:{fr.url}', fr) for fr in frames if fr != page.main_frame]
        for tname, t in targets:
            for sel in ['text=/guest/i', 'text=/continue without/i', 'text=/new account/i',
                        'text=/log ?in/i', 'a:has-text("Search")', 'button:has-text("Search")',
                        'text=/online records search/i', 'text=/case search/i',
                        'text=/court records/i']:
                try:
                    loc = t.locator(sel).first
                    if loc.count() > 0:
                        log.info(f"[{tname}] candidate control matching {sel!r}: "
                                 f"text={loc.inner_text()[:100]!r}")
                except Exception:
                    pass

        log.info(f"XHR/interesting network responses observed ({len(xhr_log)}):")
        for m, s, u in xhr_log[:80]:
            log.info(f"  {m} {s} {u}")

        # ── PHASE 2 — Guest Login ───────────────────────────────────────────
        # ors_UserLogin.html's "Guest Login" button calls JS GuestSubmit(),
        # which sets OPERCODE=orguest / PASSWD=orguest then xSubmit()s the
        # form (POST /cgi-bin/webshell.asp). Fetch login.js first (same-origin
        # fetch from inside the page, so it rides the existing session/cookies)
        # to see exactly what xSubmit() does before triggering it blind.
        menu_frame = next((fr for fr in frames if fr.name == 'menu'), None)
        if not menu_frame:
            log.error("Could not find frame named 'menu' — aborting Phase 2.")
            browser.close()
            return

        log.info("=" * 70)
        log.info("PHASE 2 — Guest Login")
        log.info("=" * 70)

        try:
            js_src = menu_frame.evaluate(
                "() => fetch('/javascript4.4/login.js').then(r => r.text())")
            log.info(f"login.js contents ({len(js_src)} chars):\n{js_src[:6000]}")
        except Exception as e:
            log.info(f"Fetching login.js FAILED: {str(e)[:300]}")

        webshell_bodies = []

        def on_response2(resp):
            try:
                if 'webshell' in resp.url.lower():
                    ct = resp.headers.get('content-type', '')
                    try:
                        body = resp.text()
                    except Exception:
                        body = '<unreadable/binary>'
                    webshell_bodies.append((resp.status, resp.url, ct, body))
            except Exception:
                pass
        page.on('response', on_response2)

        clicked = False
        try:
            guest_btn = menu_frame.locator('#GuestLogIn')
            log.info(f"Guest Login button count={guest_btn.count()}")
            guest_btn.click(timeout=10000)
            clicked = True
            log.info("Clicked #GuestLogIn")
        except Exception as e:
            log.error(f"Click #GuestLogIn FAILED: {str(e)[:300]}")
            try:
                menu_frame.evaluate("GuestSubmit()")
                clicked = True
                log.info("Invoked GuestSubmit() directly via evaluate() as fallback")
            except Exception as e2:
                log.error(f"Direct GuestSubmit() evaluate ALSO failed: {str(e2)[:300]}")

        if not clicked:
            browser.close()
            return

        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state('load', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        log.info(f"[post-guest-login] webshell.asp responses captured ({len(webshell_bodies)}):")
        for status, url, ct, body in webshell_bodies:
            log.info(f"  status={status} url={url} content-type={ct!r}")
            log.info(f"  body ({len(body)} chars): {body[:4000]!r}")

        log.info(f"[post-guest-login] Final top URL: {page.url!r} title={page.title()!r}")
        try:
            page.screenshot(path='ors_after_guest_login.png', full_page=True)
            log.info("Saved screenshot -> ors_after_guest_login.png")
        except Exception as e:
            log.info(f"post-login screenshot FAILED: {str(e)[:200]}")

        frames2 = dump_frames(page)
        for i, fr in enumerate(frames2):
            label = f"post-login-frame[{i}]:{fr.url}"
            try:
                fr.wait_for_load_state('load', timeout=8000)
            except Exception:
                pass
            check_structure(fr, label)
            safe_body_text(fr, label, limit=5000)
            dump_form(fr, label)
            try:
                fr_html = fr.content()
                fname = f'ors_postlogin_frame_{i}.html'
                with open(fname, 'w') as f:
                    f.write(fr_html)
                log.info(f"[{label}] saved raw HTML ({len(fr_html)} chars) -> {fname}")
            except Exception as e:
                log.info(f"[{label}] frame.content() FAILED: {str(e)[:200]}")

        browser.close()


if __name__ == '__main__':
    main()
