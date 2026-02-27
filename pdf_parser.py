"""
pdf_parser.py — Extracción y clasificación semántica de PDFs académicos
Usa PyMuPDF (fitz) para extraer texto con metadatos de fuente.

REGLA CRÍTICA: El texto se extrae VERBATIM. Sin modificaciones, resúmenes
ni paráfrasis. El contenido del PDF va íntegro al curso Rise 360.
"""

import re
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from utils import logger
import config


# ── Estructuras de datos ──────────────────────────────────────────────────

@dataclass
class ContentBlock:
    """Representa un bloque de contenido extraído del PDF."""
    block_type: str          # titulo, introduccion, h1, h2, h3, parrafo, lista_vinetas, tabla, imagen, conclusion, referencias
    text: str                # Texto verbatim del PDF
    level: int               # Nivel jerárquico (0=raíz, 1=H1, 2=H2, 3=H3)
    page_num: int            # Página del PDF (0-indexed)
    font_size: float         # Tamaño de fuente en puntos
    font_flags: int          # Flags PyMuPDF (bit 0 = bold, bit 1 = italic)
    is_bold: bool
    metadata: dict = field(default_factory=dict)  # Datos adicionales (rect, etc.)

    @property
    def is_heading(self) -> bool:
        return self.block_type in ("titulo", "h1", "h2", "h3",
                                   "introduccion", "conclusion", "referencias")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    """Sección del curso (corresponde a un H1 o sección especial)."""
    section_type: str        # introduccion, h1, conclusion, referencias
    heading: str             # Texto del encabezado
    blocks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": self.section_type,
            "heading": self.heading,
            "blocks": [b.to_dict() if isinstance(b, ContentBlock) else b
                       for b in self.blocks],
        }


# ── Función principal pública ─────────────────────────────────────────────

