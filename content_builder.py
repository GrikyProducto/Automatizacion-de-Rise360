"""
content_builder.py — Orquestador de inserción de contenido en Rise 360

Enfoque de Diseñador Instruccional Experto:
  - Cada H2 del PDF → su propia lección (NUNCA agrupar 2+ H2 en 1 lección)
  - Si faltan lecciones en la plantilla, se duplican automáticamente
  - Si faltan bloques en una lección, se agregan automáticamente
  - Usa heading blocks para TANTO títulos COMO párrafos (patrón humano: 53%)
  - Flashcards: edita front/back via sidebar panel
  - UX instructions: agrega statements antes de bloques interactivos
  - Tablas: preserva formato pipe en text blocks
  - Todo el texto se inserta VERBATIM del PDF
  - Funciona con CUALQUIER plantilla + CUALQUIER PDF
"""

import json
import re
import time
from typing import Callable, Optional
from utils import logger, with_retry, take_screenshot, paste_large_text
from rise_automation import RiseAutomation, SKIP_BLOCK_TYPES
import config


# ── ContentLayoutPlanner: planifica la distribución como un diseñador ────


class ContentLayoutPlanner:
    """
    Reemplaza a ContentPool. En lugar de 2 colas estáticas, genera un PLAN
    de acciones que describe exactamente cómo debe quedar la lección.

    Cada acción es un dict con:
      - action: "EDIT" | "ADD" | "KEEP" | "FLASHCARD"
      - block_type: tipo de bloque Rise ("text", "statement", etc.)
      - texts: lista de textos para los editables del bloque
      - target_index: (para EDIT) índice del bloque existente a editar
    """

    # Max chars per heading/text block (patrón humano: 100-700 chars)
    CHUNK_SIZE = 500
    # Max content items per lesson (soft cap — warns but doesn't truncate)
    MAX_ITEMS_PER_LESSON = 60

    def plan_lesson(
        self,
        content_groups: list[dict],
        existing_blocks: list[dict],
    ) -> list[dict]:
        """
        Genera un plan de acciones para una lección.

        Args:
            content_groups: Lista de {title, text} del PDF para esta lección
            existing_blocks: Lista de bloques existentes en el template
                             [{index, type, editables_count}, ...]

        Returns:
            Lista de acciones ordenadas de arriba a abajo
        """
        plan = []

        # Separate template blocks by function
        interactive_blocks = []
        editable_blocks = []
        visual_blocks = []

        for block in existing_blocks:
            bt = block["type"]
            if bt in SKIP_BLOCK_TYPES:
                visual_blocks.append(block)
            elif bt in ("flashcards", "accordion", "sorting", "process",
                        "labeled", "tabs"):
                interactive_blocks.append(block)
            elif bt == "image":
                visual_blocks.append(block)
            elif bt in ("banner", "mondrian"):
                # Banners may have editable title overlay
                if block.get("editables_count", 0) > 0:
                    editable_blocks.append(block)
                else:
                    visual_blocks.append(block)
            else:
                editable_blocks.append(block)

        # 1. Process content into flat list of content items
        content_items = self._flatten_content(content_groups)

        # 2. Plan: use existing editable blocks first, then ADD new ones
        content_idx = 0
        edit_block_idx = 0

        for item in content_items:
            item_type = item["type"]  # "heading", "paragraph", "table", "list"
            text = item["text"]

            if edit_block_idx < len(editable_blocks):
                # EDIT an existing block
                block = editable_blocks[edit_block_idx]
                plan.append({
                    "action": "EDIT",
                    "block_type": block["type"],
                    "target_index": block["index"],
                    "texts": [text],
                    "editables_count": block.get("editables_count", 1),
                })
                edit_block_idx += 1
            else:
                # ADD a new block
                plan.append({
                    "action": "ADD",
                    "block_type": "text",
                    "texts": [text],
                })

        # 3. Handle interactive blocks (flashcards, accordion, etc.)
        for iblock in interactive_blocks:
            bt = iblock["type"]

            # Add UX instruction BEFORE interactive block
            ux_text = config.UX_INSTRUCTIONS.get(bt, "")
            if ux_text:
                plan.append({
                    "action": "ADD_UX",
                    "block_type": "statement",
                    "texts": [ux_text],
                    "before_index": iblock["index"],
                })

            # For flashcards, plan sidebar editing
            if bt == "flashcards":
                cards = self._build_flashcard_data(content_groups)
                if cards:
                    plan.append({
                        "action": "FLASHCARD",
                        "target_index": iblock["index"],
                        "cards": cards,
                    })
            else:
                # Edit interactive block editables with content
                ed_count = iblock.get("editables_count", 0)
                if ed_count > 0:
                    texts = []
                    remaining = self._get_remaining_content(
                        content_groups, content_idx
                    )
                    for i in range(ed_count):
                        if i % 2 == 0:
                            # Title/heading position
                            texts.append(
                                remaining.pop(0) if remaining else ""
                            )
                        else:
                            # Body/content position
                            texts.append(
                                remaining.pop(0) if remaining else ""
                            )
                    plan.append({
                        "action": "EDIT",
                        "block_type": bt,
                        "target_index": iblock["index"],
                        "texts": [t for t in texts if t],
                        "editables_count": ed_count,
                    })

        logger.info(
            f"  Plan generado: "
            f"{sum(1 for a in plan if a['action'] == 'EDIT')} EDIT, "
            f"{sum(1 for a in plan if a['action'] == 'ADD')} ADD, "
            f"{sum(1 for a in plan if a['action'] == 'ADD_UX')} UX, "
            f"{sum(1 for a in plan if a['action'] == 'FLASHCARD')} FLASHCARD"
        )

        return plan

    def _flatten_content(self, content_groups: list[dict]) -> list[dict]:
        """
        Convierte content_groups en lista plana de items,
        cada uno con tipo y texto.

        Patrón humano: usa heading blocks tanto para títulos como párrafos.
        Chunking: ~500 chars por bloque.
        Merge: párrafos cortos consecutivos se fusionan hasta CHUNK_SIZE.
        """
        items = []

        for group in content_groups:
            title = group.get("title", "").strip()
            text = group.get("text", "").strip()

            # H3 title → heading item
            if title and len(title) > 5:
                items.append({"type": "heading", "text": title})

            if not text:
                continue

            # Split text into paragraphs
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

            for para in paragraphs:
                # Skip if it's the same as the title
                if para == title:
                    continue

                # Detect tables (pipes or tabs)
                if self._is_table(para):
                    items.append({"type": "table", "text": para})
                    continue

                # Detect bulleted lists
                if self._is_list(para):
                    items.append({"type": "list", "text": para})
                    continue

                # Regular paragraph: chunk if too long
                if len(para) <= self.CHUNK_SIZE + 100:
                    items.append({"type": "paragraph", "text": para})
                else:
                    for chunk in self._split_at_sentences(para):
                        items.append({"type": "paragraph", "text": chunk})

        # Merge consecutive short paragraphs into larger chunks
        # This prevents micro-blocks (60 chars each) from inflating item count
        items = self._merge_short_paragraphs(items)

        # Warn but do NOT truncate — all content must be preserved
        if len(items) > self.MAX_ITEMS_PER_LESSON:
            logger.warning(
                f"  Lección grande: {len(items)} items "
                f"(recomendado max {self.MAX_ITEMS_PER_LESSON})"
            )

        logger.info(
            f"  Contenido aplanado: {len(items)} items "
            f"({sum(1 for i in items if i['type'] == 'heading')} headings, "
            f"{sum(1 for i in items if i['type'] == 'paragraph')} paragraphs, "
            f"{sum(1 for i in items if i['type'] == 'table')} tables, "
            f"{sum(1 for i in items if i['type'] == 'list')} lists)"
        )
        return items

    def _merge_short_paragraphs(self, items: list[dict]) -> list[dict]:
        """
        Merge consecutive short paragraph items into larger chunks
        (up to CHUNK_SIZE). Headings, tables, and lists are never merged.

        This prevents PDF micro-blocks (~60 chars each) from creating
        dozens of tiny items per lesson.
        """
        merged = []
        buffer_text = ""

        for item in items:
            # Only merge paragraphs — headings, tables, lists stay separate
            if item["type"] != "paragraph":
                # Flush buffer before non-paragraph
                if buffer_text:
                    merged.append({"type": "paragraph", "text": buffer_text})
                    buffer_text = ""
                merged.append(item)
                continue

            text = item["text"]

            if not buffer_text:
                buffer_text = text
            elif len(buffer_text) + len(text) + 2 <= self.CHUNK_SIZE:
                # Merge: fits within chunk size
                buffer_text = buffer_text + "\n\n" + text
            else:
                # Flush current buffer, start new one
                merged.append({"type": "paragraph", "text": buffer_text})
                buffer_text = text

        # Flush remaining buffer
        if buffer_text:
            merged.append({"type": "paragraph", "text": buffer_text})

        return merged

    def _split_at_sentences(self, text: str) -> list[str]:
        """Split text at sentence boundaries into ~CHUNK_SIZE char chunks."""
        sentences = re.split(r"(?<=[.!?;:])\s+", text)
        chunks, current = [], ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if current and len(current) + len(s) + 1 > self.CHUNK_SIZE:
                chunks.append(current)
                current = s
            else:
                current = f"{current} {s}".strip() if current else s
        if current:
            chunks.append(current)

        # Safety: break any remaining oversized chunks at word boundaries
        result = []
        for chunk in chunks:
            if len(chunk) > self.CHUNK_SIZE * 1.5:
                words = chunk.split()
                sub = ""
                for w in words:
                    if sub and len(sub) + len(w) + 1 > self.CHUNK_SIZE:
                        result.append(sub)
                        sub = w
                    else:
                        sub = f"{sub} {w}".strip() if sub else w
                if sub:
                    result.append(sub)
            else:
                result.append(chunk)
        return result

    def _is_table(self, text: str) -> bool:
        """Detect tabular content (pipes, multiple tabs)."""
        lines = text.split("\n")
        pipe_lines = sum(1 for l in lines if "|" in l and l.count("|") >= 2)
        return pipe_lines >= 2

    def _is_list(self, text: str) -> bool:
        """Detect bulleted or numbered lists."""
        lines = text.split("\n")
        if len(lines) < 2:
            return False
        bullet_lines = sum(
            1 for l in lines
            if l.strip().startswith(("•", "-", "–", "✓", "→"))
            or re.match(r"^\d+[\.\)]\s", l.strip())
        )
        return bullet_lines >= 2

    def _build_flashcard_data(
        self, content_groups: list[dict]
    ) -> list[dict]:
        """
        Builds flashcard card data from content groups.
        Uses H3 titles as fronts and first non-title paragraph as back.
        """
        cards = []
        for group in content_groups:
            title = group.get("title", "").strip()
            text = group.get("text", "").strip()
            if title and text:
                # Front = title, Back = first paragraph that's NOT the title
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                back_text = ""
                for para in paragraphs:
                    if para != title and len(para) > 20:
                        back_text = para
                        break
                if back_text:
                    cards.append({"front": title, "back": back_text})
        return cards[:6]  # Max 6 flashcards

    def _get_remaining_content(
        self, content_groups: list[dict], start_idx: int
    ) -> list[str]:
        """Get remaining content items as flat text list."""
        items = []
        for group in content_groups[start_idx:]:
            title = group.get("title", "").strip()
            text = group.get("text", "").strip()
            if title:
                items.append(title)
            if text:
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                items.extend(paragraphs)
        return items


