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

        logger.info(
            f"  Contenido aplanado: {len(items)} items "
            f"({sum(1 for i in items if i['type'] == 'heading')} headings, "
            f"{sum(1 for i in items if i['type'] == 'paragraph')} paragraphs, "
            f"{sum(1 for i in items if i['type'] == 'table')} tables, "
            f"{sum(1 for i in items if i['type'] == 'list')} lists)"
        )
        return items

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
        self._blocks_inserted = 0
        self._blocks_failed = 0
        self._failed_log: list[dict] = []

    # ── API pública ───────────────────────────────────────────────────────

    def build_course(self, content_json: dict):
        """Entry point. Extrae temas, escala lecciones, inserta con criterio."""
        self._progress("Preparando contenido del curso...", 58)

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

        for lesson_idx, topic_data in lesson_map.items():
            pct = int(
                62 + list(lesson_map.keys()).index(lesson_idx) * progress_per
            )
            self._progress(
                f"Lección {lesson_idx + 1}/{total_lessons}: "
                f"{topic_data['title'][:40]}...",
                pct,
            )

            # Rename lesson in outline
            title = topic_data.get("title", "")
            if title:
                self.rise.rename_lesson(lesson_idx, title)
                time.sleep(1)

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

    # ── Extracción de temas del PDF ──────────────────────────────────────

    def _extract_topics(self, content_json: dict) -> list[dict]:
        topics = []
        sections = content_json.get("sections", [])

        for section in sections:
            blocks = section.get("blocks", [])
            h2_indices = [
                i for i, b in enumerate(blocks) if b.get("block_type") == "h2"
            ]

            if h2_indices:
                pre_h2 = self._filter_labels(blocks[: h2_indices[0]])
                if pre_h2:
                    topics.append(
                        {
                            "type": "intro",
                            "title": "Introducción general",
                            "content_groups": self._group_by_h3(pre_h2),
                        }
                    )

                for idx, h2_pos in enumerate(h2_indices):
                    end = (
                        h2_indices[idx + 1]
                        if idx + 1 < len(h2_indices)
                        else len(blocks)
                    )
                    topic_blocks = self._filter_labels(blocks[h2_pos:end])
                    h2_text = blocks[h2_pos].get("text", f"Tema {idx + 1}")
                    topics.append(
                        {
                            "type": "topic",
                            "title": h2_text,
                            "content_groups": self._group_by_h3(topic_blocks),
                        }
                    )

            elif section.get("type") == "conclusion":
                clean = self._filter_labels(blocks)
                if clean:
                    topics.append(
                        {
                            "type": "conclusion",
                            "title": section.get("heading", "Conclusión"),
                            "content_groups": [
                                {"title": "", "text": self._blocks_to_text(clean)}
                            ],
                        }
                    )

            elif section.get("type") == "referencias" and len(blocks) > 2:
                clean = self._filter_labels(blocks)
                if clean:
                    topics.append(
                        {
                            "type": "references",
                            "title": "Referencias",
                            "content_groups": [
                                {"title": "", "text": self._blocks_to_text(clean)}
                            ],
                        }
                    )

        return topics

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

    # ── Agrupamiento por subtemas H3 ─────────────────────────────────────

    def _group_by_h3(self, blocks: list) -> list[dict]:
        if not blocks:
            return []

        h3_positions = []
        for i, b in enumerate(blocks):
            fs = b.get("font_size", 0)
            text = b.get("text", "")
            bt = b.get("block_type", "")
            if bt == "h2":
                continue
            if fs >= 12 and any(text.startswith(f"{n}.") for n in range(1, 5)):
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
                h3_positions[idx + 1] if idx + 1 < len(h3_positions) else len(blocks)
            )
            h3_blocks = blocks[h3_pos:end]
            h3_title = blocks[h3_pos].get("text", "")
            text = self._blocks_to_text(h3_blocks)
            if text.strip():
                groups.append({"title": h3_title, "text": text})

        return groups

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

            if bt == "h2" or (
                fs >= 12 and any(text.startswith(f"{n}.") for n in range(1, 5))
            ):
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                paragraphs.append(text)
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
        Abre una lección, genera plan con ContentLayoutPlanner,
        ejecuta: primero ADDs, luego EDITs, luego flashcards.
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

        # 2. Pre-scan: get existing blocks with editable counts
        existing_blocks = self.rise.count_editables_in_lesson()
        total_editables = sum(b["editables_count"] for b in existing_blocks)
        logger.info(
            f"  Bloques existentes: {len(existing_blocks)}, "
            f"{total_editables} editables"
        )

        # 3. Generate plan
        plan = self._planner.plan_lesson(content_groups, existing_blocks)

        # 4. Count how many ADD actions we need
        add_count = sum(1 for a in plan if a["action"] == "ADD")
        ux_count = sum(1 for a in plan if a["action"] == "ADD_UX")

        # 5. Add new blocks if needed
        if add_count > 0:
            logger.info(f"  Agregando {add_count} bloques nuevos...")
            # Find last editable block index as insertion point
            last_editable_idx = -1
            for block in existing_blocks:
                if block["type"] not in SKIP_BLOCK_TYPES:
                    last_editable_idx = block["index"]

            added = self.rise.add_multiple_blocks(
                last_editable_idx, "text", add_count
            )
            logger.info(f"  Bloques agregados: {added}/{add_count}")
            time.sleep(1)

        # 6. Add UX instruction blocks
        if ux_count > 0:
            logger.info(f"  Agregando {ux_count} instrucciones UX...")
            for action in plan:
                if action["action"] == "ADD_UX":
                    before_idx = action.get("before_index", 0)
                    # Add statement block before the interactive block
                    if self.rise.add_block_at_position(
                        before_idx - 1, "statement"
                    ):
                        time.sleep(0.5)

        # 7. Scroll back to top before editing
        try:
            self.rise.page.keyboard.press("Control+Home")
            time.sleep(1)
        except Exception:
            pass

        # 8. Re-scan blocks after additions (indices may have changed)
        all_blocks = self.rise._catalog_blocks_in_editor()
        wrappers = self.rise.page.locator("[class*='block-wrapper']")

        # 9. Build flat content queue from plan
        content_queue = []
        for action in plan:
            if action["action"] in ("EDIT", "ADD"):
                for text in action.get("texts", []):
                    if text and text.strip():
                        content_queue.append(text)
            elif action["action"] == "ADD_UX":
                for text in action.get("texts", []):
                    if text and text.strip():
                        content_queue.append(text)

        # 10. Single-pass edit: iterate all blocks, fill with content
        content_idx = 0
        total_edited = 0

        for block in all_blocks:
            block_type = block["type"]
            block_idx = block["index"]

            if block_type in SKIP_BLOCK_TYPES:
                continue

            # Skip flashcard blocks (handled separately via sidebar)
            if block_type == "flashcards":
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
                    self.rise.page.keyboard.press("Escape")
                    time.sleep(0.2)
                    continue

                edited_in_block = 0
                for i in range(count):
                    if content_idx >= len(content_queue):
                        break
                    text = content_queue[content_idx]
                    try:
                        ed = editables.nth(i)
                        if not ed.is_visible(timeout=1_000):
                            continue
                        ed.click()
                        time.sleep(0.2)
                        self.rise.page.keyboard.press("Control+a")
                        time.sleep(0.1)
                        self.rise.page.keyboard.press("Delete")
                        time.sleep(0.1)
                        paste_large_text(self.rise.page, text)
                        time.sleep(0.3)
                        total_edited += 1
                        edited_in_block += 1
                        content_idx += 1
                    except Exception as e:
                        logger.debug(
                            f"  Block {block_idx} editable {i} falló: {e}"
                        )
                        try:
                            self.rise.page.keyboard.press("Escape")
                        except Exception:
                            pass

                if edited_in_block > 0:
                    logger.info(
                        f"  [{block_type}:{block_idx}] "
                        f"{edited_in_block}/{count} editables"
                    )

                self.rise.page.keyboard.press("Escape")
                time.sleep(0.3)

            except Exception as e:
                logger.warning(
                    f"  Error en block {block_idx} [{block_type}]: {e}"
                )
                try:
                    self.rise.page.keyboard.press("Escape")
                except Exception:
                    pass

        self._blocks_inserted += total_edited

        # 11. Handle flashcard blocks via sidebar
        flashcard_actions = [a for a in plan if a["action"] == "FLASHCARD"]
        for action in flashcard_actions:
            cards = action.get("cards", [])
            if cards:
                # Re-find the flashcard block index after additions
                fc_blocks = [
                    b for b in all_blocks if b["type"] == "flashcards"
                ]
                if fc_blocks:
                    fc_idx = fc_blocks[0]["index"]
                    edited = self.rise.edit_flashcard_sidebar(fc_idx, cards)
                    self._blocks_inserted += edited
                    logger.info(f"  Flashcards editadas: {edited} cards")

        # 12. Log stats
        remaining = len(content_queue) - content_idx
        if remaining > 0:
            logger.info(
                f"  {remaining} fragmentos de contenido no insertados"
            )

        logger.info(
            f"  Lección {lesson_idx} completada: "
            f"{total_edited} editables editados"
        )

        # 13. UX/UI verification
        self._verify_lesson_ux(lesson_idx, title)

        # 14. Back to outline
        self.rise.go_back_to_outline()
        time.sleep(2)

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
