"""
visual_learner.py — Módulo de aprendizaje visual para Rise 360 Automator
Usa OpenCV + Tesseract para identificar tipos de bloques mediante análisis
de screenshots. Se usa como fallback cuando los selectores DOM fallan.
"""

import json
import time
from pathlib import Path
from typing import Optional
from utils import logger, take_screenshot, ocr_image, configure_tesseract
import config


class VisualLearner:
    """
    Analiza visualmente el curso de referencia en Rise 360 para aprender
    patrones de diseño, y usa ese aprendizaje para identificar bloques
    durante la automatización.

    Estrategia:
    1. Durante la fase de aprendizaje: toma screenshots de cada bloque del
       curso de referencia y los etiqueta manualmente/automáticamente.
    2. Durante la automatización: compara screenshots actuales con las
       referencias usando template matching de OpenCV.
    3. Usa Tesseract OCR para leer texto en las capturas y ayudar a
       identificar el tipo de bloque por su contenido.
    """

    def __init__(self, learning_map: dict):
        self.learning_map = learning_map
        self.reference_images: dict = {}   # label → numpy array
        self._cv2_available = False
        self._load_cv2()
        self._load_reference_images()

    def _load_cv2(self):
        """Intenta cargar OpenCV. Registra si no está disponible."""
        try:
            import cv2
            import numpy as np
            self._cv2_available = True
            logger.debug("OpenCV disponible para análisis visual")
        except ImportError:
            logger.warning(
                "OpenCV no disponible. El análisis visual usará solo OCR. "
                "Instala con: pip install opencv-python"
            )

    def _load_reference_images(self):
        """Carga imágenes de referencia guardadas en screenshots/ref_*.png"""
        if not self._cv2_available:
            return
        import cv2
        for img_path in config.SCREENSHOTS_DIR.glob("ref_*.png"):
            label = img_path.stem.replace("ref_", "")
            img = cv2.imread(str(img_path))
            if img is not None:
                self.reference_images[label] = img
                logger.debug(f"Referencia visual cargada: {label}")
        logger.info(f"Referencias visuales cargadas: {len(self.reference_images)}")

    # ── API pública ───────────────────────────────────────────────────────

    def capture_reference(self, page, block_label: str) -> Path:
        """
        Toma un screenshot del bloque actualmente visible y lo guarda
        como imagen de referencia para el label dado.

        Uso: llamar cuando el usuario ha navegado al bloque correcto.
        """
        configure_tesseract()
        path = take_screenshot(page, label=f"ref_{block_label}")
        if self._cv2_available:
            import cv2
            img = cv2.imread(str(path))
            if img is not None:
                self.reference_images[block_label] = img
        logger.info(f"Referencia capturada para bloque: {block_label}")
        return path

    def identify_block_type(self, page) -> Optional[str]:
        """
        Identifica el tipo de bloque actualmente visible en la página.
        Usa template matching si OpenCV está disponible, sino solo OCR.

        Retorna el label del bloque o None si no se identifica con confianza.
        """
        if not self.reference_images:
            logger.debug("Sin referencias visuales. No se puede identificar bloque.")
            return self._identify_by_ocr_only(page)

        current_path = take_screenshot(page, label="current_identify")

        if self._cv2_available:
            return self._identify_by_template_matching(current_path)
        else:
            return self._identify_by_ocr_only(page)

    def detect_block_regions(self, page) -> list[dict]:
        """
        Escanea la página completa y detecta regiones individuales de bloques.
        Útil durante el análisis del curso de referencia.

        Retorna lista de:
        {
            "bbox": (x, y, w, h),
            "ocr_text": str,
            "block_type_guess": str | None,
            "color_signature": str,
            "region_index": int
        }
        """
        path = take_screenshot(page, label="block_scan")

        if self._cv2_available:
            return self._detect_regions_cv2(path)
        else:
            return self._detect_regions_fallback(page)

    def analyze_reference_course(self, page) -> dict:
        """
        Analiza el curso de referencia completo para extraer el mapa de diseño.
        Navega por todas las secciones del curso tomando screenshots.

        Retorna dict con:
        {
            "detected_patterns": [{"block_type": str, "design_notes": str}],
            "block_order": [str, ...],
            "visual_map": {...}
        }
        """
        logger.info("Analizando curso de referencia visualmente...")
        results = {
            "detected_patterns": [],
            "block_order": [],
            "visual_map": {},
            "screenshots": [],
        }

        # Tomar screenshot inicial
        path = take_screenshot(page, label="reference_initial")
        results["screenshots"].append(str(path))

        # OCR del contenido visible para entender el curso
        text_content = ocr_image(path)
        logger.debug(f"OCR del curso de referencia:\n{text_content[:500]}...")

        # Detectar regiones de bloques en la vista actual
        regions = self.detect_block_regions(page)
        for i, region in enumerate(regions):
            block_type = region.get("block_type_guess") or "desconocido"
            results["block_order"].append(block_type)
            results["detected_patterns"].append({
                "index": i,
                "block_type": block_type,
                "ocr_preview": region.get("ocr_text", "")[:100],
                "color": region.get("color_signature", "unknown"),
            })

        logger.info(f"Curso de referencia analizado: {len(regions)} bloques detectados")
        return results

    # ── Implementaciones internas ─────────────────────────────────────────

    def _identify_by_template_matching(self, current_path: Path) -> Optional[str]:
        """
        Identifica bloque usando cv2.matchTemplate (TM_CCOEFF_NORMED).
        Retorna el label con mayor score si supera el umbral de confianza.
        """
        import cv2
        current_img = cv2.imread(str(current_path))
        if current_img is None:
            return None

        best_match = None
        best_score = 0.0

        for label, ref_img in self.reference_images.items():
            score = self._template_match_score(current_img, ref_img)
            logger.debug(f"Match score [{label}]: {score:.3f}")
            if score > best_score:
                best_score = score
                best_match = label

        if best_score >= config.VISUAL_MATCH_THRESHOLD:
            logger.debug(f"Bloque identificado: {best_match} (score: {best_score:.3f})")
            return best_match
        else:
            logger.debug(
                f"Identificación visual inconclusa "
                f"(mejor: {best_match}, score: {best_score:.3f})"
            )
            return None

    def _identify_by_ocr_only(self, page) -> Optional[str]:
        """
        Identifica tipo de bloque usando solo OCR sin OpenCV.
        Heurística basada en palabras clave en el texto visible.
        """
        path = take_screenshot(page, label="ocr_identify")
        text = ocr_image(path).lower()

        if not text:
            return None

        # Heurísticas de texto para identificar tipos de bloques
        if any(k in text for k in ["introducción", "introduccion"]):
            return "text"
        if any(k in text for k in ["tema 1", "tema 2", "tema 3", "tema 4"]):
            return "section_banner"
        if any(k in text for k in ["conclusión", "conclusion"]):
            return "text"
        if any(k in text for k in ["referencias", "bibliografía"]):
            return "text"
        if text.count("•") > 2 or text.count("-") > 4:
            return "bulleted_list"

        return None

    def _template_match_score(self, current, template) -> float:
        """
        Calcula el score de template matching entre imagen actual y referencia.
        Redimensiona el template si es más grande que la imagen actual.
        Retorna score normalizado [0, 1].
        """
        import cv2
        import numpy as np

        if current is None or template is None:
            return 0.0

        try:
            th, tw = template.shape[:2]
            ch, cw = current.shape[:2]

            # Redimensionar template si es más grande
            if th > ch or tw > cw:
                scale = min(ch / th, cw / tw) * 0.8
                new_w = max(1, int(tw * scale))
                new_h = max(1, int(th * scale))
                template = cv2.resize(template, (new_w, new_h))
                th, tw = template.shape[:2]

            # Verificar que el template cabe en current
            if th > ch or tw > cw:
                return 0.0

            result = cv2.matchTemplate(current, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            return float(max_val)
        except Exception as e:
            logger.debug(f"Template match error: {e}")
            return 0.0

    def _detect_regions_cv2(self, page_screenshot_path: Path) -> list[dict]:
        """
        Detecta regiones de bloques usando detección de bordes de OpenCV.
        Busca líneas horizontales que separan bloques de Rise 360.
        """
        import cv2
        import numpy as np

        img = cv2.imread(str(page_screenshot_path))
        if img is None:
            return []

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)

        # Detectar líneas horizontales que separan bloques
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180, threshold=100,
            minLineLength=int(img.shape[1] * 0.5),  # 50% del ancho
            maxLineGap=15,
        )

        y_separators = [0]
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if abs(y1 - y2) < 5:  # Línea horizontal
                    y_separators.append((y1 + y2) // 2)
        y_separators.append(img.shape[0])
        y_separators = sorted(set(y_separators))

        regions = []
        for i in range(len(y_separators) - 1):
            y_start = y_separators[i]
            y_end = y_separators[i + 1]
            if y_end - y_start < 40:  # Ignorar regiones muy pequeñas
                continue

            # Extraer subimagen de la región
            region_img = img[y_start:y_end, :]
            region_path = config.SCREENSHOTS_DIR / f"region_{i}.png"
            cv2.imwrite(str(region_path), region_img)

            # OCR de la región
            ocr_text = ocr_image(region_path)

            # Análisis de color
            color_sig = self._analyze_color(region_img)

            # Guess del tipo de bloque
            block_guess = self._guess_from_text_and_color(ocr_text, color_sig)

            regions.append({
                "bbox": (0, y_start, img.shape[1], y_end - y_start),
                "ocr_text": ocr_text,
                "block_type_guess": block_guess,
                "color_signature": color_sig,
                "region_index": i,
            })

        return regions

    def _detect_regions_fallback(self, page) -> list[dict]:
        """
        Fallback sin OpenCV: retorna una región genérica con OCR de la página completa.
        """
        path = take_screenshot(page, label="fallback_scan")
        text = ocr_image(path)
        return [{
            "bbox": (0, 0, 1920, 1080),
            "ocr_text": text,
            "block_type_guess": None,
            "color_signature": "unknown",
            "region_index": 0,
        }]

    def _analyze_color(self, region) -> str:
        """
        Analiza el color dominante de una región de imagen.
        Usa el canal V (brillo) del espacio HSV.
        Retorna: "white" | "light" | "medium" | "dark"
        """
        try:
            import cv2
            import numpy as np
            hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
            mean_v = np.mean(hsv[:, :, 2])
            if mean_v > 230:
                return "white"
            elif mean_v > 180:
                return "light"
            elif mean_v > 100:
                return "medium"
            else:
                return "dark"
        except Exception:
            return "unknown"

    def _guess_from_text_and_color(
        self, text: str, color: str
    ) -> Optional[str]:
        """
        Guess heurístico del tipo de bloque combinando texto OCR y color.
        """
        text_lower = text.lower()

        # Color oscuro suele ser banner
        if color == "dark":
            return "banner"

        # Keywords de sección
        if any(k in text_lower for k in ["tema ", "capítulo", "unidad "]):
            return "section_banner"

        # Introducción/Conclusión
        if any(k in text_lower for k in ["introducción", "introduccion", "conclusión", "conclusion"]):
            return "text"

        # Lista con viñetas
        if text.count("•") > 2 or text.count("-") > 3:
            return "bulleted_list"

        # Referencias
        if any(k in text_lower for k in ["referencias", "bibliografía"]):
            return "text"

        # Default: texto genérico
        if text.strip():
            return "text"

        return None

    def save_learned_patterns(self, patterns: dict):
        """Persiste los patrones aprendidos en el learning_map.json."""
        try:
            self.learning_map["visual_map"] = patterns
            with open(config.LEARNING_MAP_PATH, "w", encoding="utf-8") as f:
                json.dump(self.learning_map, f, ensure_ascii=False, indent=2)
            logger.info("Patrones visuales guardados en learning_map.json")
        except Exception as e:
            logger.warning(f"No se pudieron guardar patrones visuales: {e}")
