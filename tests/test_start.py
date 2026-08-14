from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from agent.start import (
    WindowsSingleInstance,
    _run_frozen_windows_supervisor,
    _server_child_command,
)
from agent.web_config import WebConfig
from agent.windows_startup import startup_command


class StartBehaviorTests(unittest.TestCase):
    def test_startup_command_quotes_executable_and_suppresses_browser(self) -> None:
        command = startup_command(
            Path(r"C:\Program Files\Drug Refer\DrugReferAgent.exe")
        )

        self.assertIn('"C:\\Program Files\\Drug Refer\\DrugReferAgent.exe"', command)
        self.assertTrue(command.endswith("--startup --no-browser"))

    def test_single_instance_is_noop_on_non_windows_test_host(self) -> None:
        instance = WindowsSingleInstance(8765)
        self.assertTrue(instance.acquire())
        instance.close()

    def test_supervisor_child_command_is_hidden_and_does_not_open_browser(self) -> None:
        config = WebConfig.from_env().with_server("127.0.0.1", 9876, False)

        command = _server_child_command(config)

        self.assertIn("--server-child", command)
        self.assertIn("--no-browser", command)
        self.assertEqual("9876", command[command.index("--port") + 1])

    def test_supervisor_restarts_failed_child_until_intentional_exit(self) -> None:
        config = WebConfig.from_env().with_server("127.0.0.1", 9876, False)
        failed_child = MagicMock()
        failed_child.poll.return_value = 70
        intentional_child = MagicMock()
        intentional_child.poll.return_value = 0

        with (
            patch(
                "agent.start.subprocess.Popen",
                side_effect=[failed_child, intentional_child],
            ) as popen,
            patch("agent.start._write_supervisor_log"),
            patch("agent.start.time.sleep"),
        ):
            result = _run_frozen_windows_supervisor(
                config,
                "http://127.0.0.1:9876/",
                open_browser=False,
            )

        self.assertEqual(0, result)
        self.assertEqual(2, popen.call_count)


if __name__ == "__main__":
    unittest.main()
