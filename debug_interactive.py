"""
debug_interactive.py — Observador DOM interactivo para Rise 360

Abre un browser logueado y navega a una URL. Luego entra en modo observador:
- Cada 2s captura un snapshot del DOM (selectores clave)
- Detecta CAMBIOS: nuevos elementos, modals, panels, inputs
- El usuario interactúa manualmente con Rise 360
- El script loguea exactamente qué selectores aparecieron/desaparecieron

Uso:
  python debug_interactive.py

El script guiará al usuario por 3 pruebas:
  1) Editar flashcards (cómo se editan front/back)
  2) Duplicar/crear lecciones en el outline
  3) Agregar bloques dentro de una lección
"""

import json
import time
import sys
import re
import threading
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from playwright.sync_api import sync_playwright

import config


def _ask_gui(title: str, message: str) -> bool:
    """Show a Yes/No dialog. Returns True for Yes, False for No/Skip."""
    result = [False]
    def _show():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        answer = messagebox.askyesno(title, message, parent=root)
        result[0] = answer
        root.destroy()
    t = threading.Thread(target=_show)
    t.start()
    t.join()
    return result[0]


def _notify_gui(title: str, message: str):
    """Show an OK dialog (blocking until user clicks OK)."""
    def _show():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message, parent=root)
        root.destroy()
    t = threading.Thread(target=_show)
    t.start()
    t.join()

HUMAN_COURSE = "https://rise.articulate.com/authoring/UQRGGAk6oiAl4-M3bvjLnTpynuEIgpmI"
SCRIPT_COURSE = "https://rise.articulate.com/authoring/fI7zDtX-AlTgV-F5UHfchFMXic1MLEjj"

