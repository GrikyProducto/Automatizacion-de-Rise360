# MEGA PROMPT — Rise 360 Course Automator desde Cero

## Objetivo
Crear una aplicación Python que automatice la creación de cursos en Articulate Rise 360 a partir de archivos PDF. La app debe simular la mente de un **diseñador instruccional senior** y un **diseñador gráfico senior**, analizando el PDF, estructurándolo pedagógicamente, y distribuyendo el contenido en bloques Rise 360 de forma inteligente.

---

## Stack Tecnológico
- **Python 3.13+** (Windows 11 x64)
- **Playwright** (Chromium) — automatización del navegador
- **PyMuPDF (fitz)** — extracción de texto del PDF
- **Groq API** (Llama 3.3-70B Versatile) — IA para diseño instruccional
- **Tkinter** — GUI simple con barra de progreso
- **OpenCV + Tesseract** (opcional) — análisis visual de referencia

---

## Arquitectura de Módulos (8 archivos)

### 1. `config.py` — Fuente única de verdad
```python
# Credenciales Rise 360
EMAIL = "info@griky.co"
PASSWORD = "GrikyRise2026!"

# URLs
RISE_BASE_URL = "https://rise.articulate.com"
RISE_DASHBOARD_URL = "https://rise.articulate.com/manage/all-content"
TEMPLATE_URL = "https://rise.articulate.com/authoring/TEMPLATE_ID"

# Groq API
GROQ_API_KEY = "gsk_..."
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_ENABLED = True
GROQ_MAX_TOKENS = 4096
GROQ_TEMPERATURE = 0.2
GROQ_TIMEOUT_SEC = 45

# Browser
BROWSER_HEADLESS = False
BROWSER_SLOW_MO = 30  # ms entre acciones
BROWSER_VIEWPORT = {"width": 1920, "height": 1080}

# Timeouts
DEFAULT_TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT = 60_000

# UX Instructions (statement blocks antes de interactivos)
UX_INSTRUCTIONS = {
    "flashcards": "Da clic en cada tarjeta para ver su información al reverso",
    "accordion": "Despliega cada sección para ver su contenido",
    "tabs": "Selecciona cada pestaña para explorar el contenido",
    "process": "Navega por cada paso del proceso",
    "sorting": "Arrastra y ordena los elementos según corresponda",
    "labeled": "Haz clic en cada punto para ver la información",
    "quote_carousel": "Navega por cada una de las frases destacadas",
}
```

### 2. `utils.py` — Utilidades compartidas
- **Logger** configurado con formato timestamp + nivel
- **`@with_retry(max_retries=3)`** — decorador para reintentos con delay exponencial
- **`take_screenshot(page, label)`** — captura pantalla con timestamp
- **`wait_for_react_idle(page)`** — espera a que React termine de renderizar (sin spinners, sin animaciones)
- **`safe_click(locator)`** — click con dismiss de overlays previo
- **`paste_large_text(page, text)`** — pega texto vía clipboard (más rápido que .type())
- **`ocr_image(path)`** — Tesseract OCR opcional
- **`configure_tesseract()`** — detecta path de Tesseract en Windows

### 3. `pdf_parser.py` — Extracción inteligente de PDF
**Input**: archivo PDF
**Output**: JSON estructurado con secciones, bloques y metadatos

```python
{
    "title": "Nombre del curso",
    "sections": [
        {
            "title": "Sección",
            "blocks": [
                {
                    "text": "texto verbatim del PDF",
                    "block_type": "parrafo|h1|h2|h3|titulo",
                    "metadata": {
                        "font": "Helvetica-Bold",
                        "font_size": 14.0,
                        "page": 3
                    }
                }
            ]
        }
    ]
}
```

**Reglas críticas del parser**:
- Texto SIEMPRE verbatim — NUNCA modificar
- Clasificación por font_size: ≥24pt=titulo, ≥18pt=h1, ≥14pt=h2, ≥11pt+bold=h3
- Detectar tablas de contenido (patrones de puntos `....N`, números standalone) y OMITIRLAS
- Merge de bloques fragmentados: "1.1." + "Título" → "1.1. Título"
- Detectar subtemas N.N. (ej: 1.1, 2.3) como grupos dentro de un tema, NO como temas separados

### 4. `rise_automation.py` — Motor Playwright (EL MÁS IMPORTANTE)

