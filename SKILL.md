---
name: syntec-lad
description: Inspect, generate, patch, remap, diff, and natively validate native Syntec MLCEdit/LadEditor .lad ladder files using deterministic JSON plans and hash-pinned changes. Includes an engineering knowledge library for SYNTEC PLC ladder design, including 8 element classes, C/S/R address map, 602-address index, 12 process patterns, lubrication/hydraulic/chip-conveyor/tool-magazine/M-code function library, field conventions, comment encoding, and anti-patterns. Use for 新代/SYNTEC PLC 梯形图, .lad, ladder, I/O 点位表, 润滑/液压/排屑/刀库/M 码/报警/互锁 work; do not use for .il/PDF/screenshot conversion, GUI click automation, or CNC upload.
metadata:
  short-description: Edit, validate, and design Syntec .lad ladder logic safely
---

# Syntec Ladder SDK + Engineering Knowledge

This skill combines two layers:

- **Native tool layer:** the bundled Python SDK and CLI inspect and modify the
  native `.lad` record container. The native 32-bit `LadDLL.SyntaxCheck` is the
  final compatibility gate.
- **Engineering knowledge layer:** an address model, 602-address index,
  12 process patterns, subsystem recipes, field conventions, comment rules,
  and anti-patterns derived from official documentation and real-machine
  practice.

The tool layer owns bytes. The knowledge layer helps choose a safe structure.
Neither layer may invent an I/O point, register, timer, counter, preset, or
interlock relationship.

## What it can do

- Inspect programs, comments, record boundaries, hashes, and network candidates.
- Build a new `.lad` from a reviewed `syntec-lad-plan/v1` document.
- Patch an existing `.lad` through hash-pinned `syntec-lad-change/v1` ranges.
- Remap PLC bit/register addresses without cascading replacements.
- Diff two ladders and run Python readback plus native LadDLL validation.
- Look up a known C/S/R address or search the bundled engineering index.
- Translate a requested process into a reviewed ladder structure using the
  knowledge references.

## What it must not do

- Do not treat `.il`, PDF, BMP, a screenshot, or an old text dump as the
  writable source. The native `.lad` is the only writable ladder source.
- Do not click the editor UI, upload to a CNC, or modify a controller.
- Do not guess record boundaries, addresses, presets, or interlock logic.
- Do not generate new axis, RS232, Keyscan, filesystem, external-function,
  Coord/Tool, or FastPLC records. Preserve unknown records byte-for-byte.

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

## WorkBuddy and other agents

The native SDK is agent-agnostic. Use the lightest integration that the host
supports:

- **WorkBuddy:** run `python .\scripts\install_workbuddy.py` or double-click
  `scripts\install_workbuddy.bat`. It installs this folder under
  `~/.workbuddy/skills/syntec-lad/` and merges the MCP entry into
  `~/.workbuddy/mcp.json`. Restart WorkBuddy, confirm `syntec-lad` under
  MCP Servers, and approve it if prompted.
- **MCP clients:** register `scripts/syntec_lad_mcp.py` using
  `mcp-config.example.json`. No Python package installation is required.
- **Agents with shell access:** run `scripts/run_syntec_lad.py` directly.
- **Agent Skills hosts:** copy the whole directory into the host's skill
  directory, preserving `SKILL.md` and `references/`.

The MCP adapter exposes `inspect_lad`, `lookup_address`, `build_plan`,
`patch_lad`, `validate_lad`, `diff_lad`, `read_reference`,
`list_references`, and `explain_capabilities`. See `PORTABLE.md`.

## Mandatory workflow

1. **Inspect before planning.**

   ```powershell
   python $LadCli inspect .\source.lad --json --output .\source.inspect.json
   ```

   Use the reported record ranges, hashes, labels, encoding, and network
   candidates. Never infer a boundary from a screenshot or a line number.

2. **Consult the knowledge layer.**

   - Address class, write authority, C/S/R meaning: `references/panming/address-map.md`
   - Exact C/S/R lookup: `python $LadCli lookup C6` or `references/panming/address-index.md`
   - Contacts, coils, timers, counters, compare, flow: `references/panming/element-grammar.md`
   - Common circuits: `references/panming/patterns.md`
   - Lubrication, hydraulic, chip conveyor, coolant, tool magazine, M code:
     `references/panming/function-library.md`
   - Real-machine layout and naming conventions: `references/panming/project-conventions.md`
   - R512~R1023 permissions: `references/panming/R512-R1023-权限表.md`

   Use `references/knowledge-map.md` to choose the smallest relevant file.

3. **Resolve missing values before writing.** Ask for every missing I/O point,
   register, timer, counter, preset, and interlock relationship. Do not
   substitute a plausible-looking address.

