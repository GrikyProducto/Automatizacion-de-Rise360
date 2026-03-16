"""
instructional_designer.py — Diseñador instruccional IA para Rise 360 Automator

Usa Groq API (Llama 3.3-70B) para reemplazar el mapeo secuencial de
ContentLayoutPlanner con decisiones pedagógicas inteligentes por lección.

Fallback automático a ContentLayoutPlanner si Groq no está disponible.

Rol 1 (Diseñador instruccional senior):
  - Analiza el tipo de contenido (definición, proceso, lista, concepto clave)
  - Elige el bloque Rise que mejor lo presenta pedagógicamente
  - Balancea bloques interactivos con texto plano

Rol 2 (Diseñador gráfico senior):
  - Variedad visual: nunca >2 text blocks consecutivos
  - Usa statement/heading para crear ritmo visual
  - Respeta la plantilla existente (KEEP visual blocks, EDIT text blocks)

REGLA CRÍTICA: el texto siempre es VERBATIM del PDF. La IA clasifica y estructura,
NUNCA reescribe ni resume.
"""

import json
import hashlib
import re
import time
from typing import Optional

from utils import logger
import config


# ── Constantes ───────────────────────────────────────────────────────────────

VALID_ACTIONS = {"EDIT", "ADD", "ADD_UX", "FLASHCARD", "KEEP"}

VALID_BLOCK_TYPES = {
    "text", "statement", "heading", "quote", "quote_carousel",
    "bulleted_list", "numbered_list", "flashcards", "accordion",
    "tabs", "process", "sorting", "labeled", "image",
    "banner", "mondrian", "divider", "spacer", "continue",
}

