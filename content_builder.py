"""
content_builder.py — Orquestador de inserción de contenido en Rise 360
Toma el JSON estructurado del PDF y lo mapea a bloques de Rise 360,
insertando el contenido exacto (verbatim) bloque a bloque.
"""

import json
import time
from typing import Callable, Optional
from utils import logger, with_retry, take_screenshot
from rise_automation import RiseAutomation
import config


# ── Mapeo de tipos de contenido → acciones Rise 360 ──────────────────────

BLOCK_HANDLERS = {
    "titulo":        "_handle_titulo",
    "introduccion":  "_handle_text_section",
    "h1":            "_handle_h1_section",
    "h2":            "_handle_h2",
    "h3":            "_handle_h3",
    "parrafo":       "_handle_paragraph",
    "lista_vinetas": "_handle_bullet_list",
    "lista_numerada":"_handle_numbered_list",
    "tabla":         "_handle_table",
    "imagen":        "_handle_image",
    "conclusion":    "_handle_text_section",
    "referencias":   "_handle_references",
    "preambulo":     "_handle_preambulo",
}


class ContentBuilder:
    """
    Inserta el contenido del PDF en el curso duplicado de Rise 360,
    bloque a bloque, respetando la jerarquía y el mapeo de diseño.

    REGLA CRÍTICA: Todo el texto se inserta VERBATIM del PDF.
    Sin modificaciones, resúmenes ni paráfrasis.
    """

    def __init__(
        self,
        rise: RiseAutomation,
        learning_map: dict,
        progress_callback: Optional[Callable] = None,
        template_structure: Optional[dict] = None,
    ):
        self.rise = rise
        self.learning_map = learning_map
        self._progress = progress_callback or (lambda msg, pct: None)
        self._mappings = learning_map.get("mappings", {})
        self._template = template_structure  # Estructura analizada de la plantilla
        self._blocks_inserted = 0
        self._blocks_failed = 0
        self._failed_log: list[dict] = []
        self._current_text_block_open = False  # Rastrea si hay un bloque de texto abierto

    # ── API pública ───────────────────────────────────────────────────────

    def build_course(self, content_json: dict):
        """
        Punto de entrada principal.
        Itera sobre las secciones del JSON y construye el curso en Rise 360.

        Usa la estructura de la plantilla como guía:
        - Si la plantilla tiene bloques existentes en cada lección, edita esos bloques
        - Si necesita más bloques, los agrega

        Args:
            content_json: Resultado de pdf_parser.parse_pdf()
        """
        title = content_json.get("title", "Curso Sin Título")
        sections = content_json.get("sections", [])
        total_sections = len(sections)

        logger.info(f"Construyendo curso: '{title}' ({total_sections} secciones)")
        self._progress(f"Insertando contenido: '{title}'", 58)

        # Insertar el título del curso en el banner principal
        self._handle_titulo_block(title)

        # Obtener las lecciones disponibles en el outline del DUPLICADO
        lessons = self.rise.get_lessons_in_outline()
        total_lessons = len(lessons)
        logger.info(f"Lecciones en el outline del duplicado: {total_lessons}")

        # Info de la plantilla (si disponible)
        template_lessons = []
        if self._template:
            template_lessons = self._template.get("lessons", [])
            logger.info(
                f"Plantilla de referencia: {len(template_lessons)} lecciones, "
                f"{sum(len(l.get('blocks', [])) for l in template_lessons)} bloques"
            )

        # Filtrar secciones relevantes (omitir labels/preámbulos sin contenido real)
        content_sections = self._filter_content_sections(sections)
        total_content = len(content_sections)
        logger.info(f"Secciones de contenido a insertar: {total_content}")

        # Calcular incremento de progreso por sección
        progress_per_section = 35 / max(total_content, 1)  # Del 58% al 93%

        for i, section in enumerate(content_sections):
            section_type = section.get("type", "parrafo")
            section_heading = section.get("heading", "")
            section_blocks = section.get("blocks", [])

            pct = int(58 + i * progress_per_section)
            self._progress(
                f"Insertando sección {i+1}/{total_content}: {section_heading[:50]}",
                pct,
            )
            logger.info(f"Procesando sección [{section_type}]: '{section_heading}'")

            # Abrir el editor de la lección correspondiente (mapeo 1:1)
            if i < total_lessons:
                if not self.rise.open_lesson_editor(i):
                    logger.warning(f"No se pudo abrir editor de lección {i}. Saltando sección.")
                    continue
            else:
                logger.warning(
                    f"No hay lección {i} en el outline ({total_lessons} disponibles). "
                    "Saltando sección."
                )
                continue

            # Obtener info de bloques de la plantilla para esta lección
            template_blocks = []
            if i < len(template_lessons):
                template_blocks = template_lessons[i].get("blocks", [])
                logger.info(
                    f"  Plantilla lección {i}: {len(template_blocks)} bloques existentes"
                )

            # Insertar la sección, usando la estructura de la plantilla como guía
            self._insert_section_with_template(
                section_type, section_heading, section_blocks, template_blocks
            )

            # Volver al outline para la siguiente lección
            self.rise.go_back_to_outline()
            time.sleep(2)

        self._progress("Guardando curso...", 95)
        self.rise.save_course()

        # Reporte final
        total = self._blocks_inserted + self._blocks_failed
        logger.info(
            f"Construcción completada. "
            f"Bloques insertados: {self._blocks_inserted}/{total}. "
            f"Fallidos: {self._blocks_failed}"
        )

        if self._failed_log:
            logger.warning(f"Bloques con errores:\n{json.dumps(self._failed_log, indent=2, ensure_ascii=False)}")

        self._progress("¡Curso completado exitosamente!", 100)

    def _filter_content_sections(self, sections: list) -> list:
        """
        Filtra secciones que tienen contenido real para insertar.
        Omite preámbulos con solo etiquetas (COURSE_TITLE, etc.) y bloques vacíos.
        """
        filtered = []
        for section in sections:
            blocks = section.get("blocks", [])
            # Filtrar bloques que son etiquetas del PDF (font_size ~6.5, texto uppercase con _)
            real_blocks = [
                b for b in blocks
                if not (
                    b.get("font_size", 0) <= 7
                    and b.get("text", "").strip().isupper()
                    and "_" in b.get("text", "")
                )
            ]
            if real_blocks or section.get("heading", "").strip():
                # Actualizar la sección con solo los bloques reales
                filtered_section = dict(section)
                filtered_section["blocks"] = real_blocks
                filtered.append(filtered_section)
        return filtered

    # ── Inserción de secciones ─────────────────────────────────────────────

    def _insert_section_with_template(
        self,
        section_type: str,
        heading: str,
        blocks: list,
        template_blocks: list,
    ):
        """
        Inserta una sección usando la estructura de la plantilla como guía.

        Si la plantilla tiene bloques existentes en esta lección:
        - Primero intenta editar los bloques existentes (click en cada uno y reemplazar texto)
        - Si necesita más bloques de los que existen, agrega nuevos

        Args:
            section_type: Tipo de sección (h1, introduccion, etc.)
            heading: Texto del encabezado (verbatim)
            blocks: Lista de bloques del PDF
            template_blocks: Bloques existentes en la plantilla (puede estar vacío)
        """
        has_template = len(template_blocks) > 0
        if has_template:
            logger.info(
                f"  Usando plantilla como guía: {len(template_blocks)} bloques existentes"
            )

        # Recopilar todo el contenido a insertar en esta sección
        content_items = []

        # 1. El encabezado de la sección (si tiene)
        if heading and heading.strip():
            content_items.append({
                "text": heading,
                "type": "heading",
                "handler": self._get_heading_handler(section_type),
            })

        # 2. Bloques internos
        for block in blocks:
            block_text = block.get("text", "").strip()
            block_type = block.get("block_type", "parrafo")
            if not block_text and block_type != "imagen":
                continue
            content_items.append({
                "text": block_text,
                "type": block_type,
                "handler": BLOCK_HANDLERS.get(block_type, "_handle_paragraph"),
            })

        if not content_items:
            logger.info("  Sin contenido para insertar en esta lección")
            return

        logger.info(f"  Contenido a insertar: {len(content_items)} ítems")

        # Estrategia: si hay bloques en la plantilla, intentar editarlos directamente
        # Si no hay o se acaban, agregar nuevos bloques
        if has_template:
            self._insert_using_existing_blocks(content_items, template_blocks)
        else:
            self._insert_creating_new_blocks(content_items)

    def _get_heading_handler(self, section_type: str) -> str:
        """Retorna el handler apropiado para el heading según el tipo de sección."""
        if section_type == "h1":
            return "_handle_h1_section"
        elif section_type in ("introduccion", "conclusion"):
            return "_handle_section_header_text"
        elif section_type == "referencias":
            return "_handle_references_header"
        return "_handle_paragraph"

    def _insert_using_existing_blocks(self, content_items: list, template_blocks: list):
        """
        Inserta contenido editando los bloques existentes de la plantilla.
        Click en cada bloque editable → seleccionar todo → escribir texto nuevo.
        Si hay más contenido que bloques existentes, agrega nuevos.
        """
        # Buscar todos los editables visibles en la lección actual
        editable_sels = [
            ".rise-tiptap[contenteditable='true']",
            ".tiptap.ProseMirror[contenteditable='true']",
            ".tiptap[contenteditable='true']",
            ".ProseMirror[contenteditable='true']",
            "[contenteditable='true']",
        ]

        editables = []
        for sel in editable_sels:
            try:
                els = self.rise.page.locator(sel)
                count = els.count()
                if count > 0:
                    for i in range(count):
                        try:
                            el = els.nth(i)
                            if el.is_visible(timeout=500):
                                editables.append(el)
                        except Exception:
                            pass
                    if editables:
                        break
            except Exception:
                pass

        logger.info(f"  Bloques editables encontrados: {len(editables)}")

        # Editar bloques existentes con contenido del PDF
        items_inserted = 0
        for i, item in enumerate(content_items):
            if i < len(editables):
                # Editar bloque existente
                try:
                    el = editables[i]
                    el.scroll_into_view_if_needed()
                    el.click()
                    time.sleep(0.3)
                    self.rise.page.keyboard.press("Control+a")
                    time.sleep(0.1)
                    self.rise.page.keyboard.press("Delete")
                    time.sleep(0.1)
                    self.rise.page.keyboard.type(item["text"])
                    time.sleep(0.3)
                    self._blocks_inserted += 1
                    items_inserted += 1
                    logger.debug(
                        f"  Bloque {i} editado: '{item['text'][:50]}...'"
                    )
                except Exception as e:
                    logger.warning(f"  Error editando bloque {i}: {e}")
                    self._blocks_failed += 1
                    self._failed_log.append({
                        "handler": "edit_existing_block",
                        "text_preview": item["text"][:100],
                        "error": str(e),
                    })
            else:
                # No hay más bloques existentes → agregar nuevos
                handler = item.get("handler", "_handle_paragraph")
                self._safe_insert(handler, item["text"], item.get("type", ""))

        logger.info(f"  Ítems insertados en lección: {items_inserted}/{len(content_items)}")

    def _insert_creating_new_blocks(self, content_items: list):
        """
        Inserta contenido creando bloques nuevos (sin plantilla existente).
        """
        for item in content_items:
            handler = item.get("handler", "_handle_paragraph")
            self._safe_insert(handler, item["text"], item.get("type", ""))

    def _insert_section(self, section_type: str, heading: str, blocks: list):
        """
        Inserta una sección completa (fallback sin plantilla).
        1. El encabezado de la sección (banner o texto)
        2. Todos los bloques internos

        Args:
            section_type: Tipo de sección (h1, introduccion, conclusion, referencias)
            heading: Texto del encabezado (verbatim)
            blocks: Lista de bloques internos
        """
        # Insertar encabezado según el tipo
        if section_type == "h1":
            self._safe_insert("_handle_h1_section", heading)
        elif section_type in ("introduccion", "conclusion"):
            self._safe_insert("_handle_section_header_text", heading, section_type)
        elif section_type == "referencias":
            self._safe_insert("_handle_references_header", heading)
        elif section_type == "preambulo":
            pass  # El preámbulo no tiene encabezado visual
        else:
            # Tipo desconocido: insertar como texto
            if heading:
                self._safe_insert("_handle_paragraph", heading)

        # Insertar bloques internos
        for block in blocks:
            block_type = block.get("block_type", "parrafo")
            block_text = block.get("text", "")

            if not block_text and block_type != "imagen":
                continue  # Saltar bloques vacíos

            handler = BLOCK_HANDLERS.get(block_type, "_handle_paragraph")
            self._safe_insert(handler, block_text, block_type)

    def _safe_insert(self, handler_name: str, text: str = "", context: str = ""):
        """
        Llama al handler con manejo de errores y retry.
        Si falla tras 3 intentos, loguea y continúa (no detiene el proceso).
        """
        handler = getattr(self, handler_name, None)
        if not handler:
            logger.warning(f"Handler no encontrado: {handler_name}")
            return

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                result = handler(text) if text else handler("")
                if result is not False:
                    self._blocks_inserted += 1
                    return
            except Exception as e:
                logger.warning(
                    f"[Reintento {attempt}/{config.MAX_RETRIES}] "
                    f"{handler_name} falló: {e}"
                )
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_DELAY_MS / 1000)
                else:
                    self._blocks_failed += 1
                    self._failed_log.append({
                        "handler": handler_name,
                        "text_preview": text[:100],
                        "context": context,
                        "error": str(e),
                    })
                    take_screenshot(self.rise.page, label=f"fail_{handler_name[:20]}")
                    logger.error(f"{handler_name} agotó reintentos. Continuando...")

    # ── Handlers de tipos de bloque ───────────────────────────────────────

    def _handle_titulo_block(self, title: str):
        """Establece el título del curso en el banner principal."""
        if not title:
            return
        logger.info(f"Estableciendo título del curso: '{title}'")
        success = self.rise.set_course_title(title)
        if success:
            self._blocks_inserted += 1
        else:
            self._blocks_failed += 1
            self._failed_log.append({
                "handler": "_handle_titulo_block",
                "text_preview": title[:100],
                "error": "set_block_title retornó False",
            })

    def _handle_titulo(self, text: str) -> bool:
        """Handler para bloques tipo 'titulo'."""
        return self.rise.set_course_title(text)

    def _handle_h1_section(self, text: str) -> bool:
        """
        Inserta un banner de sección para Tema principal (H1).
        En Rise 360: Divider block o Section Banner.
        """
        logger.debug(f"H1 Section: '{text[:60]}'")
        # Agregar bloque de tipo section_banner (Divider en Rise)
        added = self.rise.add_block("section_banner")
        if not added:
            # Fallback: insertar como texto con estilo H1
            added = self.rise.add_block("text")

        if added:
            self.rise.page.wait_for_timeout(600)
            self.rise.insert_heading(text, level=1)
            return True
        return False

    def _handle_section_header_text(self, text: str, section_type: str = "text") -> bool:
        """
        Inserta el encabezado de una sección especial (Introducción, Conclusión)
        como texto con estilo destacado.
        """
        logger.debug(f"Section header [{section_type}]: '{text[:60]}'")
        added = self.rise.add_block("text")
        if added:
            self.rise.page.wait_for_timeout(600)
            self.rise.insert_heading(text, level=2)
            return True
        return False

    def _handle_references_header(self, text: str) -> bool:
        """Inserta el encabezado de la sección de referencias."""
        logger.debug(f"Referencias header: '{text[:60]}'")
        added = self.rise.add_block("text")
        if added:
            self.rise.page.wait_for_timeout(600)
            self.rise.insert_heading(text, level=2)
            return True
        return False

    def _handle_text_section(self, text: str) -> bool:
        """Inserta una sección de texto genérica."""
        return self._handle_paragraph(text)

    def _handle_h2(self, text: str) -> bool:
        """
        Inserta un subtema (H2) en el curso.
        Se inserta como Subheading dentro de un bloque de texto.
        """
        logger.debug(f"H2: '{text[:60]}'")
        # Si hay un bloque de texto abierto, agregar H2 dentro
        # Si no, abrir un nuevo bloque de texto
        if not self._current_text_block_open:
            self.rise.add_block("text")
            self.rise.page.wait_for_timeout(500)
            self._current_text_block_open = True

        return self.rise.insert_heading(text, level=2)

    def _handle_h3(self, text: str) -> bool:
        """Inserta un sub-subtema (H3)."""
        logger.debug(f"H3: '{text[:60]}'")
        if not self._current_text_block_open:
            self.rise.add_block("text")
            self.rise.page.wait_for_timeout(500)
            self._current_text_block_open = True

        return self.rise.insert_heading(text, level=3)

    def _handle_paragraph(self, text: str, block_type: str = "parrafo") -> bool:
        """
        Inserta un párrafo de texto verbatim.
        Cierra el bloque anterior si era un heading (H2/H3) y abre texto nuevo.
        """
        if not text.strip():
            return True  # Ignorar vacíos

        logger.debug(f"Párrafo ({len(text)} chars): '{text[:60]}...'")

        # Agregar nuevo bloque de texto
        added = self.rise.add_block("text")
        if not added:
            logger.warning("No se pudo agregar bloque de texto")
            return False

        self.rise.page.wait_for_timeout(600)
        self._current_text_block_open = True

        result = self.rise.insert_text(text, clear_first=True)
        if result:
            self._current_text_block_open = False  # El bloque quedó con contenido
        return result

    def _handle_bullet_list(self, text: str, block_type: str = "lista_vinetas") -> bool:
        """
        Inserta una lista con viñetas.
        El texto puede contener múltiples ítems separados por \n.
        Cada ítem se inserta en el bloque de lista de Rise 360.
        """
        logger.debug(f"Lista viñetas: '{text[:60]}'")

        # Agregar bloque de lista en Rise
        added = self.rise.add_block("bulleted_list")
        if not added:
            # Fallback: insertar como texto con • al inicio
            return self._handle_paragraph(text)

        self.rise.page.wait_for_timeout(600)

        # Insertar ítems de la lista
        # El texto ya viene verbatim del PDF con el marcador (•, -, etc.)
        return self.rise.insert_text(text, clear_first=True)

    def _handle_numbered_list(self, text: str, block_type: str = "lista_numerada") -> bool:
        """Inserta una lista numerada."""
        logger.debug(f"Lista numerada: '{text[:60]}'")
        added = self.rise.add_block("numbered_list")
        if not added:
            return self._handle_paragraph(text)

        self.rise.page.wait_for_timeout(600)
        return self.rise.insert_text(text, clear_first=True)

    def _handle_table(self, text: str, block_type: str = "tabla") -> bool:
        """
        Inserta una tabla.
        El texto contiene filas separadas por \n y columnas por | o tabs.
        Rise 360 tiene un Table block con UI propia para edición.

        Estrategia:
        1. Agregar Table block
        2. Parsear el texto para determinar filas y columnas
        3. Llenar celda por celda usando Tab para navegar entre celdas
        """
        logger.debug(f"Tabla: '{text[:80]}'")

        # Parsear la tabla del texto
        rows = self._parse_table_text(text)
        if not rows:
            # Fallback: insertar como texto si no se puede parsear
            return self._handle_paragraph(text)

        added = self.rise.add_block("table")
        if not added:
            return self._handle_paragraph(text)

        self.rise.page.wait_for_timeout(1_000)

        # Llenar la tabla celda por celda
        return self._fill_table_cells(rows)

    def _parse_table_text(self, text: str) -> list[list[str]]:
        """
        Parsea el texto de una tabla en filas y columnas.
        Soporta: separador |, tabs, y espacios múltiples.

        Retorna lista de listas [[col1, col2, ...], ...]
        """
        rows = []
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("---") or line.startswith("==="):
                continue  # Líneas separadoras de tabla markdown

            if "|" in line:
                cols = [c.strip() for c in line.split("|") if c.strip()]
            elif "\t" in line:
                cols = [c.strip() for c in line.split("\t") if c.strip()]
            else:
                cols = [line]

            if cols:
                rows.append(cols)

        return rows

    def _fill_table_cells(self, rows: list[list[str]]) -> bool:
        """
        Llena las celdas de una tabla en Rise 360.
        Usa Tab para navegar entre celdas y Enter para nueva fila.
        """
        try:
            # Hacer click en la primera celda
            first_cell_sels = [
                ".table-cell:first-child [contenteditable='true']",
                ".ql-editor",
                "td [contenteditable='true']",
                "[data-testid='table-cell'] [contenteditable='true']",
            ]
            for sel in first_cell_sels:
                try:
                    cell = self.rise.page.locator(sel).first
                    if cell.is_visible(timeout=2_000):
                        cell.click()
                        break
                except Exception:
                    pass

            for row_idx, row in enumerate(rows):
                for col_idx, cell_text in enumerate(row):
                    # Insertar texto verbatim
                    self.rise.page.keyboard.press("Control+a")
                    self.rise.page.keyboard.type(cell_text)

                    # Navegar a la siguiente celda
                    if col_idx < len(row) - 1:
                        self.rise.page.keyboard.press("Tab")
                        self.rise.page.wait_for_timeout(150)

                # Nueva fila: Rise puede usar Tab desde la última celda
                if row_idx < len(rows) - 1:
                    self.rise.page.keyboard.press("Tab")
                    self.rise.page.wait_for_timeout(200)

            logger.debug(f"Tabla llenada: {len(rows)} filas")
            return True

        except Exception as e:
            logger.warning(f"Error llenando tabla: {e}")
            return False

    def _handle_image(self, text: str = "", block_type: str = "imagen") -> bool:
        """
        Placeholder para imágenes del PDF.
        Por ahora inserta un bloque de imagen vacío con nota.
        La inserción real de imágenes requiere subir el archivo a Rise 360.
        """
        logger.debug("Bloque imagen detectado — insertando placeholder")
        # TODO: Implementar extracción y upload de imágenes del PDF
        # Por ahora, skip silencioso
        return True

    def _handle_references(self, text: str, block_type: str = "referencias") -> bool:
        """Inserta las referencias bibliográficas como texto verbatim."""
        return self._handle_paragraph(text)

    def _handle_preambulo(self, text: str, block_type: str = "preambulo") -> bool:
        """Inserta bloques del preámbulo (antes del primer H1)."""
        if text.strip():
            return self._handle_paragraph(text)
        return True

    # ── Utilidades ─────────────────────────────────────────────────────────

    def get_build_report(self) -> dict:
        """Retorna un reporte del proceso de construcción."""
        return {
            "blocks_inserted": self._blocks_inserted,
            "blocks_failed": self._blocks_failed,
            "total": self._blocks_inserted + self._blocks_failed,
            "success_rate": (
                self._blocks_inserted / max(self._blocks_inserted + self._blocks_failed, 1)
                * 100
            ),
            "failed_details": self._failed_log,
        }
