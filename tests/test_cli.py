from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from syntec_lad.native import find_lad_dll


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_PLAN = PROJECT_ROOT / "examples" / "demo.plan.json"


def run_cli(*args: object, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "syntec_lad", *(str(arg) for arg in args)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )


class CliTests(unittest.TestCase):
    def test_build_inspect_validate_diff_and_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.lad"
            second = root / "second.lad"

            built = run_cli("build", DEMO_PLAN, first, "--no-native")
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertIn("parser readback: PASS", built.stdout)
            self.assertTrue(first.is_file())

            inspected = run_cli("inspect", first, "--json")
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            report = json.loads(inspected.stdout)
            self.assertEqual(report["integrity"]["issues"], [])
            self.assertEqual(report["programs"][0]["name"], "MAIN")
            self.assertIn("演示主程序", report["comments"][0]["text"])

            validated = run_cli("validate", first, "--no-native")
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertIn("parser  : PASS", validated.stdout)

            same = run_cli("diff", first, first)
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertIn("same  : True", same.stdout)

            plan = json.loads(DEMO_PLAN.read_text(encoding="utf-8"))
            plan["programs"][0]["networks"].append(
                {
                    "condition": {"contact": "I050"},
                    "actions": [{"op": "OUT", "operand": "O050"}],
                }
            )
            changed_plan = root / "changed.plan.json"
            changed_plan.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            changed = run_cli("build", changed_plan, second, "--no-native")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            different = run_cli("diff", first, second)
            self.assertEqual(different.returncode, 1, different.stderr)

            refused = run_cli("build", DEMO_PLAN, first, "--no-native")
            self.assertEqual(refused.returncode, 2)
            self.assertIn("output exists", refused.stderr)

            replaced = run_cli(
                "build",
                DEMO_PLAN,
                first,
                "--in-place",
                "--no-native",
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertEqual(len(list(root.glob(f"{first.name}.*.bak"))), 1)

    def test_patch_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.lad"
            output = root / "patched.lad"
            built = run_cli("build", DEMO_PLAN, source, "--no-native")
            self.assertEqual(built.returncode, 0, built.stderr)

            inspected = run_cli("inspect", source, "--json")
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            report = json.loads(inspected.stdout)
            candidate = report["network_candidates"][0]
            change = {
                "schema": "syntec-lad-change/v1",
                "base_sha256": report["source"]["sha256"],
                "operations": [
                    {
                        "op": "replace",
                        "record_start": candidate["record_start"],
                        "record_end": candidate["record_end"],
                        "expected_sha256": candidate["sha256"],
                        "networks": [
                            {
                                "condition": {"contact": "I060"},
                                "actions": [{"op": "OUT", "operand": "O060"}],
                            }
                        ],
                    }
                ],
            }
            change_path = root / "change.json"
            change_path.write_text(
                json.dumps(change, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            patched = run_cli("patch", source, change_path, output, "--no-native")
            self.assertEqual(patched.returncode, 0, patched.stderr)
            self.assertIn("parser readback: PASS", patched.stdout)
            self.assertTrue(output.is_file())

            refused = run_cli("patch", source, change_path, output, "--no-native")
            self.assertEqual(refused.returncode, 2)
            self.assertIn("output exists", refused.stderr)

    def test_remap_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.lad"
            output = root / "remapped.lad"
            report_path = root / "remap.json"
            built = run_cli("build", DEMO_PLAN, source, "--no-native")
            self.assertEqual(built.returncode, 0, built.stderr)

            remapped = run_cli(
                "remap",
                source,
                output,
                "--map",
                "I0=I400",
                "--map",
                "I1=I401",
                "--map",
                "I2=I402",
                "--map",
                "I10=I410",
                "--require-all-sources",
                "--report",
                report_path,
                "--no-native",
            )
            self.assertEqual(remapped.returncode, 0, remapped.stderr)
            self.assertIn("parser readback: PASS", remapped.stdout)
            self.assertTrue(output.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["changed_operands"], 6)
            self.assertEqual(report["changed_records"], 5)
            self.assertEqual(report["unmatched_sources"], [])

            inspected = run_cli("inspect", output, "--json")
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            inspection = json.loads(inspected.stdout)
            operands = [
                operand["value"]
                for row in inspection["rows"]
                if row.get("kind") == "code"
                for cell in row.get("cells", [])
                for operand in cell.get("operands", [])
            ]
            self.assertIn("I400", operands)
            self.assertIn("I401", operands)
            self.assertIn("I410", operands)
            self.assertNotIn("I000", operands)

            refused = run_cli(
                "remap",
                source,
                root / "missing.lad",
                "--map",
                "I9=I409",
                "--require-all-sources",
                "--no-native",
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("not found", refused.stderr)

    def test_inspect_refuses_to_overwrite_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.lad"
            built = run_cli("build", DEMO_PLAN, source, "--no-native")
            self.assertEqual(built.returncode, 0, built.stderr)
            before = source.read_bytes()
            result = run_cli("inspect", source, "--output", source)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must not overwrite", result.stderr)
            self.assertEqual(source.read_bytes(), before)

            bad_encoding = run_cli("inspect", source, "--encoding", "not-a-codec")
            self.assertEqual(bad_encoding.returncode, 2)
            self.assertIn("unknown ladder comment encoding", bad_encoding.stderr)

    def test_native_cli_integration(self) -> None:
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "native.lad"
            built = run_cli("build", DEMO_PLAN, output)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertIn("native syntax: PASS", built.stdout)
            validated = run_cli("validate", output)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertIn("native  : PASS", validated.stdout)


if __name__ == "__main__":
    unittest.main()