SYSTEM_PROMPT = """Eres el diseñador instruccional y diseñador gráfico senior que construye cursos corporativos en Articulate Rise 360 para clientes latinoamericanos. Tu trabajo replica exactamente lo que hace un diseñador humano experto: lees el contenido del PDF y decides qué bloque Rise lo presenta mejor para el estudiante.

## REGLAS ABSOLUTAS (nunca romper bajo ninguna circunstancia):

1. **TEXTO VERBATIM**: El campo "texts" de cada acción debe contener EXACTAMENTE el texto del input, carácter por carácter. NUNCA reescribas, resumas, parafrasees ni traduzcas. Si el texto tiene errores ortográficos, los conservas. Si tiene citas con formato "(Autor, 2020)", lo conservas exactamente.

2. **FIDELIDAD TOTAL**: Cada fragmento de contenido del input debe aparecer en algún lugar del plan. Si no sabes dónde ponerlo, úsalo en un bloque "heading". NUNCA omitas contenido.

3. **TODO EN ESPAÑOL**: El contenido siempre va en español (ya viene así del PDF). Los únicos strings en inglés son nombres de acciones (EDIT, ADD, KEEP) y tipos de bloque (heading, text, flashcards, etc.).

4. **RESPONDE SOLO JSON**: La respuesta es únicamente {"plan": [...]}. Sin explicaciones, sin markdown, sin texto adicional.

## CÓMO LEER EL CAMPO "suggested_block"

Cada fragmento de contenido incluye un campo "suggested_block" con una sugerencia de clasificación semántica:
- "quote_carousel" → el texto tiene cita de autor (según X, citado por Y, (Apellido, año))
- "bulleted_list" → el texto original tiene viñetas (•, -, *, →)
- "numbered_list" → el texto original tiene numeración (1., 2., 3.)
- "flashcard_candidate" → el texto tiene estructura "término: definición"
- "statement" → frase corta e impactante, menos de 180 caracteres
- "text_table" → el texto contiene una tabla
- "heading" → párrafo expositivo general (default)

La sugerencia es orientativa. Puedes usarla o ignorarla si el contexto de la lección lo justifica mejor. Lo importante es el criterio pedagógico y visual.

## TAXONOMÍA DE DECISIÓN: QUÉ BLOQUE USAR

| Tipo de contenido | Bloque Rise | Cuándo |
|---|---|---|
| Párrafo expositivo | heading | Default para la mayoría del contenido. El humano usa heading para párrafos normales (53%+ de bloques). |
| Concepto + definición | flashcards | Cuando hay pares claro término→explicación. 1-2 por lección máximo. |
| Cita de autor | quote_carousel | Cuando el texto dice "según X", "de acuerdo con Y", o tiene (Apellido, año). |
| Lista con viñetas | bulleted_list | Solo cuando el texto ORIGINAL ya tiene •, -, *, →. |
| Lista numerada | numbered_list | Solo cuando el texto ORIGINAL ya tiene 1., 2., 3. o pasos. |
| Principio clave | statement | Frases cortas e impactantes (<180 chars). Máximo 3 por lección. |
| Dos conceptos paralelos | text_twocol | Cuando hay una comparación A vs B o dos ideas complementarias de igual peso. |
| Tabla del PDF | text | Preservar con saltos de línea y espaciado legible. |
| Imagen/figura del PDF | image | Placeholder con caption como texto del bloque. |
| Separador entre secciones | divider | Con moderación, entre secciones grandes. |

## REGLAS DE DISEÑO VISUAL

- **Ritmo**: no más de 2 bloques del mismo tipo consecutivos (excepto heading, que es dominante).
- **Interactividad**: cada lección debe tener al menos 1 bloque interactivo si el contenido lo permite (flashcards, quote_carousel, accordion, tabs).
- **Posición de interactivos**: siempre en el MEDIO de la lección, nunca al inicio.
- **ADD_UX**: agregar statement con instrucción UX ANTES de cada bloque interactivo.
- **Proporción**: entre 8 y 20 bloques por lección. Ni muy pocas ni demasiadas.

## INSTRUCCIONES UX (usar EXACTAMENTE estos textos):
- Antes de flashcards: "Da clic en cada tarjeta para ver su información al reverso"
- Antes de accordion: "Despliega cada sección para ver su contenido"
- Antes de tabs: "Selecciona cada pestaña para explorar el contenido"
- Antes de quote_carousel: "Navega por cada una de las frases destacadas"
- Antes de process: "Navega por cada paso del proceso"
- Antes de sorting: "Arrastra y ordena los elementos según corresponda"
- Antes de labeled: "Haz clic en cada punto para ver la información"

## REGLAS DE PLANTILLA

- **Banners sin editables** (editables=0): son corporativos del cliente → siempre KEEP, nunca modificar.
- **Banners con editables**: pueden recibir el título de la lección.
- **Wrapper blocks**: siempre KEEP (son estructurales).
- **Estrategia EDIT > ADD**: reutilizar bloques existentes es más rápido (2s) que agregar nuevos (15-30s). Usa EDIT siempre que haya bloques disponibles. Puedes cambiar el tipo del bloque existente especificando block_type diferente al actual.

## ESQUEMA DE ACCIONES

KEEP — bloque visual de plantilla sin cambios:
{"action": "KEEP", "block_type": "<tipo>", "target_index": <int>}

EDIT — reutilizar bloque existente (cambia tipo si block_type difiere del actual):
{"action": "EDIT", "block_type": "<tipo_deseado>", "target_index": <int>, "texts": ["<texto verbatim>"]}

ADD — nuevo bloque (SOLO cuando no hay bloques existentes reutilizables):
{"action": "ADD", "block_type": "<tipo>", "texts": ["<texto verbatim>"]}

ADD_UX — instrucción UX antes de bloque interactivo:
{"action": "ADD_UX", "block_type": "statement", "texts": ["<texto UX exacto>"], "before_index": <int>}

FLASHCARD — poblar tarjetas del sidebar:
{"action": "FLASHCARD", "target_index": <int>, "cards": [{"front": "<término verbatim>", "back": "<definición verbatim>"}]}

## EJEMPLO DE PLAN CORRECTO

Input: lección sobre "Concepto y Evolución de la Cadena de Suministro"
Plantilla tiene: [banner(0,editables=0), text(1), text(2), text(3), flashcards(4), wrapper(5)]
Fragmentos: intro expositiva + definición SCM con cita + lista de 4 flujos + cita de Chopra y Meindl

Plan correcto:
{
  "plan": [
    {"action": "KEEP", "block_type": "banner", "target_index": 0},
    {"action": "EDIT", "block_type": "heading", "target_index": 1,
     "texts": ["En el entorno competitivo actual, las organizaciones enfrentan el desafío constante de optimizar sus procesos para satisfacer las necesidades del cliente al menor costo posible."]},
    {"action": "EDIT", "block_type": "heading", "target_index": 2,
     "texts": ["Más que una simple evolución de la logística tradicional, el SCM representa un enfoque integral que articula decisiones operativas, tácticas y estratégicas, abarcando desde la gestión de proveedores hasta la entrega final."]},
    {"action": "EDIT", "block_type": "quote_carousel", "target_index": 3,
     "texts": ["Según Chopra y Meindl (2016), una cadena de suministro eficiente y flexible es uno de los pilares fundamentales para alcanzar niveles superiores de productividad, rentabilidad y diferenciación."]},
    {"action": "ADD_UX", "block_type": "statement",
     "texts": ["Da clic en cada tarjeta para ver su información al reverso"], "before_index": 4},
    {"action": "FLASHCARD", "target_index": 4,
     "cards": [
       {"front": "Supply Chain Management (SCM)", "back": "Red dinámica de organizaciones, procesos y recursos que colaboran en la planificación, ejecución y control de actividades destinadas a satisfacer las necesidades del cliente final."},
       {"front": "Flujo de información", "back": "Datos sobre demanda, inventarios y pedidos que fluyen en ambas direcciones entre los actores de la cadena."},
       {"front": "Flujo financiero", "back": "Movimiento de pagos, créditos y condiciones financieras entre los actores de la cadena."},
       {"front": "Flujo de materiales", "back": "Movimiento físico de productos desde proveedores hasta el cliente final, incluyendo logística inversa."}
     ]},
    {"action": "KEEP", "block_type": "wrapper", "target_index": 5}
  ]
}

Responde ÚNICAMENTE con el JSON. Sin explicaciones, sin markdown, sin texto adicional."""


