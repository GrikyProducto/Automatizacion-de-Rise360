"""
utils.py — Infraestructura compartida para Rise 360 Automator
Logger, retry decorator, screenshots, OCR helpers, smart waits.
"""

import logging
import time
import functools
import shutil
import os
import winreg
from pathlib import Path
from datetime import datetime
from typing import Callable, Any, Optional

import config


# ── Logger setup ──────────────────────────────────────────────────────────

def setup_logger(name: str = "rise_automator") -> logging.Logger:
    """
    Crea un logger con:
    - FileHandler → logs/rise_YYYYMMDD_HHMMSS.log (DEBUG)
    - StreamHandler → consola (INFO)
    """
    log_file = config.LOGS_DIR / f"rise_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger  # Evitar handlers duplicados si se re-importa

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


logger = setup_logger()


# ── Retry decorator ───────────────────────────────────────────────────────

def with_retry(
    max_attempts: int = config.MAX_RETRIES,
    delay_ms: int = config.RETRY_DELAY_MS,
    exceptions: tuple = (Exception,),
    log_name: str = "",
):
    """
    Decorator que reintenta la función hasta max_attempts veces.
    Loguea cada fallo. Re-lanza la última excepción al agotar intentos.

    Uso:
        @with_retry(max_attempts=3)
        def click_login_button(page):
            page.locator("#login-btn").click()
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            fn_name = log_name or func.__name__
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    logger.warning(
                        f"[Reintento {attempt}/{max_attempts}] {fn_name} falló: {e}"
                    )
                    if attempt < max_attempts:
                        time.sleep(delay_ms / 1000)
            logger.error(f"{fn_name} agotó {max_attempts} reintentos. Último error: {last_exc}")
            raise last_exc
        return wrapper
    return decorator


# ── Screenshots ───────────────────────────────────────────────────────────

def take_screenshot(page, label: str = "screen") -> Path:
    """
    Captura screenshot de la página actual.
    Guarda en screenshots/{label}_{timestamp}.png
    Retorna el Path del archivo guardado.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    path = config.SCREENSHOTS_DIR / f"{label}_{ts}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        logger.debug(f"Screenshot guardado: {path.name}")
    except Exception as e:
        logger.warning(f"Error al tomar screenshot '{label}': {e}")
    return path


def take_element_screenshot(element_handle, label: str = "element") -> Path:
    """Captura screenshot de un elemento específico."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    path = config.SCREENSHOTS_DIR / f"{label}_{ts}.png"
    try:
        element_handle.screenshot(path=str(path))
        logger.debug(f"Element screenshot: {path.name}")
    except Exception as e:
        logger.warning(f"Error al tomar element screenshot '{label}': {e}")
    return path


# ── Tesseract OCR helpers ─────────────────────────────────────────────────

def find_tesseract() -> Optional[str]:
    """
    Busca el ejecutable de Tesseract en:
    1. PATH del sistema
    2. Registro de Windows (SOFTWARE\\Tesseract-OCR)
    3. Rutas conocidas de instalación
    Retorna el path completo o None si no se encuentra.
    """
    # 1. PATH
    in_path = shutil.which("tesseract")
    if in_path:
        return in_path

    # 2. Registro Windows
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tesseract-OCR")
        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
        candidate = Path(install_dir) / "tesseract.exe"
        if candidate.exists():
            return str(candidate)
    except Exception:
        pass

    # 3. Rutas conocidas
    username = os.environ.get("USERNAME", "")
    all_paths = [config.TESSERACT_DEFAULT_PATH] + [
        p.format(username=username) for p in config.TESSERACT_ALT_PATHS
    ]
    for p in all_paths:
        if Path(p).exists():
            return p

    return None


def configure_tesseract():
    """Configura pytesseract con el path de Tesseract encontrado."""
    tess_path = find_tesseract()
    if tess_path:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = tess_path
            logger.debug(f"Tesseract configurado en: {tess_path}")
        except ImportError:
            logger.warning("pytesseract no está instalado. OCR no disponible.")
    else:
        logger.warning("Tesseract OCR no encontrado. El módulo visual_learner funcionará con capacidad reducida.")


def ocr_image(image_path) -> str:
    """
    Ejecuta Tesseract OCR sobre una imagen.
    Retorna el texto extraído. Fallback silencioso a string vacío.
    """
    try:
        import pytesseract
        configure_tesseract()
        text = pytesseract.image_to_string(
            str(image_path),
            lang=config.OCR_LANG,
            config=config.OCR_CONFIG,
        )
        return text.strip()
    except Exception as e:
        logger.debug(f"OCR falló en {image_path}: {e}")
        return ""


# ── Smart waits (Playwright) ──────────────────────────────────────────────

def wait_for_react_idle(page, timeout_ms: int = 5_000):
    """
    Espera a que React termine de renderizar usando networkidle.
    En SPAs con polling, networkidle puede timeout — se captura silenciosamente.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass  # Normal en SPAs con websocket/polling activo


def wait_for_url_contains(page, fragment: str, timeout_ms: int = 30_000):
    """Espera hasta que la URL actual contenga el fragmento dado."""
    page.wait_for_url(f"**{fragment}**", timeout=timeout_ms)


def wait_for_selector_any(page, selectors: list[str], timeout_ms: int = 10_000):
    """
    Espera hasta que aparezca cualquiera de los selectores dados.
    Retorna el primero que aparezca.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    deadline = time.time() + timeout_ms / 1000
    interval = 0.3

    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=300):
                    return sel
            except Exception:
                pass
        time.sleep(interval)

    raise TimeoutError(
        f"Ningún selector encontrado en {timeout_ms}ms: {selectors}"
    )


def paste_large_text(page, text: str):
    """
    Estrategia eficiente para insertar texto largo en Rise 360:
    - Usa pyperclip para poner en clipboard
    - Envía Ctrl+V a la página
    Fallback: keyboard.type() para textos cortos.

    REGLA: El texto se inserta EXACTAMENTE como viene (verbatim).
    """
    if len(text) <= 300:
        # Texto corto: keyboard.type es más confiable
        page.keyboard.type(text, delay=5)
    else:
        # Texto largo: clipboard es más rápido
        try:
            import pyperclip
            pyperclip.copy(text)
            page.keyboard.press("Control+v")
            time.sleep(0.3)
        except ImportError:
            # Si no hay pyperclip, usar keyboard.type igualmente
            logger.debug("pyperclip no disponible, usando keyboard.type para texto largo")
            page.keyboard.type(text, delay=2)


def safe_click(page, locator, label: str = "elemento"):
    """
    Click seguro con scroll-into-view y espera a que sea clickeable.
    Loguea la acción.
    """
    try:
        locator.scroll_into_view_if_needed()
        locator.wait_for(state="visible", timeout=config.ELEMENT_WAIT_MS)
        locator.click()
        logger.debug(f"Click exitoso en: {label}")
        return True
    except Exception as e:
        logger.warning(f"Click fallido en {label}: {e}")
        take_screenshot(page, label=f"click_fail_{label[:30]}")
        return False
