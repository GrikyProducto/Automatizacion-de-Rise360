# Rise 360 Automator

Herramienta de automatización para crear cursos completos en **Articulate Rise 360** a partir de un PDF de Desarrollo Teórico. El sistema analiza visualmente un curso de referencia, duplica una plantilla y transfiere el contenido del PDF bloque a bloque, sin modificar ni alterar ningún texto.

---

## Qué hace

1. El usuario proporciona un **PDF con el Desarrollo Teórico** y el **link de una plantilla Rise 360**
2. El script inicia sesión en Rise 360, analiza visualmente el curso de referencia para aprender la estructura de diseño
3. Duplica la plantilla y la renombra con el título del curso
4. Extrae todo el contenido del PDF respetando la jerarquía (introducción, temas, subtemas, tablas, listas, conclusión, referencias)
5. Inserta ese contenido **exactamente como aparece en el PDF** en los bloques correspondientes de Rise 360
6. Aprende de las correcciones del usuario para mejorar en ejecuciones futuras

---

## Requisitos del sistema

- Windows 10 / 11 (64-bit)
- Python 3.11 o superior — [descargar aquí](https://python.org)
- Conexión a internet

Las demás dependencias (Chromium, Tesseract OCR, librerías Python) se instalan automáticamente.

---

## Instalación

### Opción A — Instalación automática (recomendada)

Ejecuta el instalador incluido:

```
install.bat
```

Este script instala en orden:
1. Todas las librerías Python (`pip install -r requirements.txt`)
2. Chromium para Playwright (`playwright install chromium`)
3. Tesseract OCR via `winget` (necesario para el análisis visual)

### Opción B — Instalación manual

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Para Tesseract OCR, descargarlo e instalarlo desde:
https://github.com/UB-Mannheim/tesseract/wiki

---

## Uso

### Iniciar la aplicación

```bash
python main.py
```

Se abre la ventana principal:

```
┌─────────────────────────────────────────────────────┐
│         Rise 360 Automator — by Griky               │
├─────────────────────────────────────────────────────┤
│ PDF (Desarrollo Teórico):  [___________] [Buscar...]│
│ Link Plantilla Rise 360:   [___________]            │
│ ─────────────────────────────────────────────────── │
│ Estado: Esperando...                                │
│ [████████░░░░░░░░░░░░░░░░░░] 30%                    │
│ Log:                                                │
│ ┌─────────────────────────────────────────────────┐ │
│ │ [10:23:01] PDF cargado correctamente            │ │
│ │ [10:23:05] Login exitoso en Rise 360            │ │
│ └─────────────────────────────────────────────────┘ │
│                  [INICIAR PROCESO]                  │
└─────────────────────────────────────────────────────┘
```

### Pasos

1. **Seleccionar el PDF** — Click en "Buscar..." y elige el archivo PDF con el Desarrollo Teórico
2. **Ingresar la URL de la plantilla** — Pegar el link de la plantilla en Rise 360
3. **Click en "INICIAR PROCESO"** — El sistema toma el control de forma autónoma

El navegador se abrirá en modo visible para que puedas supervisar el proceso en tiempo real.

---

## Estructura del PDF de entrada

El sistema reconoce automáticamente la siguiente estructura académica estándar:

```
Título del curso
├── Introducción
├── Tema 1
│   ├── 1.1 Subtema
│   ├── 1.2 Subtema
│   └── ...
├── Tema 2
│   └── ...
├── Tema 3
│   └── ...
├── Tema 4
│   └── ...
├── Conclusión
└── Referencias
```

La clasificación se hace por tamaño de fuente y palabras clave:

| Tamaño de fuente | Tipo detectado |
|---|---|
| ≥ 24 pt | Título del curso |
| ≥ 18 pt | Tema principal (H1) |
| ≥ 14 pt | Subtema (H2) |
| ≥ 11 pt + negrita | Sub-subtema (H3) |
| ≤ 10 pt | Párrafo normal |
| Empieza con `•`, `-`, `*` | Lista con viñetas |
| Contiene múltiples `\|` o tabulaciones | Tabla |
| Palabra clave: "Introducción" | Sección introducción |
| Palabra clave: "Conclusión" | Sección conclusión |
| Palabra clave: "Referencias" | Sección referencias |

> **Regla fundamental:** El texto del PDF se inserta **íntegramente y sin modificaciones** en Rise 360. El sistema no resume, no parafrasea ni altera el contenido original.

---

## Mapeo de contenido a bloques Rise 360

| Tipo en PDF | Bloque en Rise 360 |
|---|---|
| Título del curso | Banner principal (hero) |
| Introducción | Text block |
| Tema (H1) | Section Banner / Divider |
| Subtema (H2) | Subheading dentro de Text block |
| Párrafo | Text block |
| Lista con viñetas | Bulleted List block |
| Lista numerada | Numbered List block |
| Tabla | Table block |
| Conclusión | Text block |
| Referencias | Text block final |

Este mapeo está definido en [`data/learning_map.json`](data/learning_map.json) y se actualiza automáticamente con el uso.

---

## Estructura del proyecto

```
ScriptUSS-Articulate/
│
├── main.py                  # Entry point — GUI Tkinter + auto-install + orquestación
├── config.py                # Credenciales, URLs, timeouts, rutas (fuente única de verdad)
├── pdf_parser.py            # Extracción y clasificación semántica del PDF con PyMuPDF
├── visual_learner.py        # Análisis visual con OpenCV + Tesseract OCR
├── rise_automation.py       # Motor Playwright — login, duplicar curso, insertar bloques
├── content_builder.py       # Inserción de contenido bloque a bloque en Rise 360
├── self_learning.py         # Monitoreo de correcciones + actualización del mapa
├── utils.py                 # Logger, @with_retry, screenshots, OCR helpers, waits
│
├── data/
│   └── learning_map.json    # Mapa de diseño (se actualiza con el uso)
│
├── assets/                  # Íconos y recursos estáticos
├── logs/                    # Logs de cada ejecución (generados en runtime)
├── screenshots/             # Capturas de diagnóstico (generadas en runtime)
│
├── requirements.txt         # Dependencias Python
├── install.bat              # Instalador automático Windows
├── build.bat                # Generador del ejecutable .exe
└── rise_automator.spec      # Configuración de PyInstaller
```

---

## Módulos en detalle

### `main.py` — Punto de entrada

- Detecta e instala automáticamente las dependencias faltantes al arrancar
- Instala Tesseract OCR via `winget` si no está presente en el sistema
- Construye la interfaz gráfica con Tkinter
- Lanza la automatización en un hilo separado para mantener la UI responsiva
- Actualiza el progreso y el log en tiempo real usando una cola thread-safe

### `config.py` — Configuración global

Contiene todas las constantes del sistema en un solo lugar:
- Credenciales de Rise 360
- URLs (dashboard, plantilla, curso de referencia)
- Timeouts y número de reintentos
- Parámetros del navegador (modo visible, velocidad)
- Umbrales de clasificación de fuente para el PDF

### `pdf_parser.py` — Extracción del PDF

Usa PyMuPDF (`fitz`) para extraer el contenido con metadatos de fuente:
1. Extrae cada span de texto con su tamaño de fuente, flags (negrita, cursiva) y página
2. Consolida spans consecutivos del mismo párrafo
3. Clasifica cada bloque por tipo semántico (keywords primero, luego tamaño de fuente)
4. Ensambla la jerarquía en un JSON estructurado
5. Cachea el resultado para reutilización si se cambia solo la plantilla

### `visual_learner.py` — Análisis visual

Usa OpenCV y Tesseract OCR para identificar bloques de Rise 360 mediante imágenes:
- Toma screenshots de la página
- Detecta regiones de bloques buscando separadores horizontales (Canny + HoughLinesP)
- Identifica el tipo de bloque por template matching y análisis de color
- Guarda imágenes de referencia en `screenshots/ref_*.png`
- Se usa como fallback cuando los selectores DOM no encuentran el elemento

### `rise_automation.py` — Motor Playwright

Controla el navegador Chromium contra Rise 360 (SPA React con Quill.js):

**Estrategia de selectores DOM (3 capas):**
1. CSS attributes y data-attributes (más confiables: `.ql-editor`, `[data-block-type]`)
2. Role selectors de Playwright (`get_by_role("menuitem", name="Duplicate")`)
3. Fallback visual por coordenadas OCR (último recurso)

**Funciones principales:**
- `login()` — Autenticación OAuth vía Articulate ID
- `duplicate_template()` — Hover → menú "..." → Duplicate → Rename
- `add_block()` — Click en "+" → selecciona tipo → espera renderizado
- `insert_text()` — Click en `.ql-editor` → Ctrl+A → type verbatim
- `insert_heading()` — Aplica H1/H2/H3 vía toolbar de Quill
- `save_course()` — Ctrl+S + verificación de indicador de guardado

### `content_builder.py` — Inserción de contenido

Orquesta la inserción bloque a bloque:
- Itera sobre las secciones del JSON del PDF
- Por cada bloque llama al handler correspondiente según su tipo
- Aplica `@with_retry(3)` en cada inserción
- Si un bloque falla tras 3 intentos: toma screenshot, loguea el error y **continúa** con el siguiente (no detiene el proceso)
- Genera un reporte final con conteo de bloques exitosos y fallidos

### `self_learning.py` — Aprendizaje continuo

Aprende del comportamiento del usuario para mejorar con el tiempo:
- Instala listeners de red en Playwright para detectar llamadas a la API de Rise 360
- Registra todas las acciones del script
- Compara el timing: si el usuario hace una acción API más de 2 segundos después que el script en el mismo contexto, se asume que fue una corrección
- Actualiza `data/learning_map.json` con el nuevo patrón aprendido
- Exporta un log completo de la sesión al terminar

### `utils.py` — Infraestructura compartida

- **Logger**: FileHandler (DEBUG, cada sesión tiene su propio archivo en `logs/`) + StreamHandler (INFO)
- **`@with_retry`**: Decorator configurable — reintenta N veces con delay, loguea cada fallo
- **`take_screenshot`**: Guarda en `screenshots/{label}_{timestamp}.png`
- **`paste_large_text`**: Para textos ≤300 chars usa `keyboard.type`; para textos largos usa clipboard (`pyperclip + Ctrl+V`) por eficiencia
- **`wait_for_react_idle`**: `wait_for_load_state("networkidle")` con captura silenciosa del timeout (normal en SPAs con websocket)
- **`find_tesseract`**: Busca en PATH → Registro de Windows → rutas conocidas de instalación

---

## Generar el ejecutable `.exe`

```bash
build.bat
```

Genera `dist/rise_automator.exe` — un ejecutable autónomo de Windows que incluye todos los módulos, el `data/learning_map.json` inicial y los assets.

> **Nota sobre Playwright en el .exe:** Los binarios del navegador Chromium no se incluyen dentro del `.exe` (son muy grandes, ~200MB). El ejecutable los instala automáticamente en la primera ejecución si no están presentes.

---

## Archivos generados en runtime

| Carpeta/Archivo | Contenido |
|---|---|
| `logs/rise_YYYYMMDD_HHMMSS.log` | Log detallado de cada ejecución (nivel DEBUG) |
| `screenshots/*.png` | Capturas de diagnóstico y de bloques fallidos |
| `data/content_cache.json` | Caché del PDF para reutilizar en sesiones siguientes |
| `data/learning_map.json` | Mapa de diseño actualizado con correcciones aprendidas |
| `logs/session_log_*.json` | Historial completo de acciones script vs. usuario |

---

## Casos de uso soportados

**Caso 1 — PDF nuevo + Plantilla nueva**
El sistema analiza visualmente la plantilla, actualiza el mapa de diseño y monta el contenido del PDF en la estructura de la nueva plantilla.

**Caso 2 — Mismo PDF + Plantilla diferente**
El sistema detecta el caché del PDF ya procesado y lo reutiliza. Solo re-ejecuta el análisis visual de la nueva plantilla y remonta el mismo contenido con el nuevo diseño.

---

## Tecnologías utilizadas

| Librería | Versión | Uso |
|---|---|---|
| `playwright` | 1.58.0 | Automatización del navegador (React SPA) |
| `PyMuPDF` (fitz) | 1.27.1 | Extracción de texto del PDF con metadatos de fuente |
| `opencv-python` | ≥ 4.9 | Detección de regiones de bloques por visión computacional |
| `pytesseract` | ≥ 0.3.13 | OCR para leer texto en screenshots |
| `Pillow` | ≥ 10.3 | Procesamiento de imágenes |
| `tkinter` | (stdlib) | Interfaz gráfica (incluido en Python) |
| `pyinstaller` | ≥ 6.0 | Empaquetado en ejecutable .exe |
| `pyperclip` | ≥ 1.9 | Portapapeles para inserción eficiente de texto largo |

---

## Consideraciones técnicas

**Por qué Playwright y no Selenium:**
Rise 360 es una Single Page Application en React. Playwright tiene esperas automáticas integradas (`auto-wait`) que funcionan mejor con el ciclo de renderizado de React, y su API de selectores incluye `get_by_role` y `get_by_text` que son más semánticos y resistentes a cambios de estructura DOM.

**Por qué el navegador es visible (no headless):**
El modo visible permite que el usuario supervise el proceso en tiempo real, intervenga si algo falla, y que el módulo de aprendizaje detecte correcciones manuales comparando las acciones del script con las del usuario.

**Mitigación de detección de bot:**
Se configuran `slow_mo=50ms` entre acciones, user-agent real del navegador, viewport de resolución estándar (1920×1080) y se desactiva la flag `AutomationControlled` que algunos sitios leen para detectar Selenium/Playwright.

**Inserción de texto en Quill.js:**
Rise 360 usa el editor de texto enriquecido Quill.js. Los bloques de texto no son `<input>` ni `<textarea>` estándar, sino `<div contenteditable="true" class="ql-editor">`. Para insertar texto se hace click en el editor → `Ctrl+A` para seleccionar todo → `keyboard.type()` para escribir. No se usa `fill()` de Playwright porque Quill gestiona su propio estado React y `fill()` puede romperlo.

---

## Solución de problemas

**El login falla**
- Verifica que las credenciales en `config.py` sean correctas
- Rise 360 puede pedir verificación adicional en logins nuevos — supervisa el browser

**El browser no abre**
- Ejecuta `python -m playwright install chromium` para reinstalar Chromium
- Verifica que Playwright esté instalado: `pip show playwright`

**OCR no funciona (el análisis visual es parcial)**
- Instala Tesseract OCR manualmente: https://github.com/UB-Mannheim/tesseract/wiki
- El sistema funciona sin Tesseract pero con menor precisión en el fallback visual

**Un bloque no se insertó correctamente**
- Revisa `screenshots/` — hay capturas del momento del fallo
- Revisa `logs/rise_*.log` para el stack trace completo
- El proceso continúa automáticamente tras cada fallo (no se detiene)

**El ejecutable .exe no arranca**
- Asegúrate de que `dist/rise_automator.exe` se ejecuta desde la misma carpeta que contiene `data/`
- En la primera ejecución puede tardar en instalar Chromium

---

## Créditos

Desarrollado para **Griky** — 2026
Stack: Python · Playwright · PyMuPDF · OpenCV · Tkinter
