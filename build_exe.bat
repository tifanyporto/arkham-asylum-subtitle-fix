@echo off
rem Builds dist\ArkhamSubtitleFix.exe (needs: pip install pyinstaller pillow)
cd /d "%~dp0"
if not exist tools\decompress.exe (
    echo tools\decompress.exe missing - run "python patch.py --apply" once or download it from gildor.org
    exit /b 1
)
python -m PyInstaller --onefile --console --name ArkhamSubtitleFix ^
    --add-binary "tools\decompress.exe;tools" patch.py
