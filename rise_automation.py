"""
rise_automation.py — Motor principal de automatización Playwright para Rise 360
Basado en inspección real del DOM de Rise 360 (febrero 2026).

Selectores 100% confirmados por debug:
  - Dashboard: /manage/all-content
  - Search: input[placeholder='Search all content']
  - Clear search: button[aria-label='Clear search']
  - Card link: a[href*='/authoring/COURSE_ID']
  - Card container: ancestor::li[1]
  - Card menu btn: button[aria-label='Content menu button']
  - Menu items: [role='menuitem'] -> Duplicate, Move, Share, Delete, etc.
  - Duplicate modal: [role='dialog'] input[type='text'] + button Duplicate/Cancel
  - Move dialog: [role='tree'] con carpetas, boton "Move"
  - Editor: TipTap (.tiptap.ProseMirror.rise-tiptap), NO Quill
  - Cookie popup: button.osano-cm-accept-all
"""

import time
import re
from typing import Optional, Callable
from pathlib import Path
from utils import (
    logger, with_retry, take_screenshot,
    wait_for_react_idle, safe_click, paste_large_text,
)
import config


class RiseAutomation:
    """
    Administra una sesión Playwright contra Rise 360.
    Diseñado para correr en un thread separado desde main.py.
    """

    def __init__(self, progress_callback: Optional[Callable] = None):
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._progress = progress_callback or (lambda msg, pct: None)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Lanza Playwright Chromium en modo visible."""
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=config.BROWSER_HEADLESS,
            slow_mo=config.BROWSER_SLOW_MO,
            args=config.BROWSER_ARGS,
        )
        self._context = self._browser.new_context(
            viewport=config.BROWSER_VIEWPORT,
            locale=config.BROWSER_LOCALE,
            timezone_id=config.BROWSER_TIMEZONE,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._context.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
        self.page = self._context.new_page()
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        logger.info("Navegador iniciado (Chromium visible)")

    def stop(self):
        """Cierra el navegador limpiamente."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
            logger.info("Navegador cerrado")
        except Exception as e:
            logger.warning(f"Error al cerrar navegador: {e}")

    # ── Cookies ───────────────────────────────────────────────────────────

    def dismiss_cookies(self):
        """Cierra el popup de cookies Osano si está presente."""
        for sel in [
            "button.osano-cm-accept-all",
            "button:has-text('Aceptar todo')",
            "button:has-text('Accept All')",
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    time.sleep(0.5)
                    logger.debug("Popup de cookies cerrado")
                    return
            except Exception:
                pass

    # ── Login ─────────────────────────────────────────────────────────────

    @with_retry(max_attempts=3)
    def login(self, email: str, password: str) -> bool:
        """Login en Rise 360 vía Articulate ID."""
        self._progress("Iniciando sesion en Rise 360...", 25)
        logger.info("Iniciando login")

        self.page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        time.sleep(2)
        self.dismiss_cookies()

        email_sel = "input[name='username'], input[type='email'], input[id*='email']"
        self.page.wait_for_selector(email_sel, timeout=config.NAVIGATION_TIMEOUT)
        self.page.locator(email_sel).first.clear()
        self.page.locator(email_sel).first.fill(email)
        logger.debug(f"Email ingresado: {email}")

        self.page.locator("button[type='submit']").first.click()
        time.sleep(1)

        pwd_sel = "input[name='password'], input[type='password']"
        try:
            self.page.wait_for_selector(pwd_sel, timeout=15_000)
        except Exception:
            pass

        self.page.locator(pwd_sel).first.clear()
        self.page.locator(pwd_sel).first.fill(password)
        self.page.locator("button[type='submit']").last.click()

        self.page.wait_for_url("**/rise.articulate.com/**", timeout=config.NAVIGATION_TIMEOUT)
        time.sleep(3)
        self.dismiss_cookies()
        time.sleep(2)

        take_screenshot(self.page, label="after_login")
        logger.info(f"Login exitoso. URL: {self.page.url}")
        self._progress("Sesion iniciada", 35)
        return True

    # ── Navegación ────────────────────────────────────────────────────────

    def navigate_to_dashboard(self):
        """Navega al dashboard de Rise 360 (/manage/all-content)."""
        dashboard_url = "https://rise.articulate.com/manage/all-content"
        logger.info(f"Navegando al dashboard: {dashboard_url}")
        self.page.goto(dashboard_url, wait_until="domcontentloaded")
        time.sleep(4)
        self.dismiss_cookies()
        time.sleep(2)

    def navigate_to_course_outline(self, course_url: str):
        """Navega al outline de un curso."""
        logger.info(f"Navegando al curso: {course_url}")
        self.page.goto(course_url, wait_until="domcontentloaded")
        time.sleep(6)
        self.dismiss_cookies()
        time.sleep(2)
        take_screenshot(self.page, label="course_outline")
        logger.info("Outline del curso cargado")

    # ── Búsqueda en dashboard ─────────────────────────────────────────────

    def _search_in_dashboard(self, query: str):
        """Busca un curso en el dashboard usando 'Search all content'."""
        search = self.page.locator("input[placeholder='Search all content']")
        search.click()
        time.sleep(0.3)
        search.fill(query)
        time.sleep(4)
        logger.debug(f"Busqueda realizada: '{query}'")

    def _clear_search(self):
        """Limpia el campo de busqueda."""
        try:
            clear_btn = self.page.locator("button[aria-label='Clear search']")
            if clear_btn.is_visible(timeout=3_000):
                clear_btn.click(timeout=5_000)
                time.sleep(2)
        except Exception:
            self.page.keyboard.press("Escape")
            time.sleep(1)
            try:
                search = self.page.locator("input[placeholder='Search all content']")
                search.click()
                search.fill("")
                time.sleep(1)
            except Exception:
                pass

    def _find_card_by_course_id(self, course_id: str):
        """Encuentra la tarjeta de un curso por su ID en la URL."""
        try:
            link = self.page.locator(f"a[href*='{course_id}']").first
            link.wait_for(state="visible", timeout=5_000)
            card = link.locator("xpath=ancestor::li[1]").first
            return card
        except Exception:
            return None

    def _open_card_menu(self, card) -> bool:
        """Abre el menu contextual de una tarjeta de curso."""
        card.hover()
        time.sleep(1)
        try:
            menu_btn = card.locator("button[aria-label='Content menu button']").first
            menu_btn.click(timeout=3_000)
            time.sleep(1)
            return True
        except Exception:
            try:
                menu_btn = card.locator("button[aria-label='Content menu button']").first
                menu_btn.dispatch_event("click")
                time.sleep(1)
                return True
            except Exception:
                return False

    def _click_menu_item(self, label: str) -> bool:
        """Hace click en un item del menu contextual por texto."""
        try:
            item = self.page.locator(f"[role='menuitem']:has-text('{label}')").first
            if item.is_visible(timeout=2_000):
                item.click()
                time.sleep(1)
                return True
        except Exception:
            pass
        return False

    # ── Duplicación de plantilla ──────────────────────────────────────────

    @with_retry(max_attempts=3)
    def duplicate_template(self, template_url: str, new_title: str) -> str:
        """
        Duplica el curso de plantilla en Rise 360.

        Flujo confirmado:
        1. Dashboard -> Buscar "PLANTILLA"
        2. Hover tarjeta -> Content menu button -> Duplicate
        3. Modal: llenar nombre -> click Duplicate
        4. Buscar nuevo curso -> Menu -> Move -> Automatización
        5. Abrir el curso duplicado

        IMPORTANTE: El template NUNCA se edita directamente.
        """
        self._progress("Duplicando plantilla desde el dashboard...", 48)
        logger.info(f"Iniciando duplicacion de: {template_url}")

        course_id = self._extract_course_id(template_url)
        if not course_id:
            raise RuntimeError(f"No se pudo extraer course_id de: {template_url}")

        # 1. Ir al dashboard
        self.navigate_to_dashboard()
        take_screenshot(self.page, label="dashboard_for_dup")

        # 2. Buscar la plantilla
        self._search_in_dashboard("PLANTILLA")

        # 3. Encontrar la tarjeta por course_id
        card = self._find_card_by_course_id(course_id)
        if not card:
            raise RuntimeError(f"No se encontro la tarjeta del template (ID: {course_id})")
        logger.info("Tarjeta del template encontrada")

        # 4. Hover -> menu -> Duplicate
        if not self._open_card_menu(card):
            raise RuntimeError("No se pudo abrir el menu de la tarjeta")
        take_screenshot(self.page, label="card_menu_open")

        if not self._click_menu_item("Duplicate"):
            raise RuntimeError("Opcion 'Duplicate' no encontrada en el menu")
        time.sleep(1.5)

        # 5. Modal "Duplicate Course"
        self._fill_duplicate_modal(new_title)
        logger.info("Esperando creacion del duplicado...")
        time.sleep(8)
        take_screenshot(self.page, label="after_duplicate")

        # 6. Cerrar overlays y buscar el nuevo curso
        self.page.keyboard.press("Escape")
        time.sleep(1)
        self._clear_search()
        self._search_in_dashboard(new_title)

        new_link = self.page.locator("a[href*='/authoring/']").first
        try:
            new_link.wait_for(state="visible", timeout=8_000)
        except Exception:
            raise RuntimeError(f"No se encontro el curso duplicado: '{new_title}'")

        new_course_url = new_link.get_attribute("href") or ""
        new_course_id = self._extract_course_id(new_course_url)
        logger.info(f"Curso duplicado encontrado: {new_course_url}")

        # 7. Mover a carpeta "Automatización"
        self._move_course_to_folder(new_course_id or "")

        # 8. Abrir el curso duplicado
        if new_course_url and not new_course_url.startswith("http"):
            new_course_url = f"https://rise.articulate.com{new_course_url}"

        self.page.goto(new_course_url, wait_until="domcontentloaded")
        time.sleep(6)
        self.dismiss_cookies()
        time.sleep(2)

        current_url = self.page.url
        logger.info(f"Curso duplicado abierto en: {current_url}")
        self._progress("Plantilla duplicada exitosamente", 55)
        take_screenshot(self.page, label="duplicated_course_outline")
        return current_url

    def _extract_course_id(self, url: str) -> Optional[str]:
        match = re.search(r"/authoring/([a-zA-Z0-9_\-]+)", url)
        return match.group(1) if match else None

    def _fill_duplicate_modal(self, new_title: str):
        """Llena el modal 'Duplicate Course' con el nuevo titulo."""
        input_sels = [
            "[role='dialog'] input[type='text']",
            "[class*='modal'] input[type='text']",
            "[role='dialog'] input",
        ]
        for sel in input_sels:
            try:
                inp = self.page.locator(sel).first
                if inp.is_visible(timeout=3_000):
                    inp.click()
                    inp.fill("")
                    inp.fill(new_title)
                    logger.info(f"Nombre del duplicado: '{new_title}'")
                    break
            except Exception:
                continue

        dup_btn_sels = [
            "[role='dialog'] button:has-text('Duplicate')",
            "[class*='modal'] button:has-text('Duplicate')",
        ]
        for sel in dup_btn_sels:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    logger.info("Boton Duplicate del modal clickeado")
                    return
            except Exception:
                continue
        raise RuntimeError("No se pudo confirmar la duplicacion en el modal")

    def _move_course_to_folder(self, course_id: str):
        """Mueve un curso a la carpeta 'Automatización' via el menu Move."""
        logger.info("Moviendo curso a carpeta Automatizacion...")

        card = self._find_card_by_course_id(course_id) if course_id else None
        if not card:
            try:
                link = self.page.locator("a[href*='/authoring/']").first
                card = link.locator("xpath=ancestor::li[1]").first
            except Exception:
                logger.warning("No se encontro tarjeta para mover")
                return

        if not self._open_card_menu(card):
            logger.warning("No se pudo abrir menu para Move")
            return

        if not self._click_menu_item("Move"):
            logger.warning("Opcion 'Move' no encontrada en el menu")
            return

        time.sleep(2)
        take_screenshot(self.page, label="move_dialog")

        # Seleccionar carpeta "Automatización" en el tree
        folder_names = ["Automatización", "Automatizacion", "Automatizaciones"]
        folder_clicked = False
        for name in folder_names:
            try:
                folder = self.page.locator(f"[role='tree'] :text('{name}')").first
                if folder.is_visible(timeout=2_000):
                    folder.click()
                    time.sleep(1)
                    folder_clicked = True
                    logger.info(f"Carpeta '{name}' seleccionada")
                    break
            except Exception:
                continue

        if not folder_clicked:
            try:
                tree = self.page.locator("[role='tree']").first
                items = tree.locator("[role='treeitem']")
                for i in range(items.count()):
                    item = items.nth(i)
                    text = item.inner_text()[:50].strip()
                    if any(n.lower() in text.lower() for n in folder_names):
                        item.click()
                        folder_clicked = True
                        logger.info(f"Carpeta encontrada: '{text}'")
                        break
            except Exception:
                pass

        if not folder_clicked:
            logger.warning("No se encontro carpeta Automatizacion")

        # Click en boton "Move" (el de confirmacion al final del dialog)
        try:
            move_btns = self.page.locator("[role='dialog'] button:has-text('Move')")
            for i in range(move_btns.count() - 1, -1, -1):
                btn = move_btns.nth(i)
                text = btn.inner_text().strip()
                if text == "Move":
                    btn.click()
                    time.sleep(3)
                    logger.info("Curso movido exitosamente")
                    take_screenshot(self.page, label="after_move")
                    return
        except Exception as e:
            logger.warning(f"Error clickeando boton Move: {e}")

        self.page.keyboard.press("Escape")
        time.sleep(1)

    # ── Navegación en el Course Outline ──────────────────────────────────

    def get_lessons_in_outline(self) -> list[dict]:
        """Retorna la lista de lecciones en el outline del curso."""
        lessons = []
        edit_btns = self.page.locator("button:has-text('Edit Content')").all()
        for i, btn in enumerate(edit_btns):
            try:
                parent = btn.locator(
                    "xpath=ancestor::li[1] | ancestor::tr[1] | "
                    "ancestor::div[contains(@class,'lesson')][1]"
                )
                title = ""
                if parent.count() > 0:
                    title_el = parent.first.locator(
                        "[class*='title'], [class*='name'], h2, h3, span"
                    ).first
                    try:
                        title = title_el.inner_text()[:80].strip()
                    except Exception:
                        pass
                lessons.append({"index": i, "title": title, "edit_button": btn})
            except Exception:
                lessons.append({"index": i, "title": f"Leccion {i+1}", "edit_button": btn})
        logger.info(f"Lecciones encontradas: {len(lessons)}")
        return lessons

    def open_lesson_editor(self, lesson_index: int = 0) -> bool:
        """Abre el editor de bloques de una leccion."""
        try:
            edit_btns = self.page.locator("button:has-text('Edit Content')")
            count = edit_btns.count()
            logger.info(f"Botones 'Edit Content': {count}")
            if count == 0:
                take_screenshot(self.page, label="no_edit_content")
                return False
            target = edit_btns.nth(min(lesson_index, count - 1))
            target.scroll_into_view_if_needed()
            target.click()
            time.sleep(4)
            self.dismiss_cookies()
            time.sleep(2)
            take_screenshot(self.page, label=f"lesson_editor_{lesson_index}")
            logger.info(f"Editor de leccion {lesson_index} abierto")
            return True
        except Exception as e:
            logger.warning(f"Error abriendo editor de leccion {lesson_index}: {e}")
            return False

    def go_back_to_outline(self):
        """Vuelve al outline del curso desde el editor."""
        try:
            back_sels = [
                "[class*='back-button']",
                "[aria-label*='back' i]",
                "a[href*='authoring']:not([href*='lesson'])",
                "button:has-text('Course')",
            ]
            for sel in back_sels:
                try:
                    btn = self.page.locator(sel).first
                    if btn.is_visible(timeout=1_500):
                        btn.click()
                        time.sleep(3)
                        self.dismiss_cookies()
                        return
                except Exception:
                    pass
            self.page.go_back()
            time.sleep(3)
        except Exception as e:
            logger.warning(f"Error volviendo al outline: {e}")

    # ── Edición de contenido ──────────────────────────────────────────────

    @with_retry(max_attempts=3)
    def insert_text(self, text: str, clear_first: bool = True) -> bool:
        """Inserta texto VERBATIM en el editor TipTap activo."""
        tiptap_sels = [
            ".rise-tiptap[contenteditable='true']",
            ".tiptap.ProseMirror[contenteditable='true']",
            ".tiptap[contenteditable='true']",
            ".ProseMirror[contenteditable='true']",
            "[contenteditable='true']",
        ]
        editor = None
        for sel in tiptap_sels:
            try:
                els = self.page.locator(sel)
                count = els.count()
                if count > 0:
                    for i in range(count):
                        e = els.nth(i)
                        if e.is_visible(timeout=500):
                            editor = e
                            try:
                                if e.evaluate("el => el === document.activeElement"):
                                    break
                            except Exception:
                                pass
                    if editor:
                        break
            except Exception:
                pass

        if not editor:
            logger.warning("No se encontro editor TipTap activo")
            take_screenshot(self.page, label="no_tiptap_editor")
            return False

        try:
            editor.scroll_into_view_if_needed()
            editor.click()
            time.sleep(0.2)
            if clear_first:
                self.page.keyboard.press("Control+a")
                time.sleep(0.1)
                self.page.keyboard.press("Delete")
                time.sleep(0.1)
            paste_large_text(self.page, text)
            time.sleep(0.3)
            logger.debug(f"Texto insertado ({len(text)} chars)")
            return True
        except Exception as e:
            logger.warning(f"Error insertando texto: {e}")
            take_screenshot(self.page, label="insert_text_fail")
            return False

    def insert_heading(self, text: str, level: int = 2) -> bool:
        """Inserta un encabezado en el editor TipTap."""
        result = self.insert_text(text, clear_first=False)
        if not result:
            return False
        self._apply_tiptap_heading(level)
        self.page.keyboard.press("Enter")
        return True

    def _apply_tiptap_heading(self, level: int) -> bool:
        toolbar_sels = [
            f"[class*='tiptap-menu'] button[data-level='{level}']",
            f"button[data-heading='{level}']",
            f".tiptap-toolbar button:has-text('H{level}')",
        ]
        for sel in toolbar_sels:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1_000):
                    btn.click()
                    return True
            except Exception:
                pass
        return False

    def set_course_title(self, title: str) -> bool:
        """Edita el titulo del curso en el outline."""
        title_sels = [
            "textarea[placeholder='Course Title']",
            ".authoring-lesson-header__title textarea",
            "textarea[maxlength='100']",
            "h1[contenteditable='true']",
        ]
        for sel in title_sels:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=3_000):
                    el.click()
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.type(title)
                    logger.info(f"Titulo del curso: '{title}'")
                    return True
            except Exception:
                pass
        logger.warning("No se pudo establecer el titulo del curso")
        return False

    def add_block(self, block_type: str) -> bool:
        """Agrega un bloque en el editor de leccion activo."""
        logger.debug(f"Intentando agregar bloque: {block_type}")
        add_btns = [
            "button[aria-label*='Add block' i]",
            "button[aria-label*='Insert block' i]",
            "button[class*='add-block']",
            "button[class*='insert-block']",
            "[data-testid*='add-block']",
            "button:has-text('Add a block')",
            "button:has-text('+')",
        ]
        for sel in add_btns:
            try:
                btn = self.page.locator(sel).last
                if btn.is_visible(timeout=1_500):
                    btn.click()
                    time.sleep(0.5)
                    label = self._get_block_menu_label(block_type)
                    if self._select_block_type_from_menu(label):
                        time.sleep(1)
                        wait_for_react_idle(self.page, timeout_ms=2_000)
                        logger.debug(f"Bloque '{block_type}' agregado")
                        return True
            except Exception:
                pass
        logger.warning("No se pudo encontrar el boton de agregar bloque")
        return False

    def _get_block_menu_label(self, block_type: str) -> str:
        import json
        try:
            with open(config.LEARNING_MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("block_menu_labels", {}).get(
                block_type, block_type.replace("_", " ").title()
            )
        except Exception:
            return block_type.replace("_", " ").title()

    def _select_block_type_from_menu(self, label: str) -> bool:
        for role in ["option", "menuitem", "listitem"]:
            try:
                item = self.page.get_by_role(role, name=re.compile(label, re.IGNORECASE))
                if item.first.is_visible(timeout=2_000):
                    item.first.click()
                    return True
            except Exception:
                pass
        try:
            item = self.page.get_by_text(re.compile(label, re.IGNORECASE)).first
            if item.is_visible(timeout=1_500):
                item.click()
                return True
        except Exception:
            pass
        return False

    # ── Guardado ──────────────────────────────────────────────────────────

    def save_course(self):
        """Rise 360 auto-guarda. Fuerza Ctrl+S como medida extra."""
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.3)
            self.page.keyboard.press("Control+s")
            time.sleep(1.5)
            logger.info("Guardado forzado con Ctrl+S")
            return True
        except Exception as e:
            logger.warning(f"Error en save_course: {e}")
            return False

    # ── Helpers ────────────────────────────────────────────────────────────

    def get_current_url(self) -> str:
        return self.page.url if self.page else ""

    def take_debug_screenshot(self, label: str = "debug"):
        if self.page:
            take_screenshot(self.page, label=label)
