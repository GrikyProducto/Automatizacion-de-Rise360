"""
content_builder.py — Orquestador de inserción de contenido en Rise 360

Enfoque de Diseñador Gráfico Senior con criterio:
  - Edita TODOS los bloques que tengan texto editable
  - Mapea contenido POR TIPO de bloque:
      headings → títulos de H3 / subtemas
      flashcards → front=término, back=definición/explicación
      statements/quotes → frases clave o impactantes
      notes → información complementaria / tips
      accordion → título=subtema, body=contenido expandible
      text/paragraph → contenido de desarrollo regular
  - Lee las instrucciones/placeholders existentes antes de reemplazar
  - Divide el contenido en fragmentos cortos (~350 chars)
  - Verifica UX/UI desplazándose por cada lección después de editarla
  - Todo el texto se inserta VERBATIM del PDF
"""

import json
import re
import time
from typing import Callable, Optional
from utils import logger, with_retry, take_screenshot, paste_large_text
from rise_automation import RiseAutomation
import config


# ── ContentPool: organiza el contenido por tipo para distribución ──────


class ContentPool:
    """
    Organiza el contenido del PDF en 2 colas SIN duplicación:
      - headings: títulos H3 y subtemas (para heading blocks, accordion titles,
                  flashcard fronts, banner titles)
      - content: párrafos adaptados al tamaño disponible (para text, statement,
                 flashcard backs, accordion bodies, notes, quotes, lists)

    El max_chunk se calcula adaptativamente para que todo el contenido
    quepa en los editables disponibles de la plantilla.
    """

    def __init__(self, content_groups: list, max_chunk: int = 800):
        self.max_chunk = max(200, min(1200, max_chunk))
        self.headings: list[str] = []
        self.content: list[str] = []

        self._build(content_groups)

    def _build(self, content_groups: list):
        """Procesa los grupos de contenido en 2 colas sin duplicar."""
        for group in content_groups:
            title = group.get("title", "").strip()
            text = group.get("text", "").strip()

            # H3 title → cola de headings (min 5 chars)
            if title and len(title) > 5:
                self.headings.append(title)

            if not text:
                continue

            # Split por párrafos
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

            for para in paragraphs:
                if para == title:
                    continue
                if len(para) <= self.max_chunk + 50:
                    self.content.append(para)
                else:
                    for sub in self._split_long(para):
                        self.content.append(sub)

        logger.info(
            f"  ContentPool: {len(self.headings)} headings, "
            f"{len(self.content)} content chunks "
            f"(max ~{self.max_chunk} chars)"
        )

    def _split_long(self, text: str) -> list[str]:
        """Split text at sentence boundaries into ~max_chunk char chunks."""
        sentences = re.split(r"(?<=[.!?;:])\s+", text)
        chunks, current = [], ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if current and len(current) + len(s) + 1 > self.max_chunk:
                chunks.append(current)
                current = s
            else:
                current = f"{current} {s}".strip() if current else s
        if current:
            chunks.append(current)

        result = []
        for chunk in chunks:
            if len(chunk) > self.max_chunk * 1.5:
                words = chunk.split()
                sub = ""
                for w in words:
                    if sub and len(sub) + len(w) + 1 > self.max_chunk:
                        result.append(sub)
                        sub = w
                    else:
                        sub = f"{sub} {w}".strip() if sub else w
                if sub:
                    result.append(sub)
            else:
                result.append(chunk)
        return result

    # ── Métodos de consumo por tipo de bloque ─────────────────────────────

    def get_for_heading(self) -> str:
        """For heading blocks: H3 subtitle or truncated paragraph."""
        if self.headings:
            return self.headings.pop(0)
        if self.content:
            # Fallback: use first sentence of next paragraph (short)
            para = self.content[0]
            sentence = re.split(r"(?<=[.!?])\s+", para)[0]
            return sentence[:120]
        return ""

    def get_for_paragraph(self) -> str:
        """For text/paragraph blocks: regular content chunk."""
        if self.content:
            return self.content.pop(0)
        return ""

    def get_for_statement(self) -> str:
        """For statement blocks: next content paragraph."""
        return self.get_for_paragraph()

    def get_for_quote(self) -> str:
        """For quote/carousel blocks: next content paragraph."""
        return self.get_for_paragraph()

    def get_for_note(self) -> str:
        """For note blocks: next content paragraph."""
        return self.get_for_paragraph()

    def get_for_flashcard(self, editable_index: int) -> str:
        """Flashcard: even=front (heading), odd=back (content)."""
        if editable_index % 2 == 0:
            return self.get_for_heading()
        return self.get_for_paragraph()

    def get_for_accordion(self, editable_index: int) -> str:
        """Accordion: even=panel title (heading), odd=panel body (content)."""
        if editable_index % 2 == 0:
            return self.get_for_heading()
        return self.get_for_paragraph()

    def get_for_list(self) -> str:
        """For list blocks: next content paragraph."""
        return self.get_for_paragraph()

    def get_for_banner(self) -> str:
        """For banner/mondrian: heading or short title."""
        return self.get_for_heading()

    def remaining_count(self) -> int:
        return len(self.headings) + len(self.content)


