"""Regression checks for shared skills and optional IDE entry points."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.project = Path(cls.temp.name)
        for directory in ("skills", "scripts"):
            shutil.copytree(
                ROOT / directory,
                cls.project / directory,
                ignore=shutil.ignore_patterns("__pycache__"),
            )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_python(self, *args):
        result = subprocess.run(
            [sys.executable, *map(str, args)],
            cwd=self.project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_all_public_commands_without_ide_directories(self):
        for script in (self.project / "skills").glob("*/scripts/*.py"):
            with self.subTest(script=script.name):
                self.run_python(script, "--help")

    def test_bot_resolves_shared_scripts_and_client_without_ide(self):
        self.run_python("-c", '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path("scripts/feishu_bug_bot").resolve()))
import tapd_paths, submit_bridge, close_bridge, query_bridge
root = Path.cwd()
assert tapd_paths.workspace_root() == root
assert submit_bridge.find_submit_bug_script() == root / "skills/tapd-submit-bug/scripts/submit_bug.py"
assert close_bridge.find_close_bug_script() == root / "skills/tapd-close-bug/scripts/close_bug.py"
client = query_bridge._ensure_tapd_client()
assert Path(client.__file__) == root / "skills/tapd-plus/scripts/tapd_client_stdlib.py"
assert client.workspace_root() == root
try:
    tapd_paths.skill_script("missing-skill", "missing.py")
except FileNotFoundError:
    pass
else:
    raise AssertionError("Missing shared script must fail explicitly")
''')

    def test_legacy_commands_and_imports_forward_to_shared_implementation(self):
        for ide in (".cursor", ".codebuddy"):
            for original in (ROOT / ide / "skills").glob("*/scripts/*.py"):
                target = self.project / original.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
                with self.subTest(ide=ide, script=target.name):
                    self.run_python(target, "--help")
                    self.run_python("-c", '''
import runpy, sys
from pathlib import Path
module = runpy.run_path(sys.argv[1])
assert module["workspace_root"]() == Path.cwd()
assert Path(module["__file__"]).is_relative_to(Path.cwd() / "skills")
''', target)


if __name__ == "__main__":
    unittest.main()