4. **Select and adapt a structure.** If the request matches a pattern or
   function-library section, adapt the structure, not the machine-specific
   addresses. Keep the upstream provenance labels: manual, editor/real-machine
   observation, inference, or single-machine habit.

5. **Create one reviewed JSON document.**

   - New file: `syntec-lad-plan/v1`
   - Existing file: `syntec-lad-change/v1` with `base_sha256` and the exact
     `expected_sha256` of every target record range.

6. **Generate a new output by default.**

   ```powershell
   python $LadCli build .\plan.json .\generated.lad
   python $LadCli patch .\source.lad .\change.json .\patched.lad
   ```

   Use `--in-place` only when the user explicitly asks to overwrite. The SDK
   creates a timestamped `.bak` and writes through a same-directory temporary
   file.

7. **Run both validation gates.** Do not report success unless the output
   includes both `parser readback: PASS` and `native syntax: PASS`
   (`error_count == 0`).

8. **Run independent checks.**

   ```powershell
   python $LadCli validate .\generated.lad
   python $LadCli diff .\source.lad .\generated.lad
   ```

9. **Report the delivery evidence.** Include absolute input/output paths,
   source/output SHA256, record counts, native result, changed record ranges,
   and any `.bak` or `.LC` sidecar handling.

10. **Open for human review only.** `open` starts `MLCEdit.exe -f <file>`.
    It is not a syntax check and must not lead to UI automation or a CNC
    upload.

## Address model

The eight element families are not naming conventions; they are different
interfaces:

| Family | Meaning | Who writes it | Limits |
|---|---|---|---|
| `I` | physical input | field wiring; ladder reads | per I/O card |
| `O` | physical output | ladder | per I/O card |
| `A` | virtual auxiliary flag | ladder | `A0`~`A511` |
| `C` | ladder-to-CNC command | ladder; verify each address | `C0`~`C511` |
| `S` | CNC-to-ladder status | CNC only; ladder reads | `S0`~`S511` |
| `R` | bidirectional data register | depends on range | `R0`~`R65535` (DOS: `R0`~`R7999`) |
| `CT` | virtual counter | ladder | `CT0`~`CT255` |
| `TM` | virtual timer | ladder | `TM0`~`TM255` |

Critical ranges:

- `R81`~`R100` are the MLC parameter window (`3401`~`3420`). The ladder reads
  them; the operator changes parameters on the parameter screen.
- `R193`~`R255` are useful retained numeric data ranges; avoid `R103`~`R192`
  when choosing a tool-number/data area.
- `R640` and above cannot be bit-addressed according to the upstream
  permission table. Use `A` for internal bits.
- `S` must never be a coil, set, or reset target. `C` must not be used as a
  general-purpose flag.

## Address notation

Both short and padded forms are accepted by the SDK parser:

```text
I0 == I000     O10 == O010     A0 == A000
C6 == C006     S29 == S029
R81 == R0081   R600.1 == R0600.01
TM0 == TM000   CT0 == CT000
```

The exact rules and conversion examples are in
`references/address-notation.md`. Use the address family and number from the
reviewed point table, not from a sample program.

The upstream code snippets use the upstream tool's `add_rung` element objects.
They are not this SDK's JSON schema. Translate the circuit structure into
`syntec-lad-plan/v1` or `syntec-lad-change/v1`; the schemas in
`syntec_lad/schema/` are authoritative.

## Hard constraints

- The last logical row must be `END`.
- A network is 10 columns wide in the native layout.
- Bit indices are `0..511`; timer/counter indices are `0..255`.
- A normal coil, a set coil, and a reset coil each have one owner per address.
  Do not create duplicate coils.
- `I0`~`I63` and `O0`~`O63` are one-point-one-meaning. Map an input to an
  `A300+n` flag and drive an output from an `A400+n` flag; do not reuse a
  physical point throughout the program.
- Use `A` for internal flags, `C` for CNC commands, `S` for read-only status,
  and `R` for numeric data. Do not use `R50.x` as a substitute for `A`.
- Do not write parameters into `R81`~`R100`; change `3401`~`3420` instead.
- Comments must use the editor-compatible cp950/Big5 traditional form. Write
  three layers: element comment, function-section comment, and program header.
- Do not hand-build `.lad` bytes when the SDK is unavailable. Install the
  bundled CLI instead.
- Do not treat a successful save as proof of correct logic. Format, topology,
  timing, and point intent are separate checks.

## Comments and encoding

Use `references/panming/comment-encoding.md` and
`references/panming/project-conventions.md`.

- Element comments are short labels.
- Function-section comments explain input, output, parameter, and purpose.
- The program header records title, revision history, parameters, I/O quick
  reference, and cautions.
- Simplified Chinese may display as question marks or corrupt text. Convert to
  traditional Chinese before writing native comments.

## Validation

Use four independent questions:

