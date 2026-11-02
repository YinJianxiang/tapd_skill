"""Exercise the scheduled-task runner in an isolated project with a fake sync."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell required")
class SyncLauncherTests(unittest.TestCase):
    def test_report_exit_code_and_apply_arguments(self):
        for success in (True, False):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as temp:
                project = Path(temp)
                (project / "scripts").mkdir()
                (project / ".tapd").mkdir()
                runner = project / "scripts/run_dingtalk_status_sync.ps1"
                shutil.copy2(ROOT / "scripts/run_dingtalk_status_sync.ps1", runner)
                (project / ".tapd/dingtalk_status_sync.ps1").write_text("$env:LAUNCHER_CONFIG_LOADED = 'yes'", encoding="utf-8")
                fake = project / "fake-python.ps1"
                output = json.dumps({"ok": success})
                fake.write_text(
                    "if ($env:LAUNCHER_CONFIG_LOADED -ne 'yes') { exit 9 }\n"
                    "if ($args[1] -ne '--apply') { exit 8 }\n"
                    f"Write-Output '{output}'\nexit {0 if success else 1}\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
                     "-Apply", "-PythonExecutable", str(fake)],
                    capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
                report = json.loads((project / ".tapd/dingtalk_status_sync_last_result.json").read_text(encoding="utf-8"))
                self.assertEqual(report["result"]["ok"], success)
                self.assertEqual(report["exit_code"], 0 if success else 1)
                self.assertIn("started_at", report)
                self.assertIn("finished_at", report)


if __name__ == "__main__":
    unittest.main()
