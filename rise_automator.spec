# -*- mode: python ; coding: utf-8 -*-
# rise_automator.spec — Configuración de PyInstaller para Rise 360 Automator
# Genera: dist/rise_automator.exe

import sys
from pathlib import Path

block_cipher = None

# Obtener el path de los browsers de Playwright para incluirlos
import os
playwright_browsers = os.environ.get(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path.home() / "AppData" / "Local" / "ms-playwright")
)

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Datos del proyecto
        ("data/", "data"),
        # Assets (ícono, etc.)
        ("assets/", "assets"),
        # Módulos del proyecto (incluir explícitamente)
        ("config.py", "."),
        ("utils.py", "."),
        ("pdf_parser.py", "."),
        ("visual_learner.py", "."),
        ("rise_automation.py", "."),
        ("content_builder.py", "."),
        ("self_learning.py", "."),
    ],
    hiddenimports=[
        # Playwright
        "playwright",
        "playwright.sync_api",
        "playwright._impl._sync_base",
        "playwright._impl._browser",
        "playwright._impl._page",
        # PyMuPDF
        "fitz",
        "fitz.fitz",
        # OpenCV
        "cv2",
        # Pytesseract
        "pytesseract",
        # Pillow
        "PIL",
        "PIL.Image",
        "PIL.ImageGrab",
        # Tkinter (incluido en Python pero a veces necesita hint)
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.scrolledtext",
        "tkinter.messagebox",
        # Otros
        "queue",
        "threading",
        "winreg",
        "pyperclip",
        "pyautogui",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Excluir lo que no se usa para reducir tamaño
        "matplotlib",
        "numpy.distutils",
        "IPython",
        "jupyter",
        "scipy",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="rise_automator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # Comprimir con UPX si está disponible
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Sin consola negra — solo la ventana Tkinter
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",  # Descomentar cuando exista el ícono
    version_file=None,
    uac_admin=False,       # No requiere admin
    uac_uiaccess=False,
)