# ── ContentBuilder principal ──────────────────────────────────────────


class ContentBuilder:
    """
    Inserta el contenido del PDF en el curso duplicado de Rise 360
    con criterio de diseñador gráfico senior.
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
        self._blocks_inserted = 0
        self._blocks_failed = 0
        self._failed_log: list[dict] = []

    # ── API pública ───────────────────────────────────────────────────────

    def build_course(self, content_json: dict):
        """Entry point. Extrae temas, mapea a lecciones, inserta con criterio."""
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

        lesson_map = self._build_lesson_map(topics, total_lessons)
        logger.info(f"Mapeo: {len(lesson_map)} lecciones a procesar")

        progress_per = 35 / max(len(lesson_map), 1)

        for lesson_idx, topic_data in lesson_map.items():
            pct = int(
                58 + list(lesson_map.keys()).index(lesson_idx) * progress_per
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

            # Edit lesson with type-aware content distribution
            self._insert_lesson_content(lesson_idx, topic_data)

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

    # ── Mapeo temas → lecciones ──────────────────────────────────────────

    def _build_lesson_map(self, topics: list, total_lessons: int) -> dict:
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
            f"  Mapeo: {len(h2_topics)} H2, "
            f"intro={'sí' if intro else 'no'}, "
            f"conclusión={'sí' if conclusion else 'no'}, "
            f"referencias={'sí' if references else 'no'}"
        )

        lesson_idx = 0
        for i, topic in enumerate(h2_topics):
            if lesson_idx >= total_lessons:
                break
            entry = dict(topic)
            if i == 0 and intro:
                entry["content_groups"] = (
                    intro.get("content_groups", [])
                    + entry.get("content_groups", [])
                )
                logger.info("  Intro fusionada con primer tema")
            lesson_map[lesson_idx] = entry
            lesson_idx += 1

        if conclusion and lesson_idx < total_lessons:
            lesson_map[lesson_idx] = conclusion
            lesson_idx += 1

        if references and lesson_idx < total_lessons:
            lesson_map[lesson_idx] = references
        elif references and conclusion:
            last_idx = lesson_idx - 1
            if last_idx in lesson_map:
                lesson_map[last_idx]["content_groups"].extend(
                    references.get("content_groups", [])
                )
                logger.info("  Referencias combinadas con conclusión")

        for idx, data in lesson_map.items():
            logger.info(
                f"  Lección {idx} → '{data['title'][:50]}' "
                f"({len(data.get('content_groups', []))} grupos)"
            )

        return lesson_map

    # ── Inserción de contenido en lecciones ──────────────────────────────

    def _insert_lesson_content(self, lesson_idx: int, topic_data: dict):
        """
        Abre una lección, hace pre-scan de editables, calcula tamaño
        adaptativo de chunks, y distribuye contenido POR TIPO de bloque.

        NO agrega bloques nuevos — adapta el contenido a la plantilla existente.

        Criterio de diseñador gráfico:
        - Headings → títulos de subtemas (NO párrafos)
        - Flashcards → front=término/pregunta, back=explicación
        - Statements/Quotes → frases clave
        - Notes → información complementaria
        - Accordion → título=subtema, body=desarrollo
        - Text → contenido de desarrollo regular
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

        # 2. PRE-SCAN: count total editables to calculate adaptive chunk size
        block_info = self.rise.count_editables_in_lesson()
        total_editables = sum(b["editables_count"] for b in block_info)

        # Calculate total text length
        total_text_chars = sum(
            len(g.get("text", "")) for g in content_groups
        )

        # Adaptive chunk size: fit ALL content into available editables
        # (subtract headings which are short and don't need chunking)
        n_headings = sum(
            1 for g in content_groups if g.get("title", "").strip()
        )
        paragraph_editables = max(total_editables - n_headings, 5)
        adaptive_max = max(200, min(1200, total_text_chars // paragraph_editables))

        logger.info(
            f"  Pre-scan: {total_editables} editables, "
            f"{total_text_chars} chars de contenido\n"
            f"  Chunk adaptativo: ~{adaptive_max} chars/editable"
        )

        # 3. Build ContentPool with adaptive chunk size
        pool = ContentPool(content_groups, max_chunk=adaptive_max)

        # 4. Define the type-aware callback
        def get_texts_for_block(
            block_type: str,
            editables_count: int,
            existing_texts: list[str],
        ) -> list[str]:
            """Returns content appropriate for the block type."""
            texts = []

            # Log existing template text for debugging
            if existing_texts and existing_texts[0]:
                logger.debug(
                    f"    Template text: '{existing_texts[0][:80]}...'"
                )

            # ── Heading blocks: ALWAYS use subtopic title ──
            if block_type in ("heading",):
                texts.append(pool.get_for_heading())

            # ── Statement blocks: key/impactful sentence ──
            elif block_type in ("statement",):
                for _ in range(editables_count):
                    texts.append(pool.get_for_statement())

            # ── Quote/carousel: notable sentence ──
            elif block_type in ("quote", "quote_carousel"):
                for _ in range(editables_count):
                    texts.append(pool.get_for_quote())

            # ── Note blocks: supplementary info ──
            elif block_type in ("note",):
                for _ in range(editables_count):
                    texts.append(pool.get_for_note())

            # ── Flashcard blocks: front=term, back=definition ──
            elif block_type in ("flashcards",):
                for i in range(editables_count):
                    texts.append(pool.get_for_flashcard(i))

            # ── Accordion blocks: title=heading, body=paragraph ──
            elif block_type in ("accordion",):
                for i in range(editables_count):
                    texts.append(pool.get_for_accordion(i))

            # ── List blocks ──
            elif block_type in ("bulleted_list", "numbered_list"):
                for _ in range(editables_count):
                    texts.append(pool.get_for_list())

            # ── Banner/mondrian: short title ──
            elif block_type in ("banner", "mondrian"):
                texts.append(pool.get_for_banner())
                for _ in range(editables_count - 1):
                    texts.append(pool.get_for_paragraph())

            # ── Text and everything else: regular paragraphs ──
            else:
                for _ in range(editables_count):
                    texts.append(pool.get_for_paragraph())

            return [t for t in texts if t and t.strip()]

        # 5. Scroll back to top before editing (pre-scan scrolled through)
        try:
            self.rise.page.keyboard.press("Control+Home")
            time.sleep(1)
        except Exception:
            pass

        # 6. EDIT PASS: click each block → discover editables → fill
        items_ok = self.rise.scan_and_edit_all_blocks(get_texts_for_block)
        self._blocks_inserted += items_ok

        # 7. Log remaining content (NOT adding new blocks — preserving template design)
        remaining_count = pool.remaining_count()
        if remaining_count > 0:
            logger.info(
                f"  {remaining_count} fragmentos no cupieron en la plantilla "
                f"(template preservado, no se agregan bloques nuevos)"
            )

        logger.info(
            f"  Lección {lesson_idx} completada: "
            f"{items_ok} editables editados exitosamente"
        )

        # 6. UX/UI verification
        self._verify_lesson_ux(lesson_idx, title)

        # 7. Back to outline
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