OUTPUT_DIR = Path("data/debug_captures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def login(page):
    """Login to Rise 360."""
    page.goto(config.RISE_BASE_URL)
    time.sleep(3)
    try:
        cookie_btn = page.locator("button.osano-cm-accept-all")
        if cookie_btn.is_visible(timeout=3000):
            cookie_btn.click()
            time.sleep(1)
    except Exception:
        pass
    try:
        email_input = page.locator("input[name='username'], input[type='email'], #email")
        email_input.first.wait_for(state="visible", timeout=10000)
        email_input.first.fill(config.EMAIL)
        time.sleep(0.5)
        page.locator("button[type='submit']").first.click()
        time.sleep(3)
    except Exception as e:
        print(f"  Email error: {e}")
    try:
        pwd_input = page.locator("input[name='password'], input[type='password']")
        pwd_input.first.wait_for(state="visible", timeout=10000)
        pwd_input.first.fill(config.PASSWORD)
        time.sleep(0.5)
        page.locator("button[type='submit']").first.click()
        time.sleep(5)
    except Exception as e:
        print(f"  Password error: {e}")
    page.wait_for_url("**/rise.articulate.com/**", timeout=30000)
    print("  Login OK")


def capture_dom_snapshot(page) -> dict:
    """Capture key DOM elements that might be relevant."""
    snapshot = {}

    # Key selectors to monitor
    selectors = {
        "contenteditable": "[contenteditable='true']",
        "modals": "[role='dialog'], [class*='modal']",
        "panels": "[class*='panel'], [class*='sidebar'], [class*='drawer']",
        "inputs": "input:visible, textarea:visible",
        "buttons_visible": "button:visible",
        "menus": "[role='menu'], [role='menuitem'], [role='listbox']",
        "flashcard_elements": "[class*='flashcard']",
        "block_wrappers": "[class*='block-wrapper']",
        "block_create": "[class*='block-create']",
        "outline_lessons": "[class*='outline-lesson'], [class*='course-outline']",
        "add_buttons": "button[class*='add'], button:has-text('Add'), [class*='add-lesson']",
        "kebab_menus": "button[aria-label*='menu' i], button[aria-label*='option' i], [class*='kebab'], [class*='more-options']",
        "settings_panels": "[class*='settings'], [class*='config'], [class*='editor-panel']",
        "tiptap_editors": ".tiptap, .ProseMirror, .rise-tiptap",
        "tabs": "[role='tab'], [role='tablist'], [role='tabpanel']",
        "popover": "[class*='popover'], [class*='tooltip'], [class*='dropdown']",
    }

    for name, sel in selectors.items():
        try:
            els = page.locator(sel)
            count = els.count()
            items = []
            for i in range(min(count, 20)):  # Max 20 per category
                try:
                    el = els.nth(i)
                    if el.is_visible(timeout=200):
                        info = {
                            "tag": el.evaluate("el => el.tagName"),
                            "class": (el.get_attribute("class") or "")[:200],
                            "role": el.get_attribute("role") or "",
                            "aria_label": el.get_attribute("aria-label") or "",
                            "text": el.inner_text()[:100].strip().replace("\n", " | "),
                            "type": el.get_attribute("type") or "",
                        }
                        # For inputs, get value
                        if info["tag"] in ("INPUT", "TEXTAREA"):
                            try:
                                info["value"] = el.input_value()[:100]
                            except Exception:
                                pass
                        items.append(info)
                except Exception:
                    pass
            snapshot[name] = {"count": count, "visible": len(items), "items": items}
        except Exception:
            snapshot[name] = {"count": 0, "visible": 0, "items": []}

    return snapshot


def diff_snapshots(before: dict, after: dict) -> dict:
    """Find what changed between two DOM snapshots."""
    changes = {}
    all_keys = set(list(before.keys()) + list(after.keys()))

    for key in all_keys:
        b = before.get(key, {"count": 0, "visible": 0, "items": []})
        a = after.get(key, {"count": 0, "visible": 0, "items": []})

        if b["count"] != a["count"] or b["visible"] != a["visible"]:
            changes[key] = {
                "before": f"{b['visible']}/{b['count']}",
                "after": f"{a['visible']}/{a['count']}",
                "delta_count": a["count"] - b["count"],
                "delta_visible": a["visible"] - b["visible"],
            }

            # Show new items
            if a["visible"] > b["visible"]:
                # Items in 'after' not in 'before'
                b_texts = {i.get("text", "") for i in b["items"]}
                new_items = [i for i in a["items"] if i.get("text", "") not in b_texts]
                if new_items:
                    changes[key]["new_items"] = new_items[:5]

    return changes


def print_changes(changes: dict, label: str = ""):
    """Pretty-print DOM changes."""
    if not changes:
        print(f"  [{label}] Sin cambios detectados")
        return

    print(f"\n  [{label}] CAMBIOS DETECTADOS:")
    for key, info in changes.items():
        delta_v = info.get("delta_visible", 0)
        delta_c = info.get("delta_count", 0)
        marker = "+++" if delta_v > 0 else "---"
        print(f"    {marker} {key}: {info['before']} -> {info['after']}")
        if "new_items" in info:
            for item in info["new_items"]:
                cls = item.get("class", "")[:80]
                text = item.get("text", "")[:60]
                role = item.get("role", "")
                aria = item.get("aria_label", "")
                print(f"        NEW: <{item['tag']}> class='{cls}' role='{role}' aria='{aria}'")
                if text:
                    print(f"             text='{text}'")


def observe_loop(page, test_name: str, interval: float = 2.0):
    """
    Main observation loop. Takes snapshots every interval seconds.
    Prints changes when detected. Runs until user clicks OK on dialog.
    """
    print(f"\n{'='*60}")
    print(f"  OBSERVANDO: {test_name}")
    print(f"  Haz lo que necesites en el browser.")
    print(f"  Cuando termines, haz clic en OK en el dialogo que aparecera.")
    print(f"{'='*60}\n")

    baseline = capture_dom_snapshot(page)
    prev_snapshot = baseline
    all_changes = []
    capture_num = 0

    user_done = threading.Event()

    def wait_for_done_click():
        """Show a blocking dialog — when user clicks OK, set the event."""
        def _show():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            messagebox.showinfo(
                f"Observando: {test_name}",
                f"Interactua con Rise 360 en el browser.\n\n"
                f"Cuando termines, haz clic en OK.",
                parent=root
            )
            root.destroy()
            user_done.set()
        _show()

    done_thread = threading.Thread(target=wait_for_done_click, daemon=True)
    done_thread.start()

    while not user_done.is_set():
        time.sleep(interval)
        try:
            current = capture_dom_snapshot(page)
            changes = diff_snapshots(prev_snapshot, current)
            if changes:
                capture_num += 1
                print_changes(changes, f"capture-{capture_num}")
                all_changes.append({
                    "capture": capture_num,
                    "timestamp": time.strftime("%H:%M:%S"),
                    "changes": changes,
                    "full_snapshot": current,
                })
                prev_snapshot = current
        except Exception as e:
            print(f"  Error en captura: {e}")

    # Save all captures
    output_file = OUTPUT_DIR / f"debug_{test_name.replace(' ', '_')}.json"
    save_data = {
        "test": test_name,
        "baseline": baseline,
        "captures": all_changes,
        "total_captures": len(all_changes),
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Datos guardados: {output_file}")
    print(f"  Total cambios detectados: {len(all_changes)}")

    # Print summary of changes vs baseline
    final = capture_dom_snapshot(page)
    total_changes = diff_snapshots(baseline, final)
    if total_changes:
        print(f"\n  RESUMEN: cambios totales vs baseline:")
        print_changes(total_changes, "TOTAL")

    return all_changes


def run_test_flashcards(page):
    """Test 0.1: Navigate to lesson with flashcards, user edits them."""
    print("\n" + "=" * 60)
    print("TEST 0.1: EDITAR FLASHCARDS")
    print("=" * 60)
    print("Voy a abrir una lección con flashcards del curso humano.")
    print("Cuando esté listo, haz clic en una flashcard y edítala.")
    print("Yo capturaré los cambios en el DOM.\n")

    # Navigate to human course, open lesson 1 (has flashcards at block 3)
    page.goto(HUMAN_COURSE, wait_until="domcontentloaded")
    time.sleep(5)

    # Wait for content to load
    try:
        page.wait_for_selector("a", timeout=30000)
        time.sleep(3)
    except Exception:
        pass

    # Open lesson 1 (second Edit Content link)
    edit_links = page.locator("a:has-text('Edit Content')")
    count = edit_links.count()
    print(f"  Edit Content links: {count}")
    if count >= 2:
        edit_links.nth(1).click()
        time.sleep(5)
        try:
            page.wait_for_selector("[class*='block-wrapper']", timeout=30000)
            time.sleep(2)
        except Exception:
            pass
        print("  Lección 1 abierta (tiene flashcards en bloque 3)")
    else:
        print("  ERROR: No encontré suficientes lecciones")
        return

    # Take screenshot before
    page.screenshot(path=str(OUTPUT_DIR / "flashcard_before.png"))
    print("  Screenshot guardado: flashcard_before.png")
    print("\n  AHORA: Haz clic en las flashcards y edítalas.")
    print("  Cuando termines, haz clic en OK en el dialogo.\n")

    return observe_loop(page, "flashcards")


def run_test_lessons(page):
    """Test 0.2: Navigate to outline, user duplicates/adds lesson."""
    print("\n" + "=" * 60)
    print("TEST 0.2: DUPLICAR/CREAR LECCIONES")
    print("=" * 60)
    print("Voy a abrir el outline del curso del script.")
    print("Cuando esté listo, duplica una lección o agrega una nueva.")
    print("Yo capturaré los cambios en el DOM.\n")

    page.goto(SCRIPT_COURSE, wait_until="domcontentloaded")
    time.sleep(5)
    try:
        page.wait_for_selector("a", timeout=30000)
        time.sleep(3)
    except Exception:
        pass

    page.screenshot(path=str(OUTPUT_DIR / "outline_before.png"))
    print("  Outline abierto. Screenshot guardado: outline_before.png")
    print("\n  AHORA: Duplica una lección o agrega una nueva.")
    print("  Cuando termines, haz clic en OK en el dialogo.\n")

    return observe_loop(page, "lesson_creation")


def run_test_blocks(page):
    """Test 0.3: Open lesson editor, user adds a new block."""
    print("\n" + "=" * 60)
    print("TEST 0.3: AGREGAR BLOQUES")
    print("=" * 60)
    print("Voy a abrir el editor de la lección 1 del curso del script.")
    print("Cuando esté listo, agrega un bloque nuevo (heading, text, etc.)")
    print("Yo capturaré los cambios en el DOM.\n")

    page.goto(SCRIPT_COURSE, wait_until="domcontentloaded")
    time.sleep(5)
    try:
        page.wait_for_selector("a", timeout=30000)
        time.sleep(3)
    except Exception:
        pass

    # Open lesson 0
    edit_links = page.locator("a:has-text('Edit Content')")
    if edit_links.count() > 0:
        edit_links.nth(0).click()
        time.sleep(5)
        try:
            page.wait_for_selector("[class*='block-wrapper']", timeout=30000)
            time.sleep(2)
        except Exception:
            pass
        print("  Editor de lección 0 abierto")
    else:
        print("  ERROR: No encontré Edit Content links")
        return

    page.screenshot(path=str(OUTPUT_DIR / "blocks_before.png"))
    print("  Screenshot guardado: blocks_before.png")
    print("\n  AHORA: Agrega un bloque nuevo (busca el '+' entre bloques).")
    print("  Cuando termines, haz clic en OK en el dialogo.\n")

    return observe_loop(page, "block_addition")


def main():
    print("=" * 60)
    print("DEBUG INTERACTIVO — Rise 360 DOM Observer")
    print("=" * 60)
    print()
    print("Este script abre el browser y observa los cambios en el DOM")
    print("mientras tú interactúas manualmente con Rise 360.")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=30,
            args=config.BROWSER_ARGS,
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale=config.BROWSER_LOCALE,
            timezone_id=config.BROWSER_TIMEZONE,
        )
        context.set_default_timeout(30000)
        page = context.new_page()

        print("\n[Login]")
        login(page)

        # Run tests sequentially
        tests = [
            ("0.1 Flashcards", run_test_flashcards),
            ("0.2 Lecciones", run_test_lessons),
            ("0.3 Bloques", run_test_blocks),
        ]

        for test_name, test_fn in tests:
            print(f"\n{'#'*60}")
            print(f"  ¿Listo para {test_name}?")
            print(f"{'#'*60}")
            proceed = _ask_gui(
                f"Test {test_name}",
                f"¿Listo para {test_name}?\n\nSi = Continuar\nNo = Saltar"
            )
            if not proceed:
                print(f"  Saltando {test_name}")
                continue
            test_fn(page)

        print("\n" + "=" * 60)
        print("TODAS LAS PRUEBAS COMPLETADAS")
        print("=" * 60)
        print(f"Archivos guardados en: {OUTPUT_DIR}/")
        print("Revisa los JSON para los selectores capturados.")

        browser.close()


if __name__ == "__main__":
    main()
