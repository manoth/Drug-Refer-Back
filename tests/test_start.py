from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from agent.start import (
    WindowsSingleInstance,
    _agent_process_ids,
    _run_frozen_windows_supervisor,
    _server_child_command,
)
from agent.web_config import WebConfig
from agent.windows_startup import startup_command


class StartBehaviorTests(unittest.TestCase):
    def test_tasklist_parser_finds_only_other_agent_processes(self) -> None:
        output = (
            '"DrugReferAgent.exe","100","Console","1","10,000 K"\n'
            '"DrugReferAgent-v1.2.0-windows-x64.exe","200","Console","1","10,000 K"\n'
            '"python.exe","300","Console","1","10,000 K"\n'
        )

        self.assertEqual([100], _agent_process_ids(output, current_pid=200))

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

    def test_new_version_takes_over_legacy_instance_without_version_field(self) -> None:
        config = WebConfig.from_env().with_server("127.0.0.1", 9876, False)
        occupied = MagicMock()
        occupied.acquire.return_value = False
        replacement = MagicMock()
        intentional_child = MagicMock()
        intentional_child.poll.return_value = 0

        with (
            patch("agent.start.WindowsSingleInstance", return_value=occupied),
            patch("agent.start._running_agent_version", return_value=""),
            patch("agent.start._terminate_other_agent_processes", return_value=True) as stop_old,
            patch("agent.start._acquire_after_takeover", return_value=replacement),
            patch("agent.start.subprocess.Popen", return_value=intentional_child),
            patch("agent.start._write_supervisor_log"),
            patch("agent.start.time.sleep"),
        ):
            result = _run_frozen_windows_supervisor(
                config,
                "http://127.0.0.1:9876/",
                open_browser=False,
                allow_takeover=True,
            )

        self.assertEqual(0, result)
        stop_old.assert_called_once_with()
        replacement.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