**Clase `RiseAutomation`** — administra toda la interacción con Rise 360.

#### Selectores DOM Confirmados (Rise 360, marzo 2026):
```
Dashboard:
  - Search: input[placeholder='Search all content']
  - Cards: a[href*='/authoring/']
  - Card menu: button[aria-label='Content menu button']
  - Menu items: [role='menuitem']
  - Duplicate modal: [role='dialog'] input[type='text'] + button Duplicate/Cancel
  - Move dialog: [role='tree'], button "Move"

Course Outline:
  - Course title: h1 → click → textarea (fill, NOT type)
  - Lesson containers: div.course-outline-lesson
  - Section headers: div.course-outline-lesson--section
  - Edit Content links: a:has-text('Edit Content') (NO <button>)
  - Lesson title: .course-outline-lesson__title-entry (DIV, click to edit)
  - Kebab menu: button.menu__trigger--dots (HOVER to show)
  - Kebab items: [role='menuitem'] → Duplicate, Delete, etc.
  - Insert lesson: button[aria-label='Insert new lesson']

Lesson Editor:
  - Block wrappers: div[class*='block-wrapper']
  - Block types from CSS: block-text, block-statement, block-flashcards, etc.
  - Text editors: .tiptap.ProseMirror.rise-tiptap[contenteditable='true'] (TipTap, NOT Quill)
  - Add block (+): button.block-create__button (between wrappers)
  - Block Library: button.block-wizard__link (categories: Text, Statement, Quote, List, Interactive...)
  - LÁPIZ/Config: .block-controls__config (CAMBIAR TIPO de bloque existente)
  - Sidebar overlay: .blocks-sidebar__overlay--active (click to dismiss)
  - Flashcard sidebar: .blocks-sidebar.blocks-sidebar--open

Cookie popup: button.osano-cm-accept-all
Loading spinner: text='Your content is loading' (esperar hasta 90s)
```

#### Métodos clave:

```python
class RiseAutomation:
    # Lifecycle
    def start()          # Launch Playwright Chromium
    def stop()           # Close browser

    # Auth
    def login()          # Email + password login
    def dismiss_cookies()

    # Course management
    def duplicate_template(template_url, new_title) -> str  # Returns new course URL
    def set_course_title(title)
    def get_lessons_in_outline() -> list[dict]
    def ensure_lesson_count(target) -> bool  # Duplicate lessons if needed
    def rename_lesson(index, name) -> bool   # Click title → type → Enter
    def open_lesson_editor(index) -> bool
    def go_back_to_outline()

    # Block operations (DENTRO del editor de lección)
    def count_editables_in_lesson() -> list[dict]   # Pre-scan blocks
    def change_block_type(block_index, new_type) -> bool  # ★ LÁPIZ — MUY RÁPIDO (~2s)
    def add_block_at_position(after_index, block_type) -> bool  # Click + → category → sub-type
    def edit_flashcard_sidebar(block_index, cards) -> int  # Edit via sidebar panel

    # Internal
    def _catalog_blocks_in_editor() -> list[dict]  # JS bulk scan of block-wrappers
    def _extract_block_type_from_class(css_class) -> str
    def _select_block_from_library(category, sub_type) -> bool
    def _get_block_category_and_type(block_type) -> tuple  # Maps: "heading" → ("Text", "Heading")
    def _get_block_type_label(block_type) -> str  # Maps: "bulleted_list" → "Bulleted list"
    def dismiss_sidebar_overlay()
    def save_course()  # Ctrl+S
```

#### Lecciones aprendidas (CRÍTICAS):
1. **Rise usa TipTap/ProseMirror, NO Quill** — selector: `.tiptap.ProseMirror.rise-tiptap[contenteditable='true']`
2. **"Edit Content" es `<a>`, NO `<button>`** — siempre usar `a:has-text('Edit Content')`
3. **El lápiz (.block-controls__config) es 10x más rápido que agregar bloques nuevos** — SIEMPRE preferir cambiar tipo de bloque existente
4. **Duplicar lecciones, NO crear vacías** — `duplicate_lesson()` vía kebab preserva estilo/colores
5. **Flashcards se editan vía sidebar**, no contenteditable en el editor principal
6. **El botón "+" cerca del Continue NO funciona** — insertar en zona segura (mitad de la lección)
7. **Tab en textarea navega fuera** — usar Enter o mouse.click para blur
8. **SVG className es SVGAnimatedString** — usar `String(el.className || '')` en JavaScript
9. **Overlay `.blocks-sidebar__overlay--active`** bloquea clicks — dismiss antes de operar
10. **Después de CADA operación de navegación**, verificar que seguimos en el outline correcto guardando la URL del curso
11. **Minimizar `time.sleep()`** — usar `wait_for()` de Playwright en vez de sleeps estáticos
12. **paste_large_text vía clipboard** es mucho más rápido que `page.keyboard.type()`

