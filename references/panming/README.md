# Upstream Engineering Knowledge Snapshot

This directory contains the engineering-knowledge layer adapted from the
public `panming/syntec-plc-ladder` project. The upstream documents are kept in
their original form so that their wording, examples, and provenance markers
remain auditable.

## Source

- Repository: https://github.com/panming/syntec-plc-ladder
- Commit: `29cde41df688055f11fb7b33a5db782b92c7e4d7`
- Commit date: 2026-11-04
- License: MIT, copyright (c) 2026 syntec-plc-ladder contributors
- License copy: `LICENSE` in this directory

## What is here

| File | Purpose |
|---|---|
| `address-map.md` | 8 element classes, C/S/R tables, parameter ranges, common misuse |
| `element-grammar.md` | contacts, coils, timers, counters, arithmetic, compare, flow |
| `patterns.md` | 12 reusable machine-process patterns |
| `function-library.md` | lubrication, hydraulic, chip conveyor/cooling, tool magazine, M code |
| `project-conventions.md` | field conventions, program skeleton, comments, disproven assumptions |
| `comment-encoding.md` | cp950/Big5 comments and simplified-to-traditional conversion |
| `lad-format.md` | compact `.lad` byte-layout reference |
| `R512-R1023-权限表.md` | R-register write/read permissions and bit-access rules |
| `TECHNICAL.md` | full file-format and opcode appendix |
| `address-db.json` | upstream reverse address database |
| `address-index.md` / `.csv` | flattened 602-address index for browsing and Excel |
| `upstream-SKILL.md` | original upstream skill instructions and full anti-pattern list |
| `upstream-README.md` | original upstream installation notes, for comparison only |

## How to use it with this skill

1. Read `../knowledge-map.md` first to choose the smallest relevant reference.
2. Use the native SDK workflow in `../../SKILL.md` for every write.
3. Convert the upstream short address notation to the SDK canonical notation
   when needed; see `../address-notation.md`.
4. Treat upstream `validate` as advisory. This skill's delivery gate is the
   Python parser readback plus the native `LadDLL.SyntaxCheck`.
5. Do not copy machine-specific addresses, customer data, or untested
   "single-machine habits" into a new program.

## Provenance discipline

The upstream documents deliberately distinguish:

- manual/specification statements;
- editor or real-machine observations;
- inference;
- single-machine convention.

Keep those labels when adapting a rule. A rule marked as inference or a
single-machine habit is not an editor requirement.

## Attribution

These files are third-party MIT-licensed material. Keep this directory and
`LICENSE` intact when redistributing the skill. See
`../../THIRD_PARTY_NOTICES.md` for the project-level notice.
