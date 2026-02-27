@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo   Rise 360 Automator — Generando Ejecutable .exe
echo   by Griky 2026
echo ============================================================
echo.

:: Verificar PyInstaller
python -m pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo Instalando PyInstaller...
    pip install pyinstaller
)

:: Asegurarse que el spec existe
if not exist rise_automator.spec (
    echo [ERROR] No se encontro rise_automator.spec
    echo Ejecuta primero: python main.py para generar la configuracion
    pause
    exit /b 1
)

:: Limpiar builds anteriores
echo Limpiando builds anteriores...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

:: Generar ejecutable
echo Generando ejecutable...
python -m pyinstaller rise_automator.spec

if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la generacion del ejecutable
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   [OK] Ejecutable generado en: dist\rise_automator.exe
echo ============================================================
echo.
pause
