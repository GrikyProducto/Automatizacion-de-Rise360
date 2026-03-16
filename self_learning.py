"""
self_learning.py — Módulo de aprendizaje continuo y retroalimentación
Monitorea las correcciones del usuario en el browser y actualiza
el learning_map.json para mejorar la precisión en futuras ejecuciones.
"""

import json
import time
import threading
from datetime import datetime
from typing import Callable, Optional
from pathlib import Path
from utils import logger
import config


class SelfLearning:
    """
    Rastrea las acciones del usuario en el browser de Rise 360 y compara
    con las acciones del script para detectar y aprender de correcciones.

    Estrategia de monitoreo:
    - Intercepta eventos de request/response del browser via Playwright
    - Registra los pasos del script internamente
    - Al detectar diferencia usuario vs. script → actualiza el mapa
    """

    def __init__(self, learning_map_path: Optional[Path] = None):
        self.map_path = learning_map_path or config.LEARNING_MAP_PATH
        self.learning_map = self._load_map()
        self._script_actions: list[dict] = []
        self._user_actions: list[dict] = []
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._page = None

    # ── Carga y guardado del mapa ─────────────────────────────────────────

    def _load_map(self) -> dict:
        """Carga el learning_map.json. Usa el default si no existe."""
        if self.map_path.exists():
            try:
                with open(self.map_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug("learning_map.json cargado exitosamente")
                return data
            except Exception as e:
                logger.warning(f"Error cargando learning_map.json: {e}. Usando default.")

        # Retornar mapa vacío si no existe (el seed ya está en data/)
        return {
            "version": "1.0",
            "mappings": {},
            "corrections_history": [],
            "learned_selectors": {},
        }

    def save_map(self):
        """Persiste el learning_map.json actualizado."""
        try:
            self.map_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.map_path, "w", encoding="utf-8") as f:
                json.dump(self.learning_map, f, ensure_ascii=False, indent=2)
            logger.debug("learning_map.json guardado")
        except Exception as e:
            logger.error(f"Error guardando learning_map.json: {e}")

    def get_mapping(self, block_type: str) -> Optional[dict]:
        """Retorna el mapeo para un tipo de bloque dado."""
        return self.learning_map.get("mappings", {}).get(block_type)

    def get_learned_selector(self, element_name: str) -> Optional[str]:
        """Retorna un selector aprendido para un elemento de la UI."""
        return self.learning_map.get("learned_selectors", {}).get(element_name)

    # ── Registro de acciones del script ───────────────────────────────────

    def record_script_action(self, action_type: str, target: str, value: str = ""):
        """
        Registra una acción que el script ejecutó.

        Args:
            action_type: "click", "type", "navigate", "add_block", etc.
            target: Selector o descripción del elemento
            value: Valor insertado (texto, URL, etc.)
        """
        action = {
            "timestamp": datetime.now().isoformat(),
            "actor": "script",
            "type": action_type,
            "target": target,
            "value": value[:200] if value else "",  # Limitar para no sobrecargar el log
        }
        self._script_actions.append(action)
        logger.debug(f"Script: {action_type} → {target[:50]}")

    def record_script_block_insert(self, block_type: str, text_preview: str = ""):
        """Shortcut para registrar inserción de un bloque por el script."""
        self.record_script_action(
            action_type="add_block",
            target=block_type,
            value=text_preview[:100],
        )

    # ── Monitoreo del usuario ─────────────────────────────────────────────

    def start_monitoring(self, page):
        """
        Inicia el monitoreo de acciones del usuario en el browser.
        Instala listeners de red en Playwright para detectar cambios.
        """
        self._page = page
        self._monitoring = True

        # Listener de requests para detectar llamadas a la API de Rise 360
        # cuando el usuario interactúa manualmente
        page.on("request", self._on_request)

        # Listener de console para detectar eventos de la app React
        page.on("console", self._on_console_message)

        logger.info("Monitoreo de acciones de usuario iniciado")

    def stop_monitoring(self):
        """Detiene el monitoreo."""
        self._monitoring = False
        if self._page:
            try:
                self._page.remove_listener("request", self._on_request)
                self._page.remove_listener("console", self._on_console_message)
            except Exception:
                pass
        logger.info("Monitoreo de acciones de usuario detenido")

    def _on_request(self, request):
        """
        Callback para interceptar requests del browser.
        Detecta llamadas a la API de Rise 360 que indican cambios de contenido.
        """
        if not self._monitoring:
            return

        url = request.url
        method = request.method

        # Rise 360 usa su propia API REST — detectar operaciones de escritura
        # Filtrar analytics/telemetry para reducir ruido en los logs
        IGNORE_URLS = ["datadoghq.com", "analytics", "/rum?", "lifecycle/refresh"]
        if any(ignore in url for ignore in IGNORE_URLS):
            return

        if any(api in url for api in ["/api/rise-runtime/ducks/", "/lesson/", "/block/"]):
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                action = {
                    "timestamp": datetime.now().isoformat(),
                    "actor": "user",
                    "type": "api_call",
                    "target": url.split("?")[0],
                    "method": method,
                    "value": "",
                }
                self._user_actions.append(action)
                logger.debug(f"User API call: {method} {url.split('?')[0][-60:]}")

                # Detectar si el usuario está corrigiendo algo
                self._detect_correction()

    def _on_console_message(self, msg):
        """Captura mensajes de consola para debugging."""
        if msg.type == "error" and "rise" in msg.text.lower():
            logger.debug(f"Browser console error: {msg.text[:200]}")

    # ── Detección y aprendizaje de correcciones ───────────────────────────

    def _detect_correction(self):
        """
        Compara las acciones recientes del usuario con las del script.
        Si detecta una discrepancia significativa, registra la corrección.
        """
        if len(self._user_actions) < 2:
            return

        recent_user = self._user_actions[-1]
        recent_script = self._script_actions[-1] if self._script_actions else None

        if recent_script is None:
            return

        # Si el usuario hizo una acción API que no fue iniciada por el script
        # (gap de más de 2 segundos entre script y usuario)
        script_time = datetime.fromisoformat(recent_script["timestamp"])
        user_time = datetime.fromisoformat(recent_user["timestamp"])
        time_gap = abs((user_time - script_time).total_seconds())

        if time_gap > 2.0:
            correction = {
                "timestamp": datetime.now().isoformat(),
                "script_action": recent_script,
                "user_correction": recent_user,
                "time_gap_seconds": time_gap,
            }
            logger.info(f"Corrección detectada: usuario modificó después del script ({time_gap:.1f}s después)")
            self._register_correction(correction)

    def _register_correction(self, correction: dict):
        """
        Registra una corrección en el historial y actualiza el mapa si es posible.
        """
        # Agregar al historial
        history = self.learning_map.setdefault("corrections_history", [])
        history.append(correction)

        # Mantener solo las últimas 100 correcciones
        if len(history) > 100:
            self.learning_map["corrections_history"] = history[-100:]

        # Intentar actualizar el mapa basado en la corrección
        self._update_map_from_correction(correction)

        # Guardar inmediatamente
        self.save_map()

    def _update_map_from_correction(self, correction: dict):
        """
        Intenta actualizar el learning_map basado en el patrón de corrección.
        Por ahora registra la corrección en learned_selectors para análisis futuro.
        """
        script_action = correction.get("script_action", {})
        user_action = correction.get("user_correction", {})

        # Si el usuario corrigió un selector de bloque
        if script_action.get("type") == "add_block":
            block_type = script_action.get("target", "")
            correction_target = user_action.get("target", "")

            if block_type and correction_target:
                learned = self.learning_map.setdefault("learned_selectors", {})
                if block_type not in learned:
                    learned[block_type] = []
                # Registrar el patrón de corrección
                learned[block_type].append({
                    "correction_url": correction_target,
                    "timestamp": correction["timestamp"],
                })
                logger.info(f"Selector aprendido para '{block_type}': {correction_target[:60]}")

    # ── API para actualización directa desde UI ───────────────────────────

    def update_block_mapping(self, block_type: str, rise_block: str, notes: str = ""):
        """
        Permite actualizar manualmente el mapeo de un tipo de bloque.
        Se puede llamar desde la GUI si el usuario indica una corrección.

        Args:
            block_type: Tipo de contenido PDF (h1, parrafo, tabla, etc.)
            rise_block: Nombre del bloque Rise 360 (text, section_banner, etc.)
            notes: Nota opcional sobre el cambio
        """
        mappings = self.learning_map.setdefault("mappings", {})
        old_mapping = mappings.get(block_type, {})

        mappings[block_type] = {
            **old_mapping,
            "rise_block": rise_block,
            "last_updated": datetime.now().isoformat(),
            "source": "user_correction",
            "notes": notes,
        }

        # Registrar el cambio en historial
        correction = {
            "timestamp": datetime.now().isoformat(),
            "type": "manual_mapping_update",
            "block_type": block_type,
            "old_rise_block": old_mapping.get("rise_block", "unknown"),
            "new_rise_block": rise_block,
            "notes": notes,
        }
        self.learning_map.setdefault("corrections_history", []).append(correction)

        self.save_map()
        logger.info(f"Mapeo actualizado: '{block_type}' → '{rise_block}'")

    def update_selector(self, element_name: str, selector: str):
        """
        Actualiza un selector de UI aprendido.
        """
        self.learning_map.setdefault("learned_selectors", {})[element_name] = selector
        self.save_map()
        logger.info(f"Selector actualizado: '{element_name}' → '{selector}'")

    # ── Estadísticas ──────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Retorna estadísticas del aprendizaje acumulado."""
        return {
            "total_corrections": len(self.learning_map.get("corrections_history", [])),
            "learned_selectors": len(self.learning_map.get("learned_selectors", {})),
            "script_actions_session": len(self._script_actions),
            "user_actions_session": len(self._user_actions),
            "map_version": self.learning_map.get("version", "unknown"),
        }

    def export_session_log(self, output_path: Optional[Path] = None) -> Path:
        """
        Exporta el log de acciones de esta sesión para análisis.
        """
        if output_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = config.LOGS_DIR / f"session_log_{ts}.json"

        session_data = {
            "session_start": self._script_actions[0]["timestamp"] if self._script_actions else None,
            "session_end": datetime.now().isoformat(),
            "script_actions": self._script_actions,
            "user_actions": self._user_actions,
            "stats": self.get_stats(),
        }

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Log de sesión exportado: {output_path}")
        except Exception as e:
            logger.warning(f"No se pudo exportar el log de sesión: {e}")

        return output_path
