from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntec_lad.compiler import compile_plan
from syntec_lad.errors import LadValidationError, NativeValidationError
from syntec_lad.native import (
    DEFAULT_DLL_CANDIDATES,
    PROJECT_ROOT,
    _search_native_files,
    ensure_checker,
    find_lad_dll,
    validate_native,
)

SAMPLE_ENV = os.environ.get("SYNTEC_LAD_SAMPLE")
SAMPLE = Path(SAMPLE_ENV) if SAMPLE_ENV else None


def small_plan() -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "programs": [
            {
                "name": "MAIN",
                "networks": [
                    {
                        "condition": {"contact": "I000"},
                        "actions": [{"op": "OUT", "operand": "O000"}],
                    }
                ],
            }
        ],
    }


class NativeSafetyTests(unittest.TestCase):
    def test_search_finds_install_files_in_nonstandard_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = (
                root
                / "any-vendor-folder"
                / "SYNTEC"
                / "LadEditor_9.9"
                / "LadEditor"
            )
            install.mkdir(parents=True)
            dll = install / "LadDLL.dll"
            editor = install / "MLCEdit.exe"
            dll.write_bytes(b"dll")
            editor.write_bytes(b"exe")

            found = _search_native_files([root])

        self.assertEqual(found["dll"], dll.resolve())
        self.assertEqual(found["editor"], editor.resolve())

    def test_search_skips_dependency_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "node_modules" / "SYNTEC" / "LadEditor"
            install.mkdir(parents=True)
            (install / "LadDLL.dll").write_bytes(b"dll")

            found = _search_native_files([root])

        self.assertEqual(found, {})

    def test_fixed_candidate_wins_without_drive_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "LadDLL.dll"
            dll.write_bytes(b"dll")
            with (
                patch("syntec_lad.native.DEFAULT_DLL_CANDIDATES", (dll,)),
                patch("syntec_lad.native._discover_native_files") as discover,
            ):
                found = find_lad_dll()
        self.assertEqual(found, dll.resolve())
        discover.assert_not_called()

    def test_fixed_candidates_do_not_contain_personal_drive_paths(self) -> None:
        personal = []
        for path in DEFAULT_DLL_CANDIDATES:
            if path.drive.casefold() != "d:":
                continue
            if not path.is_relative_to(PROJECT_ROOT):
                personal.append(str(path))
        self.assertEqual(personal, [])

    def test_missing_native_components_raise_clear_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(NativeValidationError):
                find_lad_dll(root / "missing-LadDLL.dll")
            with self.assertRaises(NativeValidationError):
                validate_native(root / "missing.lad")

            with patch("syntec_lad.native._first_existing", return_value=None):
                with self.assertRaisesRegex(NativeValidationError, "csc.exe"):
                    ensure_checker(root / "LadSyntaxCheck.exe", force=True)

    def test_atomic_save_failure_keeps_existing_file(self) -> None:
        document = compile_plan(small_plan())
        original = document.to_bytes()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.lad"
            target.write_bytes(original)
            with patch(
                "syntec_lad.model.os.replace",
                side_effect=OSError("simulated write failure"),
            ):
                with self.assertRaises(OSError):
                    document.save(target, in_place=True)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_save_requires_explicit_overwrite(self) -> None:
        document = compile_plan(small_plan())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.lad"
            target.write_bytes(document.to_bytes())
            with self.assertRaises(LadValidationError):
                document.save(target)

    def test_native_validation_of_compiled_document(self) -> None:
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        document = compile_plan(small_plan())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "compiled.lad"
            target.write_bytes(document.to_bytes())
            result = validate_native(target)
        self.assertTrue(result["passed"])
        self.assertEqual(result["error_count"], 0)

    @unittest.skipUnless(
        SAMPLE is not None and SAMPLE.exists(),
        "real cnc.lad sample is unavailable",
    )
    def test_native_validation_of_real_sample(self) -> None:
        assert SAMPLE is not None
        try:
            find_lad_dll()
        except Exception as exc:
            self.skipTest(f"native LadDLL unavailable: {exc}")
        result = validate_native(SAMPLE)
        self.assertTrue(result["passed"])
        self.assertEqual(result["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
