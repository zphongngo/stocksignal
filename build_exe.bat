@echo off
REM Dong goi bang gia thanh 1 file: dist\StockBoard.exe
cd /d "%~dp0"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile ^
  --name StockBoard ^
  --add-data "app.py;." ^
  --collect-all streamlit ^
  --copy-metadata streamlit ^
  --hidden-import truststore ^
  run_app.py
