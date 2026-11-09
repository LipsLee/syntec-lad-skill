#!/usr/bin/env python3
"""Install the Syntec ladder skill and MCP server into WorkBuddy.

Default target:
  ~/.workbuddy/skills/syntec-lad/
  ~/.workbuddy/mcp.json

The installer backs up an existing skill directory and mcp.json before
changing them. It can also be pointed at another Agent Skills directory or
MCP config with --skills-dir / --mcp-config.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBUDDY = Path.home() / ".workbuddy"
IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.py[cod]",
    ".git",
    ".pytest_cache",
    "dist",
    "build",
    "*.egg-info",
)


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _say(tag: str, message: str) -> None:
    print(f"[{tag}] {message}")


def _check_python() -> bool:
    version = sys.version_info
    if version < (3, 10):
        _say("!!", f"Python {version.major}.{version.minor} 太旧，需要 3.10 以上")
        return False
    _say("OK", f"Python {version.major}.{version.minor}.{version.micro}")
    return True


def _check_cli() -> bool:
    launcher = SKILL_ROOT / "scripts" / "run_syntec_lad.py"
    if not launcher.is_file():
        _say("!!", f"找不到启动器：{launcher}")
        return False
    result = subprocess.run(
        [sys.executable, str(launcher), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        _say("!!", f"CLI 自检失败：{output}")
        return False
    _say("OK", f"CLI 可用：{output}")
    return True


def _check_mcp() -> bool:
    server = SKILL_ROOT / "scripts" / "syntec_lad_mcp.py"
    if not server.is_file():
        _say("!!", f"找不到 MCP 适配器：{server}")
        return False
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ensure_ascii=False,
    )
    result = subprocess.run(
        [sys.executable, str(server)],
        input=request + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    line = (result.stdout or "").splitlines()
    if result.returncode != 0 or not line:
        _say("!!", f"MCP 自检失败：{(result.stderr or result.stdout).strip()}")
        return False
    try:
        payload = json.loads(line[0])
        name = payload["result"]["serverInfo"]["name"]
    except (json.JSONDecodeError, KeyError, TypeError):
        _say("!!", f"MCP 返回格式异常：{line[0]}")
        return False
    _say("OK", f"MCP 适配器可用：{name}")
    return True


def _backup(path: Path, backup_root: Path | None = None) -> Path | None:
    if not path.exists():
        return None
    root = backup_root if backup_root is not None else path.parent
    root.mkdir(parents=True, exist_ok=True)
    backup = root / f"{path.name}.bak-{_stamp()}"
    if path.is_dir():
        shutil.move(str(path), str(backup))
    else:
        shutil.copy2(path, backup)
    return backup


def _skill_backup_root(skill_dir: Path) -> Path:
    return skill_dir.parent.parent / f"{skill_dir.parent.name}-backups"


def _install_skill(skill_dir: Path) -> None:
    skill_dir.parent.mkdir(parents=True, exist_ok=True)
    source = SKILL_ROOT.resolve()
    target = skill_dir.resolve()
    if target == source:
        _say("OK", f"目标就是当前 Skill 目录，跳过复制：{skill_dir}")
        return
    if source in target.parents:
        raise RuntimeError("目标 Skill 目录不能放在源 Skill 目录内部")
    backup = _backup(skill_dir, _skill_backup_root(skill_dir))
    if backup is not None:
        _say("OK", f"旧目录已备份：{backup}")
    shutil.copytree(SKILL_ROOT, skill_dir, ignore=IGNORE)
    count = sum(1 for path in skill_dir.rglob("*") if path.is_file())
    _say("OK", f"Skill 已安装：{skill_dir}（{count} 个文件）")


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} 的 JSON 根节点必须是对象")
    return value


def _install_mcp(config_path: Path, skill_dir: Path) -> None:
    data = _load_config(config_path)
    backup = _backup(config_path) if config_path.exists() else None
    if backup is not None:
        _say("OK", f"MCP 配置已备份：{backup}")

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"{config_path} 的 mcpServers 必须是对象")
    server_path = skill_dir / "scripts" / "syntec_lad_mcp.py"
    servers["syntec-lad"] = {
        "command": str(Path(sys.executable).resolve()),
        "args": [str(server_path.resolve())],
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_name(config_path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, config_path)
    _say("OK", f"MCP 已登记：{config_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install syntec-lad as a WorkBuddy/Agent Skills package and MCP server."
    )
    parser.add_argument("--check", action="store_true", help="只检查环境，不改文件")
    parser.add_argument(
        "--skills-dir",
        help="目标 Skill 目录（默认 ~/.workbuddy/skills/syntec-lad）",
    )
    parser.add_argument(
        "--mcp-config",
        help="目标 MCP 配置（默认 ~/.workbuddy/mcp.json）",
    )
    parser.add_argument("--no-mcp", action="store_true", help="只安装 Skill，不写 MCP 配置")
    args = parser.parse_args(argv)

    skill_dir = (
        Path(args.skills_dir).expanduser().resolve()
        if args.skills_dir
        else (DEFAULT_WORKBUDDY / "skills" / "syntec-lad").resolve()
    )
    config_path = (
        Path(args.mcp_config).expanduser().resolve()
        if args.mcp_config
        else (DEFAULT_WORKBUDDY / "mcp.json").resolve()
    )

    print("Syntec Ladder SDK -> WorkBuddy / Agent Skills")
    print(f"Skill 源目录：{SKILL_ROOT}")
    print(f"目标 Skill：{skill_dir}")
    print(f"MCP 配置：{config_path}")
    print()

    if not _check_python():
        return 1
    if not _check_cli():
        return 1
    if not args.no_mcp and not _check_mcp():
        return 1
    if args.check:
        _say("OK", "检查完成，没有修改任何文件")
        return 0

    try:
        _install_skill(skill_dir)
    except RuntimeError as exc:
        _say("!!", str(exc))
        return 1
    if args.no_mcp:
        _say("OK", "按参数要求跳过 MCP 配置")
    else:
        try:
            _install_mcp(config_path, skill_dir)
        except RuntimeError as exc:
            _say("!!", str(exc))
            return 1

    print()
    _say("OK", "安装完成")
    print()
    print("下一步：")
    print("1. 完全退出并重新打开 WorkBuddy（托盘图标右键退出）。")
    print("2. 到“MCP 服务器”页面，确认 syntec-lad 已出现；如有提示，点允许/信任。")
    print("3. 新对话里让它先调用 explain_capabilities，再开始处理 .lad。")
    print()
    print("原生 LadDLL 校验仍要求 Windows、32 位 LadDLL.dll 和 .NET Framework 4.x。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消，未完成安装。")
        raise SystemExit(130)
