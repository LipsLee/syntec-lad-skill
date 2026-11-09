from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_workbuddy.py"


class WorkBuddyInstallerTests(unittest.TestCase):
    def test_install_keeps_backups_outside_skills_scan_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            skill_dir = base / "skills" / "syntec-lad"
            config = base / "mcp.json"
            command = [
                sys.executable,
                str(INSTALLER),
                "--skills-dir",
                str(skill_dir),
                "--mcp-config",
                str(config),
            ]

            first = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertTrue((skill_dir / "SKILL.md").is_file())
            self.assertTrue((skill_dir / "scripts" / "syntec_lad_mcp.py").is_file())

            second = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

            self.assertFalse(
                any(skill_dir.parent.glob("syntec-lad.bak-*")),
                "a backup inside skills/ can be scanned as a duplicate skill",
            )
            backups = list((base / "skills-backups").glob("syntec-lad.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "SKILL.md").is_file())

            report = json.loads(config.read_text(encoding="utf-8"))
            server = report["mcpServers"]["syntec-lad"]
            self.assertEqual(server["command"], str(Path(sys.executable).resolve()))
            self.assertTrue(Path(server["args"][0]).is_file())


if __name__ == "__main__":
    unittest.main()
