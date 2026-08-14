"""PyInstaller entry point for the Windows Drug Refer Agent executable."""

import ctypes
import os
import sys
import traceback
from pathlib import Path

from agent.start import UNEXPECTED_SERVER_EXIT, main


def _report_background_startup_error(
    exc: BaseException,
    *,
    show_dialog: bool,
) -> None:
    local_data = Path(os.getenv("LOCALAPPDATA", Path(sys.executable).parent))
    log_path = local_data / "DrugReferAgent" / "logs" / "startup-error.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        encoding="utf-8",
    )
    if show_dialog:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Drug Refer Agent could not start.\n\n{exc}\n\nLog: {log_path}",
            "Drug Refer Agent",
            0x10,
        )


if __name__ == "__main__":
    is_server_child = "--server-child" in sys.argv
    exit_code = 0
    try:
        exit_code = main()
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
        if exit_code != 0 and sys.platform == "win32" and getattr(
            sys, "frozen", False
        ):
            _report_background_startup_error(
                exc,
                show_dialog=not is_server_child,
            )
    except BaseException as exc:
        exit_code = UNEXPECTED_SERVER_EXIT
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            _report_background_startup_error(
                exc,
                show_dialog=not is_server_child,
            )
        else:
            raise
    raise SystemExit(exit_code)
