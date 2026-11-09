# Portable Use: Codex, WorkBuddy, and Other Agents

The skill is deliberately split into portable and platform-specific parts.

| Part | Portable? | Notes |
|---|---|---|
| `SKILL.md` | Yes | Standard Agent Skill entry point |
| `references/` | Yes | Plain Markdown, JSON, CSV |
| `scripts/run_syntec_lad.py` | Yes | Any agent that can run Python |
| `scripts/syntec_lad_mcp.py` | Yes | MCP stdio adapter |
| `LadDLL.SyntaxCheck` | Windows only | Requires 32-bit LadDLL.dll and .NET Framework |
| `MLCEdit.exe` review | Windows only | Human review step only |

## WorkBuddy

Install into the WorkBuddy skill and MCP locations:

```powershell
python .\scripts\install_workbuddy.py
```

Or double-click `scripts\install_workbuddy.bat`.

The installer:

1. copies this directory to `~/.workbuddy/skills/syntec-lad/`;
2. backs up an existing skill directory outside the skills scan directory;
3. merges a `syntec-lad` entry into `~/.workbuddy/mcp.json`;
4. backs up an existing `mcp.json`;
5. runs CLI and MCP self-checks before changing anything.

WorkBuddy 5.9.3 reads `~/.workbuddy/mcp.json` and treats an entry with a
`command` as a stdio server. After installation, fully exit WorkBuddy,
reopen it, open the MCP Servers page, and confirm `syntec-lad` is listed.
Approve/trust it if WorkBuddy prompts.

## Other MCP clients

Claude Desktop, Cursor, and other MCP clients can register the same stdio
server. See `mcp-config.example.json` and replace the path:

```json
{
  "mcpServers": {
    "syntec-lad": {
      "command": "python",
      "args": ["C:/path/to/syntec-lad/scripts/syntec_lad_mcp.py"]
    }
  }
}
```

The MCP tools are:

- `inspect_lad`
- `lookup_address`
- `build_plan`
- `patch_lad`
- `validate_lad`
- `diff_lad`
- `read_reference`
- `list_references`
- `explain_capabilities`

## Agent Skills directories

For an agent that reads the Agent Skills layout but does not use WorkBuddy,
copy this whole directory into its skill directory. The default Codex location
is:

```text
~/.codex/skills/syntec-lad/
```

The same folder can be used from any checkout:

```powershell
python .\scripts\run_syntec_lad.py --version
python .\scripts\run_syntec_lad.py inspect .\source.lad
python .\scripts\run_syntec_lad.py lookup C6
```

## Platform boundary

Knowledge lookup, parsing, plan generation, patch preparation, and diffing work
without LadEditor. Final native validation still requires Windows, a 32-bit
`LadDLL.dll`, and .NET Framework 4.x (`csc.exe`). Do not describe a file as
fully validated if only the Python readback ran.

The MCP server never clicks the editor and never uploads to a CNC. `open` is
not exposed as an MCP tool; human review remains an explicit CLI action.
