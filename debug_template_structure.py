"""
debug_template_structure.py — v3: Entra a cada lección de la plantilla y cataloga bloques.

Hallazgos previos:
  - "Edit Content" es un <a>, NO un <button>
  - 5 lecciones: Tema 1, Tema 2, Tema 3, Conclusiones, Referencias
  - Section header: "ACTIVACIÓN"
  - Carga tarda ~11s ("Your content is loading")
"""

import sys
import time
from pathlib import Path
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import config

SCREENSHOTS_DIR = config.SCREENSHOTS_DIR
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def ss(page, label):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"tpl3_{label}_{ts}.png"
    page.screenshot(path=str(path), full_page=False)
    print(f"  [SS] {path.name}")


def dump_html(page, selector, label, max_len=30000):
    try:
        el = page.locator(selector).first
        if el.is_visible(timeout=3000):
            html = el.inner_html()
            if len(html) > max_len:
                html = html[:max_len] + "\n...(truncado)"
            out = SCREENSHOTS_DIR / f"tpl3_dom_{label}.html"
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  [DOM] {out.name} ({len(html)} chars)")
    except Exception as e:
        print(f"  [DOM ERR] {label}: {e}")


def dismiss_cookies(page):
    for sel in ["button.osano-cm-accept-all", "button:has-text('Accept All')"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(0.5)
                return
        except Exception:
            pass


def wait_for_content_loaded(page, max_wait=90):
    """Espera a que desaparezca 'Your content is loading'."""
    print("  Esperando carga...")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            loading = page.locator("text='Your content is loading'")
            if loading.is_visible(timeout=500):
                elapsed = int(time.time() - start)
                if elapsed % 10 == 0:
                    print(f"    Aún cargando... ({elapsed}s)")
                time.sleep(1)
                continue
        except Exception:
            pass
        # Check for real content
        try:
            btns = page.locator("button:visible")
            if btns.count() > 3:
                print(f"  [OK] Cargado en {int(time.time()-start)}s")
                time.sleep(2)
                return True
        except Exception:
            pass
        time.sleep(1)
    print(f"  [TIMEOUT] {max_wait}s")
    return False


def main():
    from playwright.sync_api import sync_playwright

    print("=" * 70)
    print("DEBUG v3: Inspección interna de lecciones de la plantilla")
    print("=" * 70)

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False, slow_mo=50, args=config.BROWSER_ARGS)
    ctx = browser.new_context(
        viewport=config.BROWSER_VIEWPORT,
        locale=config.BROWSER_LOCALE,
        timezone_id=config.BROWSER_TIMEZONE,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    )
    ctx.set_default_timeout(60000)
    page = ctx.new_page()

    try:
        # ── Login ──────────────────────────────────────────────────────
        print("\n[1] Login...")
        page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        time.sleep(2)
        dismiss_cookies(page)
        page.locator("input[name='username'], input[type='email']").first.fill(config.EMAIL)
        page.locator("button[type='submit']").first.click()
        time.sleep(1)
        try:
            page.wait_for_selector("input[type='password']", timeout=15000)
        except Exception:
            pass
        page.locator("input[type='password']").first.fill(config.PASSWORD)
        page.locator("button[type='submit']").last.click()
        page.wait_for_url("**/rise.articulate.com/**", timeout=60000)
        time.sleep(3)
        dismiss_cookies(page)
        time.sleep(2)
        print(f"  [OK] URL: {page.url}")

        # ── Navegar a la plantilla ──────────────────────────────────────
        print("\n[2] Navegando a plantilla...")
        page.goto(config.TEMPLATE_URL, wait_until="domcontentloaded")
        wait_for_content_loaded(page)
        dismiss_cookies(page)
        time.sleep(2)
        ss(page, "01_outline")

        # ── Identificar lecciones ───────────────────────────────────────
        print("\n[3] Identificando lecciones...")

        # "Edit Content" es un <a>, NO un <button>
        edit_links = page.locator("a:has-text('Edit Content')")
        lesson_count = edit_links.count()
        print(f"  Total lecciones con 'Edit Content': {lesson_count}")

        # Obtener info de cada lección
        lessons_info = []
        for i in range(lesson_count):
            link = edit_links.nth(i)
            href = link.get_attribute("href") or ""
            # Buscar el título de la lección en el outline
            # El outline muestra: "Lesson\nTema X: Nombre del tema\nEdit Content"
            # El padre del link debería tener el nombre
            try:
                parent = link.locator("xpath=ancestor::div[contains(@class,'course-outline-lesson')][1]")
                if parent.count() > 0:
                    text = parent.first.inner_text()[:200].strip().replace("\n", " | ")
                else:
                    text = ""
            except Exception:
                text = ""
            lessons_info.append({"index": i, "href": href, "text": text})
            print(f"  [{i}] href='{href}' => '{text[:80]}'")

        # ── Entrar en cada lección y catalogar bloques ──────────────────
        for lesson in lessons_info:
            i = lesson["index"]
            print(f"\n{'='*60}")
            print(f"[4.{i}] Entrando a lección {i}: {lesson['text'][:50]}")
            print(f"{'='*60}")

            # Click en "Edit Content"
            try:
                link = edit_links.nth(i)
                link.scroll_into_view_if_needed()
                time.sleep(0.5)
                link.click()
                time.sleep(2)

                # Esperar carga del editor de lección
                wait_for_content_loaded(page, max_wait=30)
                time.sleep(2)
                ss(page, f"02_lesson_{i}")
                print(f"  URL: {page.url}")

                # ── Catalogar bloques ────────────────────────────────
                print(f"\n  --- Bloques en lección {i} ---")

                # Buscar todos los bloques visibles
                block_sels = [
                    "[data-block-type]",
                    "[class*='block-type']",
                    "[class*='lesson-block']",
                    "[class*='block-wrapper']",
                    "[class*='block-container']",
                    "[class*='content-block']",
                    "[class*='authoring-block']",
                    "[class*='block-']",
                ]
                for sel in block_sels:
                    try:
                        els = page.locator(sel)
                        count = els.count()
                        if count > 0:
                            print(f"\n    {sel} -> {count} bloques:")
                            for j in range(min(count, 15)):
                                el = els.nth(j)
                                if el.is_visible(timeout=500):
                                    cls = (el.get_attribute("class") or "")[:100]
                                    data_type = el.get_attribute("data-block-type") or ""
                                    text = el.inner_text()[:100].strip().replace("\n", " | ")
                                    tag = el.evaluate("el => el.tagName")
                                    print(f"      [{j}] <{tag} type='{data_type}' class='{cls}'> '{text[:60]}'")
                    except Exception:
                        pass

                # Buscar contenido editable
                print(f"\n  --- Editables en lección {i} ---")
                editable_sels = [
                    "[contenteditable='true']",
                    ".rise-tiptap",
                    ".tiptap",
                    ".ProseMirror",
                ]
                for sel in editable_sels:
                    try:
                        els = page.locator(sel)
                        count = els.count()
                        if count > 0:
                            print(f"    {sel} -> {count}")
                            for j in range(min(count, 10)):
                                el = els.nth(j)
                                if el.is_visible(timeout=500):
                                    text = el.inner_text()[:100].strip().replace("\n", " | ")
                                    cls = (el.get_attribute("class") or "")[:80]
                                    print(f"      [{j}] class='{cls}' text='{text[:60]}'")
                    except Exception:
                        pass

                # Buscar banners, cards, imágenes y otros elementos visuales
                print(f"\n  --- Elementos visuales en lección {i} ---")
                visual_sels = [
                    "[class*='banner']",
                    "[class*='card']",
                    "[class*='image']",
                    "[class*='video']",
                    "[class*='divider']",
                    "[class*='quote']",
                    "[class*='callout']",
                    "[class*='accordion']",
                    "[class*='tabs']",
                    "[class*='carousel']",
                    "[class*='timeline']",
                    "[class*='gallery']",
                    "[class*='embed']",
                    "img:visible",
                ]
                for sel in visual_sels:
                    try:
                        els = page.locator(sel)
                        count = els.count()
                        if count > 0:
                            for j in range(min(count, 5)):
                                el = els.nth(j)
                                if el.is_visible(timeout=500):
                                    cls = (el.get_attribute("class") or "")[:80]
                                    tag = el.evaluate("el => el.tagName")
                                    text = el.inner_text()[:60].strip().replace("\n", " | ") if tag != "IMG" else ""
                                    src = el.get_attribute("src") or "" if tag == "IMG" else ""
                                    print(f"    {sel}[{j}] <{tag} class='{cls}'> text='{text}' src='{src[:40]}'")
                    except Exception:
                        pass

                # Buscar botones "Add block" dentro de la lección
                print(f"\n  --- Botón Add block ---")
                add_sels = [
                    "button[aria-label*='add' i]",
                    "button[aria-label*='insert' i]",
                    "button:has-text('Add a block')",
                    "[class*='add-block']",
                ]
                for sel in add_sels:
                    try:
                        els = page.locator(sel)
                        count = els.count()
                        if count > 0:
                            for j in range(min(count, 3)):
                                el = els.nth(j)
                                if el.is_visible(timeout=500):
                                    cls = (el.get_attribute("class") or "")[:60]
                                    aria = el.get_attribute("aria-label") or ""
                                    print(f"    {sel}[{j}] aria='{aria}' class='{cls}'")
                    except Exception:
                        pass

                # Texto visible completo
                print(f"\n  --- Texto visible de la lección ---")
                try:
                    body_text = page.locator("main").first.inner_text()[:1500]
                    print(f"  {body_text}")
                except Exception:
                    try:
                        body_text = page.locator("body").first.inner_text()[:1500]
                        print(f"  {body_text}")
                    except Exception:
                        pass

                # Dump DOM
                dump_html(page, "main", f"lesson_{i}")

                # ── Volver al outline ────────────────────────────────
                print(f"\n  Volviendo al outline...")
                page.go_back()
                time.sleep(2)
                wait_for_content_loaded(page, max_wait=30)
                time.sleep(2)

                # Re-obtener los links porque el DOM se reconstruyó
                edit_links = page.locator("a:has-text('Edit Content')")
                new_count = edit_links.count()
                print(f"  [OK] De vuelta en outline. Links: {new_count}")

            except Exception as e:
                print(f"  [ERR] {e}")
                import traceback
                traceback.print_exc()
                # Intentar volver al outline
                try:
                    page.goto(config.TEMPLATE_URL, wait_until="domcontentloaded")
                    wait_for_content_loaded(page)
                    edit_links = page.locator("a:has-text('Edit Content')")
                except Exception:
                    pass

        # ── Resumen ─────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("DEBUG v3 COMPLETADO")
        print("=" * 70)
        print("\n  Navegador abierto 15s...")
        time.sleep(15)

    except Exception as e:
        print(f"\n  [ERROR] {e}")
        import traceback
        traceback.print_exc()
        ss(page, "error")
        time.sleep(15)
    finally:
        ctx.close()
        browser.close()
        pw.stop()
        print("  Cerrado.")


if __name__ == "__main__":
    main()