# ── ContentBuilder principal ──────────────────────────────────────────


class ContentBuilder:
    """
    Inserta el contenido del PDF en el curso duplicado de Rise 360
    con criterio de diseñador instruccional experto.

    Diferencias clave vs versión anterior:
    - Cada H2 → su propia lección (nunca agrupa)
    - Agrega bloques nuevos si la plantilla no tiene suficientes
    - Usa heading blocks para desarrollo (patrón humano)
    - Edita flashcards via sidebar
    - Agrega instrucciones UX antes de interactivos
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
        self._template = template_structure
        self._planner = ContentLayoutPlanner()
        self._ai_designer = self._load_ai_designer()
        self._blocks_inserted = 0
        self._blocks_failed = 0
        self._failed_log: list[dict] = []

    # ── IA Designer (lazy load) ─────────────────────────────────────────

    def _load_ai_designer(self):
        """Carga InstructionalDesigner si Groq está habilitado."""
        try:
            from instructional_designer import InstructionalDesigner
            if not config.GROQ_ENABLED:
                return None
            designer = InstructionalDesigner()
            logger.info(
                "Diseñador instruccional IA cargado (Groq/Llama 3.3-70B)"
            )
            return designer
        except Exception as e:
            logger.info(
                f"AI designer no disponible: {e} — "
                f"usando planner por reglas"
            )
            return None

    # ── API pública ───────────────────────────────────────────────────────

    def _extract_course_title(self, content_json: dict) -> str:
        """
        Extrae el título real del curso del PDF como lo haría un humano:
        1. Busca bloques con block_type "titulo" (font_size >= 24pt)
        2. Si no hay, toma los primeros bloques Bold del preámbulo
        3. Aplica limpieza: corta en primer separador (: — ;), max 80 chars
        """
        sections = content_json.get("sections", [])

        # Strategy 1: Look for explicit "titulo" blocks
        for section in sections[:2]:
            for b in section.get("blocks", []):
                if b.get("block_type") == "titulo":
                    raw = b.get("text", "").strip()
                    if raw and len(raw) > 5:
                        return self._clean_course_title(raw)

        # Strategy 2: First bold blocks from preámbulo (before any H2)
        for section in sections[:2]:
            blocks = section.get("blocks", [])
            title_parts = []
            for b in blocks:
                text = b.get("text", "").strip()
                font = b.get("metadata", {}).get("font", "")
                bt = b.get("block_type", "")
                # Stop at first heading or TOC marker
                if bt in ("h2", "h1") or text.lower() in ("contenido", "índice", "indice"):
                    break
                if "Bold" in font and text and len(text) > 5:
                    title_parts.append(text)
                elif title_parts:
                    break  # Stop collecting after first non-bold block
            if title_parts:
                raw = " ".join(title_parts)
                return self._clean_course_title(raw)

        # Fallback: parser title (may be filename)
        return content_json.get("title", "Curso")

    def _clean_course_title(self, raw_title: str) -> str:
        """Clean course title: cut at first separator, max 80 chars."""
        for sep in [':', ' - ', ' — ', ' – ', '; ']:
            if sep in raw_title:
                raw_title = raw_title.split(sep)[0].strip()
                break
        if len(raw_title) > 80:
            raw_title = raw_title[:77].rsplit(' ', 1)[0]
        return raw_title

    def build_course(self, content_json: dict):
        """Entry point. Extrae temas, escala lecciones, inserta con criterio."""
        self._progress("Preparando contenido del curso...", 58)

        # Set course title from PDF content (not filename)
        course_title = self._extract_course_title(content_json)
        logger.info(f"Título del curso: '{course_title}'")
        self.rise.set_course_title(course_title)
        time.sleep(1)

        topics = self._extract_topics(content_json)
        logger.info(f"Temas extraídos del PDF: {len(topics)}")
        for t in topics:
            logger.info(
                f"  [{t['type']}] '{t['title'][:60]}' "
                f"→ {len(t.get('content_groups', []))} grupos"
            )

        lessons = self.rise.get_lessons_in_outline()
        total_lessons = len(lessons)
        logger.info(f"Lecciones en outline: {total_lessons}")

        if total_lessons == 0:
            logger.error("No se encontraron lecciones")
            return

        # Build lesson map (each H2 gets its own lesson)
        lesson_map = self._build_lesson_map(topics, total_lessons)
        needed_lessons = max(lesson_map.keys()) + 1 if lesson_map else total_lessons
        logger.info(f"Mapeo: {len(lesson_map)} lecciones necesarias")

        # PHASE: Scale lessons if needed
        if needed_lessons > total_lessons:
            self._progress(
                f"Escalando lecciones: {total_lessons} → {needed_lessons}...",
                60,
            )
            logger.info(
                f"Escalando lecciones: {total_lessons} → {needed_lessons}"
            )
            if self.rise.ensure_lesson_count(needed_lessons):
                total_lessons = self.rise.page.locator(
                    "a:has-text('Edit Content')"
                ).count()
                logger.info(f"Lecciones después de escalar: {total_lessons}")
            else:
                logger.warning("No se pudieron crear todas las lecciones necesarias")
                # Rebuild map with available lessons
                total_lessons = self.rise.page.locator(
                    "a:has-text('Edit Content')"
                ).count()
                lesson_map = self._build_lesson_map(topics, total_lessons)

        progress_per = 30 / max(len(lesson_map), 1)

        # PASS 1: Rename ALL lessons first (while on the outline)
        # Format: "Tema N: NOMBRE" for H2 topics; intro/conclusion/references keep their name
        self._progress("Renombrando lecciones...", 63)
        tema_counter = 0
        for lesson_idx, topic_data in lesson_map.items():
            title = topic_data.get("title", "")
            if not title:
                continue
            topic_type = topic_data.get("type", "topic")
            if topic_type == "topic":
                tema_counter += 1
                formatted_title = f"Tema {tema_counter}: {title}"
            else:
                # intro, conclusion, references: keep original name
                formatted_title = title
            self.rise.rename_lesson(lesson_idx, formatted_title)
            time.sleep(0.5)
        logger.info("Todas las lecciones renombradas")

        # PASS 2: Edit content per lesson
        for lesson_idx, topic_data in lesson_map.items():
            pct = int(
                65 + list(lesson_map.keys()).index(lesson_idx) * progress_per
            )
            self._progress(
                f"Lección {lesson_idx + 1}/{total_lessons}: "
                f"{topic_data['title'][:40]}...",
                pct,
            )

            # Edit lesson with layout planner
            self._execute_lesson_plan(lesson_idx, topic_data)

        self._progress("Guardando curso...", 95)
        self.rise.save_course()

        total = self._blocks_inserted + self._blocks_failed
        logger.info(
            f"Construcción completada. "
            f"Editables insertados: {self._blocks_inserted}/{total}. "
            f"Fallidos: {self._blocks_failed}"
        )
        if self._failed_log:
            logger.warning(
                "Bloques con errores:\n"
                + json.dumps(self._failed_log, indent=2, ensure_ascii=False)
            )

        self._progress("¡Curso completado exitosamente!", 100)

    # ── Extracción de temas del PDF (con criterio de diseño instruccional) ──

    def _extract_topics(self, content_json: dict) -> list[dict]:
        """
        Análisis pedagógico del PDF → estructura de lecciones.

        Enfoque UNIFICADO que funciona con CUALQUIER PDF:
        1. Recopila TODOS los bloques de contenido (omite TOC, metadata)
        2. Detecta límites de tema por DOS estrategias:
           a) H2 por font-size (PDFs bien estructurados)
           b) Patrón implícito N. + título bold (PDFs sin estructura de fuentes)
        3. Subtemas (N.N.) NO son límites de tema — quedan dentro de su tema
        4. Limpia numeración mecánica del PDF
        5. Agrupa por bloques conceptuales, no por numeración
        """
        sections = content_json.get("sections", [])

        # ── Phase 1: Separar bloques de contenido de secciones especiales ──
        all_blocks = []
        conclusion_blocks = []
        reference_blocks = []

        for section in sections:
            blocks = section.get("blocks", [])
            stype = section.get("type", "")

            # SKIP: Tabla de contenido
            if self._is_table_of_contents(section):
                logger.debug(
                    f"  Omitiendo tabla de contenido: "
                    f"'{section.get('heading', '')[:40]}'"
                )
                continue

            # SKIP: Metadata sections (portada, placeholders vacíos)
            if self._is_metadata_section(section):
                logger.debug(
                    f"  Omitiendo metadata: "
                    f"'{section.get('heading', '')[:40]}'"
                )
                continue

            # Conclusiones → aparte
            if stype == "conclusion":
                clean = self._filter_labels(blocks)
                if clean:
                    conclusion_blocks = clean
                continue

            # Referencias → aparte
            if stype == "referencias":
                clean = self._filter_labels(blocks)
                if clean and len(blocks) > 2:
                    reference_blocks = clean
                continue

            # Todo lo demás → acumular para análisis unificado
            all_blocks.extend(blocks)

        if not all_blocks:
            topics = []
        else:
            # ── Phase 2: Pre-procesar bloques ──
            all_blocks = self._merge_consecutive_h2s(all_blocks)
            all_blocks = self._merge_numbered_headings(all_blocks)

            # ── Phase 3: Detectar límites de tema (TOPIC level) ──
            topic_boundaries = self._find_topic_boundaries(all_blocks)

            # ── Phase 4: Construir temas ──
            topics = self._build_topics_from_boundaries(
                all_blocks, topic_boundaries
            )

        # ── Phase 5: Agregar conclusiones y referencias ──
        if conclusion_blocks:
            topics.append({
                "type": "conclusion",
                "title": "Conclusiones",
                "content_groups": [
                    {"title": "", "text": self._blocks_to_text(conclusion_blocks)}
                ],
            })

        if reference_blocks:
            topics.append({
                "type": "references",
                "title": "Referencias",
                "content_groups": [
                    {"title": "", "text": self._blocks_to_text(reference_blocks)}
                ],
            })

        return topics

    def _find_topic_boundaries(self, blocks: list) -> list[dict]:
        """
        Detecta límites de TEMA (no subtemas) usando dos estrategias.

        Funciona con CUALQUIER PDF:
        - Strategy 1: H2 por font-size que NO sea subtema (sin patrón N.N.)
        - Strategy 2: Patrón implícito: bloque standalone N. + siguiente bold
          (para PDFs donde todo el texto tiene la misma fuente)

        Subtemas (N.N.) se detectan luego en _group_by_subtopic.
        """
        boundaries = []

        for i, b in enumerate(blocks):
            text = b.get("text", "").strip()
            bt = b.get("block_type", "")
            font = b.get("metadata", {}).get("font", "")

            if not text or self._is_label_block(b):
                continue

            # ── Strategy 1: H2 por font-size (PDFs estructurados) ──
            if bt == "h2":
                # Skip artefactos (punto solo, texto muy corto)
                if len(text) <= 2 or re.match(r"^\.+$", text):
                    continue

                # Skip subtemas N.N. — NO son límites de tema
                if re.match(r"^\d+\.\d+", text):
                    continue

                # Skip metadata-like headings
                if text.lower() in (
                    "contenido", "tabla de contenido", "índice",
                    "indice", "table of contents",
                ):
                    continue

                boundaries.append({
                    "index": i,
                    "title": text,
                    "source": "h2_font",
                })
                continue

            # ── Strategy 2: Implícito N. + bold title (PDFs planos) ──
            # Case A: Standalone "N." followed by bold title (separate blocks)
            if re.match(r"^\d+\.$", text):
                if i + 1 >= len(blocks):
                    continue

                next_b = blocks[i + 1]
                next_font = next_b.get("metadata", {}).get("font", "")
                next_text = next_b.get("text", "").strip()

                # El siguiente bloque debe ser bold, con título real
                # (no puntos de paginación, no muy corto)
                if ("Bold" in next_font
                        and len(next_text) > 5
                        and "..." not in next_text
                        and not re.match(r"^\.+\s*\d*$", next_text)):

                    boundaries.append({
                        "index": i,
                        "title": f"{text} {next_text}",
                        "source": "implicit",
                    })
                continue

            # Case B: Merged "N. Title" (after _merge_numbered_headings)
            # Matches "3. Mapa conceptual..." but NOT "3.1. Subtema..."
            m = re.match(r"^(\d+)\.\s+([A-Za-z\u00C0-\u024F\u00BF\u00A1].{4,})", text)
            if m and not re.match(r"^\d+\.\d+", text):
                if "Bold" in font:
                    boundaries.append({
                        "index": i,
                        "title": text,
                        "source": "implicit_merged",
                    })

        implicit_count = sum(
            1 for b in boundaries if b["source"] in ("implicit", "implicit_merged")
        )
        logger.info(
            f"  Límites de tema detectados: {len(boundaries)} "
            f"(H2: {sum(1 for b in boundaries if b['source'] == 'h2_font')}, "
            f"implícitos: {implicit_count})"
        )
        for b in boundaries:
            logger.debug(
                f"    [{b['index']}] ({b['source']}) "
                f"'{self._clean_topic_title(b['title'])[:60]}'"
            )

        return boundaries

    def _build_topics_from_boundaries(
        self, blocks: list, boundaries: list[dict]
    ) -> list[dict]:
        """
        Construye la lista de temas a partir de los límites detectados.

        - Antes del primer límite → Introducción
        - Cada límite → un tema con su contenido hasta el siguiente límite
        """
        topics = []

        if not boundaries:
            # Sin límites → todo es introducción
            clean = self._filter_labels(blocks)
            if clean:
                text = self._blocks_to_text(clean)
                if text.strip() and len(text.strip()) > 50:
                    topics.append({
                        "type": "intro",
                        "title": "Introducción",
                        "content_groups": self._group_by_subtopic(clean),
                    })
            return topics

        # Intro: todo antes del primer límite de tema
        first_pos = boundaries[0]["index"]
        intro_blocks = self._filter_labels(blocks[:first_pos])
        if intro_blocks:
            text = self._blocks_to_text(intro_blocks)
            if text.strip() and len(text.strip()) > 50:
                topics.append({
                    "type": "intro",
                    "title": "Introducción",
                    "content_groups": self._group_by_subtopic(intro_blocks),
                })

        # Cada límite → un tema
        for idx, boundary in enumerate(boundaries):
            start = boundary["index"]
            end = (
                boundaries[idx + 1]["index"]
                if idx + 1 < len(boundaries)
                else len(blocks)
            )

            topic_blocks = self._filter_labels(blocks[start:end])
            clean_title = self._clean_topic_title(boundary["title"])

            content_groups = self._group_by_subtopic(topic_blocks)
            if content_groups:
                topics.append({
                    "type": "topic",
                    "title": clean_title,
                    "content_groups": content_groups,
                })

        return topics

    # ── Detección inteligente de secciones a omitir ────────────────────────

    def _is_table_of_contents(self, section: dict) -> bool:
        """
        Detecta si una sección es una tabla de contenido (índice del PDF).

        Funciona con CUALQUIER PDF detectando:
        - Números standalone ("1.", "1.1.", "2.3.1.")
        - Patrones de puntos/dots (".............. 4")
        - Líneas cortas + prefijos numerados ("1. Definición...")
        """
        blocks = section.get("blocks", [])
        if len(blocks) < 5:
            return False

        real_blocks = [
            b for b in blocks
            if b.get("text", "").strip()
            and not self._is_label_block(b)
        ]
        if len(real_blocks) < 5:
            return False

        toc_indicators = 0
        short_count = 0

        for b in real_blocks:
            text = b.get("text", "").strip()
            if len(text) < 80:
                short_count += 1

            # Standalone number: "1.", "1.1.", "2.3.1."
            if re.match(r"^\d+(\.\d+)*\.?\s*$", text):
                toc_indicators += 1
            # Dots pattern (with optional page number): ".............. 4"
            elif re.match(r"^\.{5,}", text):
                toc_indicators += 1
            # Number prefix + title: "1. Definición...", "1.1. Concepto..."
            elif re.match(r"^\d+(\.\d+)*\.?\s+\S", text):
                toc_indicators += 1

        short_ratio = short_count / len(real_blocks)
        toc_ratio = toc_indicators / len(real_blocks)

        # Dots patterns are a strong TOC signal (page references)
        dots_count = sum(
            1 for b in real_blocks
            if re.match(r"^\.{3,}", b.get("text", "").strip())
        )
        has_dots = dots_count >= 3

        if has_dots:
            # With dots: moderate thresholds
            return short_ratio > 0.6 and toc_ratio > 0.3
        else:
            # Without dots: much stricter — needs overwhelming evidence
            return short_ratio > 0.8 and toc_ratio > 0.6

    def _is_metadata_section(self, section: dict) -> bool:
        """Detecta secciones de metadata (portada, placeholders)."""
        blocks = section.get("blocks", [])
        if not blocks:
            return True

        real_texts = [
            b.get("text", "").strip()
            for b in blocks
            if b.get("text", "").strip()
            and not self._is_label_block(b)
        ]

        # If all real text is metadata markers
        metadata_markers = {
            "COURSE_TITLE", "COURSE_SUBTITLE", "SECTION_INTRO",
            "Contenido", "TABLE_OF_CONTENTS",
        }
        if all(t in metadata_markers or len(t) < 5 for t in real_texts):
            return True

        return False

    def _clean_topic_title(self, title: str) -> str:
        """
        Limpia títulos eliminando numeración mecánica del PDF.
        "1. Definición de la cadena..." → "Definición de la cadena..."
        "3.1. ¿Qué es un mapa?" → "¿Qué es un mapa?"
        """
        # Strip leading numbering: N., N.N., N.N.N.
        cleaned = re.sub(r"^\d+(\.\d+)*\.?\s*", "", title).strip()
        # Capitalize first letter if needed
        if cleaned and cleaned[0].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned if cleaned else title

    def _merge_numbered_headings(self, blocks: list) -> list:
        """
        Merge consecutive bold blocks where the first is a number prefix
        (N., N.N., N.N.N.) and the second is the actual title text.

        Handles PDFs where the parser splits "1.1." and "Concepto general"
        into separate blocks even though they form one heading.
        """
        if not blocks:
            return blocks

        merged = []
        i = 0
        while i < len(blocks):
            b = blocks[i]
            text = b.get("text", "").strip()
            font = b.get("metadata", {}).get("font", "")
            bt = b.get("block_type", "")

            # Skip H2 blocks — they're handled by _merge_consecutive_h2s
            if bt == "h2":
                merged.append(b)
                i += 1
                continue

            # Check if this is a standalone number prefix (bold)
            if (re.match(r"^\d+(\.\d+)*\.?$", text)
                    and "Bold" in font
                    and i + 1 < len(blocks)):

                next_b = blocks[i + 1]
                next_bt = next_b.get("block_type", "")
                next_text = next_b.get("text", "").strip()

                # Next block must have real title text (not another number,
                # not dots pattern). Font can be bold OR light — some PDFs
                # use bold numbers with light/regular titles.
                if (next_bt != "h2"
                        and next_text
                        and len(next_text) > 2
                        and not re.match(r"^\d+(\.\d+)*\.?$", next_text)
                        and not re.match(r"^\.{3,}", next_text)):
                    # Merge: number + title
                    combined = dict(b)
                    sep = " " if text.endswith(".") else ". "
                    combined["text"] = text + sep + next_text
                    merged.append(combined)
                    i += 2
                    continue

            merged.append(b)
            i += 1

        return merged

    def _merge_consecutive_h2s(self, blocks: list) -> list:
        """
        Merge consecutive H2 blocks that form a single heading.
        "3.1." + "Título real" → "3.1. Título real"
        Artifacts (empty, single char) → removed.
        """
        if not blocks:
            return blocks

        merged = []
        i = 0
        while i < len(blocks):
            b = blocks[i]
            bt = b.get("block_type", "")
            text = b.get("text", "").strip()

            if bt == "h2":
                if len(text) <= 1:
                    i += 1
                    continue

                is_number_prefix = bool(
                    re.match(r"^\d+\.\d*\.?$", text)
                )

                if is_number_prefix and i + 1 < len(blocks):
                    next_b = blocks[i + 1]
                    next_bt = next_b.get("block_type", "")
                    next_text = next_b.get("text", "").strip()

                    if next_bt == "h2" and next_text:
                        combined = dict(b)
                        sep = " " if text.endswith(".") else ". "
                        combined["text"] = text + sep + next_text
                        merged.append(combined)
                        i += 2
                        continue

                merged.append(b)
            else:
                merged.append(b)
            i += 1

        return merged

    def _filter_labels(self, blocks: list) -> list:
        return [b for b in blocks if not self._is_label_block(b)]

    def _is_label_block(self, block: dict) -> bool:
        fs = block.get("font_size", 0)
        text = block.get("text", "").strip()
        if fs <= 7 and text.isupper():
            if "_" in text or text in {
                "PARAGRAPH",
                "REFERENCE_ITEM",
                "TOPIC_TITLE",
                "SUBTOPIC_TITLE",
                "NUMBERED_LIST",
            }:
                return True
        return False

    # ── Agrupamiento por subtemas ────────────────────────────────────────

    def _group_by_subtopic(self, blocks: list) -> list[dict]:
        """
        Agrupa bloques por subtemas (detectados por bold + patrón N.N.).
        Limpia numeración de subtítulos para presentación limpia.

        Funciona con CUALQUIER PDF: detecta subtemas por:
        - Bold + patrón N.N. (genérico)
        - Font-size H2/H3 + patrón N.N. (PDFs con jerarquía de fuentes)
        """
        if not blocks:
            return []

        # Pre-process: merge "1.1." + "Title" into single block
        blocks = self._merge_numbered_headings(blocks)

        h3_positions = []
        for i, b in enumerate(blocks):
            fs = b.get("font_size", 0)
            text = b.get("text", "").strip()
            bt = b.get("block_type", "")
            font = b.get("metadata", {}).get("font", "")

            # Skip the topic-level heading (first block, typically H2 or N. title)
            if i == 0 and (bt == "h2" or re.match(r"^\d+\.\s+\S", text)):
                continue

            # Detect subtopic headings by numbered pattern
            if re.match(r"^\d+\.\d+", text):
                # Must be either: bold font, or larger font, or H2/H3
                is_bold = "Bold" in font or (b.get("font_flags", 0) & 1)
                is_heading = bt in ("h2", "h3")
                if is_bold or is_heading or fs >= 12:
                    h3_positions.append(i)

        if not h3_positions:
            text = self._blocks_to_text(blocks)
            return [{"title": "", "text": text}] if text.strip() else []

        groups = []
        pre_h3 = blocks[: h3_positions[0]]
        text = self._blocks_to_text(pre_h3)
        if text.strip():
            groups.append({"title": "", "text": text})

        for idx, h3_pos in enumerate(h3_positions):
            end = (
                h3_positions[idx + 1]
                if idx + 1 < len(h3_positions)
                else len(blocks)
            )
            h3_blocks = blocks[h3_pos:end]
            raw_title = blocks[h3_pos].get("text", "")
            # Clean numbering from subtopic title
            clean_title = self._clean_topic_title(raw_title)
            text = self._blocks_to_text(h3_blocks)
            if text.strip():
                groups.append({"title": clean_title, "text": text})

        return groups

    # Keep backward compatibility
    _group_by_h3 = _group_by_subtopic

    def _blocks_to_text(self, blocks: list) -> str:
        paragraphs = []
        current_parts = []
        last_real_fs = 0

        for b in blocks:
            text = b.get("text", "").strip()
            fs = b.get("font_size", 0)
            bt = b.get("block_type", "")

            if self._is_label_block(b):
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                    last_real_fs = 0
                continue

            if not text:
                continue

            # Headings and subtopic titles: clean numbering
            is_subtopic = fs >= 12 and re.match(r"^\d+\.\d+", text)
            if bt == "h2" or is_subtopic:
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                # Clean numbering from heading text
                clean_heading = self._clean_topic_title(text)
                paragraphs.append(clean_heading)
                last_real_fs = fs
                continue

            if bt == "lista_vinetas":
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                paragraphs.append(text)
                last_real_fs = fs
                continue

            if fs == last_real_fs and current_parts:
                current_parts.append(text)
            else:
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                current_parts = [text]
            last_real_fs = fs

        if current_parts:
            paragraphs.append(" ".join(current_parts))

        return "\n\n".join(paragraphs)

    # ── Mapeo temas → lecciones (v2: cada H2 = 1 lección) ────────────────

    def _build_lesson_map(self, topics: list, total_lessons: int) -> dict:
        """
        Cada H2 del PDF → su propia lección. NUNCA agrupar 2+ H2.
        Si faltan lecciones, se duplicarán en build_course().
        """
        lesson_map = {}
        intro = None
        h2_topics = []
        conclusion = None
        references = None

        for topic in topics:
            if topic["type"] == "intro":
                intro = topic
            elif topic["type"] == "topic":
                h2_topics.append(topic)
            elif topic["type"] == "conclusion":
                conclusion = topic
            elif topic["type"] == "references":
                references = topic

        logger.info(
            f"  Mapeo v2: {len(h2_topics)} H2, "
            f"intro={'sí' if intro else 'no'}, "
            f"conclusión={'sí' if conclusion else 'no'}, "
            f"referencias={'sí' if references else 'no'}"
        )

        lesson_idx = 0

        # Intro gets its own lesson (lesson 0)
        if intro:
            lesson_map[lesson_idx] = intro
            lesson_idx += 1

        # Each H2 → its own lesson
        for topic in h2_topics:
            lesson_map[lesson_idx] = dict(topic)
            lesson_idx += 1

        # Conclusion
        if conclusion:
            lesson_map[lesson_idx] = conclusion
            lesson_idx += 1

        # References
        if references:
            lesson_map[lesson_idx] = references
            lesson_idx += 1

        for idx, data in lesson_map.items():
            logger.info(
                f"  Lección {idx} → '{data['title'][:50]}' "
                f"({len(data.get('content_groups', []))} grupos)"
            )

        return lesson_map

    # ── Ejecución del plan de lección ─────────────────────────────────────

    def _execute_lesson_plan(self, lesson_idx: int, topic_data: dict):
        """
        Abre una lección, genera plan con IA, ejecuta:
        1. EDIT actions: cambiar tipo vía lápiz si necesario + llenar contenido
        2. ADD actions: agregar bloque con tipo específico + llenar contenido
        3. FLASHCARD actions: editar vía sidebar
        """
        title = topic_data.get("title", "")
        content_groups = topic_data.get("content_groups", [])
        logger.info(
            f"\n{'='*60}\n"
            f"Procesando lección {lesson_idx}: '{title}'\n"
            f"{'='*60}"
        )

        if not content_groups:
            logger.info("  Sin contenido para esta lección")
            return

        # 1. Open lesson editor
        if not self.rise.open_lesson_editor(lesson_idx):
            logger.warning(f"No se pudo abrir editor de lección {lesson_idx}")
            self._blocks_failed += len(content_groups)
            return

        # 2. Pre-scan existing blocks
        existing_blocks = self.rise.count_editables_in_lesson()
        logger.info(
            f"  Bloques existentes: {len(existing_blocks)}, "
            f"{sum(b['editables_count'] for b in existing_blocks)} editables"
        )

        # 3. Generate plan (IA si disponible, sino reglas)
        if self._ai_designer is not None:
            plan = self._ai_designer.plan_lesson_with_ai(
                content_groups, existing_blocks,
                lesson_title=title,
                lesson_type=topic_data.get("type", "topic"),
            )
        else:
            plan = self._planner.plan_lesson(content_groups, existing_blocks)

        # Build index map of existing blocks for quick lookup
        existing_type_map = {b["index"]: b["type"] for b in existing_blocks}

        # 4. Scroll to top before processing
        try:
            self.rise.page.keyboard.press("Control+Home")
            time.sleep(0.5)
        except Exception:
            pass

        self.rise.dismiss_sidebar_overlay()

        total_edited = 0
        add_queue = []  # collect ADD actions to batch-process

        # ── Phase 1: Process EDIT actions (change type + fill) ──────────
        edit_actions = [a for a in plan if a["action"] == "EDIT"]
        for action in edit_actions:
            target_idx = action.get("target_index", -1)
            desired_type = action.get("block_type", "text")
            texts = action.get("texts", [])

            if target_idx < 0:
                # No valid target — demote to ADD
                add_queue.append(action)
                continue

            current_type = existing_type_map.get(target_idx, "unknown")

            # Change block type via pencil icon if needed
            if current_type != desired_type and desired_type not in SKIP_BLOCK_TYPES:
                # Only change type for simple editable blocks
                changeable = {
                    "text", "heading", "statement", "quote",
                    "bulleted_list", "numbered_list", "note",
                }
                if desired_type in changeable:
                    changed = self.rise.change_block_type(target_idx, desired_type)
                    if changed:
                        logger.info(
                            f"  [{target_idx}] Tipo cambiado: "
                            f"{current_type} → {desired_type}"
                        )
                    else:
                        logger.debug(
                            f"  [{target_idx}] No se pudo cambiar tipo, "
                            f"editando como {current_type}"
                        )

            # Fill editables with content
            edited = self._fill_block_content(target_idx, texts)
            total_edited += edited

        # ── Phase 2: Process ADD_UX actions ─────────────────────────────
        ux_actions = [a for a in plan if a["action"] == "ADD_UX"]
        for action in ux_actions:
            texts = action.get("texts", [])
            if not texts:
                continue
            # Find safe insertion point
            insertion_idx = self._find_safe_insertion_point(existing_blocks)
            if self.rise.add_block_at_position(insertion_idx, "statement"):
                time.sleep(0.3)
                # Fill the newly added block (it should be right after insertion)
                # Re-catalog to find it
                new_blocks = self.rise._catalog_blocks_in_editor()
                # Find a statement block near our insertion point
                for nb in new_blocks:
                    if nb["type"] == "statement" and nb["index"] >= insertion_idx:
                        self._fill_block_content(nb["index"], texts)
                        total_edited += 1
                        break

        # ── Phase 3: Process ADD actions ────────────────────────────────
        # Collect remaining ADDs from plan + demoted EDITs
        for a in plan:
            if a["action"] == "ADD":
                add_queue.append(a)

        if add_queue:
            logger.info(f"  Agregando {len(add_queue)} bloques nuevos...")
            insertion_idx = self._find_safe_insertion_point(existing_blocks)

            for action in add_queue:
                desired_type = action.get("block_type", "text")
                texts = action.get("texts", [])

                if self.rise.add_block_at_position(insertion_idx, desired_type):
                    time.sleep(0.3)
                    # Re-catalog to find the new block
                    new_blocks = self.rise._catalog_blocks_in_editor()
                    # The new block should be near the insertion index
                    # Find first block of desired_type near insertion_idx
                    filled = False
                    for nb in new_blocks:
                        if nb["index"] >= insertion_idx and nb["type"] != "continue":
                            edited = self._fill_block_content(nb["index"], texts)
                            total_edited += edited
                            filled = True
                            insertion_idx = nb["index"] + 1
                            break
                    if not filled and texts:
                        logger.warning(
                            f"  No se encontró bloque recién agregado "
                            f"para '{texts[0][:30]}...'"
                        )

        # ── Phase 4: Handle flashcard blocks via sidebar ────────────────
        flashcard_actions = [a for a in plan if a["action"] == "FLASHCARD"]
        for action in flashcard_actions:
            cards = action.get("cards", [])
            target_idx = action.get("target_index", -1)
            if cards and target_idx >= 0:
                edited = self.rise.edit_flashcard_sidebar(target_idx, cards)
                total_edited += edited
                logger.info(f"  Flashcards editadas: {edited} cards")
            elif cards:
                # Find first flashcard block
                all_blocks = self.rise._catalog_blocks_in_editor()
                fc = [b for b in all_blocks if b["type"] == "flashcards"]
                if fc:
                    edited = self.rise.edit_flashcard_sidebar(
                        fc[0]["index"], cards
                    )
                    total_edited += edited
                    logger.info(f"  Flashcards editadas: {edited} cards")

        self._blocks_inserted += total_edited

        logger.info(
            f"  Lección {lesson_idx} completada: "
            f"{total_edited} editables editados"
        )

        # Back to outline
        self.rise.go_back_to_outline()
        time.sleep(1)

    def _find_safe_insertion_point(self, existing_blocks: list[dict]) -> int:
        """Find a safe block index for inserting new blocks (avoid Continue)."""
        safe_text_types = {
            "text", "statement", "heading", "quote",
            "bulleted_list", "numbered_list",
        }
        safe_indices = [
            b["index"] for b in existing_blocks
            if b["type"] in safe_text_types
        ]
        if safe_indices:
            mid = len(safe_indices) // 2
            return safe_indices[mid]
        elif existing_blocks:
            for block in existing_blocks:
                if block["type"] not in SKIP_BLOCK_TYPES:
                    return block["index"]
        return 0

    def _fill_block_content(
        self, block_idx: int, texts: list[str]
    ) -> int:
        """Fill a block's editables with the given texts. Returns count edited."""
        if not texts:
            return 0

        wrappers = self.rise.page.locator("[class*='block-wrapper']")
        if block_idx >= wrappers.count():
            return 0

        try:
            wrapper = wrappers.nth(block_idx)
            wrapper.scroll_into_view_if_needed()
            time.sleep(0.15)

            try:
                wrapper.click(timeout=2_000)
            except Exception:
                try:
                    wrapper.click(force=True)
                except Exception:
                    return 0
            time.sleep(0.2)

            # Check block type for interactive handling
            cls = ""
            try:
                cls = wrapper.get_attribute("class") or ""
            except Exception:
                pass

            block_type = self.rise._extract_block_type_from_class(cls)

            if block_type in ("quote_carousel", "accordion", "tabs"):
                edited = self._edit_interactive_block(
                    wrapper, block_type, block_idx, texts, 0
                )
                try:
                    self.rise.page.keyboard.press("Escape")
                except Exception:
                    pass
                if edited > 0:
                    logger.info(
                        f"  [{block_type}:{block_idx}] "
                        f"{edited} editables (interactive)"
                    )
                return edited

            editables = wrapper.locator("[contenteditable='true']")
            count = editables.count()

            if count == 0:
                self.rise.page.keyboard.press("Escape")
                return 0

            edited = 0
            for i in range(min(count, len(texts))):
                text = texts[i] if i < len(texts) else ""
                if not text or not text.strip():
                    continue
                try:
                    ed = editables.nth(i)
                    if not ed.is_visible(timeout=500):
                        continue
                    ed.click()
                    time.sleep(0.05)
                    self.rise.page.keyboard.press("Control+a")
                    self.rise.page.keyboard.press("Delete")
                    time.sleep(0.05)
                    paste_large_text(self.rise.page, text)
                    time.sleep(0.15)
                    edited += 1
                except Exception as e:
                    logger.debug(
                        f"  Block {block_idx} editable {i} falló: {e}"
                    )
                    try:
                        self.rise.page.keyboard.press("Escape")
                    except Exception:
                        pass

            if edited > 0:
                logger.info(
                    f"  [{block_type}:{block_idx}] {edited}/{count} editables"
                )

            self.rise.page.keyboard.press("Escape")
            time.sleep(0.1)
            return edited

        except Exception as e:
            logger.warning(f"  Error en block {block_idx}: {e}")
            try:
                self.rise.page.keyboard.press("Escape")
                self.rise.dismiss_sidebar_overlay()
            except Exception:
                pass
            return 0

    # ── Interactive Block Editing ──────────────────────────────────────────

    def _edit_interactive_block(
        self,
        wrapper,
        block_type: str,
        block_idx: int,
        content_queue: list[str],
        content_idx: int,
    ) -> int:
        """
        Edit interactive blocks that hide editables behind UI interactions.
        Returns the number of content items consumed from content_queue.

        Handles:
        - quote_carousel: navigate slides via next arrow, edit visible editables per slide
        - accordion: expand each panel, edit title + body
        - tabs: click each tab, edit visible editables
        """
        page = self.rise.page
        edited = 0

        try:
            if block_type == "quote_carousel":
                edited = self._edit_carousel(wrapper, page, content_queue, content_idx)
            elif block_type == "accordion":
                edited = self._edit_accordion(wrapper, page, content_queue, content_idx)
            elif block_type == "tabs":
                edited = self._edit_tabs(wrapper, page, content_queue, content_idx)
        except Exception as e:
            logger.warning(f"  Error editando {block_type} block {block_idx}: {e}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

        return edited

    def _edit_carousel(self, wrapper, page, content_queue, content_idx) -> int:
        """Navigate carousel slides and edit visible editables on each."""
        edited = 0
        max_slides = 10  # safety limit

        for slide_num in range(max_slides):
            if content_idx + edited >= len(content_queue):
                break

            # Find visible editables on current slide
            editables = wrapper.locator("[contenteditable='true']:visible")
            time.sleep(0.3)
            count = editables.count()

            for i in range(count):
                if content_idx + edited >= len(content_queue):
                    break
                try:
                    ed = editables.nth(i)
                    if not ed.is_visible(timeout=500):
                        continue
                    ed.click()
                    time.sleep(0.1)
                    page.keyboard.press("Control+a")
                    time.sleep(0.05)
                    page.keyboard.press("Delete")
                    time.sleep(0.05)
                    paste_large_text(page, content_queue[content_idx + edited])
                    time.sleep(0.2)
                    edited += 1
                except Exception as e:
                    logger.debug(f"    Carousel editable {i} failed: {e}")

            # Try to navigate to next slide
            next_btn = wrapper.locator(
                "button[class*='next'], "
                "button[class*='arrow-right'], "
                "button[aria-label*='next' i], "
                "button[aria-label*='Next' i], "
                "[class*='carousel'] button:last-child"
            )
            if next_btn.count() > 0:
                try:
                    next_btn.first.click()
                    time.sleep(0.5)
                except Exception:
                    break  # No more slides
            else:
                break  # No next button found

        return edited

    def _edit_accordion(self, wrapper, page, content_queue, content_idx) -> int:
        """Expand each accordion panel and edit its editables."""
        edited = 0

        # Find accordion items/panels
        panels = wrapper.locator(
            "[class*='accordion-item'], "
            "[class*='accordion__item'], "
            "[class*='accordion'] > div"
        )
        panel_count = panels.count()

        if panel_count == 0:
            # Fallback: try direct children that look like panels
            panels = wrapper.locator("[class*='block-accordion'] > div > div")
            panel_count = panels.count()

        for i in range(panel_count):
            if content_idx + edited >= len(content_queue):
                break

            panel = panels.nth(i)
            try:
                # Click the panel header/trigger to expand it
                trigger = panel.locator(
                    "button, "
                    "[class*='trigger'], "
                    "[class*='header'], "
                    "[class*='title'], "
                    "[role='button']"
                ).first
                trigger.scroll_into_view_if_needed()
                trigger.click()
                time.sleep(0.5)

                # Now find visible editables inside the expanded panel
                editables = panel.locator("[contenteditable='true']:visible")
                ed_count = editables.count()

                for j in range(ed_count):
                    if content_idx + edited >= len(content_queue):
                        break
                    try:
                        ed = editables.nth(j)
                        if not ed.is_visible(timeout=500):
                            continue
                        ed.click()
                        time.sleep(0.1)
                        page.keyboard.press("Control+a")
                        time.sleep(0.05)
                        page.keyboard.press("Delete")
                        time.sleep(0.05)
                        paste_large_text(page, content_queue[content_idx + edited])
                        time.sleep(0.2)
                        edited += 1
                    except Exception as e:
                        logger.debug(f"    Accordion panel {i} editable {j} failed: {e}")
            except Exception as e:
                logger.debug(f"    Accordion panel {i} expand failed: {e}")

        return edited

    def _edit_tabs(self, wrapper, page, content_queue, content_idx) -> int:
        """Click each tab and edit visible editables in its content area."""
        edited = 0

        # Find tab buttons
        tab_buttons = wrapper.locator(
            "[role='tab'], "
            "[class*='tab-button'], "
            "[class*='tabs__tab'], "
            "[class*='tab-label'], "
            "button[class*='tab']"
        )
        tab_count = tab_buttons.count()

        if tab_count == 0:
            # Fallback: try nav links inside tabs
            tab_buttons = wrapper.locator("[class*='tabs'] nav button, [class*='tabs'] nav a")
            tab_count = tab_buttons.count()

        for i in range(tab_count):
            if content_idx + edited >= len(content_queue):
                break

            try:
                tab = tab_buttons.nth(i)
                tab.scroll_into_view_if_needed()
                tab.click()
                time.sleep(0.5)

                # Edit visible editables in the active tab content
                editables = wrapper.locator("[contenteditable='true']:visible")
                ed_count = editables.count()

                for j in range(ed_count):
                    if content_idx + edited >= len(content_queue):
                        break
                    try:
                        ed = editables.nth(j)
                        if not ed.is_visible(timeout=500):
                            continue
                        ed.click()
                        time.sleep(0.1)
                        page.keyboard.press("Control+a")
                        time.sleep(0.05)
                        page.keyboard.press("Delete")
                        time.sleep(0.05)
                        paste_large_text(page, content_queue[content_idx + edited])
                        time.sleep(0.2)
                        edited += 1
                    except Exception as e:
                        logger.debug(f"    Tab {i} editable {j} failed: {e}")
            except Exception as e:
                logger.debug(f"    Tab {i} click failed: {e}")

        return edited

    # ── Verificación UX/UI ───────────────────────────────────────────────

    def _verify_lesson_ux(self, lesson_idx: int, title: str = ""):
        """Scroll through the lesson to verify visual presentation."""
        logger.info(f"  Verificando UX/UI de lección {lesson_idx}...")
        try:
            page = self.rise.page
            page.keyboard.press("Control+Home")
            time.sleep(1)
            take_screenshot(page, label=f"ux_L{lesson_idx}_top")

            for _ in range(5):
                page.keyboard.press("PageDown")
                time.sleep(0.5)
            take_screenshot(page, label=f"ux_L{lesson_idx}_mid")

            page.keyboard.press("Control+End")
            time.sleep(1)
            take_screenshot(page, label=f"ux_L{lesson_idx}_bottom")

            logger.info(
                f"  Verificación visual lección {lesson_idx}: "
                f"'{title[:40]}' — screenshots guardados"
            )
        except Exception as e:
            logger.warning(f"  Error en verificación UX lección {lesson_idx}: {e}")

    # ── Utilidades ─────────────────────────────────────────────────────────

    def get_build_report(self) -> dict:
        total = self._blocks_inserted + self._blocks_failed
        return {
            "blocks_inserted": self._blocks_inserted,
            "blocks_failed": self._blocks_failed,
            "total": total,
            "success_rate": self._blocks_inserted / max(total, 1) * 100,
            "failed_details": self._failed_log,
        }
