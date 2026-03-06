"""
debug_dom.py — Inspecciona el DOM real de Rise 360 para encontrar selectores correctos.
Toma screenshots y vuelca los elementos clave del editor.
Ejecutar: python debug_dom.py
"""

import sys
import json
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from utils import logger, take_screenshot

from playwright.sync_api import sync_playwright


def _dismiss_cookies(page):
    """Acepta/cierra el popup de cookies de Osano si está presente."""
    cookie_sels = [
        "button.osano-cm-accept-all",
        "button.osano-cm-denyAll",
        "button:has-text('Aceptar todo')",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "[data-testid='accept-cookies']",
    ]
    for sel in cookie_sels:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2_000):
                btn.click()
                print(f"  [Cookie] Popup cerrado con: {sel}")
                time.sleep(0.5)
                return
        except Exception:
            pass


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=100,
            args=config.BROWSER_ARGS,
        )
        ctx = browser.new_context(
            viewport=config.BROWSER_VIEWPORT,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()

        # ── Login ──────────────────────────────────────────────────────
        print("Haciendo login...")
        page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("input[name='username'], input[type='email']", timeout=30_000)
        page.locator("input[name='username'], input[type='email']").first.fill(config.EMAIL)
        page.locator("button[type='submit']").first.click()
        page.wait_for_selector("input[name='password'], input[type='password']", timeout=15_000)
        page.locator("input[name='password'], input[type='password']").first.fill(config.PASSWORD)
        page.locator("button[type='submit']").last.click()
        page.wait_for_url("**/rise.articulate.com/**", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass  # SPA con conexiones persistentes — normal
        print("Login OK")
        take_screenshot(page, label="debug_01_dashboard")

        # ── Abrir la plantilla ─────────────────────────────────────────
        print(f"Abriendo plantilla: {config.TEMPLATE_URL}")
        # Aceptar cookies si aparece el popup de Osano
        _dismiss_cookies(page)

        page.goto(config.TEMPLATE_URL, wait_until="domcontentloaded")
        time.sleep(4)

        # Aceptar cookies de nuevo si reaparecen en el editor
        _dismiss_cookies(page)
        time.sleep(4)

        take_screenshot(page, label="debug_02_template_opened")
        print(f"URL actual: {page.url}")

        # ── Dump de TODOS los botones visibles ─────────────────────────
        print("\n=== BOTONES VISIBLES ===")
        buttons = page.locator("button").all()
        for btn in buttons[:40]:
            try:
                txt = btn.inner_text()[:60].strip().replace("\n", " ")
                aria = btn.get_attribute("aria-label") or ""
                cls = btn.get_attribute("class") or ""
                data = {k: btn.get_attribute(k) for k in ["data-testid", "data-type", "data-block-type"] if btn.get_attribute(k)}
                if txt or aria:
                    print(f"  BUTTON | text='{txt}' | aria='{aria}' | class='{cls[:60]}' | data={data}")
            except Exception:
                pass

        # ── Dump de elementos con 'add' o 'block' en atributos ────────
        print("\n=== ELEMENTOS CON 'add' / 'block' / 'lesson' ===")
        for sel in [
            "[class*='add']", "[class*='block']", "[class*='lesson']",
            "[data-testid*='add']", "[data-testid*='block']",
            "[aria-label*='add' i]", "[aria-label*='block' i]",
            "[aria-label*='Add' i]",
        ]:
            try:
                els = page.locator(sel).all()
                for el in els[:5]:
                    tag = el.evaluate("e => e.tagName")
                    txt = el.inner_text()[:50].strip().replace("\n", " ")
                    aria = el.get_attribute("aria-label") or ""
                    cls = (el.get_attribute("class") or "")[:80]
                    data_id = el.get_attribute("data-testid") or ""
                    print(f"  [{sel}] <{tag}> text='{txt}' aria='{aria}' class='{cls}' testid='{data_id}'")
            except Exception:
                pass

        # ── Scroll hasta el final de la lección y screenshot ──────────
        print("\nScrollando al final de la lección...")
        page.keyboard.press("End")
        time.sleep(1)
        page.keyboard.press("End")
        time.sleep(1)
        take_screenshot(page, label="debug_03_bottom_lesson")

        # ── Dump del HTML de la zona editable ─────────────────────────
        print("\n=== ESTRUCTURA HTML DEL EDITOR (primeros 3000 chars) ===")
        try:
            html = page.evaluate("""() => {
                const editor = document.querySelector('.lesson-content, .course-content, [data-lesson], .authoring-content, main');
                return editor ? editor.innerHTML.substring(0, 3000) : 'NO ENCONTRADO';
            }""")
            print(html)
        except Exception as e:
            print(f"Error: {e}")

        # ── Buscar el botón + específicamente ─────────────────────────
        print("\n=== BUSCANDO BOTÓN '+' ===")
        plus_selectors = [
            "button:has-text('+')",
            "button:has-text('Add')",
            "button:has-text('Agregar')",
            "[title*='Add']",
            "[title*='add']",
            "svg[class*='plus']",
            "[class*='plus']",
            "[class*='insert']",
            "[class*='adder']",
        ]
        for sel in plus_selectors:
            try:
                count = page.locator(sel).count()
                if count > 0:
                    el = page.locator(sel).first
                    txt = el.inner_text()[:40].strip()
                    aria = el.get_attribute("aria-label") or ""
                    cls = (el.get_attribute("class") or "")[:80]
                    print(f"  FOUND [{sel}] count={count} text='{txt}' aria='{aria}' class='{cls}'")
            except Exception:
                pass

        # ── Hacer hover en medio de la pantalla para revelar controles ─
        print("\nHovering en el centro de la pantalla...")
        page.mouse.move(960, 540)
        time.sleep(1)
        page.mouse.move(960, 400)
        time.sleep(1)
        take_screenshot(page, label="debug_04_hover_middle")

        # Re-buscar después del hover
        print("\n=== POST-HOVER: botones nuevos visibles ===")
        buttons_after = page.locator("button:visible").all()
        for btn in buttons_after[:30]:
            try:
                txt = btn.inner_text()[:60].strip().replace("\n", " ")
                aria = btn.get_attribute("aria-label") or ""
                cls = (btn.get_attribute("class") or "")[:60]
                if "add" in cls.lower() or "add" in aria.lower() or "+" in txt:
                    print(f"  >> BUTTON | text='{txt}' | aria='{aria}' | class='{cls}'")
            except Exception:
                pass

        # ── Dump del URL actual (quizás es el editor read-only) ────────
        print(f"\nURL final: {page.url}")
        print("\nscreenshots guardados en:", config.SCREENSHOTS_DIR)

        # ── Entrar en la primera lección del outline ──────────────────
        print("\n=== INTENTANDO ENTRAR EN UNA LECCIÓN ===")
        # En el outline, las lecciones son links o divs clickeables
        lesson_sels = [
            ".course-outline-lesson__title",
            ".course-outline-lesson a",
            ".authoring-lesson-header",
            "[class*='lesson-title']",
            "[class*='outline-item'] a",
        ]
        lesson_clicked = False
        for sel in lesson_sels:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2_000):
                    txt = el.inner_text()[:60].strip()
                    print(f"  Haciendo click en lección: '{txt}' (selector: {sel})")
                    el.click()
                    time.sleep(3)
                    lesson_clicked = True
                    break
            except Exception:
                pass

        if not lesson_clicked:
            # Intentar click en el primer link de la página
            print("  Intentando primer link de lección...")
            links = page.locator("a[href*='lesson'], a[href*='authoring']").all()
            for link in links[:5]:
                try:
                    href = link.get_attribute("href") or ""
                    txt = link.inner_text()[:40]
                    print(f"  Link: href='{href}' text='{txt}'")
                except Exception:
                    pass

        take_screenshot(page, label="debug_05_lesson_editor")
        print(f"URL tras click en lección: {page.url}")

        # ── Dump del editor de lección ─────────────────────────────────
        print("\n=== DOM DEL EDITOR DE LECCIÓN ===")
        # Buscar todos los elementos con 'block' en la clase
        for sel in [
            "[class*='block']", "[class*='tiptap']", "[class*='lesson-block']",
            "[contenteditable='true']", ".fr-view",
            "[class*='add-block']", "[class*='insert-block']",
        ]:
            try:
                els = page.locator(sel).all()
                if els:
                    print(f"\n  Selector '{sel}': {len(els)} elementos")
                    for el in els[:4]:
                        try:
                            tag = el.evaluate("e => e.tagName")
                            cls = (el.get_attribute("class") or "")[:100]
                            aria = el.get_attribute("aria-label") or ""
                            testid = el.get_attribute("data-testid") or ""
                            inner = el.inner_text()[:60].strip().replace("\n", " ")
                            print(f"    <{tag}> class='{cls}' aria='{aria}' testid='{testid}' text='{inner}'")
                        except Exception:
                            pass
            except Exception:
                pass

        # ── Todos los botones en el editor de lección ──────────────────
        print("\n=== BOTONES EN EL EDITOR DE LECCIÓN ===")
        all_btns = page.locator("button").all()
        for btn in all_btns[:60]:
            try:
                txt = btn.inner_text()[:60].strip().replace("\n", " ")
                aria = btn.get_attribute("aria-label") or ""
                cls = (btn.get_attribute("class") or "")[:100]
                testid = btn.get_attribute("data-testid") or ""
                if any(k in (txt + aria + cls + testid).lower()
                       for k in ["add", "block", "insert", "lesson", "text", "+"]):
                    print(f"  BTN | '{txt}' | aria='{aria}' | class='{cls}' | testid='{testid}'")
            except Exception:
                pass

        # ── HTML crudo de la zona principal ───────────────────────────
        print("\n=== HTML ZONA PRINCIPAL (2000 chars) ===")
        try:
            html = page.evaluate("""() => {
                const candidates = [
                    document.querySelector('[class*=\"lesson-content\"]'),
                    document.querySelector('[class*=\"authoring-lesson\"]'),
                    document.querySelector('main'),
                    document.querySelector('[class*=\"course-content\"]'),
                ];
                const el = candidates.find(e => e !== null);
                return el ? el.className + '\\n---\\n' + el.innerHTML.substring(0, 2000) : 'NO ENCONTRADO';
            }""")
            print(html)
        except Exception as e:
            print(f"Error: {e}")

        browser.close()


if __name__ == "__main__":
    run()
