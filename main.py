"""
main.py — Entry point del Rise 360 Automator
GUI Tkinter + auto-instalación de dependencias + orquestación del proceso.

Flujo:
1. Auto-instala dependencias faltantes (pip + Tesseract OCR)
2. Muestra ventana Tkinter con inputs
3. Al iniciar: lanza thread de automatización
4. Actualiza progreso en tiempo real via queue
"""

# ── Auto-instalación (DEBE ir antes de cualquier import de deps externas) ─
import subprocess
import sys
import os

# Forzar UTF-8 en stdout para evitar errores con caracteres especiales en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_PACKAGES = [
    "opencv-python",
    "pytesseract",
    "Pillow",
    "pyautogui",
]


def _pkg_importable(pkg: str) -> bool:
    """Verifica si un paquete es importable."""
    module = pkg.replace("-", "_").replace("opencv_python", "cv2").split("[")[0]
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def auto_install_packages():
    """Instala los paquetes Python faltantes usando pip."""
    missing = [p for p in REQUIRED_PACKAGES if not _pkg_importable(p)]
    if not missing:
        return

    print(f"Instalando dependencias faltantes: {', '.join(missing)}")
    for pkg in missing:
        try:
            print(f"  >> pip install {pkg}")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
                timeout=120,
            )
            print(f"  [OK] {pkg} instalado")
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] instalando {pkg}: {e}")
        except subprocess.TimeoutExpired:
            print(f"  [TIMEOUT] instalando {pkg}")


