from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx


DEFAULT_REPOSITORY = "manoth/Drug-Refer-Back"
MAX_UPDATE_BYTES = 100 * 1024 * 1024


def version_tuple(value: str) -> Tuple[int, int, int]:
    normalized = value.strip().lower().removeprefix("v")
    parts = normalized.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    release_url: str
    asset_name: str
    asset_url: str
    digest: str
    checksum_url: str = ""

    def public_status(self, current_version: str) -> Dict[str, object]:
        return {
            "current_version": current_version,
            "latest_version": self.version,
            "update_available": version_tuple(self.version)
            > version_tuple(current_version),
            "release_url": self.release_url,
        }


class UpdateManager:
    def __init__(
        self,
        current_version: str,
        update_root: Path,
        repository: str = DEFAULT_REPOSITORY,
        cache_seconds: float = 3600.0,
    ) -> None:
        self.current_version = current_version
        self.update_root = update_root
        self.repository = repository
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached_release: Optional[ReleaseInfo] = None

    @property
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.repository}/releases/latest"

    def _fetch_latest(self) -> ReleaseInfo:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"DrugReferAgent/{self.current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
            response = client.get(self.api_url)
            response.raise_for_status()
            payload = response.json()
        tag = str(payload.get("tag_name") or "")
        version_tuple(tag)
        assets = payload.get("assets") or []
        executable = next(
            (
                asset
                for asset in assets
                if str(asset.get("name", "")).lower().endswith("windows-x64.exe")
            ),
            None,
        )
        if executable is None:
            raise RuntimeError("Latest release has no Windows x64 EXE asset")
        checksum = next(
            (
                asset
                for asset in assets
                if str(asset.get("name", "")).casefold() == "sha256sums.txt"
            ),
            {},
        )
        return ReleaseInfo(
            version=tag.removeprefix("v"),
            tag=tag,
            release_url=str(payload.get("html_url") or ""),
            asset_name=Path(str(executable["name"])).name,
            asset_url=str(executable["browser_download_url"]),
            digest=str(executable.get("digest") or ""),
            checksum_url=str(checksum.get("browser_download_url") or ""),
        )

    def check_for_update(self, force: bool = False) -> Dict[str, object]:
        with self._lock:
            if (
                force
                or self._cached_release is None
                or time.monotonic() - self._cached_at >= self.cache_seconds
            ):
                self._cached_release = self._fetch_latest()
                self._cached_at = time.monotonic()
            return self._cached_release.public_status(self.current_version)

    def _expected_sha256(self, release: ReleaseInfo) -> str:
        if release.digest.lower().startswith("sha256:"):
            return release.digest.split(":", 1)[1].strip().lower()
        if not release.checksum_url:
            raise RuntimeError("Release has no SHA-256 digest or checksum asset")
        response = httpx.get(
            release.checksum_url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": f"DrugReferAgent/{self.current_version}"},
        )
        response.raise_for_status()
        for line in response.text.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and Path(parts[1].lstrip("* ")).name == release.asset_name:
                return parts[0].lower()
        raise RuntimeError("EXE checksum is missing from SHA256SUMS.txt")

    def download_latest(self) -> tuple[ReleaseInfo, Path]:
        with self._lock:
            release = self._fetch_latest()
            if version_tuple(release.version) <= version_tuple(self.current_version):
                raise RuntimeError("Agent is already running the latest version")
            expected = self._expected_sha256(release)
            destination_dir = self.update_root / release.version
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / release.asset_name
            if destination.is_file() and self._sha256(destination) == expected:
                return release, destination
            partial = destination.with_suffix(destination.suffix + ".part")
            digest = hashlib.sha256()
            total = 0
            try:
                with httpx.stream(
                    "GET",
                    release.asset_url,
                    timeout=60,
                    follow_redirects=True,
                    headers={"User-Agent": f"DrugReferAgent/{self.current_version}"},
                ) as response:
                    response.raise_for_status()
                    with partial.open("wb") as output:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > MAX_UPDATE_BYTES:
                                raise RuntimeError("Downloaded update exceeds 100 MB")
                            digest.update(chunk)
                            output.write(chunk)
                if digest.hexdigest().lower() != expected:
                    raise RuntimeError("Downloaded EXE failed SHA-256 verification")
                os.replace(partial, destination)
            finally:
                if partial.exists():
                    partial.unlink()
            return release, destination

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower()

    def launch(self, executable: Path) -> None:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            raise RuntimeError("Automatic installation is available in Windows EXE only")
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        subprocess.Popen(
            [str(executable)],
            close_fds=True,
            creationflags=creationflags,
        )
