from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.updater import ReleaseInfo, UpdateManager, version_tuple


class UpdateManagerTests(unittest.TestCase):
    def test_semantic_version_comparison_is_numeric(self) -> None:
        self.assertGreater(version_tuple("v1.10.0"), version_tuple("1.9.9"))
        with self.assertRaises(ValueError):
            version_tuple("not-a-version")

    def test_release_status_reports_newer_version(self) -> None:
        release = ReleaseInfo(
            version="1.5.0",
            tag="v1.5.0",
            release_url="https://github.example/release",
            asset_name="DrugReferAgent-v1.5.0-windows-x64.exe",
            asset_url="https://github.example/agent.exe",
            digest="sha256:" + "a" * 64,
        )

        status = release.public_status("1.4.0")

        self.assertTrue(status["update_available"])
        self.assertEqual("1.5.0", status["latest_version"])

    def test_update_check_uses_bounded_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager("1.4.0", Path(directory))
            release = ReleaseInfo(
                version="1.5.0",
                tag="v1.5.0",
                release_url="https://github.example/release",
                asset_name="DrugReferAgent-v1.5.0-windows-x64.exe",
                asset_url="https://github.example/agent.exe",
                digest="sha256:" + "b" * 64,
            )
            with patch.object(
                manager,
                "_fetch_latest",
                return_value=release,
            ) as fetch:
                first = manager.check_for_update()
                second = manager.check_for_update()

        self.assertTrue(first["update_available"])
        self.assertEqual(first, second)
        fetch.assert_called_once_with()

    def test_sha256_helper_hashes_downloaded_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.exe"
            path.write_bytes(b"verified update")

            digest = UpdateManager._sha256(path)

        self.assertEqual(
            "59f19f34399b14e5f1628642e9ce341d660094ba76898e4db6b1875f525b6a6a",
            digest,
        )


if __name__ == "__main__":
    unittest.main()
