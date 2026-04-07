@echo off
chcp 65001 >nul
echo ============================================
echo  Firecast BIB Converter - Build EXE
echo ============================================
echo.

echo [1/3] Instalando dependencias...
python -m pip install pyinstaller flask flaskwebgui tkinterdnd2 --quiet
if errorlevel 1 (
    echo ERRO: falha ao instalar dependencias.
    pause
    exit /b 1
)

echo [2/3] Gerando executavel...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "BIB-Converter" ^
    --collect-all flaskwebgui ^
    --collect-all tkinterdnd2 ^
    --clean ^
    app_webview.py

if errorlevel 1 (
    echo ERRO: falha ao gerar o executavel.
    pause
    exit /b 1
)

echo.
echo [3/3] Concluido!
echo O executavel esta em:  dist\BIB-Converter.exe
echo.
pause
