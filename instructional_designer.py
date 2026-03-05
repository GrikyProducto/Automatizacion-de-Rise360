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

SYSTEM_PROMPT = """Eres un diseñador instruccional senior y diseñador gráfico senior especializado en cursos Articulate Rise 360.

Tu trabajo: dado el contenido parseado de UNA lección del curso y los bloques existentes en la plantilla Rise, generar el plan ÓPTIMO de distribución de bloques. Tú decides qué tipo de bloque Rise presenta mejor cada pieza de contenido.

## REGLAS ABSOLUTAS (nunca romper):
1. EL TEXTO ES VERBATIM — NUNCA reescribas, resumas o parafrasees contenido. El campo "texts" en cada acción debe contener EXACTAMENTE el texto del input, carácter por carácter.
2. CADA pieza de contenido debe aparecer en algún lugar del plan — no se pierde nada. 0% pérdida.
3. El plan debe ser un JSON válido con la estructura: {"plan": [array de acciones]}
4. No incluyas explicaciones, solo el JSON.
5. TODO EL CONTENIDO DEBE ESTAR EN ESPAÑOL. NUNCA generes texto en inglés. Si el contenido del PDF está en español, el plan debe estar 100% en español. No traduzcas, no agregues texto en inglés, no mezcles idiomas. Los únicos campos en inglés son los nombres de acciones (EDIT, ADD, KEEP) y block_type (text, heading, etc.).

## CAPACIDAD CLAVE — CAMBIO DE TIPO VÍA LÁPIZ:
El sistema puede CAMBIAR el tipo de cualquier bloque existente usando el ícono de lápiz (config).
Esto es MUY RÁPIDO (~2 segundos) vs agregar un bloque nuevo (~15-30 segundos).

ESTRATEGIA OBLIGATORIA:
1. Toma los primeros N bloques editables existentes (text, heading, statement, quote, list, etc.)
2. Para cada pieza de contenido, CAMBIA el tipo del bloque existente al tipo ideal usando EDIT con block_type diferente al actual.
3. SOLO usa ADD cuando el contenido excede la cantidad de bloques editables existentes.

Ejemplo: si hay 10 bloques "text" y necesitas 3 headings + 5 texts + 2 statements:
→ EDIT block 0 con block_type="heading" (el lápiz cambiará text→heading)
→ EDIT block 1 con block_type="text"
→ EDIT block 2 con block_type="heading"
→ etc.
NO crear bloques nuevos si ya hay suficientes bloques editables existentes.

## GUÍA DE BLOQUES RISE 360:
- "text": Párrafos explicativos largos (>200 caracteres). El más versátil.
- "heading": Para títulos de subtemas y frases impactantes cortas (<100 chars). Crear estructura visual.
- "statement": Para principios clave, reglas, definiciones que merecen destacarse.
- "quote": Para frases memorables o citas textuales cortas (<150 chars). Máximo 1-2 por lección.
- "bulleted_list": Cuando el texto fuente YA tiene marcadores de viñeta (•, -, *, →).
- "numbered_list": Para pasos secuenciales o items enumerados.
- "accordion": 3-6 subtemas con estructura título+cuerpo.
- "tabs": 2-4 categorías paralelas para comparación.
- "flashcards": Pares concepto-definición. Usar acción FLASHCARD con front/back.

## REGLAS DE RITMO VISUAL:
- NUNCA colocar más de 2 bloques "text" consecutivos.
- Mezclar tipos de bloques para variedad visual.
- Bloques interactivos en el MEDIO de la lección, no al inicio.
- Total de bloques por lección: 8-15.

## PRIORIDAD DE ACCIONES (DE MÁS A MENOS RÁPIDA):
1. KEEP — bloques visuales de plantilla (image, banner, divider, spacer, continue, mondrian). NUNCA eliminar.
2. EDIT — reutilizar bloques existentes. Si el tipo actual no es el ideal, especifica el tipo deseado en block_type y el sistema lo cambiará automáticamente via lápiz. ESTA ES LA ACCIÓN MÁS EFICIENTE.
3. ADD — SOLO cuando NO hay suficientes bloques existentes. Cada ADD toma 15-30 segundos, así que minimízalos.
4. ADD_UX — instrucción UX (statement) ANTES de cada bloque interactivo.
5. FLASHCARD — para bloques de flashcards.

## TEXTOS UX (usar EXACTAMENTE estos strings):
- Antes de flashcards: "Da clic en cada tarjeta para ver su información al reverso"
- Antes de accordion: "Despliega cada sección para ver su contenido"
- Antes de tabs: "Selecciona cada pestaña para explorar el contenido"
- Antes de quote_carousel: "Navega por cada una de las frases destacadas"
- Antes de process: "Navega por cada paso del proceso"
- Antes de sorting: "Arrastra y ordena los elementos según corresponda"
- Antes de labeled: "Haz clic en cada punto para ver la información"

## ESQUEMA DE ACCIONES:

EDIT (reutilizar bloque existente — cambiar tipo si necesario):
{"action": "EDIT", "block_type": "<tipo_deseado>", "target_index": <int>, "texts": ["<texto verbatim>"]}
Nota: si block_type difiere del tipo actual del bloque, el sistema lo cambiará automáticamente.

ADD (insertar bloque nuevo — SOLO si no hay bloques reutilizables):
{"action": "ADD", "block_type": "<tipo>", "texts": ["<texto verbatim>"]}

ADD_UX (instrucción UX antes de interactivo):
{"action": "ADD_UX", "block_type": "statement", "texts": ["<texto UX exacto>"], "before_index": <int>}

FLASHCARD (poblar sidebar de flashcards):
{"action": "FLASHCARD", "target_index": <int>, "cards": [{"front": "<verbatim>", "back": "<verbatim>"}]}

KEEP (bloque visual de plantilla sin cambios):
{"action": "KEEP", "block_type": "<tipo>", "target_index": <int>}

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

        # Simplificar content_groups (mantener todo el texto verbatim)
        content_simplified = []
        for g in content_groups:
            item = {}
            title = g.get("title", "").strip()
            text = g.get("text", "").strip()
            if title:
                item["title"] = title
            if text:
                item["text"] = text
            if item:
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
        Safety net: verifica que TODO el contenido del PDF aparece en el plan.
        Si la IA omitió algo, lo agrega como ADD text al final.
        """
        # Recopilar todos los textos ya en el plan
        plan_text_prefixes = set()
        for action in plan:
            for t in action.get("texts", []):
                if t and len(t) >= 30:
                    plan_text_prefixes.add(t[:50])
            for card in action.get("cards", []):
                back = card.get("back", "")
                if back and len(back) >= 30:
                    plan_text_prefixes.add(back[:50])

        # Verificar cada content_group
        missing_count = 0
        for group in content_groups:
            text = group.get("text", "").strip()
            if not text or len(text) < 30:
                continue

            # Buscar si el texto (o su inicio) aparece en el plan
            prefix = text[:50]
            found = prefix in plan_text_prefixes

            if not found:
                # Buscar parcialmente (la IA pudo haber dividido el texto)
                found = any(
                    prefix[:25] in tp
                    for tp in plan_text_prefixes
                )

            if not found:
                logger.warning(
                    f"  [IA] Contenido no encontrado en plan — "
                    f"agregando: '{text[:40]}...'"
                )
                plan.append({
                    "action": "ADD",
                    "block_type": "text",
                    "texts": [text],
                })
                missing_count += 1

        if missing_count > 0:
            logger.warning(
                f"  [IA] Se agregaron {missing_count} fragmentos "
                f"faltantes como texto"
            )

        return plan

    # ── Utilidades ───────────────────────────────────────────────────────

    def _content_hash(self, content_groups: list[dict]) -> str:
        """Hash MD5 de content_groups para cache."""
        raw = json.dumps(content_groups, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode()).hexdigest()[:12]
