---
name: syntec-lad
description: Inspect, generate, patch, remap, diff, and natively validate native Syntec MLCEdit/LadEditor .lad ladder files using deterministic JSON plans and hash-pinned changes. Use for PLC ladder work on native .lad files; do not use for .il/PDF/screenshot conversion, GUI click automation, or CNC upload.
metadata:
  short-description: Edit and validate Syntec .lad ladder files safely
---

# Syntec Ladder SDK

Use the bundled SDK/CLI to inspect and modify native Syntec LadEditor `.lad`
files. The model prepares a reviewed JSON plan; the Python compiler owns the
record bytes and the native LadDLL check owns final syntax compatibility.

## Run the bundled CLI

For the default Codex skill installation:

```powershell
$SkillRoot = Join-Path $HOME '.codex\skills\syntec-lad'
$LadCli = Join-Path $SkillRoot 'scripts\run_syntec_lad.py'
python $LadCli --version
```

If the skill is cloned elsewhere, set `$SkillRoot` to the directory containing
this `SKILL.md`. From a checkout, `python -m syntec_lad ...` and
`syntec-lad ...` also work after `python -m pip install -e .`.

## Mandatory workflow

1. Start every natural-language change with `inspect`. Use its record ranges,
   hashes, labels, encoding, and network candidates; never guess a boundary.

   ```powershell
   python $LadCli inspect .\source.lad --json --output .\source.inspect.json
   ```

2. Before writing JSON, ask for every missing I/O address, register, timer,
   counter, preset, and interlock relationship. Do not invent PLC values or
   startup/stop/reset logic.

3. Convert the request into one reviewed JSON document:
   - New file: `syntec-lad-plan/v1`
   - Existing file: `syntec-lad-change/v1` with `base_sha256` and the exact
     `expected_sha256` of each target record range.

4. Generate a new output by default:

   ```powershell
   python $LadCli build .\plan.json .\generated.lad
   python $LadCli patch .\source.lad .\change.json .\patched.lad
   ```

   Use `--in-place` only when the user explicitly asks to overwrite. The SDK
   creates a timestamped `.bak` and writes through a same-directory temporary
   file.

5. Keep unknown or unsupported records byte-for-byte. Do not add new axis,
   RS232, Keyscan, filesystem, external-function, Coord/Tool, or FastPLC
   logic.

6. Do not report success unless the output includes both
   `parser readback: PASS` and `native syntax: PASS`. If the native check is
   unavailable, stop and report the missing `LadDLL.dll`, x86 checker, or
   .NET Framework toolchain. Do not use `--no-native` for final delivery
   unless the user explicitly requested a parser-only diagnostic.

7. For an independent check:

   ```powershell
   python $LadCli validate .\generated.lad
   python $LadCli diff .\source.lad .\generated.lad
   ```

8. Report absolute input/output paths, source and output SHA256 values, record
   counts, native validation result (`error_count == 0`), changed record
   ranges, and any `.bak` or `.LC` sidecar handling.

## Useful commands

```powershell
python $LadCli inspect .\file.lad
python $LadCli remap .\source.lad .\mapped.lad --mapping-file .\mapping.json --expected-sha256 <sha256>
python $LadCli validate .\file.lad
python $LadCli diff .\before.lad .\after.lad
python $LadCli open .\file.lad
```

`open` only starts `MLCEdit.exe -f <file>` for human review. It is not a syntax
check, and this skill must not click the UI or upload anything to a CNC.

## Native setup

Set these when LadEditor is not in a known location:

```powershell
$env:SYNTEC_LAD_DLL = 'C:\path\to\LadEditor\LadDLL.dll'
$env:SYNTEC_LAD_EDITOR = 'C:\path\to\LadEditor\MLCEdit.exe'
```

Native validation requires Windows, a 32-bit `LadDLL.dll`, and .NET Framework
4.x (`csc.exe`). The SDK builds its own x86 helper on first use. The parser and
most CLI commands work without LadEditor.

## References

- Read [references/workflow.md](references/workflow.md) for plan/change examples,
  hash-pinned patching, remapping, and delivery checks.
- Read the exact schemas in `syntec_lad/schema/lad-plan.schema.json` and
  `syntec_lad/schema/lad-change.schema.json` before creating JSON.
- Use `examples/demo.plan.json` and `examples/mvp.plan.json` as format fixtures,
  not as machine-ready logic. Their addresses are placeholders.
