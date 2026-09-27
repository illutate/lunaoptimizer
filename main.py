from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        script = str(Path(__file__).resolve())
        params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}" {params}'.strip(), None, 1)
        return int(rc) > 32
    except Exception:
        return False


def main() -> int:
    if os.name != "nt":
        print("Luna System Core requires Windows 11.")
        return 2
    if not is_admin():
        if relaunch_elevated():
            return 0
        print("Administrator privileges are required.")
        return 1
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    from app.ui import AppPaths, MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Luna System Core")
    app.setApplicationDisplayName("Luna System Core")
    app.setOrganizationName("Luna")
    app.setOrganizationDomain("local.luna")
    for family in QFontDatabase.families():
        if family.lower() == "jetbrains mono":
            app.setFont(app.font())
            break
    paths = AppPaths()
    config = paths.load_config()
    first_run = not bool(config.get("onboarding_complete"))
    window = MainWindow(paths, first_run=first_run)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