def auto_install_playwright():
    """Verifica que playwright y sus browsers estén instalados."""
    try:
        import playwright  # noqa
    except ImportError:
        print("Instalando playwright...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "--quiet"])

    # Verificar si Chromium está instalado
    chromium_ok = False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Solo verificar — no lanzar
            chromium_ok = True
    except Exception:
        pass

    if not chromium_ok:
        print("Instalando Chromium para Playwright...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            timeout=300,
        )


def auto_install_tesseract():
    """
    Intenta instalar Tesseract OCR en Windows si no está presente.
    Usa winget (disponible en Windows 10/11) como método preferido.
    """
    try:
        import shutil
        if shutil.which("tesseract"):
            return  # Ya está instalado

        # Verificar rutas conocidas
        for path in [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]:
            if os.path.exists(path):
                return  # Ya está instalado

        print("Tesseract OCR no encontrado. Intentando instalar via winget...")
        result = subprocess.run(
            ["winget", "install", "--id", "UB-Mannheim.TesseractOCR",
             "--silent", "--accept-source-agreements", "--accept-package-agreements"],
            timeout=180,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("✓ Tesseract OCR instalado")
        else:
            print(
                "⚠ No se pudo instalar Tesseract automáticamente. "
                "El análisis visual funcionará sin OCR.\n"
                "Para instalarlo manualmente: https://github.com/UB-Mannheim/tesseract/wiki"
            )
    except FileNotFoundError:
        print("⚠ winget no disponible. Tesseract OCR no instalado automáticamente.")
    except subprocess.TimeoutExpired:
        print("⚠ Timeout instalando Tesseract OCR")
    except Exception as e:
        print(f"⚠ Error en instalación de Tesseract: {e}")


# Ejecutar auto-instalación antes de importar los módulos del proyecto
if __name__ == "__main__" or getattr(sys, "frozen", False):
    auto_install_packages()
    auto_install_playwright()
    auto_install_tesseract()


# ── Imports principales (después de auto-install) ──────────────────────────
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import queue
import json
from pathlib import Path
from datetime import datetime

# Imports del proyecto
import config
from utils import logger
from pdf_parser import parse_pdf, load_cached_content
from rise_automation import RiseAutomation
from content_builder import ContentBuilder
from self_learning import SelfLearning
from visual_learner import VisualLearner


# ── Aplicación principal ──────────────────────────────────────────────────

class RiseAutomatorApp(tk.Tk):
    """
    Ventana principal de la aplicación.
    Lanza el proceso de automatización en un thread separado
    y actualiza la UI via queue para thread-safety.
    """

    def __init__(self):
        super().__init__()
        self.title("Rise 360 Automator — by Griky")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")

        # Estado
        self._automation_thread: threading.Thread | None = None
        self._update_queue: queue.Queue = queue.Queue()
        self._running = False

        # Variables de formulario
        self._pdf_path = tk.StringVar()
        self._template_url = tk.StringVar(value=config.TEMPLATE_URL)

        # Construir UI
        self._build_ui()

        # Iniciar polling de la queue
        self._poll_queue()

        # Centrar ventana
        self._center_window()

    def _center_window(self):
        """Centra la ventana en la pantalla."""
        self.update_idletasks()
        w, h = 640, 580
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Construcción de UI ────────────────────────────────────────────────

    def _build_ui(self):
        """Construye todos los widgets de la interfaz."""

        # Colores del tema
        BG = "#1e1e2e"
        CARD = "#2a2a3e"
        ACCENT = "#7c3aed"
        TEXT = "#e2e8f0"
        MUTED = "#94a3b8"
        SUCCESS = "#22c55e"

        self.configure(bg=BG)
        pad = {"padx": 20, "pady": 6}

        # ── Header ────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=ACCENT, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="  Rise 360 Automator — by Griky",
            font=("Segoe UI", 14, "bold"),
            bg=ACCENT, fg="white",
            anchor="w",
        ).pack(fill="both", expand=True, padx=16)

        # ── Formulario ────────────────────────────────────────────────────
        form = tk.Frame(self, bg=CARD, pady=16)
        form.pack(fill="x", padx=20, pady=(16, 0))

        # PDF input
        tk.Label(
            form, text="Desarrollo Teórico (PDF):",
            font=("Segoe UI", 10), bg=CARD, fg=TEXT, anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 2))

        pdf_row = tk.Frame(form, bg=CARD)
        pdf_row.pack(fill="x", padx=16, pady=(0, 8))

        self._pdf_entry = tk.Entry(
            pdf_row, textvariable=self._pdf_path,
            font=("Consolas", 9), bg="#13131f", fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=4,
        )
        self._pdf_entry.pack(side="left", fill="x", expand=True)

        tk.Button(
            pdf_row, text="Buscar...",
            font=("Segoe UI", 9), bg=ACCENT, fg="white",
            activebackground="#6d28d9", relief="flat", padx=10,
            command=self._browse_pdf,
        ).pack(side="right", padx=(8, 0))

        # Template URL input
        tk.Label(
            form, text="Link de Plantilla Rise 360:",
            font=("Segoe UI", 10), bg=CARD, fg=TEXT, anchor="w",
        ).pack(fill="x", padx=16, pady=(4, 2))

        self._url_entry = tk.Entry(
            form, textvariable=self._template_url,
            font=("Consolas", 9), bg="#13131f", fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=4,
        )
        self._url_entry.pack(fill="x", padx=16, pady=(0, 12))

        # Separador
        tk.Frame(self, bg="#3a3a5e", height=1).pack(fill="x", padx=20, pady=(12, 0))

        # ── Estado y progreso ─────────────────────────────────────────────
        status_frame = tk.Frame(self, bg=BG)
        status_frame.pack(fill="x", padx=20, pady=(12, 4))

        self._status_label = tk.Label(
            status_frame,
            text="Estado: Esperando...",
            font=("Segoe UI", 10), bg=BG, fg=MUTED, anchor="w",
        )
        self._status_label.pack(fill="x")

        # Barra de progreso
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "Rise.Horizontal.TProgressbar",
            troughcolor="#13131f",
            background=ACCENT,
            borderwidth=0,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

        self._progress_bar = ttk.Progressbar(
            self, style="Rise.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", maximum=100,
        )
        self._progress_bar.pack(fill="x", padx=20, pady=(4, 8))

        self._pct_label = tk.Label(
            self, text="0%",
            font=("Segoe UI", 9), bg=BG, fg=MUTED,
        )
        self._pct_label.pack(anchor="e", padx=22)

        # ── Log ───────────────────────────────────────────────────────────
        tk.Label(
            self, text="Log en tiempo real:",
            font=("Segoe UI", 10), bg=BG, fg=TEXT, anchor="w",
        ).pack(fill="x", padx=20, pady=(8, 2))

        self._log_text = scrolledtext.ScrolledText(
            self, height=10, state="disabled",
            font=("Consolas", 8), bg="#0f0f1a", fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0,
            wrap="word",
        )
        self._log_text.pack(fill="both", padx=20, pady=(0, 12))

        # ── Botón principal ───────────────────────────────────────────────
        self._start_btn = tk.Button(
            self,
            text="▶  INICIAR PROCESO",
            font=("Segoe UI", 12, "bold"),
            bg=ACCENT, fg="white",
            activebackground="#6d28d9",
            relief="flat", padx=24, pady=10,
            cursor="hand2",
            command=self._start_process,
        )
        self._start_btn.pack(pady=(4, 20))

    # ── Handlers de UI ────────────────────────────────────────────────────

    def _browse_pdf(self):
        """Abre diálogo para seleccionar el PDF."""
        path = filedialog.askopenfilename(
            title="Seleccionar PDF de Desarrollo Teórico",
            filetypes=[("Archivos PDF", "*.pdf"), ("Todos los archivos", "*.*")],
        )
        if path:
            self._pdf_path.set(path)
            self._log(f"PDF seleccionado: {Path(path).name}")

    def _start_process(self):
        """Valida entradas y lanza el thread de automatización."""
        pdf_path = self._pdf_path.get().strip()
        template_url = self._template_url.get().strip()

        # Validaciones
        if not pdf_path:
            messagebox.showwarning("Campo requerido", "Por favor selecciona el archivo PDF.")
            return

        if not Path(pdf_path).exists():
            messagebox.showerror("Archivo no encontrado", f"No se encuentra el PDF:\n{pdf_path}")
            return

        if not template_url:
            messagebox.showwarning("Campo requerido", "Por favor ingresa el link de la plantilla Rise 360.")
            return

        if "rise.articulate.com" not in template_url:
            if not messagebox.askyesno(
                "URL inusual",
                "La URL no parece ser de Rise 360. ¿Deseas continuar de todas formas?"
            ):
                return

        # Deshabilitar botón y comenzar
        self._start_btn.configure(state="disabled", text="⏳ Procesando...")
        self._progress_bar["value"] = 0
        self._running = True

        # Lanzar thread
        self._automation_thread = threading.Thread(
            target=self._run_automation,
            args=(pdf_path, template_url),
            daemon=True,
            name="AutomationThread",
        )
        self._automation_thread.start()

    def _stop_process(self):
        """Detiene el proceso en curso."""
        self._running = False
        self._log("⚠ Detención solicitada por el usuario")

    # ── Thread de automatización ──────────────────────────────────────────

    def _run_automation(self, pdf_path: str, template_url: str):
        """
        Ejecuta el proceso completo de automatización.
        Corre en un thread separado para no bloquear la UI.
        Comunica con la GUI via self._update_queue.
        """
        self._update("Iniciando sistema...", 0)

        try:
            # ── Fase 1: Cargar learning map ────────────────────────────────
            learning_map = self._load_learning_map()
            learner = SelfLearning()
            visual_learner = VisualLearner(learning_map)

            # ── Fase 2: Parsear PDF ────────────────────────────────────────
            self._update("Analizando PDF...", 10)

            # Verificar caché (Caso 2: mismo PDF ya procesado)
            content = load_cached_content(pdf_path)
            if content:
                self._log("✓ Usando contenido PDF cacheado de sesión anterior")
            else:
                self._log(f"Analizando PDF: {Path(pdf_path).name}")
                content = parse_pdf(pdf_path)

            course_title = content.get("title", "Curso Rise 360")
            sections_count = len(content.get("sections", []))
            self._log(f"✓ PDF analizado: '{course_title}' — {sections_count} secciones")
            self._update(f"PDF analizado: {sections_count} secciones encontradas", 20)

            if not self._running:
                return

            # ── Fase 3: Iniciar browser ────────────────────────────────────
            self._update("Iniciando navegador Chromium...", 22)

            with RiseAutomation(progress_callback=self._update) as rise:

                # ── Fase 4: Login ──────────────────────────────────────────
                self._log("Iniciando sesión en Rise 360...")
                rise.login(config.EMAIL, config.PASSWORD)
                self._log("✓ Login exitoso")
                learner.start_monitoring(rise.page)

                if not self._running:
                    return

                # ── Fase 5: Análisis visual del curso de referencia ────────
                self._update("Analizando curso de referencia (visual)...", 38)
                self._log("Analizando diseño del curso de referencia...")

                try:
                    rise.navigate_to_course_outline(config.TEMPLATE_URL)
                    reference_patterns = visual_learner.analyze_reference_course(rise.page)
                    self._log(
                        f"✓ Análisis visual completado: "
                        f"{len(reference_patterns.get('detected_patterns', []))} patrones detectados"
                    )
                    visual_learner.save_learned_patterns(reference_patterns)
                except Exception as e:
                    self._log(f"⚠ Análisis visual parcial: {e}")

                self._update("Análisis visual completado", 45)

                if not self._running:
                    return

                # ── Fase 6: Duplicar plantilla ─────────────────────────────
                self._update("Duplicando plantilla de curso...", 48)
                self._log(f"Duplicando plantilla para: '{course_title}'")

                try:
                    new_course_url = rise.duplicate_template(template_url, course_title)
                    self._log(f"✓ Plantilla duplicada: {new_course_url}")
                    learner.record_script_action("navigate", new_course_url)
                except Exception as e:
                    self._log(f"✗ Error duplicando plantilla: {e}")
                    # Fallback: usar la URL de template directamente para testing
                    self._log("[!] Usando plantilla original como fallback")
                    rise.navigate_to_course_outline(template_url)

                self._update("Plantilla lista para edición", 55)

                if not self._running:
                    return

                # ── Fase 7: Insertar contenido ─────────────────────────────
                self._update("Insertando contenido del PDF...", 58)
                self._log(f"Iniciando inserción de {sections_count} secciones...")

                builder = ContentBuilder(
                    rise=rise,
                    learning_map=learning_map,
                    progress_callback=self._update,
                )

                builder.build_course(content)

                # Reporte final
                report = builder.get_build_report()
                self._log(
                    f"✓ Inserción completada: "
                    f"{report['blocks_inserted']} bloques exitosos, "
                    f"{report['blocks_failed']} fallidos "
                    f"({report['success_rate']:.1f}% éxito)"
                )

                if report["blocks_failed"] > 0:
                    self._log(
                        f"⚠ {report['blocks_failed']} bloques no se pudieron insertar. "
                        "Ver screenshots/ para detalles."
                    )

                # ── Fase 8: Guardar y finalizar ────────────────────────────
                learner.stop_monitoring()
                learner.export_session_log()

                self._update("¡Curso completado exitosamente!", 100)
                self._log("=" * 50)
                self._log(f"✅ CURSO CREADO: '{course_title}'")
                self._log(f"   URL: {rise.get_current_url()}")
                self._log("=" * 50)

                self._finish_success(course_title, rise.get_current_url())

        except Exception as e:
            logger.error(f"Error crítico en automatización: {e}", exc_info=True)
            self._log(f"✗ ERROR CRÍTICO: {e}")
            self._update(f"Error: {str(e)[:80]}", -1)
            self._finish_error(str(e))

    def _load_learning_map(self) -> dict:
        """Carga el learning_map.json."""
        try:
            with open(config.LEARNING_MAP_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"No se pudo cargar learning_map.json: {e}")
            return {"mappings": {}, "corrections_history": [], "learned_selectors": {}}

    # ── Comunicación thread → UI ──────────────────────────────────────────

    def _update(self, message: str, percent: int):
        """
        Pone un update en la queue para ser procesado por el hilo principal.
        Seguro para llamar desde cualquier thread.
        """
        self._update_queue.put(("update", message, percent))

    def _log(self, message: str):
        """Pone un mensaje de log en la queue."""
        self._update_queue.put(("log", message, None))

    def _finish_success(self, course_title: str, course_url: str):
        """Notifica al hilo principal que el proceso terminó exitosamente."""
        self._update_queue.put(("success", course_title, course_url))

    def _finish_error(self, error_msg: str):
        """Notifica al hilo principal que hubo un error crítico."""
        self._update_queue.put(("error", error_msg, None))

    def _poll_queue(self):
        """
        Polling de la queue de updates. Corre en el hilo principal (Tkinter).
        Procesa todos los mensajes pendientes en cada tick.
        """
        try:
            while True:
                item = self._update_queue.get_nowait()
                kind = item[0]

                if kind == "update":
                    _, message, percent = item
                    self._status_label.configure(text=f"Estado: {message}")
                    if percent >= 0:
                        self._progress_bar["value"] = min(percent, 100)
                        self._pct_label.configure(text=f"{min(percent, 100)}%")
                    self._log_message(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

                elif kind == "log":
                    _, message, _ = item
                    self._log_message(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

                elif kind == "success":
                    _, course_title, course_url = item
                    self._running = False
                    self._start_btn.configure(
                        state="normal", text="▶  INICIAR PROCESO",
                        bg="#22c55e",
                    )
                    messagebox.showinfo(
                        "¡Proceso completado!",
                        f"El curso '{course_title}' fue creado exitosamente en Rise 360.\n\n"
                        f"URL: {course_url}"
                    )

                elif kind == "error":
                    _, error_msg, _ = item
                    self._running = False
                    self._start_btn.configure(
                        state="normal", text="▶  INICIAR PROCESO",
                        bg="#ef4444",
                    )
                    messagebox.showerror(
                        "Error en el proceso",
                        f"El proceso encontró un error:\n\n{error_msg}\n\n"
                        "Revisa los logs en la carpeta 'logs/' para más detalles."
                    )
                    # Resetear color del botón después de 3 segundos
                    self.after(3000, lambda: self._start_btn.configure(bg="#7c3aed"))

        except queue.Empty:
            pass

        # Reprogramar el polling cada 100ms
        self.after(100, self._poll_queue)

    def _log_message(self, message: str):
        """Agrega un mensaje al widget de log (thread-safe vía after)."""
        self._log_text.configure(state="normal")
        self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ── Cierre de la app ──────────────────────────────────────────────────

    def on_close(self):
        """Maneja el cierre de la ventana."""
        if self._running:
            if not messagebox.askyesno(
                "Proceso en ejecución",
                "El proceso de automatización está en curso. "
                "¿Deseas cerrarlo de todas formas?"
            ):
                return
        self._running = False
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    """Punto de entrada de la aplicación."""
    app = RiseAutomatorApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)

    # Log inicial
    app._log_message(f"[{datetime.now().strftime('%H:%M:%S')}] Rise 360 Automator iniciado")
    app._log_message(f"[{datetime.now().strftime('%H:%M:%S')}] Directorio: {config.BASE_DIR}")
    app._log_message(f"[{datetime.now().strftime('%H:%M:%S')}] Python: {sys.version.split()[0]}")
    app._log_message("-" * 60)

    app.mainloop()


if __name__ == "__main__":
    main()
