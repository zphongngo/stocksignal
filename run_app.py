"""Launcher de dong goi app.py thanh file .exe bang PyInstaller (xem build_exe.bat)."""
import os
import sys

from streamlit.web import cli as stcli

# Cac thu vien app.py dung - import o day de PyInstaller dong goi kem (app.py duoc nhung dang file du lieu)
import numpy  # noqa: F401
import openpyxl  # noqa: F401
import requests  # noqa: F401
import truststore  # noqa: F401
import telegram_inbox  # noqa: F401
import stock_data  # noqa: F401


def main():
    # Khi chay tu .exe, PyInstaller giai nen file vao thu muc tam sys._MEIPASS
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    sys.argv = [
        "streamlit", "run", os.path.join(base_dir, "app.py"),
        "--global.developmentMode=false",
        "--server.headless=false",          # tu mo trinh duyet
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=viewer",      # an nut Deploy/menu danh cho lap trinh vien
        # Giao dien toi (giong .streamlit/config.toml)
        "--theme.base=dark",
        "--theme.backgroundColor=#0d1117",
        "--theme.secondaryBackgroundColor=#161b22",
        *sys.argv[1:],                      # tuy chon them, vd: StockBoard.exe --server.port 8600
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
