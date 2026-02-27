@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo   Rise 360 Automator — Instalacion de Dependencias
echo   by Griky 2026
echo ============================================================
echo.

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta instalado.
    echo Descarga Python 3.11+ desde https://python.org
    pause
    exit /b 1
)

echo [OK] Python detectado:
python --version
echo.

:: Instalar dependencias Python
echo [1/4] Instalando dependencias Python...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas
echo.

:: Instalar Playwright browsers
echo [2/4] Instalando Chromium para Playwright...
python -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Fallo la instalacion de Chromium para Playwright
    echo Intenta manualmente: python -m playwright install chromium
) else (
    echo [OK] Chromium instalado
)
echo.

:: Instalar Tesseract OCR
echo [3/4] Instalando Tesseract OCR...
where tesseract >nul 2>&1
if not errorlevel 1 (
    echo [OK] Tesseract ya esta instalado
) else (
    winget install --id UB-Mannheim.TesseractOCR --silent --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [WARN] No se pudo instalar Tesseract automaticamente
        echo Descargalo manualmente desde:
        echo https://github.com/UB-Mannheim/tesseract/wiki
    ) else (
        echo [OK] Tesseract OCR instalado
    )
)
echo.

:: Verificar instalacion
echo [4/4] Verificando instalacion...
python -c "import playwright, fitz, cv2, pytesseract, PIL; print('[OK] Todos los modulos disponibles')" 2>nul
if errorlevel 1 (
    echo [WARN] Algunos modulos pueden no estar disponibles
    echo Ejecuta: python main.py para ver los detalles
)
echo.

echo ============================================================
echo   Instalacion completada. Ejecuta:
echo   python main.py
echo ============================================================
echo.
pause
