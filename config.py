"""
config.py — Fuente única de verdad para Rise 360 Automator
Credenciales, URLs, rutas, timeouts y configuración global.
"""

import os
from pathlib import Path

# ── Credenciales Rise 360 ─────────────────────────────────────────────────
EMAIL = "info@griky.co"
PASSWORD = "GrikyRise2026!"

# ── URLs ──────────────────────────────────────────────────────────────────
RISE_BASE_URL = "https://rise.articulate.com"
RISE_DASHBOARD_URL = "https://rise.articulate.com/manage/all-content"
TEMPLATE_URL = "https://rise.articulate.com/authoring/3mktt-_LKSTVtVKb8QC-i3D0EHSD-n-v"
REFERENCE_COURSE_TITLE = "Gestión de la Cadena de Suministro en Entornos Competitivos"

# ── IA / Groq ──────────────────────────────────────────────────────────────
GROQ_API_KEY = "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"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_ENABLED = True
GROQ_MAX_TOKENS = 4096
GROQ_TEMPERATURE = 0.2
GROQ_TIMEOUT_SEC = 45

# ── Rutas del proyecto ─────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
ASSETS_DIR = BASE_DIR / "assets"
LEARNING_MAP_PATH = DATA_DIR / "learning_map.json"
CONTENT_CACHE_PATH = DATA_DIR / "content_cache.json"

# Crear directorios si no existen
for _dir in [DATA_DIR, LOGS_DIR, SCREENSHOTS_DIR, ASSETS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# ── Timeouts (ms) ─────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT = 60_000
ELEMENT_WAIT_MS = 10_000
MAX_RETRIES = 3
RETRY_DELAY_MS = 1_500

# ── Configuración del navegador ───────────────────────────────────────────
BROWSER_HEADLESS = False           # Visible para supervisión del usuario
BROWSER_SLOW_MO = 30               # ms entre acciones — reducido para velocidad
BROWSER_VIEWPORT = {"width": 1920, "height": 1080}
BROWSER_LOCALE = "es-419"          # Español latinoamericano
BROWSER_TIMEZONE = "America/Bogota"
# Desactivar flags de automatización para evitar detección bot
BROWSER_ARGS = [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
]

# ── OCR (Tesseract) ───────────────────────────────────────────────────────
OCR_LANG = "spa"
OCR_CONFIG = "--psm 6 --oem 3"
TESSERACT_DEFAULT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_ALT_PATHS = [
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\{username}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
]

# ── Umbral de confianza para matching visual ──────────────────────────────
VISUAL_MATCH_THRESHOLD = 0.70      # cv2.matchTemplate score mínimo

# ── Tamaño de fuente para clasificar bloques PDF ──────────────────────────
FONT_SIZE_TITULO = 24              # >= 24pt → título del documento
FONT_SIZE_H1 = 18                  # >= 18pt → Tema 1, Tema 2...
FONT_SIZE_H2 = 14                  # >= 14pt → Subtemas
FONT_SIZE_H3 = 11                  # >= 11pt + bold → subsección menor
# < FONT_SIZE_H3 → párrafo normal

# ── Instrucciones UX para bloques interactivos ──────────────────────────
# Se insertan ANTES de cada bloque interactivo como statement block
UX_INSTRUCTIONS = {
    "flashcards": "Da clic en cada tarjeta para ver su información al reverso",
    "accordion": "Despliega cada sección para ver su contenido",
    "sorting": "Arrastra y ordena los elementos según corresponda",
    "process": "Navega por cada paso del proceso",
    "embed": "Revisa la siguiente cápsula interactiva",
    "labeled": "Haz clic en cada punto para ver la información",
    "quote_carousel": "Navega por cada una de las frases destacadas",
    "tabs": "Selecciona cada pestaña para explorar el contenido",
}

# ── Texto de progreso UI ──────────────────────────────────────────────────
PROGRESS_STEPS = {
    "init":           (0,  "Iniciando sistema..."),
    "pdf_parse":      (10, "Analizando PDF..."),
    "pdf_done":       (20, "PDF analizado correctamente"),
    "browser_start":  (22, "Iniciando navegador..."),
    "login":          (25, "Iniciando sesión en Rise 360..."),
    "login_done":     (35, "Sesión iniciada"),
    "visual_learn":   (38, "Analizando curso de referencia..."),
    "visual_done":    (45, "Análisis visual completado"),
    "duplicate":      (48, "Duplicando plantilla..."),
    "duplicate_done": (55, "Plantilla duplicada"),
    "build_start":    (58, "Insertando contenido..."),
    "build_done":     (95, "Contenido insertado"),
    "save":           (97, "Guardando curso..."),
    "complete":       (100, "¡Curso completado exitosamente!"),
}
