"""
debug_duplicate_flow.py — v4: Maneja modal Duplicate + captura Move dialog.

Selectores 100% confirmados:
  - Dashboard: /manage/all-content
  - Search: input[placeholder='Search all content']
  - Clear search: button[aria-label='Clear search']
  - Card link: a[href*='/authoring/COURSE_ID']
  - Card container: ancestor::li[1]
  - Card menu btn: button[aria-label='Content menu button']
  - Menu items: [role='menuitem'] -> Duplicate, Move, Share, Delete, etc.
  - Duplicate modal: titulo "Duplicate Course", input con nombre, botones Duplicate/Cancel
"""

import sys
import time
import re
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
    path = SCREENSHOTS_DIR / f"v4_{label}_{ts}.png"
    page.screenshot(path=str(path), full_page=False)
    print(f"  [SS] {path.name}")

def dump(page, selector, label, max_len=15000):
    try:
        html = page.locator(selector).first.inner_html()
        if len(html) > max_len:
            html = html[:max_len] + "\n...(truncado)"
        out = SCREENSHOTS_DIR / f"v4_dom_{label}.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [DOM] {out.name} ({len(html)} chars)")
    except Exception as e:
        print(f"  [DOM ERR] {label}: {e}")

def show(page, desc, sels):
    print(f"\n  --- {desc} ---")
    for sel in sels:
        try:
            els = page.locator(sel)
            count = els.count()
            if count == 0:
                continue
            texts = []
            for i in range(min(count, 8)):
                try:
                    e = els.nth(i)
                    if e.is_visible(timeout=300):
                        t = e.inner_text()[:100].strip().replace("\n", " | ")
                        if t:
                            texts.append(t)
                except:
                    pass
            print(f"    {sel} -> count={count}")
            for t in texts[:5]:
                print(f"      '{t}'")
        except:
            pass
    print()

