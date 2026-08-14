from __future__ import annotations

import logging
import shutil
from pathlib import Path


LOGGER = logging.getLogger("hosxp-polling-agent.web-assets")
REQUIRED_ASSETS = (
    Path("templates/base.html"),
    Path("templates/login.html"),
    Path("templates/logs.html"),
    Path("static/app.css"),
    Path("static/app.js"),
)


def prepare_web_assets(
    bundled_agent_dir: Path,
    project_root: Path,
    *,
    frozen: bool,
    version: str,
) -> Path:
    """Return a web asset root that survives PyInstaller temp cleanup.

    A one-file PyInstaller process imports Python modules into memory but Jinja
    and StaticFiles read their files lazily. Windows cleanup software may remove
    the live ``_MEI...`` extraction directory after several days, leaving the
    service healthy while every rendered page fails with TemplateNotFound.
    Frozen builds therefore refresh a small versioned, persistent asset copy on
    every child start and serve web files from that copy.
    """
    if not frozen:
        return bundled_agent_dir

    destination = project_root / "runtime-assets" / version
    destination.mkdir(parents=True, exist_ok=True)
    for directory in ("templates", "static"):
        source = bundled_agent_dir / directory
        if not source.is_dir():
            raise RuntimeError(f"Bundled web asset directory is missing: {source}")
        shutil.copytree(
            source,
            destination / directory,
            dirs_exist_ok=True,
        )

    missing = [str(path) for path in REQUIRED_ASSETS if not (destination / path).is_file()]
    if missing:
        raise RuntimeError(
            "Persistent web assets are incomplete: " + ", ".join(missing)
        )
    LOGGER.info("Persistent web assets ready: %s", destination)
    return destination