USER_PROMPT_TEMPLATE = """## LECCIÓN: {lesson_title}
## TIPO: {lesson_type}

## BLOQUES EXISTENTES EN LA PLANTILLA (en orden, de arriba a abajo):
{existing_blocks_json}

## CONTENIDO DE LA LECCIÓN (verbatim del PDF):
{content_groups_json}

## TU TAREA:
Genera el plan óptimo para esta lección.
- Usa KEEP para bloques visuales (image, banner, divider, spacer, continue, mondrian).
- Usa EDIT para reutilizar bloques editables existentes (preserva estilo).
- Usa ADD para contenido que no cabe en bloques existentes.
- Si hay un bloque flashcards en la plantilla Y el contenido tiene pares concepto-definición, crea una acción FLASHCARD.
- Agrega ADD_UX antes de cada bloque interactivo.
- Cada fragmento de contenido incluye un campo "suggested_block" con sugerencia semántica. Úsala como guía, pero aplica tu criterio de diseñador si el contexto lo justifica mejor.
- Responde ÚNICAMENTE con {{"plan": [...]}}"""


# ── Clase principal ──────────────────────────────────────────────────────────

class InstructionalDesigner:
    """
    Wrapper de Groq API (Llama 3.3-70B) que reemplaza ContentLayoutPlanner
    para decisiones de distribución de contenido en lecciones Rise 360.
    """

    def __init__(self):
        self._client = None
        self._fallback = None  # lazy import ContentLayoutPlanner
        self._call_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._plan_cache: dict = {}

    # ── API pública ──────────────────────────────────────────────────────

    def plan_lesson_with_ai(
        self,
        content_groups: list[dict],
        existing_blocks: list[dict],
        lesson_title: str = "",
        lesson_type: str = "topic",
    ) -> list[dict]:
        """
        Genera un plan de lección usando IA.
        Retorna lista de acciones compatible con _execute_lesson_plan().
        Fallback automático a ContentLayoutPlanner si falla.
        """
        # Check cache
        cache_key = self._content_hash(content_groups)
        if cache_key in self._plan_cache:
            logger.info(f"  [IA] Cache hit para '{lesson_title[:40]}'")
            return self._plan_cache[cache_key]

        client = self._get_client()
        if client is None:
            return self._get_fallback().plan_lesson(
                content_groups, existing_blocks
            )

        try:
            raw_plan = self._call_groq(
                client, content_groups, existing_blocks,
                lesson_title, lesson_type,
            )
            validated = self._validate_plan(
                raw_plan, content_groups, existing_blocks
            )
            validated = self._ensure_content_completeness(
                validated, content_groups
            )
            self._plan_cache[cache_key] = validated
            logger.info(
                f"  [IA] Plan generado: {len(validated)} acciones "
                f"(call #{self._call_count})"
            )
            return validated

        except Exception as e:
            logger.warning(
                f"  [IA] Groq falló: {e} — usando planner por reglas"
            )
            return self._get_fallback().plan_lesson(
                content_groups, existing_blocks
            )

    def get_stats(self) -> dict:
        """Retorna estadísticas de uso de la API."""
        return {
            "api_calls": self._call_count,
            "cache_hits": len(self._plan_cache),
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
        }

    # ── Cliente Groq (lazy init) ─────────────────────────────────────────

    def _get_client(self):
        """Inicializa el cliente Groq solo cuando se necesita."""
        if self._client is not None:
            return self._client
        try:
            from groq import Groq
            api_key = config.GROQ_API_KEY
            if not api_key:
                logger.warning(
                    "GROQ_API_KEY no configurada — usando fallback"
                )
                return None
            self._client = Groq(
                api_key=api_key,
                timeout=config.GROQ_TIMEOUT_SEC,
            )
            logger.info("Cliente Groq inicializado (Llama 3.3-70B)")
            return self._client
        except ImportError:
            logger.warning(
                "SDK groq no instalado — usando fallback. "
                "Instala con: pip install groq"
            )
            return None
        except Exception as e:
            logger.warning(f"Groq init falló: {e} — usando fallback")
            return None

    def _get_fallback(self):
        """Lazy-load ContentLayoutPlanner para evitar import circular."""
        if self._fallback is None:
            from content_builder import ContentLayoutPlanner
            self._fallback = ContentLayoutPlanner()
        return self._fallback

    # ── Llamada a Groq ───────────────────────────────────────────────────

    def _call_groq(
        self,
        client,
        content_groups: list[dict],
        existing_blocks: list[dict],
        lesson_title: str,
        lesson_type: str,
    ) -> list[dict]:
        """Construye mensajes, llama la API y parsea la respuesta."""
        user_message = self._build_user_message(
            content_groups, existing_blocks, lesson_title, lesson_type,
        )

        logger.debug(
            f"  [IA] Enviando a Groq: {len(user_message)} chars, "
            f"lección '{lesson_title[:30]}'"
        )

        start = time.time()
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=config.GROQ_TEMPERATURE,
            max_tokens=config.GROQ_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start

        self._call_count += 1

        # Track token usage
        usage = response.usage
        if usage:
            self._total_input_tokens += usage.prompt_tokens or 0
            self._total_output_tokens += usage.completion_tokens or 0
            logger.info(
                f"  [IA] Respuesta en {elapsed:.1f}s — "
                f"{usage.prompt_tokens} in / "
                f"{usage.completion_tokens} out tokens"
            )

        raw_text = response.choices[0].message.content
        return self._parse_response(raw_text)

    def _build_user_message(
        self,
        content_groups: list[dict],
        existing_blocks: list[dict],
        lesson_title: str,
        lesson_type: str,
    ) -> str:
        """Construye el mensaje de usuario con datos de la lección."""
        # Simplificar existing_blocks para el prompt
        blocks_simplified = [
            {
                "index": b["index"],
                "type": b["type"],
                "editables": b.get("editables_count", 0),
            }
            for b in existing_blocks
        ]

        # Pre-clasificar contenido semánticamente
        classified_groups = self._pre_classify_content(content_groups)

        # Simplificar content_groups (mantener texto verbatim + suggested_block)
        content_simplified = []
        for g in classified_groups:
            item = {}
            title = g.get("title", "").strip()
            text = g.get("text", "").strip()
            suggested = g.get("suggested_block", "heading")
            if title:
                item["title"] = title
            if text:
                item["text"] = text
            item["suggested_block"] = suggested
            if item.get("title") or item.get("text"):
                content_simplified.append(item)

        return USER_PROMPT_TEMPLATE.format(
            lesson_title=lesson_title,
            lesson_type=lesson_type,
            existing_blocks_json=json.dumps(
                blocks_simplified, ensure_ascii=False, indent=2
            ),
            content_groups_json=json.dumps(
                content_simplified, ensure_ascii=False, indent=2
            ),
        )

    # ── Parseo y validación ──────────────────────────────────────────────

    def _parse_response(self, response_text: str) -> list[dict]:
        """Extrae la lista de acciones del JSON de respuesta."""
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.warning(f"  [IA] JSON inválido: {e}")
            logger.debug(f"  [IA] Respuesta raw: {response_text[:500]}")
            raise ValueError(f"Groq devolvió JSON inválido: {e}")

        # Extraer el array del plan
        if isinstance(data, dict):
            plan = data.get("plan", [])
        elif isinstance(data, list):
            plan = data
        else:
            raise ValueError(f"Formato inesperado de Groq: {type(data)}")

        if not isinstance(plan, list):
            raise ValueError(f"'plan' no es array: {type(plan)}")

        return plan

    def _validate_plan(
        self,
        raw_plan: list,
        content_groups: list[dict],
        existing_blocks: list[dict],
    ) -> list[dict]:
        """Valida y repara cada acción del plan."""
        valid = []
        existing_indices = {b["index"] for b in existing_blocks}

        for action in raw_plan:
            if not isinstance(action, dict):
                continue

            act = action.get("action", "")
            if act not in VALID_ACTIONS:
                logger.debug(f"  [IA] Acción inválida ignorada: {act}")
                continue

            bt = action.get("block_type", "text")
            if bt not in VALID_BLOCK_TYPES:
                logger.debug(
                    f"  [IA] block_type '{bt}' reparado a 'text'"
                )
                action["block_type"] = "text"

            # EDIT/KEEP/FLASHCARD: verificar target_index
            if act in ("EDIT", "KEEP", "FLASHCARD"):
                idx = action.get("target_index", -1)
                if idx not in existing_indices:
                    if act == "KEEP":
                        continue  # Drop invalid KEEP
                    # Demote EDIT/FLASHCARD to ADD
                    logger.debug(
                        f"  [IA] target_index {idx} no existe, "
                        f"demotando {act} a ADD"
                    )
                    action["action"] = "ADD"
                    action.pop("target_index", None)

            # Limpiar texts
            texts = action.get("texts", [])
            if isinstance(texts, list):
                action["texts"] = [
                    t for t in texts
                    if isinstance(t, str) and t.strip()
                ]
            else:
                action["texts"] = []

            # FLASHCARD: verificar cards
            if act == "FLASHCARD":
                cards = action.get("cards", [])
                action["cards"] = [
                    c for c in cards
                    if isinstance(c, dict)
                    and c.get("front", "").strip()
                    and c.get("back", "").strip()
                ]
                if not action["cards"]:
                    continue  # Skip empty flashcard

            # KEEP no necesita texts
            if act == "KEEP":
                action.pop("texts", None)

            valid.append(action)

        return valid

    def _ensure_content_completeness(
        self,
        plan: list[dict],
        content_groups: list[dict],
    ) -> list[dict]:
        """
        Safety net mejorado: verifica que TODO el contenido del PDF
        aparece en el plan. Usa múltiples estrategias de comparación
        con normalización para detectar textos partidos o reformateados.
        """
        # Recopilar todos los textos ya en el plan (normalizados)
        plan_texts_normalized = set()
        for action in plan:
            for t in action.get("texts", []):
                if t and len(t) >= 20:
                    normalized = re.sub(
                        r"[\s\.\,\;\:\-]+", " ", t.lower()
                    ).strip()
                    for i in range(0, min(len(normalized), 100), 20):
                        plan_texts_normalized.add(normalized[i:i + 20])
            for card in action.get("cards", []):
                back = card.get("back", "")
                if back and len(back) >= 20:
                    normalized = re.sub(
                        r"[\s\.\,\;\:\-]+", " ", back.lower()
                    ).strip()
                    plan_texts_normalized.add(normalized[:20])

        missing_count = 0
        for group in content_groups:
            text = group.get("text", "").strip()
            if not text or len(text) < 30:
                continue

            normalized = re.sub(
                r"[\s\.\,\;\:\-]+", " ", text.lower()
            ).strip()
            fragment = normalized[:20]

            if fragment not in plan_texts_normalized:
                logger.warning(
                    f"  [IA] Contenido faltante detectado — "
                    f"agregando: '{text[:50]}...'"
                )
                plan.append({
                    "action": "ADD",
                    "block_type": "heading",
                    "texts": [text],
                })
                missing_count += 1

        if missing_count > 0:
            logger.warning(
                f"  [IA] Safety net activado: {missing_count} fragmentos "
                f"agregados como heading"
            )

        return plan

    # ── Pre-clasificación semántica ─────────────────────────────────────

    def _pre_classify_content(
        self, content_groups: list[dict]
    ) -> list[dict]:
        """
        Analiza semánticamente cada fragmento ANTES de enviarlo a Groq.
        Añade campo 'suggested_block' para orientar la decisión de la IA.
        La IA puede ignorar la sugerencia si el contexto lo justifica.
        """
        classified = []

        for group in content_groups:
            text = group.get("text", "").strip()
            title = group.get("title", "").strip()
            suggestion = "heading"  # default: patrón humano dominante

            if not text:
                classified.append({**group, "suggested_block": "heading"})
                continue

            # Cita de autor → quote_carousel
            if re.search(
                r"(según|citado por|de acuerdo con|afirma|señala|plantea|indica)\s+\w+",
                text, re.IGNORECASE,
            ) or re.search(r"\(\w[\w\s]+,\s*\d{4}\)", text):
                suggestion = "quote_carousel"

            # Lista numerada → numbered_list
            elif re.search(
                r"^\s*\d+[\.\)]\s+\w", text, re.MULTILINE
            ) and text.count("\n") >= 2:
                suggestion = "numbered_list"

            # Lista con viñetas → bulleted_list
            elif re.search(
                r"^\s*[•\-\*→▪]\s+\w", text, re.MULTILINE
            ) and text.count("\n") >= 2:
                suggestion = "bulleted_list"

            # Definición corta (término: definición) → flashcard_candidate
            elif re.search(
                r"^[A-ZÁÉÍÓÚÑ][^:]{3,50}:\s+[A-ZÁÉÍÓÚÑ]", text
            ) and len(text) < 500 and text.count("\n") < 3:
                suggestion = "flashcard_candidate"

            # Frase corta impactante → statement
            elif (
                len(text) < 180
                and not title
                and not re.search(r"[•\-\*\d+\.]", text[:10])
            ):
                suggestion = "statement"

            # Tabla (contiene pipes o tabs con datos) → text_table
            elif re.search(r"\|.+\|", text) or text.count("\t") > 3:
                suggestion = "text_table"

            classified.append({**group, "suggested_block": suggestion})

        return classified

    # ── Utilidades ───────────────────────────────────────────────────────

    def _content_hash(self, content_groups: list[dict]) -> str:
        """Hash MD5 de content_groups para cache."""
        raw = json.dumps(content_groups, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode()).hexdigest()[:12]