def dismiss_cookies(page):
    for sel in ["button.osano-cm-accept-all", "button:has-text('Accept All')"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(0.5)
                return
        except:
            pass

def main():
    from playwright.sync_api import sync_playwright

    NEW_COURSE_NAME = "TEST_AUTOMATIZACION_DEBUG"

    print("=" * 60)
    print("DEBUG v4: Modal Duplicate + Move to folder")
    print("=" * 60)

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False, slow_mo=50, args=config.BROWSER_ARGS)
    ctx = browser.new_context(
        viewport=config.BROWSER_VIEWPORT, locale=config.BROWSER_LOCALE,
        timezone_id=config.BROWSER_TIMEZONE,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    )
    ctx.set_default_timeout(30000)
    page = ctx.new_page()

    try:
        # ── Login ──────────────────────────────────────────────────────────
        print("\n[1] Login...")
        page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        time.sleep(2)
        dismiss_cookies(page)
        page.locator("input[name='username'], input[type='email']").first.fill(config.EMAIL)
        page.locator("button[type='submit']").first.click()
        time.sleep(1)
        try:
            page.wait_for_selector("input[type='password']", timeout=15000)
        except:
            pass
        page.locator("input[type='password']").first.fill(config.PASSWORD)
        page.locator("button[type='submit']").last.click()
        page.wait_for_url("**/rise.articulate.com/**", timeout=60000)
        time.sleep(3)
        dismiss_cookies(page)
        time.sleep(2)
        print(f"  [OK] URL: {page.url}")

        # ── Buscar plantilla ──────────────────────────────────────────────
        print("\n[2] Buscando 'PLANTILLA'...")
        search = page.locator("input[placeholder='Search all content']")
        search.click()
        search.fill("PLANTILLA")
        time.sleep(4)

        # Encontrar tarjeta por course_id
        course_id = re.search(r"/authoring/([a-zA-Z0-9_\-]+)", config.TEMPLATE_URL).group(1)
        target = page.locator(f"a[href*='{course_id}']").first
        target.wait_for(state="visible", timeout=5000)
        print(f"  [OK] Tarjeta: '{target.inner_text()}'")

        # ── Hover + menu + Duplicate ──────────────────────────────────────
        print("\n[3] Hover + menu + Duplicate...")
        card = target.locator("xpath=ancestor::li[1]").first
        card.hover()
        time.sleep(1)

        card.locator("button[aria-label='Content menu button']").first.click()
        time.sleep(1)

        page.locator("[role='menuitem']:has-text('Duplicate')").first.click()
        time.sleep(1.5)
        ss(page, "01_duplicate_modal")

        # ── Manejar modal "Duplicate Course" ─────────────────────────────
        print("\n[4] Manejando modal Duplicate Course...")

        # Capturar estructura del modal
        dump(page, "[role='dialog'], [class*='modal']", "duplicate_modal")
        show(page, "Modal Duplicate", [
            "[role='dialog']",
            "[role='dialog'] input",
            "[role='dialog'] button",
            "[class*='modal'] input",
            "[class*='modal'] button",
        ])

        # Buscar el input del nombre
        modal_input = None
        input_sels = [
            "[role='dialog'] input[type='text']",
            "[class*='modal'] input[type='text']",
            "[role='dialog'] input",
            "[class*='modal'] input",
            "input[value*='Copy of']",
        ]
        for sel in input_sels:
            try:
                inp = page.locator(sel).first
                if inp.is_visible(timeout=2000):
                    val = inp.input_value()
                    print(f"  [OK] Input encontrado: '{val}' (selector: {sel})")
                    modal_input = inp
                    break
            except:
                pass

        if modal_input:
            # Limpiar y escribir el nuevo nombre
            modal_input.click()
            modal_input.fill("")
            modal_input.fill(NEW_COURSE_NAME)
            time.sleep(0.5)
            print(f"  [OK] Nombre cambiado a: '{NEW_COURSE_NAME}'")
            ss(page, "02_modal_name_filled")

            # Click en boton "Duplicate" del modal
            dup_btn_sels = [
                "[role='dialog'] button:has-text('Duplicate')",
                "[class*='modal'] button:has-text('Duplicate')",
                "button:has-text('Duplicate'):visible",
            ]
            for sel in dup_btn_sels:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        print(f"  [OK] Click en boton Duplicate del modal")
                        break
                except:
                    pass

            # Esperar a que se cierre el modal y se cree el duplicado
            print("  Esperando creacion del duplicado...")
            time.sleep(8)
            ss(page, "03_after_modal_duplicate")
            print(f"  URL: {page.url}")

            # ── Buscar el duplicado creado ─────────────────────────────────
            print(f"\n[5] Buscando curso '{NEW_COURSE_NAME}'...")

            # Limpiar busqueda
            clear_btn = page.locator("button[aria-label='Clear search']")
            try:
                clear_btn.click(timeout=5000)
            except:
                # Si el modal overlay bloquea, intentar con Escape
                page.keyboard.press("Escape")
                time.sleep(1)
                try:
                    clear_btn.click(timeout=5000)
                except:
                    pass

            time.sleep(2)

            # Buscar el nuevo curso
            search = page.locator("input[placeholder='Search all content']")
            search.click()
            search.fill(NEW_COURSE_NAME)
            time.sleep(4)
            ss(page, "04_search_new_course")

            # Verificar que aparece
            new_links = page.locator("a[href*='/authoring/']")
            new_count = new_links.count()
            print(f"  Resultados: {new_count} cursos")
            for i in range(min(new_count, 5)):
                try:
                    l = new_links.nth(i)
                    if l.is_visible(timeout=300):
                        print(f"    [{i}] '{l.inner_text()[:80]}'")
                except:
                    pass

            if new_count > 0:
                # ── Hover + menu + Move ───────────────────────────────────
                print(f"\n[6] Menu del duplicado -> Move...")
                new_link = new_links.first
                new_card = new_link.locator("xpath=ancestor::li[1]").first
                new_card.hover()
                time.sleep(1)

                new_card.locator("button[aria-label='Content menu button']").first.click()
                time.sleep(1)
                ss(page, "05_new_course_menu")

                # Click en "Move"
                page.locator("[role='menuitem']:has-text('Move')").first.click()
                time.sleep(2)
                ss(page, "06_move_dialog")

                # ── Capturar dialogo Move ─────────────────────────────────
                print("\n[7] Inspeccionando dialogo Move...")
                dump(page, "[role='dialog'], [class*='modal'], body", "move_dialog")

                show(page, "Dialogo Move", [
                    "[role='dialog']",
                    "[role='dialog'] button",
                    "[role='tree']",
                    "[role='treeitem']",
                    "[class*='folder']",
                    "[class*='tree']",
                    "[class*='breadcrumb']",
                    "button:has-text('Move')",
                    "button:has-text('Cancel')",
                ])

                # Buscar carpetas visibles
                print("\n  --- Carpetas/elementos visibles en Move dialog ---")
                all_text_in_dialog = ""
                try:
                    dialog = page.locator("[role='dialog']").first
                    if dialog.is_visible(timeout=2000):
                        all_text_in_dialog = dialog.inner_text()
                        print(f"  Texto del dialog:\n{all_text_in_dialog[:500]}")
                except:
                    pass

                # Buscar Private
                for txt in ["Private", "Automatizaciones", "Personal", "All Content", "Shared"]:
                    try:
                        el = page.locator(f"[role='dialog'] :text('{txt}')").first
                        if el.is_visible(timeout=1000):
                            print(f"  [VISIBLE] '{txt}'")
                        else:
                            print(f"  [HIDDEN] '{txt}'")
                    except:
                        print(f"  [NOT FOUND] '{txt}'")

                # Intentar click en Private
                try:
                    private_el = page.locator("[role='dialog'] :text('Private')").first
                    if private_el.is_visible(timeout=2000):
                        private_el.click()
                        time.sleep(2)
                        ss(page, "07_private_folder")
                        print("  [OK] Click en Private")

                        # Buscar Automatizaciones dentro de Private
                        try:
                            auto_el = page.locator("[role='dialog'] :text('Automatizaciones')").first
                            if auto_el.is_visible(timeout=2000):
                                auto_el.click()
                                time.sleep(1)
                                ss(page, "08_automatizaciones")
                                print("  [OK] Click en Automatizaciones")
                            else:
                                print("  [WARN] Automatizaciones no visible")
                                # Listar todo lo visible en el dialog ahora
                                show(page, "Contenido de Private", [
                                    "[role='dialog'] [role='treeitem']",
                                    "[role='dialog'] li",
                                    "[role='dialog'] button",
                                ])
                        except Exception as e:
                            print(f"  [ERR] {e}")

                        # Click en boton Move para confirmar
                        try:
                            move_btn = page.locator("[role='dialog'] button:has-text('Move')").last
                            if move_btn.is_visible(timeout=2000):
                                ss(page, "09_before_move_confirm")
                                move_btn.click()
                                time.sleep(3)
                                print("  [OK] Move confirmado!")
                                ss(page, "10_after_move")
                        except:
                            pass
                except:
                    pass

            # ── Limpiar: borrar el curso de test ──────────────────────────
            print(f"\n[8] Limpieza: borrando '{NEW_COURSE_NAME}'...")
            # Re-buscar
            try:
                clear_btn = page.locator("button[aria-label='Clear search']")
                if clear_btn.is_visible(timeout=3000):
                    clear_btn.click()
                    time.sleep(1)
            except:
                pass

            search = page.locator("input[placeholder='Search all content']")
            search.click()
            search.fill(NEW_COURSE_NAME)
            time.sleep(3)

            del_links = page.locator("a[href*='/authoring/']")
            if del_links.count() > 0:
                del_card = del_links.first.locator("xpath=ancestor::li[1]").first
                del_card.hover()
                time.sleep(1)
                del_card.locator("button[aria-label='Content menu button']").first.click()
                time.sleep(1)
                page.locator("[role='menuitem']:has-text('Delete')").first.click()
                time.sleep(1)
                ss(page, "11_delete_confirm")

                # Confirmar eliminacion
                try:
                    page.locator("button:has-text('Delete')").last.click()
                    time.sleep(2)
                    print("  [OK] Curso de test eliminado")
                except:
                    print("  [WARN] No se pudo confirmar eliminacion")

        else:
            print("  [FAIL] No se encontro el curso duplicado")

        # ── Resumen ───────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("DEBUG v4 COMPLETADO")
        print("=" * 60)
        print(f"  URL: {page.url}")

        print("\n  Navegador abierto 20s...")
        time.sleep(20)

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