### 5. `content_builder.py` — Orquestador de contenido

**Clase `ContentLayoutPlanner`** — planner por reglas (fallback si Groq no está disponible)
**Clase `ContentBuilder`** — orquesta todo el flujo

#### Flujo principal de `build_course()`:
```
1. Guardar URL del curso duplicado (self._course_url)
2. Extraer título del PDF → set_course_title
3. _ensure_on_outline() (verificar que estamos en el outline correcto)
4. _extract_topics(content_json) → lista de temas con content_groups
5. Escalar lecciones si necesario (ensure_lesson_count)
6. PASS 1: Renombrar TODAS las lecciones ("Tema N: Nombre")
7. _ensure_on_outline()
8. PASS 2: Para cada lección → _execute_lesson_plan()
9. save_course()
```

#### `_execute_lesson_plan()` — EL MÉTODO MÁS IMPORTANTE:
```
1. open_lesson_editor(lesson_idx)
2. count_editables_in_lesson() → pre-scan bloques existentes
3. Generar plan con IA (Groq) o fallback (reglas)
4. Scroll to top
5. EJECUTAR plan linealmente, acción por acción:
   - KEEP: skip (bloque visual de plantilla)
   - EDIT:
     a. Si tipo actual ≠ tipo deseado → change_block_type() vía LÁPIZ (~2s)
     b. Llenar editables con paste_large_text()
   - ADD: add_block_at_position() + llenar editables
   - ADD_UX: agregar statement con instrucción UX
   - FLASHCARD: edit_flashcard_sidebar()
6. go_back_to_outline()
```

#### `_ensure_on_outline()`:
Verificar que estamos en el outline del curso correcto. Si no:
→ Navegar a `self._course_url` directamente (NUNCA go_back que puede ir a otro curso)

#### `_extract_topics()` — Detección genérica de temas:
- **Estrategia 1**: Detectar límites H2 por font-size ≥14pt
- **Estrategia 2**: Detectar límites implícitos por patrón `N. Título` en bold
- Subtemas N.N. se agrupan dentro de su tema padre
- Secciones especiales: Introducción, Conclusiones, Referencias
- Tabla de contenido: detectar y omitir

### 6. `instructional_designer.py` — IA Groq (Llama 3.3-70B)

**Clase `InstructionalDesigner`** — reemplaza ContentLayoutPlanner con decisiones IA

#### System Prompt (resumen de la lógica):
- Rol 1: Diseñador instruccional — clasifica contenido pedagógicamente
- Rol 2: Diseñador gráfico — variedad visual, ritmo de aprendizaje
- TEXTO VERBATIM — nunca reescribir
- TODO EN ESPAÑOL — nunca generar texto en inglés
- 0% pérdida de contenido
- **ESTRATEGIA LÁPIZ**: reutilizar bloques existentes cambiando su tipo (EDIT con block_type diferente) ANTES de agregar nuevos (ADD)
- Max 2 text blocks consecutivos
- Bloques interactivos en el medio, no al inicio
- 8-15 bloques por lección

#### Formato de respuesta:
```json
{"plan": [
  {"action": "KEEP", "block_type": "banner", "target_index": 0},
  {"action": "EDIT", "block_type": "heading", "target_index": 1, "texts": ["1.1 Título"]},
  {"action": "EDIT", "block_type": "text", "target_index": 2, "texts": ["Párrafo largo..."]},
  {"action": "EDIT", "block_type": "statement", "target_index": 3, "texts": ["Concepto clave..."]},
  {"action": "ADD", "block_type": "text", "texts": ["Contenido extra..."]},
  {"action": "ADD_UX", "block_type": "statement", "texts": ["Da clic en cada tarjeta..."]},
  {"action": "FLASHCARD", "target_index": 5, "cards": [{"front": "SCM", "back": "Definición..."}]}
]}
```

