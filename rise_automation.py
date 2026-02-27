"""
rise_automation.py — Motor principal de automatización Playwright para Rise 360
Controla el browser para: login, duplicación de cursos, edición de bloques.

Rise 360 es un SPA React con Quill.js para edición de texto.
Estrategia DOM:
  CAPA 1: Selectores CSS/data-attributes (más confiables)
  CAPA 2: Text/role selectors de Playwright (fallback)
  CAPA 3: Coordenadas visuales por OCR (último recurso)
"""

import time
import re
from typing import Optional, Callable
from pathlib import Path
from utils import (
    logger, with_retry, take_screenshot,
    wait_for_react_idle, wait_for_url_contains,
    wait_for_selector_any, safe_click, paste_large_text,
)
import config


class RiseAutomation:
    """
    Administra una sesión Playwright contra Rise 360.
    Diseñado para correr en un thread separado desde main.py.
    """

    def __init__(self, progress_callback: Optional[Callable] = None):
        """
        Args:
            progress_callback: función (msg: str, pct: int) para actualizar la GUI.
        """
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._progress = progress_callback or (lambda msg, pct: None)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

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
            # User agent real para reducir detección de bot
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._context.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
        self.page = self._context.new_page()

        # Inyectar script para desactivar flags de automatización
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

    # ── Login ─────────────────────────────────────────────────────────────

    @with_retry(max_attempts=3)
    def login(self, email: str, password: str) -> bool:
        """
        Inicia sesión en Rise 360 vía Articulate ID.

        Flujo OAuth:
        1. Navegar a rise.articulate.com → redirect a id.articulate.com/u/login
        2. Llenar email → click Continue
        3. Llenar password → click Sign In
        4. Esperar redirect a rise.articulate.com
        """
        self._progress("Iniciando sesión en Rise 360...", 25)
        logger.info("Iniciando proceso de login")

        self.page.goto(config.RISE_BASE_URL, wait_until="domcontentloaded")

        # Esperar página de login de Articulate ID
        email_sel = "input[name='username'], input[type='email'], input[id*='email']"
        self.page.wait_for_selector(email_sel, timeout=config.NAVIGATION_TIMEOUT)

        # Llenar email
        email_input = self.page.locator(email_sel).first
        email_input.clear()
        email_input.fill(email)
        logger.debug(f"Email ingresado: {email}")

        # Click en Continue/Submit
        submit_btn = self.page.locator("button[type='submit']").first
        submit_btn.click()

        # Esperar campo de password (puede tardar en aparecer en flujo OAuth)
        pwd_sel = "input[name='password'], input[type='password']"
        try:
            self.page.wait_for_selector(pwd_sel, timeout=15_000)
        except Exception:
            # Algunos flujos muestran el password en la misma pantalla
            logger.debug("Password no apareció en pantalla separada, buscando en vista actual")

        pwd_input = self.page.locator(pwd_sel).first
        pwd_input.clear()
        pwd_input.fill(password)
        logger.debug("Password ingresado")

        # Submit login
        self.page.locator("button[type='submit']").last.click()

        # Esperar redirect al dashboard de Rise 360
        try:
            self.page.wait_for_url("**/rise.articulate.com/**", timeout=config.NAVIGATION_TIMEOUT)
        except Exception:
            # Si no redirige, verificar si hay error de login
            error_text = self._get_visible_error()
            if error_text:
                raise ValueError(f"Error de login: {error_text}")
            raise TimeoutError("Login no completó el redirect a Rise 360 en el tiempo esperado")

        wait_for_react_idle(self.page)
        take_screenshot(self.page, label="after_login")

        logger.info("Login exitoso en Rise 360")
        self._progress("Sesión iniciada exitosamente", 35)
        return True

    def _get_visible_error(self) -> Optional[str]:
        """Intenta leer mensajes de error visibles en la página de login."""
        error_sels = [
            ".error-message", ".alert-error", "[role='alert']",
            ".notification-danger", "p.error",
        ]
        for sel in error_sels:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=1_000):
                    return el.inner_text()
            except Exception:
                pass
        return None

    # ── Navegación al curso ───────────────────────────────────────────────

    def navigate_to_course_editor(self, course_url: str):
        """
        Navega directamente a la URL de un curso y espera que cargue.
        """
        logger.info(f"Navegando al curso: {course_url}")
        self.page.goto(course_url, wait_until="domcontentloaded")
        wait_for_react_idle(self.page, timeout_ms=10_000)
        take_screenshot(self.page, label="course_editor")
        logger.info("Editor del curso cargado")

    # ── Duplicación de plantilla ──────────────────────────────────────────

    @with_retry(max_attempts=3)
    def duplicate_template(self, template_url: str, new_title: str) -> str:
        """
        Duplica el curso de plantilla y lo renombra con el título del nuevo curso.

        Flujo Rise 360:
        1. Ir al dashboard
        2. Localizar la tarjeta del curso de plantilla
        3. Hover → menú "..." → click "Duplicate"
        4. Esperar que aparezca "Copy of [nombre]" en el dashboard
        5. Renombrar el nuevo curso con new_title
        6. Abrir el curso duplicado → retornar URL

        Retorna la URL del editor del nuevo curso.
        """
        self._progress("Duplicando plantilla de curso...", 48)
        logger.info(f"Duplicando plantilla: {template_url}")

        # Extraer el ID del curso de la URL
        course_id = self._extract_course_id(template_url)
        if not course_id:
            raise ValueError(f"No se pudo extraer el ID del curso de: {template_url}")

        # Navegar al dashboard
        self.page.goto(config.RISE_DASHBOARD_URL, wait_until="domcontentloaded")
        wait_for_react_idle(self.page, timeout_ms=8_000)

        # Buscar la tarjeta del curso en el dashboard
        card = self._find_course_card_by_id(course_id)
        if not card:
            # Intentar por URL directa
            card = self._find_course_card_by_url_segment(course_id)

        if not card:
            raise ValueError(f"Curso no encontrado en el dashboard (ID: {course_id})")

        # Hover sobre la tarjeta para revelar el menú
        card.hover()
        self.page.wait_for_timeout(600)

        # Click en menú "..." (tres puntos / more options)
        menu_opened = self._open_course_options_menu(card)
        if not menu_opened:
            raise RuntimeError("No se pudo abrir el menú de opciones del curso")

        # Click en "Duplicate" / "Duplicar"
        dup_item = self._click_menu_item(["Duplicate", "Duplicar", "duplicate"])
        if not dup_item:
            raise RuntimeError("Opción 'Duplicate' no encontrada en el menú")

        # Esperar a que aparezca el nuevo curso ("Copy of...")
        logger.info("Esperando que se cree el duplicado...")
        self.page.wait_for_timeout(3_000)
        wait_for_react_idle(self.page, timeout_ms=10_000)

        # Encontrar el curso duplicado ("Copy of ..." o "Copia de ...")
        new_card = self._find_newest_course_card()
        if not new_card:
            raise RuntimeError("No se encontró el curso duplicado en el dashboard")

        # Renombrar el curso duplicado
        self._rename_course(new_card, new_title)
        self.page.wait_for_timeout(1_500)

        # Abrir el curso para edición
        new_card.hover()
        self.page.wait_for_timeout(400)

        # Click en "Edit" / "Editar"
        edit_clicked = self._click_edit_course(new_card)
        if not edit_clicked:
            # Intentar doble clic en la tarjeta
            new_card.dbl_click()

        # Esperar que cargue el editor
        self.page.wait_for_load_state("domcontentloaded")
        wait_for_react_idle(self.page, timeout_ms=10_000)

        current_url = self.page.url
        logger.info(f"Curso duplicado abierto: {current_url}")
        self._progress("Plantilla duplicada exitosamente", 55)
        take_screenshot(self.page, label="after_duplicate")

        return current_url

    def _extract_course_id(self, url: str) -> Optional[str]:
        """Extrae el ID del curso de la URL de Rise 360."""
        # Formato: /authoring/COURSE_ID o como parámetro
        match = re.search(r"/authoring/([a-zA-Z0-9_\-]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"([a-zA-Z0-9_\-]{20,})", url)
        if match:
            return match.group(1)
        return None

    def _find_course_card_by_id(self, course_id: str):
        """Busca la tarjeta del curso por ID en el atributo data del DOM."""
        selectors = [
            f"[data-id='{course_id}']",
            f"[data-course-id='{course_id}']",
            f"a[href*='{course_id}']",
        ]
        for sel in selectors:
            try:
                card = self.page.locator(sel).first
                if card.is_visible(timeout=2_000):
                    return card
            except Exception:
                pass
        return None

    def _find_course_card_by_url_segment(self, url_segment: str):
        """Fallback: busca tarjeta por segmento de URL en href."""
        try:
            card = self.page.locator(f"a[href*='{url_segment}']").first
            if card.is_visible(timeout=2_000):
                return card
        except Exception:
            pass
        return None

    def _open_course_options_menu(self, card) -> bool:
        """Abre el menú de opciones (tres puntos) de una tarjeta de curso."""
        menu_selectors = [
            "button[aria-label*='more' i]",
            "button[aria-label*='options' i]",
            "button[aria-label*='More options' i]",
            ".course-options-button",
            "[data-testid='course-options']",
            "button.options-trigger",
        ]
        for sel in menu_selectors:
            try:
                btn = card.locator(sel).first
                if not btn.is_visible(timeout=1_000):
                    btn = self.page.locator(sel).last
                if btn.is_visible(timeout=1_000):
                    btn.click()
                    self.page.wait_for_timeout(500)
                    return True
            except Exception:
                pass

        # Fallback: buscar el botón por coordenadas en la esquina superior derecha de la tarjeta
        try:
            bbox = card.bounding_box()
            if bbox:
                x = bbox["x"] + bbox["width"] - 20
                y = bbox["y"] + 20
                self.page.mouse.click(x, y)
                self.page.wait_for_timeout(500)
                return True
        except Exception as e:
            logger.warning(f"Fallback de click en menú falló: {e}")

        return False

    def _click_menu_item(self, labels: list[str]) -> bool:
        """Hace click en un ítem del menú desplegable por texto."""
        for label in labels:
            try:
                item = self.page.get_by_role("menuitem", name=re.compile(label, re.IGNORECASE))
                if item.is_visible(timeout=2_000):
                    item.click()
                    return True
            except Exception:
                pass
            try:
                item = self.page.get_by_text(re.compile(label, re.IGNORECASE)).first
                if item.is_visible(timeout=1_000):
                    item.click()
                    return True
            except Exception:
                pass
        return False

    def _find_newest_course_card(self):
        """Encuentra el curso más reciente en el dashboard (el duplicado recién creado)."""
        # Rise ordena por fecha de creación/modificación — el nuevo es el primero
        course_card_selectors = [
            ".course-card",
            "[data-testid='course-card']",
            ".content-card",
            "li.course-item",
        ]
        for sel in course_card_selectors:
            try:
                cards = self.page.locator(sel)
                count = cards.count()
                if count > 0:
                    return cards.first  # El más reciente suele estar primero
            except Exception:
                pass
        return None

    def _rename_course(self, card, new_title: str):
        """Renombra el curso haciendo doble clic en el título."""
        title_selectors = [
            ".course-title",
            "[data-testid='course-title']",
            "h3", "h2", ".card-title",
        ]
        for sel in title_selectors:
            try:
                title_el = card.locator(sel).first
                if title_el.is_visible(timeout=1_500):
                    title_el.dbl_click()
                    self.page.wait_for_timeout(400)
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.type(new_title)
                    self.page.keyboard.press("Enter")
                    logger.info(f"Curso renombrado a: {new_title}")
                    return True
            except Exception:
                pass

        logger.warning("No se pudo renombrar el curso automáticamente")
        return False

    def _click_edit_course(self, card) -> bool:
        """Hace click en el botón Edit de una tarjeta de curso."""
        edit_selectors = [
            "a[href*='authoring']",
            "button:has-text('Edit')",
            "button:has-text('Editar')",
            "[data-testid='edit-course']",
            ".edit-button",
        ]
        for sel in edit_selectors:
            try:
                btn = card.locator(sel).first
                if not btn.is_visible(timeout=1_000):
                    btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1_000):
                    btn.click()
                    return True
            except Exception:
                pass
        return False

    # ── Navegación dentro del editor ──────────────────────────────────────

    def scroll_to_lesson(self, lesson_index: int = 0):
        """
        Navega a una lección específica del curso en el editor.
        Rise 360 organiza el contenido en lecciones (lessons) dentro de secciones.
        """
        try:
            # Sidebar de lecciones en el editor
            lesson_items = self.page.locator(".lesson-item, [data-lesson-index], .sidebar-lesson")
            count = lesson_items.count()
            if count > lesson_index:
                lesson_items.nth(lesson_index).click()
                wait_for_react_idle(self.page, timeout_ms=3_000)
                logger.debug(f"Navegado a lección {lesson_index}")
        except Exception as e:
            logger.warning(f"Error navegando a lección {lesson_index}: {e}")

    def get_lesson_count(self) -> int:
        """Retorna el número de lecciones en el curso actual."""
        selectors = [
            ".lesson-item",
            "[data-lesson-index]",
            ".sidebar-lesson",
            ".outline-item",
        ]
        for sel in selectors:
            try:
                count = self.page.locator(sel).count()
                if count > 0:
                    return count
            except Exception:
                pass
        return 0

    # ── Gestión de bloques ─────────────────────────────────────────────────

    def add_block(self, block_type: str, after_current: bool = True) -> bool:
        """
        Agrega un nuevo bloque del tipo especificado al curso.

        Flujo Rise 360:
        1. Hacer click en el botón "+" entre bloques
        2. Seleccionar el tipo de bloque en el menú desplegable
        3. Esperar que el bloque aparezca en la vista

        Args:
            block_type: Tipo de bloque según learning_map.json
                        (text, section_banner, bulleted_list, table, etc.)
            after_current: Si True, agrega después del bloque actual

        Retorna True si se agregó exitosamente.
        """
        logger.debug(f"Agregando bloque de tipo: {block_type}")

        # Obtener el label de menú desde el learning_map
        block_labels = self._get_block_menu_labels()
        menu_label = block_labels.get(block_type, block_type.replace("_", " ").title())

        # Hacer click en botón "+" para agregar bloque
        add_button_clicked = self._click_add_block_button()
        if not add_button_clicked:
            logger.warning("No se pudo hacer click en el botón '+' para agregar bloque")
            return False

        # Esperar el menú de selección de bloques
        self.page.wait_for_timeout(500)

        # Buscar y click en el tipo de bloque
        selected = self._select_block_from_menu(menu_label)
        if not selected:
            logger.warning(f"Tipo de bloque '{menu_label}' no encontrado en el menú")
            return False

        # Esperar que el bloque aparezca
        self.page.wait_for_timeout(800)
        wait_for_react_idle(self.page, timeout_ms=3_000)
        logger.debug(f"Bloque '{block_type}' agregado")
        return True

    def _click_add_block_button(self) -> bool:
        """Hace click en el botón '+' para agregar un bloque en Rise 360."""
        add_selectors = [
            "button[aria-label*='Add block' i]",
            "button[aria-label*='Agregar bloque' i]",
            ".add-block-button",
            "[data-testid='add-block']",
            "button.block-adder",
            ".lesson-add-block button",
            "button:has-text('+')",
        ]
        for sel in add_selectors:
            try:
                btn = self.page.locator(sel).last
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    return True
            except Exception:
                pass

        # Fallback: keyboard shortcut si existe en Rise 360
        # Rise no tiene shortcut estándar, así que scrollear al final y buscar
        try:
            self.page.keyboard.press("End")
            self.page.wait_for_timeout(300)
            btn = self.page.locator(".add-block-button, [data-testid='add-block']").last
            if btn.is_visible(timeout=1_500):
                btn.click()
                return True
        except Exception:
            pass

        return False

    def _select_block_from_menu(self, menu_label: str) -> bool:
        """Selecciona un tipo de bloque del menú desplegable de Rise 360."""
        try:
            # Buscar en el menú de bloques de Rise 360
            item = self.page.get_by_role("option", name=re.compile(menu_label, re.IGNORECASE))
            if item.first.is_visible(timeout=2_000):
                item.first.click()
                return True
        except Exception:
            pass

        # Fallback: buscar por texto
        try:
            item = self.page.get_by_text(re.compile(menu_label, re.IGNORECASE)).first
            if item.is_visible(timeout=2_000):
                item.click()
                return True
        except Exception:
            pass

        # Fallback: buscar en lista de bloques del panel
        block_panel_selectors = [
            f"[data-block-name='{menu_label.lower()}']",
            f".block-option:has-text('{menu_label}')",
            f"li:has-text('{menu_label}')",
        ]
        for sel in block_panel_selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=1_500):
                    el.click()
                    return True
            except Exception:
                pass

        return False

    def _get_block_menu_labels(self) -> dict:
        """Retorna el mapeo de tipo_bloque → label en menú Rise 360."""
        try:
            import json
            with open(config.LEARNING_MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("block_menu_labels", {})
        except Exception:
            return {
                "text": "Text",
                "section_banner": "Divider",
                "bulleted_list": "Bulleted List",
                "numbered_list": "Numbered List",
                "table": "Table",
                "image": "Image",
                "accordion": "Accordion",
            }

    # ── Edición de contenido ──────────────────────────────────────────────

    def click_block_to_edit(self, block_locator=None, nth: int = -1) -> bool:
        """
        Hace click en un bloque para entrar en modo edición.
        Si nth >= 0, selecciona el n-ésimo bloque editable.
        Si block_locator se provee, hace click en ese elemento específico.
        """
        if block_locator:
            try:
                block_locator.click()
                self.page.wait_for_timeout(400)
                return True
            except Exception as e:
                logger.warning(f"Error al hacer click en bloque: {e}")
                return False

        if nth >= 0:
            try:
                editors = self.page.locator(".ql-editor[contenteditable='true']")
                count = editors.count()
                if count > nth:
                    editors.nth(nth).click()
                    self.page.wait_for_timeout(400)
                    return True
            except Exception as e:
                logger.warning(f"Error al hacer click en bloque #{nth}: {e}")

        return False

    @with_retry(max_attempts=3)
    def insert_text(self, text: str, clear_first: bool = True) -> bool:
        """
        Inserta texto VERBATIM en el editor Quill activo.

        Estrategia:
        1. Localizar .ql-editor[contenteditable="true"] activo
        2. Si clear_first: Ctrl+A → Delete para limpiar
        3. Insertar texto via paste_large_text (keyboard.type o clipboard)

        REGLA: El texto se inserta exactamente como se recibe. Sin cambios.
        """
        quill_sel = ".ql-editor[contenteditable='true']"
        try:
            editors = self.page.locator(quill_sel)
            count = editors.count()
            if count == 0:
                logger.warning("No se encontró editor Quill activo")
                return False

            # Usar el editor que tiene el foco, o el último visible
            editor = None
            for i in range(count):
                e = editors.nth(i)
                if e.is_visible(timeout=500):
                    editor = e
                    # Priorizar el que tiene foco
                    try:
                        if e.evaluate("el => el === document.activeElement"):
                            break
                    except Exception:
                        pass

            if editor is None:
                logger.warning("Ningún editor Quill visible")
                return False

            editor.click()
            self.page.wait_for_timeout(200)

            if clear_first:
                self.page.keyboard.press("Control+a")
                self.page.wait_for_timeout(100)
                self.page.keyboard.press("Delete")
                self.page.wait_for_timeout(100)

            # Insertar texto verbatim
            paste_large_text(self.page, text)
            self.page.wait_for_timeout(300)

            logger.debug(f"Texto insertado ({len(text)} chars)")
            return True

        except Exception as e:
            logger.warning(f"Error insertando texto: {e}")
            take_screenshot(self.page, label="insert_text_fail")
            return False

    def insert_heading(self, text: str, level: int = 1) -> bool:
        """
        Inserta un encabezado (H1, H2, H3) en el editor Quill activo.

        En Quill, los headings se aplican vía el toolbar o atajos de teclado.
        Rise 360 expone el toolbar de Quill — se puede hacer click en los botones H1/H2.
        """
        quill_sel = ".ql-editor[contenteditable='true']"
        try:
            editor = self.page.locator(quill_sel).first
            editor.click()
            self.page.wait_for_timeout(200)

            # Intentar aplicar formato de heading vía toolbar
            heading_applied = self._apply_quill_heading(level)

            # Escribir el texto (verbatim)
            paste_large_text(self.page, text)
            self.page.keyboard.press("Enter")

            if not heading_applied:
                logger.debug(f"Heading H{level} aplicado sin formato (solo texto)")

            return True
        except Exception as e:
            logger.warning(f"Error insertando heading H{level}: {e}")
            return False

    def _apply_quill_heading(self, level: int) -> bool:
        """
        Aplica formato de heading en el toolbar de Quill de Rise 360.
        """
        # Selectores del toolbar de Quill
        toolbar_selectors = [
            f".ql-toolbar button.ql-header[value='{level}']",
            f".ql-header[value='{level}']",
        ]
        for sel in toolbar_selectors:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1_000):
                    btn.click()
                    self.page.wait_for_timeout(200)
                    return True
            except Exception:
                pass

        # Fallback: usar el dropdown de headers si existe
        try:
            header_select = self.page.locator("select.ql-header, .ql-picker.ql-header").first
            if header_select.is_visible(timeout=1_000):
                header_select.select_option(str(level))
                return True
        except Exception:
            pass

        return False

    def set_block_title(self, title: str) -> bool:
        """
        Edita el título del banner principal del curso.
        El título suele estar en un input o contenteditable especial.
        """
        title_selectors = [
            ".lesson-banner-title [contenteditable='true']",
            "[data-block-type='banner'] .ql-editor",
            ".course-title-editor",
            "h1[contenteditable='true']",
            ".banner-heading [contenteditable='true']",
        ]
        for sel in title_selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=2_000):
                    el.click()
                    self.page.wait_for_timeout(200)
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.type(title)
                    logger.info(f"Título del curso establecido: {title}")
                    return True
            except Exception:
                pass

        logger.warning("No se pudo establecer el título del curso")
        return False

    # ── Guardado ──────────────────────────────────────────────────────────

    def save_course(self):
        """
        Rise 360 guarda automáticamente (auto-save).
        Este método fuerza el guardado con Ctrl+S como medida de seguridad
        y espera a que el indicador de guardado confirme.
        """
        try:
            self.page.keyboard.press("Escape")  # Salir de modo edición si está activo
            self.page.wait_for_timeout(300)
            self.page.keyboard.press("Control+s")
            self.page.wait_for_timeout(1_000)

            # Esperar indicador de "Saved" / "Guardado"
            save_selectors = [
                "text=Saved",
                "text=Guardado",
                ".save-indicator",
                "[data-testid='save-status']",
            ]
            for sel in save_selectors:
                try:
                    el = self.page.locator(sel).first
                    el.wait_for(state="visible", timeout=5_000)
                    logger.info("Curso guardado")
                    return True
                except Exception:
                    pass

            logger.debug("No se detectó indicador de guardado (puede ser auto-save)")
            return True
        except Exception as e:
            logger.warning(f"Error en save_course: {e}")
            return False

    # ── Helpers de estado ─────────────────────────────────────────────────

    def get_current_url(self) -> str:
        """Retorna la URL actual del browser."""
        return self.page.url if self.page else ""

    def take_debug_screenshot(self, label: str = "debug"):
        """Toma un screenshot de diagnóstico."""
        if self.page:
            take_screenshot(self.page, label=label)
