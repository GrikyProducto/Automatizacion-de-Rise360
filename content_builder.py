"""
content_builder.py — Orquestador de inserción de contenido en Rise 360

Enfoque de Diseñador Gráfico Senior:
  - Solo edita bloques de TEXTO (text, statement, quote, list, heading)
  - NO toca elementos visuales (imágenes, divisores, banners, flashcards)
  - Organiza contenido por subtemas (H3) para una jerarquía visual limpia
  - Todo el texto se inserta VERBATIM del PDF, sin modificaciones

Estructura del PDF esperada:
  - Una sección grande (type="introduccion") con 4 H2 topics adentro
  - Cada H2 = 1 tema = 1 lesson en Rise 360
  - Dentro de cada H2, subtemas H3 que se mapean a bloques de texto
  - Secciones separadas para Conclusión y Referencias
"""

import json
import time
from typing import Callable, Optional
from utils import logger, with_retry, take_screenshot, paste_large_text
from rise_automation import RiseAutomation
import config


class ContentBuilder:
    """
    Inserta el contenido del PDF en el curso duplicado de Rise 360.

    Estrategia:
    1. Extrae temas (H2) del PDF
    2. Mapea cada tema a una lección de Rise
    3. Dentro de cada lección, identifica solo bloques de TEXTO
    4. Distribuye el contenido agrupado por subtemas (H3)
    5. Deja intactos los elementos visuales (imágenes, divisores, etc.)

    REGLA CRÍTICA: Todo el texto se inserta VERBATIM del PDF.
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
        """
        Punto de entrada principal.
        Extrae los temas H2 del PDF y los inserta en las lecciones de Rise.
        """
        self._progress("Preparando contenido del curso...", 58)

        # 1. Extraer temas del PDF (split por H2)
        topics = self._extract_topics(content_json)
        logger.info(f"Temas extraídos del PDF: {len(topics)}")
        for t in topics:
            logger.info(
                f"  [{t['type']}] '{t['title'][:60]}' "
                f"→ {len(t.get('content_groups', []))} grupos de contenido"
            )

        # 2. Obtener lecciones disponibles en el outline del duplicado
        lessons = self.rise.get_lessons_in_outline()
        total_lessons = len(lessons)
        logger.info(f"Lecciones en el curso duplicado: {total_lessons}")

        if total_lessons == 0:
            logger.error("No se encontraron lecciones en el outline")
            return

        # 3. Mapear temas a lecciones
        lesson_map = self._build_lesson_map(topics, total_lessons)
        logger.info(f"Mapeo tema→lección: {len(lesson_map)} lecciones a procesar")

        # 4. Procesar cada lección
        progress_per_lesson = 35 / max(len(lesson_map), 1)  # 58% → 93%

        for lesson_idx, topic_data in lesson_map.items():
            pct = int(58 + list(lesson_map.keys()).index(lesson_idx) * progress_per_lesson)
            self._progress(
                f"Lección {lesson_idx + 1}/{total_lessons}: "
                f"{topic_data['title'][:40]}...",
                pct,
            )

            self._insert_lesson_content(lesson_idx, topic_data)

        # 5. Guardar
        self._progress("Guardando curso...", 95)
        self.rise.save_course()

        # Reporte
        total = self._blocks_inserted + self._blocks_failed
        logger.info(
            f"Construcción completada. "
            f"Bloques editados: {self._blocks_inserted}/{total}. "
            f"Fallidos: {self._blocks_failed}"
        )
        if self._failed_log:
            logger.warning(
                f"Bloques con errores:\n"
                f"{json.dumps(self._failed_log, indent=2, ensure_ascii=False)}"
            )

        self._progress("¡Curso completado exitosamente!", 100)

    # ── Extracción de temas del PDF ──────────────────────────────────────

    def _extract_topics(self, content_json: dict) -> list[dict]:
        """
        Extrae temas individuales del contenido del PDF.

        El PDF tiene una sección grande (type "introduccion") que contiene
        los 4 H2 topics. Los separa por H2 y agrupa sus subtemas H3.
        También extrae Conclusión y Referencias como temas separados.
        """
        topics = []
        sections = content_json.get("sections", [])

        for section in sections:
            blocks = section.get("blocks", [])
            h2_indices = [
                i for i, b in enumerate(blocks)
                if b.get("block_type") == "h2"
            ]

            if h2_indices:
                # Sección con H2 topics — splitear
                # Contenido ANTES del primer H2 (introducción general)
                pre_h2 = self._filter_labels(blocks[:h2_indices[0]])
                if pre_h2:
                    intro_groups = self._group_by_h3(pre_h2)
                    topics.append({
                        "type": "intro",
                        "title": "Introducción general",
                        "content_groups": intro_groups,
                    })

                # Cada H2 topic
                for idx, h2_pos in enumerate(h2_indices):
                    end = h2_indices[idx + 1] if idx + 1 < len(h2_indices) else len(blocks)
                    topic_blocks = self._filter_labels(blocks[h2_pos:end])
                    h2_text = blocks[h2_pos].get("text", f"Tema {idx + 1}")
                    content_groups = self._group_by_h3(topic_blocks)
                    topics.append({
                        "type": "topic",
                        "title": h2_text,
                        "content_groups": content_groups,
                    })

            elif section.get("type") == "conclusion":
                clean = self._filter_labels(blocks)
                if clean:
                    topics.append({
                        "type": "conclusion",
                        "title": section.get("heading", "Conclusión"),
                        "content_groups": [
                            {"title": "", "text": self._blocks_to_text(clean)}
                        ],
                    })

            elif section.get("type") == "referencias" and len(blocks) > 2:
                clean = self._filter_labels(blocks)
                if clean:
                    topics.append({
                        "type": "references",
                        "title": "Referencias",
                        "content_groups": [
                            {"title": "", "text": self._blocks_to_text(clean)}
                        ],
                    })

        return topics

    def _filter_labels(self, blocks: list) -> list:
        """
        Filtra bloques que son etiquetas/metadatos del PDF (font_size ≤7,
        texto UPPERCASE con underscores como PARAGRAPH, SUBTOPIC_TITLE, etc.)
        """
        return [
            b for b in blocks
            if not self._is_label_block(b)
        ]

    def _is_label_block(self, block: dict) -> bool:
        """Detecta si un bloque es una etiqueta de metadatos del PDF."""
        fs = block.get("font_size", 0)
        text = block.get("text", "").strip()
        if fs <= 7 and text.isupper():
            if "_" in text or text in {
                "PARAGRAPH", "REFERENCE_ITEM", "TOPIC_TITLE",
                "SUBTOPIC_TITLE", "NUMBERED_LIST",
            }:
                return True
        return False

    # ── Agrupamiento por subtemas H3 ─────────────────────────────────────

    def _group_by_h3(self, blocks: list) -> list[dict]:
        """
        Agrupa bloques por subtemas H3 para organizar el contenido.
        Cada H3 y sus párrafos siguientes forman un grupo.

        Retorna lista de {"title": str, "text": str} donde:
        - title: el texto del H3 (o vacío para contenido pre-H3)
        - text: todo el contenido del grupo formateado
        """
        if not blocks:
            return []

        # Encontrar posiciones de H3 (font_size 13, con numeración X.Y.)
        h3_positions = []
        for i, b in enumerate(blocks):
            fs = b.get("font_size", 0)
            text = b.get("text", "")
            bt = b.get("block_type", "")
            if bt == "h2":
                continue  # H2 es el título del topic, no un grupo
            if fs >= 12 and any(text.startswith(f"{n}.") for n in range(1, 5)):
                h3_positions.append(i)

        if not h3_positions:
            # Sin H3: todo el contenido es un solo grupo
            text = self._blocks_to_text(blocks)
            if text.strip():
                return [{"title": "", "text": text}]
            return []

        groups = []

        # Contenido ANTES del primer H3 (H2 title + intro)
        pre_h3 = blocks[:h3_positions[0]]
        text = self._blocks_to_text(pre_h3)
        if text.strip():
            groups.append({"title": "", "text": text})

        # Cada H3 subtema como un grupo separado
        for idx, h3_pos in enumerate(h3_positions):
            end = h3_positions[idx + 1] if idx + 1 < len(h3_positions) else len(blocks)
            h3_blocks = blocks[h3_pos:end]
            h3_title = blocks[h3_pos].get("text", "")
            text = self._blocks_to_text(h3_blocks)
            if text.strip():
                groups.append({"title": h3_title, "text": text})

        return groups

    def _blocks_to_text(self, blocks: list) -> str:
        """
        Convierte bloques del PDF a texto formateado para Rise 360.

        Reglas:
        - Los labels (PARAGRAPH, etc.) actúan como separadores de párrafos
        - Bloques consecutivos del mismo font_size se unen con espacio (mismo párrafo)
        - H2/H3 van como línea propia
        - Listas mantienen sus marcadores
        - Se usa \\n\\n entre párrafos para buena lectura
        """
        paragraphs = []
        current_parts = []
        last_real_fs = 0

        for b in blocks:
            text = b.get("text", "").strip()
            fs = b.get("font_size", 0)
            bt = b.get("block_type", "")

            # Skip labels (ya deberían estar filtrados, pero por seguridad)
            if self._is_label_block(b):
                # Flush current paragraph
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                    last_real_fs = 0
                continue

            if not text:
                continue

            # H2 y H3: línea propia
            if bt == "h2" or (fs >= 12 and any(text.startswith(f"{n}.") for n in range(1, 5))):
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                paragraphs.append(text)
                last_real_fs = fs
                continue

            # Lista: línea propia
            if bt == "lista_vinetas":
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                    current_parts = []
                paragraphs.append(text)
                last_real_fs = fs
                continue

            # Texto normal: agrupar si es continuación del mismo párrafo
            # (mismo font_size y sin label intermediario)
            if fs == last_real_fs and current_parts:
                # Continuación del mismo párrafo
                current_parts.append(text)
            else:
                # Nuevo párrafo
                if current_parts:
                    paragraphs.append(" ".join(current_parts))
                current_parts = [text]

            last_real_fs = fs

        # Flush último párrafo
        if current_parts:
            paragraphs.append(" ".join(current_parts))

        return "\n\n".join(paragraphs)

    # ── Mapeo temas → lecciones ──────────────────────────────────────────

    def _build_lesson_map(self, topics: list, total_lessons: int) -> dict:
        """
        Mapea temas extraídos del PDF a lecciones de Rise 360.

        Template tiene: Tema 1, Tema 2, Tema 3, Conclusiones, Referencias
        PDF tiene: intro + 4 H2 topics + conclusión + referencias

        Estrategia:
        - Intro se fusiona con el primer topic
        - Cada H2 topic → una lección
        - Conclusión → penúltima lección
        - Referencias → última lección
        """
        lesson_map = {}

        # Separar por tipo
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
            f"  Mapeo: {len(h2_topics)} temas H2, "
            f"intro={'sí' if intro else 'no'}, "
            f"conclusión={'sí' if conclusion else 'no'}, "
            f"referencias={'sí' if references else 'no'}"
        )

        # Asignar H2 topics a lecciones (fusionar intro con topic 1)
        lesson_idx = 0
        for i, topic in enumerate(h2_topics):
            if lesson_idx >= total_lessons:
                logger.warning(
                    f"  No hay más lecciones disponibles para tema: '{topic['title'][:40]}'"
                )
                break

            entry = dict(topic)
            # Fusionar intro con primer topic
            if i == 0 and intro:
                intro_groups = intro.get("content_groups", [])
                entry["content_groups"] = intro_groups + entry.get("content_groups", [])
                logger.info("  Intro fusionada con primer tema")

            lesson_map[lesson_idx] = entry
            lesson_idx += 1

        # Conclusión
        if conclusion and lesson_idx < total_lessons:
            lesson_map[lesson_idx] = conclusion
            lesson_idx += 1

        # Referencias
        if references and lesson_idx < total_lessons:
            lesson_map[lesson_idx] = references
        elif references and conclusion:
            # Combinar referencias al final de la conclusión
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
        Abre una lección, identifica SOLO los bloques de texto,
        y distribuye el contenido organizado por subtemas.

        Enfoque de diseñador gráfico senior:
        - Solo edita bloques de texto (text, statement, quote, list, heading)
        - Deja intactos: imágenes, divisores, banners, flashcards
        - Contenido organizado por subtemas para jerarquía visual clara
        - Si no hay suficientes bloques de texto, agrega nuevos
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

        # 1. Abrir el editor de la lección
        if not self.rise.open_lesson_editor(lesson_idx):
            logger.warning(f"No se pudo abrir editor de lección {lesson_idx}")
            self._blocks_failed += len(content_groups)
            return

        # 2. Identificar SOLO bloques de texto (ignorar imágenes, divisores, etc.)
        text_blocks = self.rise.get_text_blocks_in_lesson()
        logger.info(
            f"  Bloques de texto disponibles: {len(text_blocks)}\n"
            f"  Grupos de contenido a insertar: {len(content_groups)}"
        )

        # 3. Distribuir contenido en los bloques de texto disponibles
        items_ok = 0
        items_fail = 0

        for i, group in enumerate(content_groups):
            group_text = group.get("text", "").strip()
            group_title = group.get("title", "")
            if not group_text:
                continue

            if i < len(text_blocks):
                # ── Editar bloque de texto existente ──
                block = text_blocks[i]
                block_idx = block["index"]  # Índice del wrapper en el DOM
                logger.info(
                    f"  [{i}] Editando bloque {block_idx} "
                    f"(tipo: {block['type']}): '{group_title[:40] or group_text[:40]}...'"
                )

                success = self.rise.edit_block_text(block_idx, group_text)
                if success:
                    self._blocks_inserted += 1
                    items_ok += 1
                else:
                    self._blocks_failed += 1
                    items_fail += 1
                    self._failed_log.append({
                        "lesson": lesson_idx,
                        "block_index": block_idx,
                        "text_preview": group_text[:100],
                        "error": "edit_block_text retornó False",
                    })
            else:
                # ── Crear nuevo bloque de texto ──
                logger.info(
                    f"  [{i}] Agregando nuevo bloque de texto: "
                    f"'{group_title[:40] or group_text[:40]}...'"
                )

                added = self.rise.add_block("text")
                if added:
                    time.sleep(1)
                    success = self.rise.insert_text(group_text, clear_first=True)
                    if success:
                        self._blocks_inserted += 1
                        items_ok += 1
                    else:
                        self._blocks_failed += 1
                        items_fail += 1
                        self._failed_log.append({
                            "lesson": lesson_idx,
                            "block_index": "new",
                            "text_preview": group_text[:100],
                            "error": "insert_text en nuevo bloque falló",
                        })
                else:
                    self._blocks_failed += 1
                    items_fail += 1
                    self._failed_log.append({
                        "lesson": lesson_idx,
                        "block_index": "new",
                        "text_preview": group_text[:100],
                        "error": "add_block falló",
                    })

        logger.info(
            f"  Lección {lesson_idx} completada: "
            f"{items_ok} exitosos, {items_fail} fallidos"
        )

        # 4. Volver al outline para la siguiente lección
        self.rise.go_back_to_outline()
        time.sleep(2)

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