def parse_pdf(pdf_path: str) -> dict:
    """
    Extrae y estructura el contenido de un PDF académico.

    Args:
        pdf_path: Ruta al archivo PDF

    Returns:
        dict con estructura:
        {
            "title": str,
            "sections": [{"type": str, "heading": str, "blocks": [...]}],
            "metadata": {"pages": int, "pdf_path": str}
        }

    Raises:
        FileNotFoundError: Si el PDF no existe
        ValueError: Si el PDF está vacío o no se puede procesar
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

    logger.info(f"Iniciando análisis del PDF: {path.name}")

    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("PyMuPDF no está instalado. Ejecuta: pip install PyMuPDF")

    doc = fitz.open(str(path))

    if doc.page_count == 0:
        raise ValueError(f"El PDF está vacío: {pdf_path}")

    logger.info(f"PDF abierto: {doc.page_count} páginas")

    # Fase 1: Extracción raw de bloques con metadatos de fuente
    raw_blocks = _extract_raw_blocks(doc)
    logger.info(f"Bloques raw extraídos: {len(raw_blocks)}")

    # Fase 2: Clasificación semántica
    classified = _classify_blocks(raw_blocks)
    logger.info(f"Bloques clasificados: {len(classified)}")

    # Fase 3: Construcción de jerarquía
    content_tree = _build_hierarchy(classified, str(path), doc.page_count)
    logger.info(f"Jerarquía construida: {len(content_tree['sections'])} secciones")

    doc.close()

    # Cachear resultado para Caso 2 (mismo PDF, diferente plantilla)
    _cache_content(content_tree, path)

    return content_tree


def load_cached_content(pdf_path: str) -> Optional[dict]:
    """
    Intenta cargar el contenido cacheado de una extracción previa.
    Retorna None si no existe caché o es de un PDF diferente.
    """
    cache_path = config.CONTENT_CACHE_PATH
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("metadata", {}).get("pdf_path") == str(Path(pdf_path).resolve()):
            logger.info("Usando contenido PDF cacheado")
            return cached
    except Exception as e:
        logger.debug(f"No se pudo leer caché: {e}")
    return None


# ── Fase 1: Extracción raw ─────────────────────────────────────────────────

def _extract_raw_blocks(doc) -> list[dict]:
    """
    Extrae todos los bloques de texto de cada página con metadatos de fuente.
    Usa get_text("dict") de PyMuPDF para acceder a tamaño y flags de fuente.

    Retorna lista de:
    {
        "text": str,      # Texto del span (verbatim)
        "size": float,    # Tamaño de fuente en pt
        "flags": int,     # Flags (bold=1, italic=2, etc.)
        "page": int,      # Página (0-indexed)
        "rect": tuple,    # Bounding box (x0, y0, x1, y1)
        "type": str       # "text" o "image"
    }
    """
    raw = []

    for page_num, page in enumerate(doc):
        page_dict = page.get_text("dict", flags=11)  # flags: texto + imágenes

        for block in page_dict.get("blocks", []):
            # Bloques de imagen
            if block.get("type") == 1:
                raw.append({
                    "type": "image",
                    "text": "",
                    "size": 0,
                    "flags": 0,
                    "page": page_num,
                    "rect": block.get("bbox", (0, 0, 0, 0)),
                })
                continue

            # Bloques de texto
            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue

                    raw.append({
                        "type": "text",
                        "text": text,
                        "size": round(span.get("size", 10), 1),
                        "flags": span.get("flags", 0),
                        "page": page_num,
                        "rect": span.get("bbox", (0, 0, 0, 0)),
                        "font": span.get("font", ""),
                        "color": span.get("color", 0),
                    })

    # Consolidar spans de la misma línea que son continuación
    return _consolidate_spans(raw)


def _consolidate_spans(raw: list[dict]) -> list[dict]:
    """
    Une spans consecutivos de la misma página con el mismo tamaño de fuente
    que forman parte del mismo párrafo. Preserva el texto verbatim.
    """
    if not raw:
        return raw

    consolidated = []
    buffer = None

    for item in raw:
        if item["type"] == "image":
            if buffer:
                consolidated.append(buffer)
                buffer = None
            consolidated.append(item)
            continue

        if buffer is None:
            buffer = dict(item)
            continue

        same_page = item["page"] == buffer["page"]
        same_size = abs(item["size"] - buffer["size"]) < 0.5
        same_flags = item["flags"] == buffer["flags"]
        vertically_close = (
            abs(item["rect"][1] - buffer["rect"][3]) < 5
            if len(item["rect"]) >= 4 and len(buffer["rect"]) >= 4
            else False
        )

        if same_page and same_size and same_flags and vertically_close:
            # Continúa el mismo bloque — une texto verbatim
            buffer["text"] = buffer["text"] + " " + item["text"]
            buffer["rect"] = (
                min(buffer["rect"][0], item["rect"][0]),
                buffer["rect"][1],
                max(buffer["rect"][2], item["rect"][2]),
                max(buffer["rect"][3], item["rect"][3]),
            )
        else:
            consolidated.append(buffer)
            buffer = dict(item)

    if buffer:
        consolidated.append(buffer)

    return consolidated


# ── Fase 2: Clasificación semántica ───────────────────────────────────────

def _classify_blocks(raw: list[dict]) -> list[ContentBlock]:
    """
    Clasifica cada bloque raw en un tipo semántico basándose en:
    1. Keywords de texto (override sobre tamaño de fuente)
    2. Tamaño de fuente
    3. Flags de fuente (bold/italic)
    4. Patrones de texto (listas, tablas)

    REGLA: El texto se preserva verbatim. Solo se clasifica, no se modifica.
    """
    classified = []
    title_found = False

    for item in raw:
        if item["type"] == "image":
            cb = ContentBlock(
                block_type="imagen",
                text="",
                level=0,
                page_num=item["page"],
                font_size=0,
                font_flags=0,
                is_bold=False,
                metadata={"rect": item["rect"]},
            )
            classified.append(cb)
            continue

        text = item["text"]
        size = item["size"]
        flags = item["flags"]
        is_bold = bool(flags & 1)  # Bit 0 de PyMuPDF flags = bold

        block_type, level = _infer_type_and_level(text, size, is_bold, title_found)

        if block_type == "titulo":
            title_found = True

        cb = ContentBlock(
            block_type=block_type,
            text=text,  # VERBATIM — sin modificar
            level=level,
            page_num=item["page"],
            font_size=size,
            font_flags=flags,
            is_bold=is_bold,
            metadata={"font": item.get("font", ""), "rect": item.get("rect")},
        )

        # Detectar lista con viñetas (override al tipo ya clasificado)
        if _is_list_item(text):
            cb.block_type = "lista_vinetas"
            # No modificar el texto: conservar el marcador y todo el contenido

        # Detectar tabla (override)
        if _is_table_row(text):
            cb.block_type = "tabla"

        classified.append(cb)

    return classified


def _infer_type_and_level(
    text: str, size: float, bold: bool, title_found: bool
) -> tuple[str, int]:
    """
    Infiere el tipo semántico y nivel jerárquico de un bloque de texto.
    Keywords tienen prioridad sobre el tamaño de fuente.
    """
    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # ── Keywords con prioridad máxima ────────────────────────────────────
    if re.match(r"^introducci[oó]n\s*$", text_lower, re.IGNORECASE):
        return "introduccion", 1
    if re.match(r"^conclusi[oó]n(es)?\s*$", text_lower, re.IGNORECASE):
        return "conclusion", 1
    if re.match(r"^referencias\s*(bibliogr[aá]ficas?)?\s*$", text_lower, re.IGNORECASE):
        return "referencias", 1
    if re.match(r"^bibliograf[ií]a\s*$", text_lower, re.IGNORECASE):
        return "referencias", 1

    # ── Clasificación por tamaño de fuente ───────────────────────────────
    if size >= config.FONT_SIZE_TITULO and not title_found:
        return "titulo", 0
    elif size >= config.FONT_SIZE_H1:
        return "h1", 1
    elif size >= config.FONT_SIZE_H2:
        return "h2", 2
    elif size >= config.FONT_SIZE_H3 and bold:
        return "h3", 3
    else:
        return "parrafo", 0


def _is_list_item(text: str) -> bool:
    """Detecta si el texto es un ítem de lista con viñeta."""
    return bool(re.match(r"^[•\-\*·▪▸►◆◇○●]\s+.+", text.strip()))


def _is_table_row(text: str) -> bool:
    """
    Heurística simple para detectar filas de tabla:
    - Contiene múltiples pipes |
    - O contiene tabs repetidos con datos
    """
    if text.count("|") >= 2:
        return True
    if text.count("\t") >= 3:
        return True
    return False


# ── Fase 3: Construcción de jerarquía ────────────────────────────────────

def _build_hierarchy(
    blocks: list[ContentBlock], pdf_path: str, total_pages: int
) -> dict:
    """
    Ensambla los bloques clasificados en una estructura jerárquica:
    {
        "title": str,
        "sections": [Section.to_dict(), ...],
        "metadata": {...}
    }

    Lógica de stack:
    - H1 / secciones especiales abren una nueva Section
    - H2, H3 y párrafos van dentro de la Section actual
    - Los bloques antes del primer H1 van a una sección "preambulo"
    """
    result = {
        "title": "",
        "sections": [],
        "metadata": {
            "pdf_path": str(Path(pdf_path).resolve()),
            "total_pages": total_pages,
            "extracted_at": __import__("datetime").datetime.now().isoformat(),
        },
    }

    current_section: Optional[Section] = None
    preambulo_blocks = []
    tabla_buffer: list[ContentBlock] = []

    def _flush_tabla():
        """Une los bloques de tabla en uno solo (combinando filas)."""
        nonlocal tabla_buffer
        if not tabla_buffer:
            return
        combined_text = "\n".join(b.text for b in tabla_buffer)
        merged = ContentBlock(
            block_type="tabla",
            text=combined_text,  # VERBATIM — todas las filas unidas
            level=0,
            page_num=tabla_buffer[0].page_num,
            font_size=tabla_buffer[0].font_size,
            font_flags=0,
            is_bold=False,
        )
        if current_section:
            current_section.blocks.append(merged)
        else:
            preambulo_blocks.append(merged)
        tabla_buffer = []

    def _add_block_to_current(block: ContentBlock):
        if current_section:
            current_section.blocks.append(block)
        else:
            preambulo_blocks.append(block)

    for cb in blocks:
        # Manejar título del curso
        if cb.block_type == "titulo":
            result["title"] = cb.text
            continue

        # Flush tabla si el bloque actual no es tabla
        if cb.block_type != "tabla" and tabla_buffer:
            _flush_tabla()

        # Acumular filas de tabla
        if cb.block_type == "tabla":
            tabla_buffer.append(cb)
            continue

        # Secciones que abren un nuevo nivel H1
        if cb.block_type in ("h1", "introduccion", "conclusion", "referencias"):
            _flush_tabla()
            # Cerrar sección actual
            if current_section:
                result["sections"].append(current_section.to_dict())

            current_section = Section(
                section_type=cb.block_type,
                heading=cb.text,
                blocks=[],
            )
        else:
            # H2, H3, párrafos, listas, imágenes → van dentro de la sección actual
            _add_block_to_current(cb)

    # Flush final
    _flush_tabla()
    if current_section:
        result["sections"].append(current_section.to_dict())

    # Si hubo bloques antes del primer H1, añadir como preámbulo
    if preambulo_blocks and not result["title"]:
        # El primer bloque grande puede ser el título
        for b in preambulo_blocks:
            if b.block_type in ("titulo", "h1"):
                result["title"] = b.text
                break

    if preambulo_blocks:
        result["sections"].insert(0, {
            "type": "preambulo",
            "heading": "",
            "blocks": [b.to_dict() for b in preambulo_blocks],
        })

    # Si el título aún no se encontró, usar el nombre del archivo
    if not result["title"]:
        result["title"] = Path(pdf_path).stem.replace("_", " ").replace("-", " ")
        logger.warning(f"Título no detectado en el PDF. Usando nombre de archivo: {result['title']}")

    return result


# ── Caché ─────────────────────────────────────────────────────────────────

def _cache_content(content: dict, pdf_path: Path):
    """Guarda el contenido extraído en caché para reutilización."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.CONTENT_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        logger.debug(f"Contenido cacheado en: {config.CONTENT_CACHE_PATH}")
    except Exception as e:
        logger.warning(f"No se pudo cachear el contenido: {e}")


# ── CLI para testing ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Analiza un PDF académico")
    parser.add_argument("pdf", help="Ruta al PDF")
    parser.add_argument("--output", "-o", help="Guardar JSON en archivo")
    parser.add_argument("--pretty", "-p", action="store_true",
                        help="Mostrar JSON formateado")
    args = parser.parse_args()

    try:
        result = parse_pdf(args.pdf)
        output = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"JSON guardado en: {args.output}")
        else:
            print(output)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
