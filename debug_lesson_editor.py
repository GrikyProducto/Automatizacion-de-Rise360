"""
debug_lesson_editor.py — Inspecciona el DOM del editor de bloques de una lección.
Hace click en "Edit Content" de la primera lección y vuelca el DOM resultante.
"""
import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import config
from utils import take_screenshot
from playwright.sync_api import sync_playwright

def dismiss_cookies(page):
    for sel in ["button.osano-cm-accept-all", "button:has-text('Aceptar todo')", "button:has-text('Accept all')"]:
        try:
            b = page.locator(sel).first
            if b.is_visible(timeout=2000):
                b.click(); time.sleep(0.5); return
        except Exception: pass

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=80, args=config.BROWSER_ARGS)
        ctx = browser.new_context(viewport=config.BROWSER_VIEWPORT,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()

        # Login
        print("Login...")
        page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        email_sel = "input[name='username'], input[type='email'], input[id*='email'], input[autocomplete*='email']"
        page.wait_for_selector(email_sel, timeout=60000)
        take_screenshot(page, label="les_00_login")
        page.locator(email_sel).first.fill(config.EMAIL)
        page.locator("button[type='submit']").first.click()
        time.sleep(2)
        pwd_sel = "input[name='password'], input[type='password']"
        try:
            page.wait_for_selector(pwd_sel, timeout=15000)
        except Exception:
            pass  # algunas versiones muestran el password en la misma pantalla
        page.locator(pwd_sel).first.fill(config.PASSWORD)
        page.locator("button[type='submit']").last.click()
        page.wait_for_url("**/rise.articulate.com/**", timeout=60000)
        time.sleep(3)
        dismiss_cookies(page)
        print("Login OK")
        take_screenshot(page, label="les_01_dashboard")

        # Abrir template
        print("Abriendo template...")
        page.goto(config.TEMPLATE_URL, wait_until="domcontentloaded")
        time.sleep(4)
        dismiss_cookies(page)
        time.sleep(2)
        take_screenshot(page, label="les_02_outline")
        print(f"URL: {page.url}")

        # Click en "Edit Content" de la primera lección
        print("\nBuscando botón 'Edit Content'...")
        edit_btns = page.locator("button:has-text('Edit Content')").all()
        print(f"  Encontrados: {len(edit_btns)} botones 'Edit Content'")
        if edit_btns:
            print("  Haciendo click en el primero...")
            edit_btns[0].click()
            time.sleep(4)
            take_screenshot(page, label="les_03_lesson_editor")
            print(f"  URL tras click: {page.url}")

            # Dump del DOM del editor de lección
            print("\n=== DOM DEL EDITOR DE BLOQUES ===")
            for sel in [
                "[contenteditable='true']",
                ".rise-tiptap",
                ".tiptap",
                ".ProseMirror",
                "[class*='block']",
                "[class*='lesson-block']",
                "[class*='add']",
                "[data-block-type]",
                ".lesson-content",
            ]:
                try:
                    els = page.locator(sel).all()
                    if els:
                        print(f"\n  '{sel}': {len(els)} elementos")
                        for el in els[:4]:
                            try:
                                tag = el.evaluate("e => e.tagName")
                                cls = (el.get_attribute("class") or "")[:100]
                                aria = el.get_attribute("aria-label") or ""
                                inner = el.inner_text()[:80].strip().replace("\n", " ")
                                print(f"    <{tag}> cls='{cls}' aria='{aria}' text='{inner}'")
                            except Exception: pass
                except Exception: pass

            print("\n=== TODOS LOS BOTONES EN EL EDITOR DE LECCIÓN ===")
            for btn in page.locator("button").all()[:50]:
                try:
                    txt = btn.inner_text()[:60].strip().replace("\n", " ")
                    aria = btn.get_attribute("aria-label") or ""
                    cls = (btn.get_attribute("class") or "")[:100]
                    testid = btn.get_attribute("data-testid") or ""
                    print(f"  BTN | '{txt}' | aria='{aria}' | cls='{cls}' | tid='{testid}'")
                except Exception: pass

            # HTML del bloque principal
            print("\n=== HTML PRINCIPAL (3000 chars) ===")
            try:
                html = page.evaluate("""() => {
                    const sels = ['[class*=\"lesson-content\"]','[class*=\"lesson-body\"]',
                        '[class*=\"block-list\"]','[class*=\"blocks\"]','main .content',
                        'main','[class*=\"authoring\"]'];
                    for(const s of sels) {
                        const el = document.querySelector(s);
                        if(el) return s+'\\n---\\n'+el.innerHTML.substring(0, 3000);
                    }
                    return 'NO ENCONTRADO';
                }""")
                print(html)
            except Exception as e:
                print(f"Error: {e}")

        # Dashboard duplication debug
        print("\n\n=== DASHBOARD — BUSCANDO TARJETA DE CURSO ===")
        page.goto(config.RISE_DASHBOARD_URL, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_cookies(page)
        time.sleep(2)
        take_screenshot(page, label="les_04_dashboard_full")

        # Encontrar la tarjeta de la plantilla
        print("Buscando tarjeta con texto 'PLANTILLA'...")
        card_sels = [
            "div[class*='card']", "li[class*='course']",
            "[class*='course-card']", "[class*='content-card']",
            "article", "[class*='grid-item']",
        ]
        for sel in card_sels:
            try:
                els = page.locator(sel).all()
                if els:
                    print(f"  Selector '{sel}': {len(els)} elementos")
                    for el in els[:3]:
                        cls = (el.get_attribute("class") or "")[:80]
                        txt = el.inner_text()[:60].strip().replace("\n", " ")
                        print(f"    cls='{cls}' text='{txt}'")
                    break
            except Exception: pass

        # Hover sobre la primera tarjeta y buscar menú
        print("\nHovering sobre primera tarjeta y buscando menú '...'")
        try:
            first_card = page.locator("[class*='card'], article, [class*='course-item']").first
            first_card.hover()
            time.sleep(1)
            take_screenshot(page, label="les_05_card_hover")
            dots_btns = page.locator("button[class*='dots'], button[class*='menu__trigger'], button[aria-label*='more' i], button[aria-label*='options' i]").all()
            print(f"  Botones '...' encontrados: {len(dots_btns)}")
            for b in dots_btns[:5]:
                aria = b.get_attribute("aria-label") or ""
                cls = (b.get_attribute("class") or "")[:80]
                print(f"    aria='{aria}' cls='{cls}'")
        except Exception as e:
            print(f"  Error: {e}")

        browser.close()
        print("\nDone.")

if __name__ == "__main__":
    run()
