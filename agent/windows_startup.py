from __future__ import annotations

import subprocess
import sys
from pathlib import Path


WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = "DrugReferAgent"


def startup_command(executable: Path) -> str:
    return subprocess.list2cmdline(
        [str(executable), "--startup", "--no-browser"]
    )


def configure_windows_startup(enabled: bool) -> bool:
    """Apply the per-user Windows Startup preference.

    Returns True when the Windows registry was updated. Source runs and
    non-Windows builds return False while still allowing the preference to be
    persisted for a later Windows EXE run.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False

    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                WINDOWS_RUN_VALUE,
                0,
                winreg.REG_SZ,
                startup_command(Path(sys.executable).resolve()),
            )
        else:
            try:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE)
            except FileNotFoundError:
                pass
    return True