#### Validación robusta:
1. `json.loads()` → extraer `["plan"]`
2. Verificar action ∈ {EDIT, ADD, ADD_UX, FLASHCARD, KEEP}
3. block_type inválido → reparar a "text"
4. EDIT/KEEP con target_index inexistente → demotar a ADD
5. FLASHCARD sin cards válidas → skip
6. **Safety net**: recorrer content_groups, verificar cada texto aparece en el plan. Si falta → append como ADD text

#### Cache + Fallback:
- Cache en memoria por hash MD5 de content_groups
- Si Groq falla (cualquier excepción) → ContentLayoutPlanner (reglas)
- Costo: ~$0.02 USD por curso (6 llamadas de ~3000 tokens)

### 7. `main.py` — GUI Tkinter + auto-install
- Verificación e instalación automática de dependencias (playwright, pymupdf, groq, opencv-python, pillow, pytesseract)
- Selector de PDF vía filedialog
- Barra de progreso + etiqueta de estado
- Ejecución en thread separado para no bloquear GUI
- Botón Iniciar/Cancelar

### 8. `visual_learner.py` (opcional) — Análisis visual
- OpenCV template matching para identificar tipos de bloques
- Tesseract OCR como fallback
- Se usa como complemento, no como método principal

---

## Flujo Completo de la Aplicación

```
[Usuario selecciona PDF] → [GUI]
    ↓
[pdf_parser] → JSON con secciones/bloques/metadatos
    ↓
[rise_automation] → Login → Duplicar template → Mover a carpeta
    ↓
[content_builder.build_course()]
    ├── Extraer título del PDF → set_course_title
    ├── _extract_topics() → 6-8 temas con content_groups
    ├── ensure_lesson_count() → duplicar lecciones si faltan
    ├── rename_lesson() → "Tema 1: Nombre", "Tema 2: Nombre"...
    └── Para cada lección:
        ├── open_lesson_editor()
        ├── count_editables_in_lesson()
        ├── [GROQ API] → plan IA con acciones EDIT/ADD/KEEP
        ├── Ejecutar plan:
        │   ├── EDIT: change_block_type vía LÁPIZ → paste contenido
        │   ├── ADD: click + → category → sub-type → paste contenido
        │   └── FLASHCARD: edit via sidebar
        └── go_back_to_outline()
    ↓
[save_course] → Ctrl+S
    ↓
[✓ Curso completado]
```

---

## Errores Conocidos y Soluciones

| Error | Causa | Solución |
|-------|-------|----------|
| "Edit Content": 0 | Página no está en outline | `_ensure_on_outline()` con URL guardada |
| className.includes is not a function | SVG elements | `String(el.className \|\| '')` |
| Bloques no se agregan cerca de Continue | Zona muerta al final | Insertar en zona media segura |
| Tab navega fuera del outline | Tab en textarea | Usar Enter o mouse.click para blur |
| Script entra a otro curso | go_back_to_outline inespecífico | Navegar a `self._course_url` directamente |
| Contenido en inglés | IA genera texto en inglés | Regla #5 en system prompt: TODO en español |
| Título = "COURSE_TITLE" | Marcador del PDF capturado | Skip METADATA_MARKERS set |
| Rename falla para lección N>0 | Outline desaparece después de rename 0 | `_ensure_on_outline()` después de cada rename |
| 1+ hora por curso | Demasiados ADD, sleeps excesivos | Lápiz para cambiar tipos + reducir sleeps |

---

## Principios de Diseño

1. **Texto VERBATIM** — El script NUNCA modifica el contenido del PDF
2. **Lápiz primero** — Cambiar tipo de bloque existente (~2s) antes de agregar nuevo (~30s)
3. **URL como ancla** — Guardar y usar `self._course_url` para toda navegación
4. **Fallback en cascada** — Groq → ContentLayoutPlanner → skip
5. **Safety net** — Verificar 0% pérdida de contenido post-plan
6. **Duplicar, no crear** — Lecciones y bloques duplicados preservan estilo
7. **Flujo lineal** — Ejecutar plan acción por acción, no en fases separadas
8. **Cada H2 = 1 lección** — Nunca mezclar temas en una lección
9. **Overlay-aware** — Dismiss `.blocks-sidebar__overlay--active` antes de operar
10. **Verificar estado** — Después de cada operación de navegación, confirmar ubicación
