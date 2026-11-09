# Changelog

## 0.2.0 - 2026-11-09

### Added

- MIT-licensed engineering knowledge snapshot from
  `panming/syntec-plc-ladder` under `references/panming/`.
- 602-address C/S/R index in Markdown and CSV form.
- `syntec-lad lookup <address|keyword>` for the bundled address index.
- Dependency-free MCP stdio adapter for WorkBuddy, Claude Desktop, Cursor,
  and other MCP clients.
- WorkBuddy/Agent Skills installer with backup and self-check behavior.
- Address notation adapter for short upstream forms and SDK canonical forms.
- Knowledge routing, source hierarchy, and upstream attribution notices.
- Expanded `SKILL.md` workflow covering address classes, process patterns,
  field conventions, comments, validation, and critical anti-patterns.

### Preserved

- Native `.lad` parsing, deterministic JSON plans, hash-pinned patches,
  remapping, Python readback, and the native `LadDLL.SyntaxCheck` gate.
- Byte-for-byte preservation of unknown records and the existing safety
  boundaries.

### Tests

- `python -m unittest discover -s tests -v`
- 64 tests passed, including native validation and MCP tests.
