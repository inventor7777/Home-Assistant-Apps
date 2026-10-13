import importlib.util
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('vnstat_server', Path(__file__).resolve().parents[1] / 'vnstat/app/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class CliTests(unittest.TestCase):
    def request(self, path):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler._is_allowed = Mock(return_value=True)
        handler._send_json = Mock()
        handler.send_error = Mock()
        handler.do_GET()
        return handler

    def test_modes_run_cli_with_config_and_interface(self):
        for mode, flags in [('normal', []), ('daily', ['--days']), ('monthly', ['--months']), ('json', ['--json'])]:
            with self.subTest(mode=mode), patch.object(server, 'INTERFACE', 'eth0'), patch.object(server.subprocess, 'run', return_value=SimpleNamespace(stdout='output')) as run:
                handler = self.request('/api/cli/' + mode)
                run.assert_called_once_with(['vnstat', '--config', server.CONFIG_FILE, *flags, '-i', 'eth0'], check=True, capture_output=True, text=True)
                handler._send_json.assert_called_once_with({'status': 'ok', 'output': 'output'})

    def test_unknown_mode_does_not_run_command(self):
        with patch.object(server.subprocess, 'run') as run:
            handler = self.request('/api/cli/--remove')
            run.assert_not_called()
            handler.send_error.assert_called_once_with(404, 'Not found')

    def test_cli_failure_returns_error(self):
        with patch.object(server.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'vnstat', stderr='Database unavailable')):
            handler = self.request('/api/cli/daily')
            handler._send_json.assert_called_once_with({'status': 'error', 'message': 'Database unavailable'}, status=500)