1. **Parser/format:** does the SDK read back the same records and bytes?
2. **Topology:** does `inspect` show the intended series,
   parallel, coil, timer, counter, and flow structure?
3. **Native syntax:** does the real 32-bit `LadDLL.SyntaxCheck` return
   `syntax_ret == 1` and `error_count == 0`?
4. **Timing/simulation:** does a scan-level simulation or target-machine test
   confirm startup, stop, reset, alarm, and interlock behavior?

The upstream `validate` is advisory. This skill's final gate is the parser
readback plus the native LadDLL result. Do not use `--no-native` for final
delivery unless the user explicitly asks for a parser-only diagnostic.

## Critical anti-patterns

- Treating `C`, `S`, or `R` as a general-purpose intermediate relay.
- Writing a user-adjustable duration, interval, count, or position as a
  hard-coded preset instead of `R81`~`R100`.
- Assuming `R81`~`R100` can be written by the ladder.
- Using the same `I`/`O` point throughout the program instead of mapping once.
- Giving one address multiple coils, or using "save succeeded" as proof.
- Writing simplified Chinese comments and assuming they will display.
- Monitoring a transient pressure/flow/level signal for the entire cycle
  instead of latching "established once this cycle".
- Releasing an alarm on the reset button without checking that the fault source
  has disappeared.
- Converting a jog function to self-holding "for consistency"; jog is
  intentionally hold-to-run.
- Forgetting `M09`/`M30` on coolant, chip conveyor, air-blast, or auxiliary
  pump outputs.
- Putting the oil cooler on `M30`/emergency-stop in the same way as coolant;
  the oil cooler may need to keep running after the spindle stops.
- Treating a single machine's use of rising-edge buttons, compare blocks, or
  non-latching alarms as an editor rule.
- Claiming "logic verified" after format validation without simulation or
  target-machine checks.

The full anti-pattern list is in
`references/panming/upstream-SKILL.md` and
`references/panming/project-conventions.md`.

## Useful commands

```powershell
python $LadCli inspect .\file.lad
python $LadCli lookup C6
python $LadCli lookup "M码"
python $LadCli build .\plan.json .\new.lad
python $LadCli patch .\source.lad .\change.json .\patched.lad
python $LadCli remap .\source.lad .\mapped.lad --mapping-file .\mapping.json --expected-sha256 <sha256>
python $LadCli validate .\file.lad
python $LadCli diff .\before.lad .\after.lad
python $LadCli open .\file.lad
```

`lookup` searches the bundled 602-address C/S/R index by exact address or
keyword. It does not replace `inspect` or a reviewed point table.

## Knowledge routing

| Need | Read |
|---|---|
| Address class, C/S/R tables, permissions | `references/panming/address-map.md` |
| Exact address lookup | `python $LadCli lookup <address>` |
| Contact/coil/timer/counter/compare/flow grammar | `references/panming/element-grammar.md` |
| 12 reusable circuits | `references/panming/patterns.md` |
| Lubrication/hydraulic/chip/coolant/tool magazine/M code | `references/panming/function-library.md` |
| Field conventions and program skeleton | `references/panming/project-conventions.md` |
| Comment encoding | `references/panming/comment-encoding.md` |
| Compact binary layout | `references/panming/lad-format.md` |
| R512~R1023 permissions | `references/panming/R512-R1023-权限表.md` |
| Full opcode/format appendix | `references/panming/TECHNICAL.md` |
| Native plan/change workflow | `references/workflow.md` |

## Native setup

Set these when LadEditor is not in a known location:

```powershell
$env:SYNTEC_LAD_DLL = 'C:\path\to\LadEditor\LadDLL.dll'
$env:SYNTEC_LAD_EDITOR = 'C:\path\to\LadEditor\MLCEdit.exe'
```

Native validation requires Windows, a 32-bit `LadDLL.dll`, and .NET Framework
4.x (`csc.exe`). The SDK builds its own x86 helper on first use. The parser and
most CLI commands work without LadEditor.

## Third-party knowledge

The engineering knowledge under `references/panming/` is adapted from the
MIT-licensed `panming/syntec-plc-ladder` project at commit
`29cde41df688055f11fb7b33a5db782b92c7e4d7`. Keep
`references/panming/LICENSE` and `THIRD_PARTY_NOTICES.md` when redistributing.
The upstream material is a secondary engineering source, not an official
Syntec manual.

## References

- `references/workflow.md` for plan/change examples, hash-pinned patching,
  remapping, and delivery checks.
- `references/knowledge-map.md` for routing and source hierarchy.
- `references/address-notation.md` for short/canonical address conversion.
- `syntec_lad/schema/lad-plan.schema.json` and
  `syntec_lad/schema/lad-change.schema.json` for exact JSON fields.
- `examples/demo.plan.json` and `examples/mvp.plan.json` as format fixtures,
  not machine-ready logic. Their addresses are placeholders.
