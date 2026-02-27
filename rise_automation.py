"""
rise_automation.py — Motor principal de automatización Playwright para Rise 360
Basado en inspección real del DOM de Rise 360 (febrero 2026).

Hallazgos del DOM real:
  - Editor de texto: TipTap (.tiptap.ProseMirror.rise-tiptap), NO Quill
  - Cookie popup: button.osano-cm-accept-all
  - Template URL abre "Course Outline" (no el editor de bloques directamente)
  - Para editar bloques de una lección: click en botón "Edit Content"
  - Dashboard URL: https://rise.articulate.com (no /home)
  - Botón 3-dot en outline: button.menu__trigger.menu__trigger--dots
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
        """
        Cierra el popup de cookies de Osano si está presente.
        Confirmado por inspección: selector = button.osano-cm-accept-all
        """
        selectors = [
            "button.osano-cm-accept-all",
            "button:has-text('Aceptar todo')",
            "button:has-text('Accept all')",
            "button:has-text('Accept All')",
        ]
        for sel in selectors:
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
        """
        Login en Rise 360 vía Articulate ID OAuth.
        Maneja el popup de cookies antes y después del login.
        """
        self._progress("Iniciando sesión en Rise 360...", 25)
        logger.info("Iniciando login")

        self.page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        time.sleep(2)
        self.dismiss_cookies()

        email_sel = (
            "input[name='username'], input[type='email'], "
            "input[id*='email'], input[autocomplete*='email']"
        )
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

        take_screenshot(self.page, label="after_login")
        logger.info("Login exitoso")
        self._progress("Sesión iniciada", 35)
        return True

    def _get_visible_error(self) -> Optional[str]:
        for sel in [".error-message", ".alert-error", "[role='alert']", "p.error"]:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=1_000):
                    return el.inner_text()
            except Exception:
                pass
        return None

    # ── Navegación al curso ───────────────────────────────────────────────

    def navigate_to_course_outline(self, course_url: str):
        """
        Navega al outline de un curso y espera que cargue completamente.
        Acepta cookies si aparecen.
        """
        logger.info(f"Navegando al curso: {course_url}")
        self.page.goto(course_url, wait_until="domcontentloaded")
        time.sleep(6)  # Rise 360 SPA necesita tiempo para renderizar
        self.dismiss_cookies()
        time.sleep(2)
        take_screenshot(self.page, label="course_outline")
        logger.info("Outline del curso cargado")

    # ── Duplicación de plantilla ──────────────────────────────────────────

    @with_retry(max_attempts=3)
    def duplicate_template(self, template_url: str, new_title: str) -> str:
        """
        Duplica el curso de plantilla desde el dashboard de Rise 360.

        Flujo:
        1. Ir al dashboard (https://rise.articulate.com)
        2. Encontrar la tarjeta del template por URL/nombre
        3. Hover → click menú "..." → click "Duplicate"
        4. Esperar que aparezca el duplicado
        5. Renombrar con new_title
        6. Abrir el duplicado y retornar su URL

        IMPORTANTE: El template NUNCA se edita directamente.
        """
        self._progress("Duplicando plantilla desde el dashboard...", 48)
        logger.info(f"Iniciando duplicación de: {template_url}")

        course_id = self._extract_course_id(template_url)
        logger.info(f"Course ID de la plantilla: {course_id}")

        # Ir al dashboard
        self.page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")
        time.sleep(4)
        self.dismiss_cookies()
        time.sleep(2)
        take_screenshot(self.page, label="dashboard_for_dup")

        # Buscar la tarjeta del curso en el dashboard
        card = self._find_course_card_in_dashboard(course_id, template_url)

        if not card:
            raise RuntimeError(
                f"No se encontró la tarjeta del template (ID: {course_id}) "
                "en el dashboard. Asegúrate de que el curso esté visible."
            )

        logger.info("Tarjeta del template encontrada")

        # Hover → revelar menú
        card.scroll_into_view_if_needed()
        card.hover()
        time.sleep(0.8)
        take_screenshot(self.page, label="card_hover")

        # Click en botón 3-dot
        if not self._click_card_dots_menu(card):
            raise RuntimeError("No se pudo abrir el menú de opciones de la tarjeta")

        time.sleep(0.5)
        take_screenshot(self.page, label="card_menu_open")

        # Click en "Duplicate" / "Duplicar"
        if not self._click_menu_item_text(["Duplicate", "Duplicar", "Duplicate course"]):
            raise RuntimeError("Opción 'Duplicate' no encontrada en el menú")

        logger.info("Duplicate clickeado — esperando que se cree el duplicado...")
        time.sleep(4)
        wait_for_react_idle(self.page, timeout_ms=8_000)
        take_screenshot(self.page, label="after_duplicate")

        # Encontrar el curso duplicado (el más reciente en el dashboard)
        new_card = self._find_duplicated_card()
        if not new_card:
            raise RuntimeError("No se encontró el curso duplicado en el dashboard")

        # Renombrar
        self._rename_course_card(new_card, new_title)
        time.sleep(1.5)

        # Abrir el duplicado
        new_course_url = self._open_course_from_card(new_card)
        if not new_course_url:
            raise RuntimeError("No se pudo abrir el curso duplicado")

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
        if match:
            return match.group(1)
        return None

    def _find_course_card_in_dashboard(self, course_id: str, template_url: str):
        """
        Busca la tarjeta del template en el dashboard.
        Intenta múltiples estrategias.
        """
        # Estrategia 1: buscar por link href con el course_id
        if course_id:
            link_sels = [
                f"a[href*='{course_id}']",
                f"[href*='{course_id}']",
            ]
            for sel in link_sels:
                try:
                    el = self.page.locator(sel).first
                    if el.is_visible(timeout=3_000):
                        # Subir al contenedor de la tarjeta
                        card = el.locator("..").locator("..").locator("..")
                        try:
                            card.hover(timeout=2_000)
                            return card
                        except Exception:
                            return el
                except Exception:
                    pass

        # Estrategia 2: buscar link que contenga "authoring" en el href
        try:
            all_authoring_links = self.page.locator("a[href*='authoring']").all()
            for link in all_authoring_links:
                href = link.get_attribute("href") or ""
                if course_id and course_id in href:
                    parent = link.locator("xpath=ancestor::li[1] | ancestor::article[1] | ancestor::div[contains(@class,'card')][1]")
                    if parent.count() > 0:
                        return parent.first
                    return link
        except Exception:
            pass

        # Estrategia 3: usar búsqueda de texto "PLANTILLA" en el dashboard
        try:
            # Scroll por la página buscando el nombre de la plantilla
            cards_with_text = self.page.locator(
                "li, article, [class*='card'], [class*='course-item']"
            ).filter(has_text="PLANTILLA").first
            if cards_with_text.is_visible(timeout=3_000):
                return cards_with_text
        except Exception:
            pass

        return None

    def _click_card_dots_menu(self, card) -> bool:
        """Hace click en el menú de 3 puntos de una tarjeta del dashboard."""
        # Confirmado por debug: los botones de tarjeta en el outline tienen estas clases
        dots_selectors = [
            "button.menu__trigger--dots",
            "button[class*='menu__trigger']",
            "button[aria-label*='more' i]",
            "button[aria-label*='options' i]",
            "button[aria-haspopup='true']",
            "[class*='options-button']",
            "[class*='dots']",
        ]
        # Intentar dentro de la tarjeta primero
        for sel in dots_selectors:
            try:
                btn = card.locator(sel).first
                if btn.is_visible(timeout=1_500):
                    btn.click()
                    return True
            except Exception:
                pass

        # Intentar en toda la página (después del hover la tarjeta puede no tener scope)
        for sel in dots_selectors:
            try:
                btn = self.page.locator(sel).last
                if btn.is_visible(timeout=1_500):
                    btn.click()
                    return True
            except Exception:
                pass

        # Fallback: click en coordenadas de esquina superior derecha de la tarjeta
        try:
            bbox = card.bounding_box()
            if bbox:
                x = bbox["x"] + bbox["width"] - 24
                y = bbox["y"] + 24
                self.page.mouse.click(x, y)
                time.sleep(0.5)
                return True
        except Exception:
            pass

        return False

    def _click_menu_item_text(self, labels: list[str]) -> bool:
        """Hace click en un ítem de menú desplegable por texto."""
        for label in labels:
            for role in ["menuitem", "option", "listitem"]:
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

    def _find_duplicated_card(self):
        """
        Encuentra el curso recién duplicado.
        Rise 360 suele poner el duplicado al inicio del grid con nombre "Copy of..."
        """
        # Esperar a que aparezca "Copy of" o el texto en español
        copy_patterns = ["Copy of", "Copia de", "copy", "PLANTILLA"]
        for pattern in copy_patterns:
            try:
                card = self.page.locator(
                    "li, article, [class*='card'], [class*='course-item']"
                ).filter(has_text=pattern).first
                if card.is_visible(timeout=3_000):
                    return card
            except Exception:
                pass

        # Fallback: el primer card del grid (el más reciente)
        card_sels = [
            "li[class*='course']",
            "article",
            "[class*='course-card']",
            "[class*='content-card']",
            "[class*='grid-item']",
        ]
        for sel in card_sels:
            try:
                cards = self.page.locator(sel)
                if cards.count() > 0:
                    return cards.first
            except Exception:
                pass

        return None

    def _rename_course_card(self, card, new_title: str) -> bool:
        """Renombra el curso desde la tarjeta del dashboard."""
        # Abrir menú y click en "Rename"
        card.hover()
        time.sleep(0.5)
        self._click_card_dots_menu(card)
        time.sleep(0.5)

        if self._click_menu_item_text(["Rename", "Renombrar"]):
            time.sleep(0.5)
            # Llenar el campo de nombre
            for sel in ["input[class*='rename']", "input[type='text']", "input"]:
                try:
                    inp = self.page.locator(sel).first
                    if inp.is_visible(timeout=2_000):
                        inp.clear()
                        inp.fill(new_title)
                        inp.press("Enter")
                        logger.info(f"Curso renombrado a: {new_title}")
                        return True
                except Exception:
                    pass
        return False

    def _open_course_from_card(self, card) -> Optional[str]:
        """Abre el editor de un curso desde su tarjeta del dashboard."""
        # Hover para revelar botón Edit/Open
        card.hover()
        time.sleep(0.5)

        # Buscar link directo al editor
        try:
            link = card.locator("a[href*='authoring']").first
            if link.is_visible(timeout=2_000):
                href = link.get_attribute("href") or ""
                link.click()
                time.sleep(6)
                self.dismiss_cookies()
                return self.page.url
        except Exception:
            pass

        # Click en el card mismo
        try:
            card.click()
            time.sleep(6)
            self.dismiss_cookies()
            return self.page.url
        except Exception:
            pass

        return None

    # ── Navegación en el Course Outline ──────────────────────────────────

    def get_lessons_in_outline(self) -> list[dict]:
        """
        Retorna la lista de lecciones visibles en el outline del curso.
        Basado en DOM real: cada lección tiene un "Edit Content" button.

        Retorna: [{"title": str, "edit_button": locator, "index": int}]
        """
        lessons = []
        # Buscar todos los botones "Edit Content" del outline
        edit_btns = self.page.locator("button:has-text('Edit Content')").all()

        for i, btn in enumerate(edit_btns):
            try:
                # Buscar el título de la lección en el mismo row
                parent = btn.locator("xpath=ancestor::li[1] | ancestor::tr[1] | ancestor::div[contains(@class,'lesson')][1]")
                title = ""
                if parent.count() > 0:
                    title_el = parent.first.locator(
                        "[class*='title'], [class*='name'], h2, h3, span"
                    ).first
                    try:
                        title = title_el.inner_text()[:80].strip()
                    except Exception:
                        pass

                lessons.append({
                    "index": i,
                    "title": title,
                    "edit_button": btn,
                })
            except Exception:
                lessons.append({"index": i, "title": f"Lección {i+1}", "edit_button": btn})

        logger.info(f"Lecciones encontradas en el outline: {len(lessons)}")
        return lessons

    def open_lesson_editor(self, lesson_index: int = 0) -> bool:
        """
        Abre el editor de bloques de una lección haciendo click en "Edit Content".
        Confirmado por inspección visual: este botón existe en el outline.
        """
        try:
            edit_btns = self.page.locator("button:has-text('Edit Content')")
            count = edit_btns.count()
            logger.info(f"Botones 'Edit Content' visibles: {count}")

            if count == 0:
                take_screenshot(self.page, label="no_edit_content")
                logger.warning("No se encontraron botones 'Edit Content'")
                return False

            target = edit_btns.nth(min(lesson_index, count - 1))
            target.scroll_into_view_if_needed()
            target.click()
            time.sleep(4)
            self.dismiss_cookies()
            time.sleep(2)
            take_screenshot(self.page, label=f"lesson_editor_{lesson_index}")
            logger.info(f"Editor de lección {lesson_index} abierto. URL: {self.page.url}")
            return True
        except Exception as e:
            logger.warning(f"Error abriendo editor de lección {lesson_index}: {e}")
            return False

    def go_back_to_outline(self):
        """Vuelve al outline del curso desde el editor de bloques."""
        try:
            # Rise 360 suele tener un botón de "back" o el breadcrumb del título del curso
            back_sels = [
                "[class*='back-button']",
                "[aria-label*='back' i]",
                "[aria-label*='volver' i]",
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
                        logger.debug("Vuelto al outline del curso")
                        return
                except Exception:
                    pass

            # Fallback: navegar via browser back
            self.page.go_back()
            time.sleep(3)
        except Exception as e:
            logger.warning(f"Error volviendo al outline: {e}")

    # ── Edición de contenido ──────────────────────────────────────────────

    @with_retry(max_attempts=3)
    def insert_text(self, text: str, clear_first: bool = True) -> bool:
        """
        Inserta texto VERBATIM en el editor TipTap activo.

        Selectores confirmados por inspección:
          .tiptap.ProseMirror.rise-tiptap  ← div contenteditable
          [contenteditable="true"]          ← genérico

        REGLA: El texto se inserta exactamente como se recibe del PDF.
        """
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
                    # Priorizar el que tiene foco o el más visible
                    for i in range(count):
                        e = els.nth(i)
                        if e.is_visible(timeout=500):
                            editor = e
                            # Si tiene foco, usar este
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
            logger.warning("No se encontró editor TipTap activo")
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

            # Insertar texto verbatim
            paste_large_text(self.page, text)
            time.sleep(0.3)

            logger.debug(f"Texto insertado ({len(text)} chars)")
            return True

        except Exception as e:
            logger.warning(f"Error insertando texto: {e}")
            take_screenshot(self.page, label="insert_text_fail")
            return False

    def insert_heading(self, text: str, level: int = 2) -> bool:
        """
        Inserta un encabezado en el editor TipTap.
        TipTap en Rise 360 soporta formato de heading vía toolbar o keyboard.
        """
        # Primero insertar el texto
        result = self.insert_text(text, clear_first=False)
        if not result:
            return False

        # Intentar aplicar formato de heading si hay toolbar de TipTap
        self._apply_tiptap_heading(level)
        self.page.keyboard.press("Enter")
        return True

    def _apply_tiptap_heading(self, level: int) -> bool:
        """Aplica formato heading en TipTap toolbar."""
        # TipTap toolbar puede tener botones de heading
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
        """
        Edita el título del curso en el outline.
        En el outline hay un textarea con placeholder "Course Title".
        Confirmado por HTML real: textarea[placeholder='Course Title']
        """
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
                    logger.info(f"Título del curso establecido: '{title}'")
                    return True
            except Exception:
                pass

        logger.warning("No se pudo establecer el título del curso")
        return False

    def add_block(self, block_type: str) -> bool:
        """
        Agrega un bloque en el editor de lección activo.
        Necesita que el editor de lección esté abierto (via open_lesson_editor).

        NOTA: Los selectores exactos del "Add block" en el editor de lección
        aún se están investigando. Esta función usará las mejores aproximaciones
        disponibles.
        """
        logger.debug(f"Intentando agregar bloque: {block_type}")

        # Selectores del botón "+" o "Add block" en el lesson block editor
        add_btns = [
            "button[aria-label*='Add block' i]",
            "button[aria-label*='Agregar bloque' i]",
            "button[aria-label*='Insert block' i]",
            "button[class*='add-block']",
            "button[class*='insert-block']",
            "[data-testid*='add-block']",
            "[data-testid*='AddBlock']",
            "button:has-text('Add a block')",
            "button:has-text('+')",
        ]

        for sel in add_btns:
            try:
                btn = self.page.locator(sel).last
                if btn.is_visible(timeout=1_500):
                    btn.click()
                    time.sleep(0.5)
                    # Seleccionar el tipo de bloque del menú
                    label = self._get_block_menu_label(block_type)
                    if self._select_block_type_from_menu(label):
                        time.sleep(1)
                        wait_for_react_idle(self.page, timeout_ms=2_000)
                        logger.debug(f"Bloque '{block_type}' agregado")
                        return True
            except Exception:
                pass

        logger.warning(f"No se pudo encontrar el botón de agregar bloque")
        return False

    def _get_block_menu_label(self, block_type: str) -> str:
        import json
        try:
            with open(config.LEARNING_MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("block_menu_labels", {}).get(block_type, block_type.replace("_", " ").title())
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
        """Rise 360 auto-guarda. Fuerza Ctrl+S como medida de seguridad."""
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

    # ── Helpers de estado ─────────────────────────────────────────────────

    def get_current_url(self) -> str:
        return self.page.url if self.page else ""

    def take_debug_screenshot(self, label: str = "debug"):
        if self.page:
            take_screenshot(self.page, label=label)
