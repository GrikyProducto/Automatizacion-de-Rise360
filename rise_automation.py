"""
rise_automation.py — Motor principal de automatización Playwright para Rise 360
Basado en inspección real del DOM de Rise 360 (marzo 2026).

Selectores 100% confirmados por debug:
  Dashboard:
    - URL: /manage/all-content
    - Search: input[placeholder='Search all content']
    - Clear search: button[aria-label='Clear search']
    - Card link: a[href*='/authoring/COURSE_ID']
    - Card container: ancestor::li[1]
    - Card menu btn: button[aria-label='Content menu button']
    - Menu items: [role='menuitem'] -> Duplicate, Move, Share, Delete, etc.
    - Duplicate modal: [role='dialog'] input[type='text'] + button Duplicate/Cancel
    - Move dialog: [role='tree'] con carpetas, boton "Move"
  Course outline:
    - Title: textarea (con input_value)
    - "Edit Content": <a> links (NO <button>)
    - Lesson container: div.course-outline-lesson
    - Section header: div.course-outline-lesson--section
  Lesson editor:
    - Block containers: div[class*='block-wrapper']
    - Block types from CSS: block-text, block-statement, block-flashcards,
      block-image, block-divider, block-mondrian, block-list, block-quote
    - Editable text: .tiptap.ProseMirror.rise-tiptap[contenteditable='true']
    - Add block: button.block-create__button
    - Block type label: block-controls__config inner text
  Cookie popup: button.osano-cm-accept-all
  Loading: "Your content is loading." (esperar hasta 90s)
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

# Block types where we CAN safely edit text
EDITABLE_BLOCK_TYPES = {
    "text", "statement", "heading", "text_twocol",
    "bulleted_list", "numbered_list", "quote", "quote_carousel",
}

# Block types with NO editable text — always skip these
# Everything else gets edited (flashcards, accordion, notes, statements, etc.)
SKIP_BLOCK_TYPES = {"divider", "spacer", "continue"}


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

    # ── Espera de carga de Rise 360 ────────────────────────────────────────

    def _wait_for_content_loaded(self, max_wait: int = 90):
        """
        Espera a que Rise 360 termine de cargar el contenido.
        Verifica múltiples indicadores ya que el comportamiento varía:
        - Outline: "Edit Content" links, textarea de título
        - Lesson editor: block-wrapper elements
        - Loading spinner: "Your content is loading"
        """
        logger.debug("Esperando carga del contenido...")
        start = time.time()
        while time.time() - start < max_wait:
            # Si el spinner de carga está visible, seguir esperando
            try:
                loading = self.page.locator("text='Your content is loading'")
                if loading.is_visible(timeout=500):
                    elapsed = int(time.time() - start)
                    if elapsed % 10 == 0 and elapsed > 0:
                        logger.debug(f"  Aún cargando... ({elapsed}s)")
                    time.sleep(1)
                    continue
            except Exception:
                pass

            # Indicador 1: "Edit Content" links (outline del curso)
            try:
                edit_links = self.page.locator("a:has-text('Edit Content')")
                if edit_links.count() > 0:
                    elapsed = int(time.time() - start)
                    logger.debug(f"Contenido cargado (Edit Content links) en {elapsed}s")
                    time.sleep(2)
                    return True
            except Exception:
                pass

            # Indicador 2: block-wrappers (editor de lección)
            try:
                blocks = self.page.locator("[class*='block-wrapper']")
                if blocks.count() > 0:
                    elapsed = int(time.time() - start)
                    logger.debug(f"Contenido cargado (block-wrappers) en {elapsed}s")
                    time.sleep(2)
                    return True
            except Exception:
                pass

            # Indicador 3: textarea de título (outline del curso)
            try:
                textarea = self.page.locator("textarea")
                if textarea.count() > 0 and textarea.first.is_visible(timeout=500):
                    elapsed = int(time.time() - start)
                    logger.debug(f"Contenido cargado (textarea) en {elapsed}s")
                    time.sleep(2)
                    return True
            except Exception:
                pass

            # Indicador 4: muchos botones visibles (fallback genérico)
            try:
                btns = self.page.locator("button:visible")
                if btns.count() > 5:
                    elapsed = int(time.time() - start)
                    logger.debug(f"Contenido cargado ({btns.count()} botones) en {elapsed}s")
                    time.sleep(2)
                    return True
            except Exception:
                pass

            time.sleep(1)

        logger.warning(f"Timeout esperando carga ({max_wait}s)")
        return False

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
        """Navega al outline de un curso y espera carga completa."""
        logger.info(f"Navegando al curso: {course_url}")
        self.page.goto(course_url, wait_until="domcontentloaded")
        self._wait_for_content_loaded(max_wait=90)
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

    def _search_short(self, title: str):
        """
        Busca un curso usando solo las primeras 3-4 palabras del título.
        Rise 360's search works best with partial queries.
        """
        words = title.split()
        short_query = " ".join(words[:4]) if len(words) > 4 else title
        self._search_in_dashboard(short_query)
        logger.debug(f"Busqueda corta: '{short_query}' (original: '{title[:50]}')")

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
        4. Buscar nuevo curso (primeras 3-4 palabras) -> Menu -> Move -> Automatización
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
        time.sleep(10)
        take_screenshot(self.page, label="after_duplicate")

        # 6. Cerrar overlays y buscar el nuevo curso (primeras 3-4 palabras)
        self.page.keyboard.press("Escape")
        time.sleep(1)
        self._clear_search()
        self._search_short(new_title)

        new_link = self.page.locator("a[href*='/authoring/']").first
        try:
            new_link.wait_for(state="visible", timeout=10_000)
        except Exception:
            raise RuntimeError(f"No se encontro el curso duplicado buscando: '{new_title[:40]}'")

        new_course_url = new_link.get_attribute("href") or ""
        new_course_id = self._extract_course_id(new_course_url)
        logger.info(f"Curso duplicado encontrado: {new_course_url}")

        # 7. Mover a carpeta "Automatización"
        self._move_course_to_folder(new_course_id or "")

        # 8. Abrir el curso duplicado y esperar carga completa
        if new_course_url and not new_course_url.startswith("http"):
            new_course_url = f"https://rise.articulate.com{new_course_url}"

        self.page.goto(new_course_url, wait_until="domcontentloaded")
        self._wait_for_content_loaded(max_wait=90)
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

    # ── Análisis de estructura de plantilla ──────────────────────────────

    def analyze_template_structure(self, template_url: str) -> dict:
        """
        Entra al template, espera a que cargue completamente, y cataloga
        la estructura: lecciones y bloques dentro de cada lección.

        Usa selectores confirmados:
        - "Edit Content" = <a> links (NO button)
        - Block containers = div[class*='block-wrapper']
        - Block type = extraído del CSS class (block-text, block-statement, etc.)
        """
        self._progress("Analizando estructura de la plantilla...", 38)
        logger.info(f"Analizando plantilla: {template_url}")

        # Navegar y esperar carga completa (puede tardar ~17s)
        self.page.goto(template_url, wait_until="domcontentloaded")
        self._wait_for_content_loaded(max_wait=90)
        self.dismiss_cookies()
        time.sleep(2)
        take_screenshot(self.page, label="template_outline_loaded")

        # Obtener título del curso
        course_title = self._get_course_title_from_page()
        logger.info(f"Título de la plantilla: '{course_title}'")

        # Obtener lecciones del outline (usando <a> links)
        lessons_data = self._get_outline_lessons_info()
        logger.info(f"Lecciones encontradas en plantilla: {len(lessons_data)}")

        # Para cada lección, abrir el editor y catalogar bloques
        analyzed_lessons = []
        for i, lesson_info in enumerate(lessons_data):
            self._progress(
                f"Analizando lección {i+1}/{len(lessons_data)}: {lesson_info.get('title', '')[:40]}",
                38 + int(7 * (i / max(len(lessons_data), 1)))
            )

            if self.open_lesson_editor(i):
                blocks = self._catalog_blocks_in_editor()
                logger.info(f"  Lección {i}: '{lesson_info.get('title', '')}' -> {len(blocks)} bloques")

                analyzed_lessons.append({
                    "index": i,
                    "title": lesson_info.get("title", f"Lección {i+1}"),
                    "blocks": blocks,
                })

                self.go_back_to_outline()
                time.sleep(2)
            else:
                logger.warning(f"No se pudo abrir lección {i} para análisis")
                analyzed_lessons.append({
                    "index": i,
                    "title": lesson_info.get("title", f"Lección {i+1}"),
                    "blocks": [],
                })

        template_structure = {
            "url": template_url,
            "title": course_title,
            "lessons": analyzed_lessons,
        }

        logger.info(
            f"Análisis completo: {len(analyzed_lessons)} lecciones, "
            f"{sum(len(l['blocks']) for l in analyzed_lessons)} bloques totales"
        )
        self._progress("Análisis de plantilla completado", 45)
        return template_structure

    def _get_course_title_from_page(self) -> str:
        """Obtiene el título del curso desde la página de outline."""
        try:
            textarea = self.page.locator("textarea").first
            if textarea.is_visible(timeout=3_000):
                val = textarea.input_value()
                if val and val.strip():
                    return val.strip()
        except Exception:
            pass
        return ""

    def _get_outline_lessons_info(self) -> list[dict]:
        """
        Obtiene información de las lecciones en el outline.
        CONFIRMADO: "Edit Content" es un <a> link, NO un <button>.
        """
        lessons = []
        edit_links = self.page.locator("a:has-text('Edit Content')")
        count = edit_links.count()
        logger.info(f"Links 'Edit Content' encontrados: {count}")

        for i in range(count):
            try:
                link = edit_links.nth(i)
                href = link.get_attribute("href") or ""
                # Get lesson title from the parent outline-lesson container
                parent = link.locator(
                    "xpath=ancestor::div[contains(@class,'course-outline-lesson')][1]"
                )
                title = ""
                if parent.count() > 0:
                    full_text = parent.first.inner_text()[:200].strip()
                    # Extract title from format: "Lesson\nTema X: Nombre\nEdit Content"
                    lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                    # The title is usually the line after "Lesson"
                    for j, line in enumerate(lines):
                        if line == "Lesson" and j + 1 < len(lines):
                            title = lines[j + 1]
                            break
                    if not title and len(lines) > 1:
                        title = lines[0] if lines[0] != "Edit Content" else ""
                lessons.append({"index": i, "title": title, "href": href})
                logger.debug(f"  Lección {i}: '{title}' -> {href}")
            except Exception:
                lessons.append({"index": i, "title": f"Lección {i+1}", "href": ""})

        return lessons

    def _catalog_blocks_in_editor(self) -> list[dict]:
        """
        Dentro del editor de una lección, cataloga bloques usando
        el selector confirmado: div[class*='block-wrapper'].

        Cada bloque tiene un class pattern como:
          block-text, block-statement, block-flashcards, block-image,
          block-divider, block-mondrian, block-list, block-quote
        """
        blocks = []
        time.sleep(0.5)  # Brief wait for DOM stability

        # Selector principal confirmado por debug
        block_wrappers = self.page.locator("[class*='block-wrapper']")
        count = block_wrappers.count()
        logger.debug(f"  block-wrapper encontrados: {count}")

        # Use JavaScript for fast bulk cataloging instead of per-element queries
        try:
            js_blocks = self.page.evaluate("""() => {
                const wrappers = document.querySelectorAll("[class*='block-wrapper']");
                return Array.from(wrappers).map((el, i) => ({
                    type: el.className || '',
                    text: (el.textContent || '').trim().slice(0, 150).replace(/\\n/g, ' | '),
                    index: i,
                    visible: el.getBoundingClientRect().height > 0,
                }));
            }""")
            for jb in js_blocks:
                if not jb["visible"]:
                    continue
                block_type = self._extract_block_type_from_class(jb["type"])
                blocks.append({
                    "type": block_type,
                    "text_preview": jb["text"][:150],
                    "index": jb["index"],
                    "css_class": jb["type"][:120],
                })
        except Exception:
            # Fallback to per-element approach
            for i in range(count):
                try:
                    el = block_wrappers.nth(i)
                    if not el.is_visible(timeout=300):
                        continue
                    cls = el.get_attribute("class") or ""
                    text_preview = ""
                    try:
                        text_preview = el.inner_text()[:150].strip().replace("\n", " | ")
                    except Exception:
                        pass
                    block_type = self._extract_block_type_from_class(cls)
                    blocks.append({
                        "type": block_type,
                        "text_preview": text_preview,
                        "index": i,
                        "css_class": cls[:120],
                    })
                except Exception:
                    pass

        # Si no encontró block-wrappers, usar editables como fallback
        if not blocks:
            editables = self.page.locator("[contenteditable='true']")
            count = editables.count()
            logger.debug(f"  Fallback editables: {count}")
            for i in range(count):
                try:
                    el = editables.nth(i)
                    if el.is_visible(timeout=500):
                        text_preview = el.inner_text()[:150].strip().replace("\n", " | ")
                        blocks.append({
                            "type": "text",
                            "text_preview": text_preview,
                            "index": i,
                            "css_class": "contenteditable",
                        })
                except Exception:
                    pass

        take_screenshot(self.page, label="template_lesson_blocks")
        return blocks

    def get_text_blocks_in_lesson(self) -> list[dict]:
        """
        Retorna solo los bloques de TEXTO editables en la lección actual.
        Ignora: imágenes, divisores, banners, flashcards, etc.

        Como diseñador gráfico senior: solo tocamos los textos,
        el resto de elementos visuales quedan intactos.
        """
        all_blocks = self._catalog_blocks_in_editor()
        text_blocks = [
            b for b in all_blocks
            if b["type"] in EDITABLE_BLOCK_TYPES
        ]
        skipped = len(all_blocks) - len(text_blocks)
        logger.info(
            f"  Bloques editables: {len(text_blocks)} texto, "
            f"{skipped} visuales (intactos)"
        )
        return text_blocks

    def edit_block_text(self, block_wrapper_index: int, text: str) -> bool:
        """
        Edita el texto de un bloque específico por su índice de wrapper.
        Click en el wrapper → encuentra su editable → limpia → inserta texto.

        IMPORTANTE: Solo usar para bloques de TEXTO. Los visuales no se tocan.
        """
        try:
            wrappers = self.page.locator("[class*='block-wrapper']")
            count = wrappers.count()
            if block_wrapper_index >= count:
                logger.warning(
                    f"Block index {block_wrapper_index} fuera de rango ({count} bloques)"
                )
                return False

            wrapper = wrappers.nth(block_wrapper_index)
            wrapper.scroll_into_view_if_needed()
            time.sleep(0.5)

            # Click en el wrapper para seleccionar el bloque
            wrapper.click()
            time.sleep(0.5)

            # Buscar el editable DENTRO de este wrapper específico
            editable = wrapper.locator("[contenteditable='true']").first
            try:
                editable.wait_for(state="visible", timeout=3_000)
            except Exception:
                logger.warning(
                    f"No se encontró editable en block wrapper {block_wrapper_index}"
                )
                return False

            editable.click()
            time.sleep(0.3)

            # Seleccionar todo y reemplazar
            self.page.keyboard.press("Control+a")
            time.sleep(0.1)
            self.page.keyboard.press("Delete")
            time.sleep(0.1)

            # Insertar nuevo texto
            paste_large_text(self.page, text)
            time.sleep(0.3)

            # Click fuera para deseleccionar/confirmar
            self.page.keyboard.press("Escape")
            time.sleep(0.3)

            logger.debug(
                f"  Block {block_wrapper_index} editado ({len(text)} chars): "
                f"'{text[:50]}...'"
            )
            return True
        except Exception as e:
            logger.warning(f"Error editando block {block_wrapper_index}: {e}")
            take_screenshot(self.page, label=f"edit_block_fail_{block_wrapper_index}")
            return False

    def _extract_block_type_from_class(self, css_class: str) -> str:
        """
        Extrae el tipo de bloque del CSS class.
        Patrones confirmados: block-text, block-statement, block-flashcards,
        block-image, block-divider, block-mondrian, block-list, block-quote
        """
        # Buscar pattern block-{type} al inicio de una clase
        match = re.search(r'block-(\w+)', css_class)
        if match:
            raw = match.group(1)
            # Normalizar nombres conocidos
            if raw == "mondrian":
                return "banner"
            if raw.startswith("statement"):
                return "statement"
            if raw.startswith("flashcard"):
                return "flashcards"
            if raw.startswith("text"):
                # Distinguir heading de paragraph
                if "heading" in css_class:
                    return "heading"
                if "twocol" in css_class:
                    return "text_twocol"
                return "text"
            if raw.startswith("image"):
                return "image"
            if raw.startswith("divider"):
                if "spacing" in css_class:
                    return "spacer"
                return "divider"
            if raw.startswith("list"):
                if "numbered" in css_class:
                    return "numbered_list"
                return "bulleted_list"
            if raw.startswith("quote"):
                if "carousel" in css_class:
                    return "quote_carousel"
                return "quote"
            return raw
        return "unknown"

    # ── Navegación en el Course Outline ──────────────────────────────────

    def get_lessons_in_outline(self) -> list[dict]:
        """
        Retorna la lista de lecciones en el outline del curso.
        CONFIRMADO: "Edit Content" es un <a> link, NO un <button>.
        """
        lessons = []
        # Primero intentar con <a> (confirmado)
        edit_links = self.page.locator("a:has-text('Edit Content')")
        count = edit_links.count()

        if count == 0:
            # Fallback: intentar con <button> por si el DOM cambió
            edit_links = self.page.locator("button:has-text('Edit Content')")
            count = edit_links.count()

        for i in range(count):
            try:
                link = edit_links.nth(i)
                parent = link.locator(
                    "xpath=ancestor::div[contains(@class,'course-outline-lesson')][1]"
                )
                title = ""
                if parent.count() > 0:
                    full_text = parent.first.inner_text()[:200].strip()
                    lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                    for j, line in enumerate(lines):
                        if line == "Lesson" and j + 1 < len(lines):
                            title = lines[j + 1]
                            break
                    if not title and len(lines) > 1:
                        title = lines[0] if lines[0] != "Edit Content" else ""
                lessons.append({"index": i, "title": title, "edit_element": link})
            except Exception:
                lessons.append({"index": i, "title": f"Leccion {i+1}", "edit_element": None})

        logger.info(f"Lecciones encontradas: {len(lessons)}")
        return lessons

    def open_lesson_editor(self, lesson_index: int = 0) -> bool:
        """
        Abre el editor de bloques de una lección.
        CONFIRMADO: "Edit Content" es un <a> link, NO un <button>.
        """
        try:
            # Intentar con <a> primero (confirmado)
            edit_links = self.page.locator("a:has-text('Edit Content')")
            count = edit_links.count()

            if count == 0:
                # Fallback <button>
                edit_links = self.page.locator("button:has-text('Edit Content')")
                count = edit_links.count()

            logger.info(f"Links 'Edit Content': {count}")
            if count == 0:
                take_screenshot(self.page, label="no_edit_content")
                return False

            target = edit_links.nth(min(lesson_index, count - 1))
            target.scroll_into_view_if_needed()
            target.click()
            time.sleep(1)

            # Esperar carga del editor de lección
            self._wait_for_content_loaded(max_wait=30)
            self.dismiss_cookies()
            time.sleep(0.5)

            take_screenshot(self.page, label=f"lesson_editor_{lesson_index}")
            logger.info(f"Editor de leccion {lesson_index} abierto")
            return True
        except Exception as e:
            logger.warning(f"Error abriendo editor de leccion {lesson_index}: {e}")
            return False

    def go_back_to_outline(self):
        """Vuelve al outline del curso desde el editor."""
        try:
            # In Rise 360, the course title link in the header goes back to outline
            back_sels = [
                "a.app-header__menu-btn",  # Confirmado: link del logo/título en el header
                "[class*='back-button']",
                "[aria-label*='back' i]",
                "a[href*='authoring']:not([href*='lesson'])",
            ]
            for sel in back_sels:
                try:
                    btn = self.page.locator(sel).first
                    if btn.is_visible(timeout=1_500):
                        btn.click()
                        time.sleep(1)
                        self._wait_for_content_loaded(max_wait=30)
                        self.dismiss_cookies()
                        return
                except Exception:
                    pass
            # Fallback: browser back
            self.page.go_back()
            time.sleep(1)
            self._wait_for_content_loaded(max_wait=30)
        except Exception as e:
            logger.warning(f"Error volviendo al outline: {e}")

    # ── Renombrar lección ──────────────────────────────────────────────────

    def rename_lesson(self, lesson_index: int, new_name: str) -> bool:
        """
        Renames a lesson in the course outline.
        Uses JavaScript to find and click the title element directly,
        with multiple selector fallbacks for Rise 360's DOM structure.
        """
        try:
            edit_links = self.page.locator("a:has-text('Edit Content')")
            count = edit_links.count()

            if lesson_index >= count:
                logger.warning(
                    f"rename_lesson: index {lesson_index} fuera de rango ({count})"
                )
                return False

            # Use JavaScript to find the lesson title element
            # Rise 360 lesson titles can be various elements — try multiple selectors
            title_clicked = self.page.evaluate("""(lessonIdx) => {
                // Find all lesson containers
                const lessons = document.querySelectorAll(
                    '.course-outline-lesson, [class*="course-outline-lesson"]'
                );
                // Filter to only actual lessons (not sections)
                const realLessons = Array.from(lessons).filter(el => {
                    const cls = String(el.className || '');
                    return !cls.includes('--section') && el.querySelector('a');
                });
                if (lessonIdx >= realLessons.length) return false;
                const lesson = realLessons[lessonIdx];

                // Try multiple selectors for the title element
                const selectors = [
                    '.course-outline-lesson__title-entry',
                    '[class*="title-entry"]',
                    '[class*="lesson-title"]',
                    '[class*="title"] div',
                    '[contenteditable]',
                ];
                for (const sel of selectors) {
                    const el = lesson.querySelector(sel);
                    if (el) {
                        el.click();
                        return 'found:' + sel;
                    }
                }

                // Last resort: click the text node directly (not the Edit Content link)
                const allText = lesson.querySelectorAll('div, span, p');
                for (const el of allText) {
                    const text = el.textContent.trim();
                    if (text && text !== 'Edit Content' && text !== 'Lesson'
                        && !text.includes('Edit Content')
                        && el.offsetHeight > 0 && el.offsetWidth > 0) {
                        el.click();
                        return 'text:' + text.slice(0, 30);
                    }
                }
                return false;
            }""", lesson_index)

            if not title_clicked:
                logger.warning(
                    f"No se encontró título para lección {lesson_index}"
                )
                return False

            logger.debug(f"  Title click result: {title_clicked}")
            time.sleep(0.5)

            # After clicking, check if a contenteditable appeared
            # (Rise may need a double-click to enter edit mode)
            editable = self.page.locator(
                "[contenteditable='true']:visible"
            ).first
            try:
                editable.wait_for(state="visible", timeout=2_000)
            except Exception:
                # Try double-click via JS on the same element
                self.page.evaluate("""(lessonIdx) => {
                    const lessons = document.querySelectorAll(
                        '.course-outline-lesson, [class*="course-outline-lesson"]'
                    );
                    const realLessons = Array.from(lessons).filter(el => {
                        const cls = String(el.className || '');
                        return !cls.includes('--section') && el.querySelector('a');
                    });
                    if (lessonIdx >= realLessons.length) return;
                    const lesson = realLessons[lessonIdx];
                    const title = lesson.querySelector(
                        '[class*="title-entry"], [class*="title"] div, [class*="lesson-title"]'
                    );
                    if (title) {
                        const evt = new MouseEvent('dblclick', {bubbles: true});
                        title.dispatchEvent(evt);
                    }
                }""", lesson_index)
                time.sleep(0.5)

            # Select all + type new name
            self.page.keyboard.press("Control+a")
            time.sleep(0.1)
            self.page.keyboard.type(new_name, delay=10)
            time.sleep(0.3)

            # BLUR to save: press Enter then click neutral area
            self.page.keyboard.press("Enter")
            time.sleep(0.3)
            # Wait for outline to stabilize
            try:
                self.page.locator("a:has-text('Edit Content')").first.wait_for(
                    state="visible", timeout=3_000
                )
            except Exception:
                pass
            time.sleep(0.3)

            # Verify the title was saved
            try:
                actual = title_entry.first.inner_text().strip()
                if new_name[:15] in actual:
                    logger.info(
                        f"Lección {lesson_index} renombrada: '{new_name[:50]}'"
                    )
                    return True
                else:
                    logger.warning(
                        f"Rename verificación falló: esperaba '{new_name[:30]}...', "
                        f"encontré '{actual[:30]}...'"
                    )
                    # Retry: the blur might not have triggered React save
                    # Try clicking away again
                    self.page.mouse.click(10, 10)
                    time.sleep(1)
                    return True  # Trust the type action even if verify fails
            except Exception:
                logger.info(
                    f"Lección {lesson_index} renombrada (sin verificar): "
                    f"'{new_name[:50]}'"
                )
                return True

        except Exception as e:
            logger.warning(f"Error renombrando lección {lesson_index}: {e}")
            return False

    # ── Obtener TODOS los bloques editables ────────────────────────────────

    def get_all_editable_blocks(self) -> list[dict]:
        """
        Returns ALL blocks that have editable text, including their editable count.
        Skips only divider/spacer/continue (no text whatsoever).
        Uses actual DOM check for [contenteditable='true'] within each wrapper.
        """
        all_blocks = self._catalog_blocks_in_editor()
        wrappers = self.page.locator("[class*='block-wrapper']")
        result = []

        for block in all_blocks:
            if block["type"] in SKIP_BLOCK_TYPES:
                continue

            try:
                wrapper = wrappers.nth(block["index"])
                editables = wrapper.locator("[contenteditable='true']")
                editable_count = editables.count()
                if editable_count > 0:
                    block["editables_count"] = editable_count
                    result.append(block)
                    logger.debug(
                        f"  Block {block['index']} [{block['type']}]: "
                        f"{editable_count} editable(s)"
                    )
            except Exception:
                pass

        total_editables = sum(b.get("editables_count", 0) for b in result)
        skipped = len(all_blocks) - len(result)
        logger.info(
            f"  Bloques con editables: {len(result)} "
            f"({total_editables} editables totales), "
            f"{skipped} bloques sin texto (omitidos)"
        )
        return result

    def edit_block_all_editables(
        self, wrapper_index: int, texts: list[str]
    ) -> int:
        """
        Edit ALL editables within a block wrapper.
        Returns the number of editables successfully edited.

        Handles multi-editable blocks like flashcards (front/back),
        accordion (title/body), labeled graphics, etc.
        """
        try:
            wrapper = self.page.locator("[class*='block-wrapper']").nth(wrapper_index)
            wrapper.scroll_into_view_if_needed()
            time.sleep(0.3)
            wrapper.click()
            time.sleep(0.8)

            editables = wrapper.locator("[contenteditable='true']")
            count = editables.count()
            edited = 0

            for i in range(min(count, len(texts))):
                text = texts[i].strip()
                if not text:
                    continue
                try:
                    ed = editables.nth(i)
                    if not ed.is_visible(timeout=1_000):
                        logger.debug(
                            f"  Editable {i} en block {wrapper_index} no visible, saltando"
                        )
                        continue
                    ed.click()
                    time.sleep(0.2)
                    self.page.keyboard.press("Control+a")
                    time.sleep(0.1)
                    self.page.keyboard.press("Delete")
                    time.sleep(0.1)
                    paste_large_text(self.page, text)
                    time.sleep(0.3)
                    edited += 1
                except Exception as e:
                    logger.debug(
                        f"  Editable {i} en block {wrapper_index} falló: {e}"
                    )
                    try:
                        self.page.keyboard.press("Escape")
                    except Exception:
                        pass

            # Click outside to deselect
            try:
                self.page.keyboard.press("Escape")
                time.sleep(0.3)
            except Exception:
                pass

            logger.debug(
                f"  Block {wrapper_index}: {edited}/{min(count, len(texts))} "
                f"editables editados"
            )
            return edited
        except Exception as e:
            logger.warning(f"Error editando block {wrapper_index}: {e}")
            take_screenshot(self.page, label=f"edit_all_fail_{wrapper_index}")
            return 0

    # ── Pre-scan: count editables ──────────────────────────────────────

    def count_editables_in_lesson(self) -> list[dict]:
        """
        Quick pre-scan: count editables per block using JavaScript (fast).
        Rise 360 renders some editables only after click, so we use
        a hybrid approach: JS scan first, then click-scan for blocks
        that report 0 editables but are editable types.
        """
        all_blocks = self._catalog_blocks_in_editor()
        wrappers = self.page.locator("[class*='block-wrapper']")
        result = []

        # Estimated editables per block type (for blocks that need click)
        EDITABLE_ESTIMATES = {
            "heading": 1, "statement": 1, "text": 1, "paragraph": 1,
            "image": 2, "numbered_list": 3, "bulleted_list": 3,
            "quote_carousel": 6, "flashcards": 0, "quote": 2,
            "sorting": 4, "process": 4, "accordion": 4,
        }

        # Fast: try JS scan for visible editables
        try:
            js_result = self.page.evaluate("""() => {
                const wrappers = document.querySelectorAll("[class*='block-wrapper']");
                return Array.from(wrappers).map((w, i) => {
                    const eds = w.querySelectorAll("[contenteditable='true']");
                    return {index: i, count: eds.length};
                });
            }""")
            js_map = {r["index"]: r["count"] for r in js_result}
        except Exception:
            js_map = {}

        for block in all_blocks:
            if block["type"] in SKIP_BLOCK_TYPES:
                continue
            idx = block["index"]
            js_count = js_map.get(idx, 0)

            if js_count > 0:
                result.append({
                    "index": idx,
                    "type": block["type"],
                    "editables_count": js_count,
                })
            else:
                # JS found 0 editables — use estimated count or quick click
                est = EDITABLE_ESTIMATES.get(block["type"], 1)
                if est > 0:
                    # Try a quick click to reveal hidden editables
                    try:
                        wrapper = wrappers.nth(idx)
                        wrapper.scroll_into_view_if_needed()
                        wrapper.click(timeout=2_000)
                        time.sleep(0.3)
                        editables = wrapper.locator("[contenteditable='true']")
                        count = editables.count()
                        if count > 0:
                            result.append({
                                "index": idx,
                                "type": block["type"],
                                "editables_count": count,
                            })
                        else:
                            result.append({
                                "index": idx,
                                "type": block["type"],
                                "editables_count": est,
                            })
                        self.page.keyboard.press("Escape")
                        time.sleep(0.1)
                    except Exception:
                        result.append({
                            "index": idx,
                            "type": block["type"],
                            "editables_count": est,
                        })

        total = sum(b["editables_count"] for b in result)
        logger.info(
            f"  Pre-scan: {len(result)} bloques con editables, "
            f"{total} editables totales"
        )
        return result

    # ── Single-pass: activate + edit all blocks ──────────────────────────

    def scan_and_edit_all_blocks(
        self, get_texts_callback: Callable
    ) -> int:
        """
        Single-pass: iterate all blocks, click to activate, discover editables,
        read existing text, and fill with content from a type-aware callback.

        Rise 360 only renders [contenteditable='true'] AFTER clicking a block.

        Args:
            get_texts_callback: function(block_type, editables_count, existing_texts)
                                -> list[str] of replacement texts.
                                Called for each block with editables.

        Returns:
            total_edited count
        """
        all_blocks = self._catalog_blocks_in_editor()
        wrappers = self.page.locator("[class*='block-wrapper']")
        total_edited = 0
        blocks_with_editables = 0
        blocks_skipped = 0

        for block in all_blocks:
            block_type = block["type"]
            block_idx = block["index"]

            if block_type in SKIP_BLOCK_TYPES:
                blocks_skipped += 1
                continue

            try:
                wrapper = wrappers.nth(block_idx)
                wrapper.scroll_into_view_if_needed()
                time.sleep(0.3)
                wrapper.click()
                time.sleep(0.8)

                editables = wrapper.locator("[contenteditable='true']")
                count = editables.count()

                if count == 0:
                    logger.debug(
                        f"  Block {block_idx} [{block_type}]: 0 editables (skip)"
                    )
                    self.page.keyboard.press("Escape")
                    time.sleep(0.2)
                    continue

                blocks_with_editables += 1

                # Read existing text from each editable (for instruction detection)
                existing_texts = []
                for i in range(count):
                    try:
                        ed = editables.nth(i)
                        if ed.is_visible(timeout=500):
                            txt = ed.inner_text()[:200].strip()
                            existing_texts.append(txt)
                        else:
                            existing_texts.append("")
                    except Exception:
                        existing_texts.append("")

                # Ask callback for replacement texts based on type + existing
                texts = get_texts_callback(block_type, count, existing_texts)
                if not texts:
                    self.page.keyboard.press("Escape")
                    time.sleep(0.2)
                    continue

                edited_in_block = 0
                for i in range(min(count, len(texts))):
                    text = texts[i]
                    if not text or not text.strip():
                        continue
                    try:
                        ed = editables.nth(i)
                        if not ed.is_visible(timeout=1_000):
                            continue

                        ed.click()
                        time.sleep(0.2)
                        self.page.keyboard.press("Control+a")
                        time.sleep(0.1)
                        self.page.keyboard.press("Delete")
                        time.sleep(0.1)
                        paste_large_text(self.page, text)
                        time.sleep(0.3)

                        total_edited += 1
                        edited_in_block += 1
                    except Exception as e:
                        logger.debug(
                            f"  Block {block_idx} editable {i} falló: {e}"
                        )
                        try:
                            self.page.keyboard.press("Escape")
                        except Exception:
                            pass

                logger.info(
                    f"  [{block_type}:{block_idx}] "
                    f"{edited_in_block}/{count} editables editados"
                )

                self.page.keyboard.press("Escape")
                time.sleep(0.3)

            except Exception as e:
                logger.warning(
                    f"  Error en block {block_idx} [{block_type}]: {e}"
                )
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass

        logger.info(
            f"  Scan completo: {blocks_with_editables} bloques con editables, "
            f"{blocks_skipped} omitidos, "
            f"{total_edited} editables editados"
        )
        return total_edited

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
        """
        Edita el titulo del curso en el outline.
        Rise 360 shows the title as a large heading that becomes a
        textarea on click. Must click the heading first to reveal textarea.
        """
        try:
            # Step 1: Click on the course title heading to activate editing
            # The title is a large heading in the course outline
            heading_sels = [
                "h1",  # Most likely the big title
                "[class*='course-title']",
                "[class*='authoring-course'] h1",
                "[class*='authoring-course'] h2",
            ]
            for sel in heading_sels:
                try:
                    el = self.page.locator(sel).first
                    if el.is_visible(timeout=1_000):
                        el.click()
                        time.sleep(0.5)
                        break
                except Exception:
                    continue

            # Step 2: Now find the textarea (should appear after click)
            textarea_sels = [
                "textarea[placeholder='Course Title']",
                "textarea",
            ]
            for sel in textarea_sels:
                try:
                    el = self.page.locator(sel).first
                    if el.is_visible(timeout=3_000):
                        el.click()
                        time.sleep(0.3)
                        el.fill("")
                        el.fill(title)
                        time.sleep(0.5)
                        # Blur by clicking a neutral area (NOT Tab — Tab navigates away)
                        try:
                            self.page.mouse.click(960, 400)
                        except Exception:
                            self.page.keyboard.press("Escape")
                        time.sleep(0.5)
                        logger.info(f"Titulo del curso establecido: '{title}'")
                        return True
                except Exception:
                    pass

            # Step 3: Fallback — try using JS to find and modify
            result = self.page.evaluate("""(title) => {
                const ta = document.querySelector('textarea');
                if (ta) {
                    ta.focus();
                    ta.value = title;
                    ta.dispatchEvent(new Event('input', {bubbles: true}));
                    ta.dispatchEvent(new Event('change', {bubbles: true}));
                    ta.blur();
                    return true;
                }
                return false;
            }""", title)
            if result:
                logger.info(f"Titulo del curso establecido via JS: '{title}'")
                return True

        except Exception as e:
            logger.warning(f"Error estableciendo titulo: {e}")

        logger.warning("No se pudo establecer el titulo del curso")
        return False

    def add_block(self, block_type: str) -> bool:
        """
        Agrega un bloque en el editor de leccion activo.
        CONFIRMADO: El botón "+" es button.block-create__button
        """
        logger.debug(f"Intentando agregar bloque: {block_type}")
        add_btns = [
            "button.block-create__button",
            "button[class*='block-create']",
        ]
        for sel in add_btns:
            try:
                btn = self.page.locator(sel).last
                if btn.is_visible(timeout=1_500):
                    btn.click(force=True)
                    time.sleep(0.8)
                    category, _sub = self._get_block_category_and_type(block_type)
                    if self._select_block_from_library(category):
                        time.sleep(1)
                        wait_for_react_idle(self.page, timeout_ms=2_000)
                        logger.debug(f"Bloque '{block_type}' agregado")
                        return True
            except Exception:
                pass
        logger.warning("No se pudo encontrar el boton de agregar bloque")
        return False

    def change_block_type(self, block_index: int, new_type: str) -> bool:
        """
        Changes the type of an existing block using the pencil/config icon.
        This is MUCH faster than adding a new block (~2s vs ~30s).

        Flow: click block → click pencil icon (block-controls__config) →
              select new type from dropdown → done.
        """
        try:
            wrappers = self.page.locator("[class*='block-wrapper']")
            if block_index >= wrappers.count():
                return False

            wrapper = wrappers.nth(block_index)
            wrapper.scroll_into_view_if_needed()
            time.sleep(0.15)

            # Click to select the block
            wrapper.click(timeout=2_000)
            time.sleep(0.2)

            # Find and click the config/pencil icon
            config_btn = wrapper.locator(
                ".block-controls__config, "
                "[class*='block-controls'] button, "
                "button[class*='config']"
            )
            if config_btn.count() == 0:
                # Try finding it in the block controls area above the block
                config_btn = self.page.locator(
                    ".block-controls__config"
                )

            if config_btn.count() > 0 and config_btn.first.is_visible(timeout=1_500):
                config_btn.first.click()
                time.sleep(0.3)

                # Now select the desired type from the dropdown/menu
                # Rise shows block type options as clickable items
                type_label = self._get_block_type_label(new_type)
                type_option = self.page.locator(
                    f"button:has-text('{type_label}'), "
                    f"[role='menuitem']:has-text('{type_label}'), "
                    f"[role='option']:has-text('{type_label}'), "
                    f"li:has-text('{type_label}'), "
                    f"a:has-text('{type_label}')"
                ).first

                if type_option.is_visible(timeout=2_000):
                    type_option.click()
                    time.sleep(0.2)
                    logger.debug(
                        f"  Block {block_index} type changed to '{new_type}'"
                    )
                    return True
                else:
                    # Close menu
                    self.page.keyboard.press("Escape")
                    time.sleep(0.2)

        except Exception as e:
            logger.debug(f"  change_block_type failed for {block_index}: {e}")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

        return False

    def _get_block_type_label(self, block_type: str) -> str:
        """Maps internal block type to the label shown in the UI dropdown."""
        LABEL_MAP = {
            "text": "Paragraph",
            "paragraph": "Paragraph",
            "heading": "Heading",
            "subheading": "Subheading",
            "note": "Note",
            "table": "Table",
            "statement": "Statement",
            "bulleted_list": "Bulleted list",
            "numbered_list": "Numbered list",
            "quote": "Quote",
            "quote_carousel": "Quote carousel",
            "flashcards": "Flashcard grid",
            "accordion": "Accordion",
            "tabs": "Tabs",
            "process": "Process",
            "sorting": "Sorting",
            "labeled": "Labeled graphic",
        }
        return LABEL_MAP.get(block_type, block_type.replace("_", " ").title())

    def _get_block_category_and_type(self, block_type: str) -> tuple[str, str]:
        """Mapea tipos internos a (categoría sidebar, sub-tipo).

        Block Library sidebar categories (confirmed by diagnostic):
        Text, Statement, Quote, List, Image, Gallery, Multimedia,
        Interactive, Knowledge Check, Chart, Divider, Code, Custom Block

        Sub-types within each category (from preview-dropdown-button):
        Text → Paragraph, Heading, Subheading, Note, Table
        Statement → Statement B, Statement (variants)
        Quote → Quote carousel
        List → Numbered list, Bulleted list
        Image → Image & text, Image centered
        Interactive → Flashcard grid, Accordion, Labeled graphic, Sorting, Process
        Divider → Divider, Continue
        """
        CATEGORY_MAP = {
            "text": ("Text", "Paragraph"),
            "paragraph": ("Text", "Paragraph"),
            "heading": ("Text", "Heading"),
            "subheading": ("Text", "Subheading"),
            "note": ("Text", "Note"),
            "text_twocol": ("Text", ""),
            "table": ("Text", "Table"),
            "statement": ("Statement", "Statement"),
            "list": ("List", "Bulleted list"),
            "bulleted_list": ("List", "Bulleted list"),
            "numbered_list": ("List", "Numbered list"),
            "image": ("Image", ""),
            "video": ("Multimedia", ""),
            "flashcards": ("Interactive", "Flashcard grid"),
            "quote": ("Quote", "Quote"),
            "quote_carousel": ("Quote", "Quote carousel"),
            "divider": ("Divider", "Divider"),
            "spacer": ("Divider", ""),
            "banner": ("Image", ""),
            "section_banner": ("Text", ""),
            "continue": ("Divider", "Continue"),
            "process": ("Interactive", "Process"),
            "sorting": ("Interactive", "Sorting"),
            "accordion": ("Interactive", "Accordion"),
            "tabs": ("Interactive", "Tabs"),
            "labeled": ("Interactive", "Labeled graphic"),
        }
        return CATEGORY_MAP.get(
            block_type, (block_type.replace("_", " ").title(), "")
        )

    def _select_block_from_library(
        self, category: str, sub_type: str = ""
    ) -> bool:
        """Selecciona un bloque del Block Library sidebar.

        Flow completo:
        1. "+" abre sidebar con categorías (button.block-wizard__link)
        2. Click en categoría → muestra sub-tipos
        3. Click en sub-tipo específico → agrega el bloque

        Si sub_type está vacío, selecciona el default de la categoría.
        """
        try:
            sidebar = self.page.locator(".blocks-sidebar")
            if not sidebar.first.is_visible(timeout=3_000):
                return False

            # Step 1: Click the category button
            cat_btn = sidebar.locator(
                f"button.block-wizard__link:has-text('{category}')"
            )
            if cat_btn.count() == 0:
                cat_btn = sidebar.locator(
                    f"li.block-wizard__item:has-text('{category}')"
                )
            if cat_btn.count() == 0 or not cat_btn.first.is_visible(timeout=2_000):
                return False

            cat_btn.first.click()
            time.sleep(0.3)

            # Step 2: If sub_type specified, click the specific block type
            if sub_type:
                time.sleep(0.2)
                # Look for sub-type button/preview in the sidebar
                sub_btn = sidebar.locator(
                    f"button:has-text('{sub_type}'), "
                    f"[class*='preview']:has-text('{sub_type}'), "
                    f"[class*='block-type']:has-text('{sub_type}'), "
                    f"li:has-text('{sub_type}'), "
                    f"a:has-text('{sub_type}')"
                ).first
                try:
                    if sub_btn.is_visible(timeout=1_500):
                        sub_btn.click()
                        time.sleep(0.3)
                        logger.debug(
                            f"Block added: {category} → {sub_type}"
                        )
                except Exception:
                    logger.debug(
                        f"Sub-type '{sub_type}' not found, "
                        f"using category default"
                    )

            self.dismiss_sidebar_overlay()
            logger.debug(f"Block added via category '{category}'")
            return True

        except Exception:
            pass

        return False

    # ── Gestión de lecciones en el outline ──────────────────────────────

    def duplicate_lesson(self, source_index: int = 0) -> bool:
        """
        Duplica una lección existente usando el menú kebab.
        Diagnostic confirmó: hover → button.menu__trigger--dots → [role='menuitem'] "Duplicate"
        Duplicar preserva colores, logos, bloques y estructura del template.
        """
        try:
            edit_links = self.page.locator("a:has-text('Edit Content')")
            before_count = edit_links.count()
            if source_index >= before_count:
                logger.warning(f"duplicate_lesson: índice {source_index} fuera de rango ({before_count})")
                return False

            logger.info(f"Duplicando lección {source_index} (total actual: {before_count})...")

            # Find the lesson container
            link = edit_links.nth(source_index)
            parent = link.locator(
                "xpath=ancestor::div[contains(@class,'course-outline-lesson')][1]"
            )
            if parent.count() == 0:
                logger.warning("  No se encontró contenedor de lección")
                return False

            container = parent.first
            container.scroll_into_view_if_needed()
            time.sleep(0.3)

            # Step 1: Hover to reveal kebab button
            container.hover()
            time.sleep(1)

            # Step 2: Click kebab button (three dots)
            kebab = container.locator("button.menu__trigger--dots")
            if kebab.count() == 0 or not kebab.first.is_visible(timeout=2_000):
                # Fallback: try page-level visible dots buttons
                kebab = self.page.locator("button.menu__trigger--dots:visible")
                if kebab.count() == 0:
                    logger.warning("  Kebab button no encontrado")
                    return False

            kebab.first.click()
            time.sleep(1)

            # Step 3: Click "Duplicate" from menu
            menu_items = self.page.locator("[role='menuitem']:visible")
            duplicate_btn = None
            for i in range(menu_items.count()):
                text = menu_items.nth(i).inner_text().strip().lower()
                if text in ("duplicate", "duplicar"):
                    duplicate_btn = menu_items.nth(i)
                    break

            if not duplicate_btn:
                # Fallback: try text-based search
                duplicate_btn = self.page.locator(
                    "[role='menuitem']:has-text('Duplicate')"
                ).first
                if duplicate_btn.count() == 0:
                    logger.warning("  Opción 'Duplicate' no encontrada en menú")
                    self.page.keyboard.press("Escape")
                    return False

            duplicate_btn.click()
            time.sleep(3)

            # Step 4: Verify duplication
            after_count = self.page.locator("a:has-text('Edit Content')").count()
            if after_count > before_count:
                logger.info(
                    f"Lección duplicada exitosamente: {before_count} → {after_count}"
                )
                return True
            else:
                # Wait a bit more — duplication might take time
                time.sleep(3)
                after_count = self.page.locator("a:has-text('Edit Content')").count()
                if after_count > before_count:
                    logger.info(
                        f"Lección duplicada (después de espera): "
                        f"{before_count} → {after_count}"
                    )
                    return True

            logger.warning(f"  Duplicación no incrementó conteo ({after_count})")
            return False

        except Exception as e:
            logger.warning(f"Error duplicando lección: {e}")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    def ensure_lesson_count(self, target_count: int) -> bool:
        """
        Asegura que el outline tenga al menos target_count lecciones.
        DUPLICA lecciones existentes (preserva colores, logos y bloques del template).

        Args:
            target_count: Número mínimo de lecciones requerido

        Returns:
            True si se alcanzó el target
        """
        current = self.page.locator("a:has-text('Edit Content')").count()
        logger.info(f"ensure_lesson_count: actual={current}, target={target_count}")

        if current >= target_count:
            return True

        # Duplicate lessons from the first "Tema" lesson (index 0)
        # to preserve template structure, colors, and logos
        attempts = 0
        max_attempts = (target_count - current) * 2 + 3

        while current < target_count and attempts < max_attempts:
            attempts += 1
            needed = target_count - current
            logger.info(
                f"  Duplicando lección (intento {attempts}, "
                f"faltan {needed})"
            )
            # Duplicate the first content lesson (index 0)
            if self.duplicate_lesson(source_index=0):
                current = self.page.locator(
                    "a:has-text('Edit Content')"
                ).count()
                time.sleep(1)
            else:
                logger.warning(f"  Fallo duplicando lección, intento {attempts}")
                if attempts >= max_attempts // 2:
                    break

        final = self.page.locator("a:has-text('Edit Content')").count()
        success = final >= target_count
        logger.info(
            f"ensure_lesson_count: resultado {final}/{target_count} "
            f"({'OK' if success else 'FALLO'})"
        )
        return success

    # ── Sidebar overlay dismissal ──────────────────────────────────────

    def dismiss_sidebar_overlay(self) -> bool:
        """
        Cierra cualquier sidebar overlay activo que bloquee clicks.
        Phase 0 confirmó: .blocks-sidebar__overlay--active es el overlay.
        """
        try:
            overlay = self.page.locator(
                ".blocks-sidebar__overlay--active"
            )
            if overlay.count() > 0 and overlay.first.is_visible(timeout=500):
                overlay.first.click(force=True)
                time.sleep(0.5)
                logger.debug("Sidebar overlay dismissed via click")
                return True
        except Exception:
            pass

        # Fallback: try closing via Escape
        try:
            sidebar = self.page.locator(".blocks-sidebar--open")
            if sidebar.count() > 0 and sidebar.first.is_visible(timeout=500):
                self.page.keyboard.press("Escape")
                time.sleep(0.5)
                logger.debug("Sidebar closed via Escape")
                return True
        except Exception:
            pass

        # Fallback: try clicking the close button
        try:
            close_btn = self.page.locator(".blocks-sidebar__close")
            if close_btn.count() > 0 and close_btn.first.is_visible(timeout=500):
                close_btn.first.click()
                time.sleep(0.5)
                logger.debug("Sidebar closed via close button")
                return True
        except Exception:
            pass

        return False

    # ── Agregar bloques en posición específica ─────────────────────────

    def add_block_at_position(self, after_index: int, block_type: str) -> bool:
        """
        Agrega un bloque DESPUÉS del bloque con índice after_index.
        Usa el botón "+" (block-create__button) que aparece entre bloques.

        El mecanismo descubierto en Phase 0:
        - Entre cada par de bloques hay un div.block-create
        - Dentro: button.block-create__button con texto "+"
        - Al hacer click: aparece menú de tipos de bloque
        - Seleccionar tipo → nuevo bloque aparece

        Args:
            after_index: Índice del bloque después del cual insertar (-1 para inicio)
            block_type: Tipo de bloque a agregar ("text", "statement", etc.)

        Returns:
            True si se agregó exitosamente
        """
        logger.debug(f"add_block_at_position: after={after_index}, type={block_type}")

        try:
            # Dismiss any open sidebar overlay first
            self.dismiss_sidebar_overlay()

            create_buttons = self.page.locator("button.block-create__button")
            btn_count = create_buttons.count()

            if btn_count == 0:
                logger.warning("No se encontraron botones block-create")
                return False

            # The "+" buttons are positioned between blocks:
            # btn[0] = before first block (block-create__button--first)
            # btn[1] = after block 0
            # btn[2] = after block 1
            # ...
            # So to insert after block N, we click btn[N+1]
            target_btn_idx = after_index + 1
            if target_btn_idx >= btn_count:
                target_btn_idx = btn_count - 1  # Last position

            btn = create_buttons.nth(target_btn_idx)
            btn.scroll_into_view_if_needed()
            time.sleep(0.2)

            # The "+" buttons may be hidden until hover
            # Try hover on the gap area first
            try:
                parent = btn.locator("xpath=ancestor::div[contains(@class,'block-create')][1]")
                if parent.count() > 0:
                    parent.first.hover()
                    time.sleep(0.3)
            except Exception:
                pass

            btn.click(force=True)
            time.sleep(0.5)

            # Select block type from sidebar category + sub-type
            category, sub_type = self._get_block_category_and_type(block_type)
            if self._select_block_from_library(category, sub_type):
                time.sleep(0.3)
                wait_for_react_idle(self.page, timeout_ms=3_000)
                logger.info(
                    f"Bloque '{block_type}' ({category}/{sub_type}) "
                    f"agregado después de bloque {after_index}"
                )
                return True
            else:
                # Close sidebar if it stayed open
                self.page.keyboard.press("Escape")
                time.sleep(0.3)
                self.dismiss_sidebar_overlay()
                logger.warning(
                    f"No se pudo seleccionar categoría '{category}' del sidebar"
                )
                return False

        except Exception as e:
            logger.warning(f"Error en add_block_at_position: {e}")
            try:
                self.page.keyboard.press("Escape")
                time.sleep(0.3)
                self.dismiss_sidebar_overlay()
            except Exception:
                pass
            return False

    def add_multiple_blocks(self, after_index: int, block_type: str, count: int) -> int:
        """
        Agrega múltiples bloques del mismo tipo, uno tras otro.
        Los índices se recalculan automáticamente después de cada inserción.

        Returns:
            Número de bloques agregados exitosamente
        """
        added = 0
        for i in range(count):
            # Each new block shifts indices by 1
            current_pos = after_index + added
            if self.add_block_at_position(current_pos, block_type):
                added += 1
                time.sleep(0.5)
            else:
                logger.warning(f"add_multiple_blocks: falló en bloque {i+1}/{count}")
                break
        logger.info(f"add_multiple_blocks: {added}/{count} bloques '{block_type}' agregados")
        return added

    # ── Edición de flashcards via sidebar ──────────────────────────────

    def edit_flashcard_sidebar(
        self, block_index: int, cards: list[dict]
    ) -> int:
        """
        Edita flashcards usando el sidebar panel descubierto en Phase 0.

        Mecanismo:
        1. Click en el bloque flashcard → abre sidebar 'blocks-sidebar--open'
        2. Sidebar contiene editables TipTap para cada card (front + back)
        3. Editar cada editable con Ctrl+A → type
        4. Cerrar sidebar

        Args:
            block_index: Índice del bloque flashcard en el editor
            cards: Lista de {front: str, back: str} para cada tarjeta

        Returns:
            Número de cards editadas exitosamente
        """
        logger.info(f"Editando flashcards en bloque {block_index}...")
        try:
            wrappers = self.page.locator("[class*='block-wrapper']")
            if block_index >= wrappers.count():
                logger.warning(f"edit_flashcard_sidebar: index {block_index} fuera de rango")
                return 0

            wrapper = wrappers.nth(block_index)
            wrapper.scroll_into_view_if_needed()
            time.sleep(0.3)
            wrapper.click()
            time.sleep(1)

            # Wait for sidebar to open
            sidebar = self.page.locator(".blocks-sidebar.blocks-sidebar--open")
            try:
                sidebar.wait_for(state="visible", timeout=5_000)
            except Exception:
                logger.warning("edit_flashcard_sidebar: sidebar no se abrió")
                return 0

            logger.debug("Sidebar de flashcards abierto")

            # Find all editables within the sidebar
            sidebar_editables = sidebar.locator(
                ".tiptap.ProseMirror.rise-tiptap[contenteditable='true'], "
                "[contenteditable='true']"
            )
            ed_count = sidebar_editables.count()
            logger.debug(f"  Editables en sidebar: {ed_count}")

            # Flashcard structure: pairs of editables (front, back)
            # Some cards may have: title-editable, subtitle-editable per side
            edited_cards = 0
            ed_idx = 0

            for card in cards:
                front = card.get("front", "").strip()
                back = card.get("back", "").strip()

                if ed_idx >= ed_count:
                    break

                # Edit front
                if front and ed_idx < ed_count:
                    try:
                        ed = sidebar_editables.nth(ed_idx)
                        if ed.is_visible(timeout=1_000):
                            ed.click()
                            time.sleep(0.2)
                            self.page.keyboard.press("Control+a")
                            time.sleep(0.1)
                            self.page.keyboard.press("Delete")
                            time.sleep(0.1)
                            paste_large_text(self.page, front)
                            time.sleep(0.3)
                    except Exception as e:
                        logger.debug(f"  Error editando front de card {edited_cards}: {e}")
                    ed_idx += 1

                # Edit back
                if back and ed_idx < ed_count:
                    try:
                        ed = sidebar_editables.nth(ed_idx)
                        if ed.is_visible(timeout=1_000):
                            ed.click()
                            time.sleep(0.2)
                            self.page.keyboard.press("Control+a")
                            time.sleep(0.1)
                            self.page.keyboard.press("Delete")
                            time.sleep(0.1)
                            paste_large_text(self.page, back)
                            time.sleep(0.3)
                    except Exception as e:
                        logger.debug(f"  Error editando back de card {edited_cards}: {e}")
                    ed_idx += 1

                edited_cards += 1

            # Close sidebar
            self.page.keyboard.press("Escape")
            time.sleep(0.5)

            logger.info(f"  Flashcards editadas: {edited_cards}/{len(cards)}")
            return edited_cards

        except Exception as e:
            logger.warning(f"Error en edit_flashcard_sidebar: {e}")
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return 0

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
