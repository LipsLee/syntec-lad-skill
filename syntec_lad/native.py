"""Thin wrapper around the x86 LadDLL syntax checker.

The installed MLCEdit ships a 32-bit native ``LadDLL.dll``.  A 64-bit Python
process cannot load that DLL directly, so this module builds and invokes a
small x86 C# helper.  The helper is deliberately separate from the document
codec: native validation is an external compatibility check, not the source
of truth for reading or writing ``.lad`` bytes.
"""

from __future__ import annotations

import ctypes
import heapq
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from .errors import NativeValidationError
from .model import LadDocument


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER_SOURCE = PROJECT_ROOT / "native" / "LadSyntaxCheck.cs"
DEFAULT_CHECKER = PROJECT_ROOT / "native" / "bin" / "LadSyntaxCheck.exe"

DEFAULT_DLL_CANDIDATES = (
    Path(r"C:\Program Files (x86)\SYNTEC\MacroDev\Bin\LadDLL.dll"),
    Path(r"C:\Program Files\SYNTEC\MacroDev\Bin\LadDLL.dll"),
    Path(r"C:\Program Files (x86)\SYNTEC\LadEditor\LadDLL.dll"),
    Path(r"C:\Program Files\SYNTEC\LadEditor\LadDLL.dll"),
    PROJECT_ROOT / "native" / "LadDLL.dll",
    PROJECT_ROOT / "native" / "bin" / "LadDLL.dll",
)

DEFAULT_EDITOR_CANDIDATES = (
    Path(r"C:\Program Files (x86)\SYNTEC\MacroDev\Bin\MLCEdit.exe"),
    Path(r"C:\Program Files\SYNTEC\MacroDev\Bin\MLCEdit.exe"),
    Path(r"C:\Program Files (x86)\SYNTEC\LadEditor\MLCEdit.exe"),
    Path(r"C:\Program Files\SYNTEC\LadEditor\MLCEdit.exe"),
    PROJECT_ROOT / "MLCEdit.exe",
)

_NATIVE_SEARCH_FILES = {
    "laddll.dll": "dll",
    "mlcedit.exe": "editor",
}

_NATIVE_SEARCH_HINTS = (
    ("syntec", 140),
    ("ladeditor", 120),
    ("mlcedit", 100),
    ("macrodev", 80),
    ("ladder", 50),
    ("cnc", 30),
)

_NATIVE_SEARCH_SKIP_DIRS = {
    "$recycle.bin",
    ".cache",
    ".git",
    ".hg",
    ".pnpm-store",
    ".svn",
    ".venv",
    "__pycache__",
    "appdata",
    "cache",
    "caches",
    "deliveryoptimization",
    "node_modules",
    "programdata",
    "recovery",
    "system volume information",
    "temp",
    "tmp",
    "venv",
    "windows",
    "windowsapps",
    "wpsystem",
    "wudownloadcache",
}

_NATIVE_SEARCH_MAX_DEPTH = 7
_NATIVE_SEARCH_MAX_DIRECTORIES = 15000
_NATIVE_SEARCH_UNRESTRICTED_DEPTH = 4
_DRIVE_FIXED = 3

_SHARP_PATH_CANDIDATES = (
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
)

_NUMBER_RE = re.compile(r"^(syntax_ret|error_count|error_line|error_type)=(-?\d+)$")


def _first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _native_discovery_cache_path() -> Path | None:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if not base:
        return None
    return Path(base) / "syntec-lad" / "native-discovery.json"


def _load_native_discovery_cache() -> dict[str, Path]:
    cache_path = _native_discovery_cache_path()
    if cache_path is None or not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[str, Path] = {}
    for key in ("dll", "editor"):
        value = payload.get(key)
        if isinstance(value, str):
            candidate = Path(value)
            if candidate.is_file():
                result[key] = candidate
    return result


def _save_native_discovery_cache(paths: dict[str, Path]) -> None:
    cache_path = _native_discovery_cache_path()
    if cache_path is None or not paths:
        return
    payload = {key: str(path) for key, path in paths.items() if path.is_file()}
    if not payload:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, cache_path)
    except OSError:
        return


def _fixed_drive_roots() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return ()
    roots: list[Path] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = chr(ord("A") + index)
        if letter == "C":
            continue
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) == _DRIVE_FIXED:
            roots.append(Path(root))
    return tuple(roots)


def _native_search_score(name: str) -> int:
    folded = name.casefold()
    score = 0
    for hint, weight in _NATIVE_SEARCH_HINTS:
        if hint in folded:
            score += weight
    return score


def _search_native_files(
    roots: Iterable[Path],
    *,
    max_depth: int = _NATIVE_SEARCH_MAX_DEPTH,
    max_directories: int = _NATIVE_SEARCH_MAX_DIRECTORIES,
) -> dict[str, Path]:
    """Search fixed-drive installs without scanning the whole disk blindly."""

    pending: list[tuple[int, int, int, Path]] = []
    sequence = 0
    seen: set[str] = set()
    found: dict[str, Path] = {}

    def push(directory: Path, depth: int) -> None:
        nonlocal sequence
        key = os.path.normcase(str(directory))
        if key in seen:
            return
        seen.add(key)
        score = _native_search_score(directory.name)
        heapq.heappush(pending, (-score, depth, sequence, directory))
        sequence += 1

    for root in roots:
        push(root, 0)

    visited = 0
    while pending and visited < max_directories and len(found) < len(_NATIVE_SEARCH_FILES):
        _, depth, _, directory = heapq.heappop(pending)
        visited += 1
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    key = _NATIVE_SEARCH_FILES.get(entry.name.casefold())
                    if key is not None and key not in found:
                        found[key] = Path(entry.path).resolve()
                    continue
                if depth >= max_depth or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            folded = entry.name.casefold()
            if folded in _NATIVE_SEARCH_SKIP_DIRS or folded.startswith("."):
                continue
            next_depth = depth + 1
            if (
                next_depth > _NATIVE_SEARCH_UNRESTRICTED_DEPTH
                and _native_search_score(entry.name) == 0
            ):
                continue
            push(Path(entry.path), next_depth)
        if len(found) == len(_NATIVE_SEARCH_FILES):
            break
    return found


def _discover_native_files() -> dict[str, Path]:
    found = _load_native_discovery_cache()
    missing = set(_NATIVE_SEARCH_FILES.values()) - set(found)
    if not missing:
        return found
    discovered = _search_native_files(_fixed_drive_roots())
    for key, path in discovered.items():
        if key in missing:
            found[key] = path
    if discovered:
        _save_native_discovery_cache(found)
    return found


def find_lad_dll(dll_path: Path | str | None = None) -> Path:
    if dll_path is not None:
        candidate = Path(dll_path).resolve()
        if not candidate.is_file():
            raise NativeValidationError(f"LadDLL.dll does not exist: {candidate}")
        return candidate
    env_path = os.environ.get("SYNTEC_LAD_DLL")
    if env_path:
        candidate = Path(env_path).resolve()
        if not candidate.is_file():
            raise NativeValidationError(f"SYNTEC_LAD_DLL does not exist: {candidate}")
        return candidate
    found = _first_existing(DEFAULT_DLL_CANDIDATES)
    if found is not None:
        return found.resolve()
    discovered = _discover_native_files().get("dll")
    if discovered is not None:
        return discovered
    raise NativeValidationError(
        "LadDLL.dll was not found in standard paths or on local fixed drives; "
        "set SYNTEC_LAD_DLL or pass --dll"
    )


def find_editor(editor_path: Path | str | None = None) -> Path:
    if editor_path is not None:
        candidate = Path(editor_path).resolve()
        if not candidate.is_file():
            raise NativeValidationError(f"MLCEdit.exe does not exist: {candidate}")
        return candidate
    env_path = os.environ.get("SYNTEC_LAD_EDITOR")
    if env_path:
        candidate = Path(env_path).resolve()
        if not candidate.is_file():
            raise NativeValidationError(f"SYNTEC_LAD_EDITOR does not exist: {candidate}")
        return candidate
    found = _first_existing(DEFAULT_EDITOR_CANDIDATES)
    if found is not None:
        return found.resolve()
    discovered = _discover_native_files().get("editor")
    if discovered is not None:
        return discovered
    raise NativeValidationError(
        "MLCEdit.exe was not found in standard paths or on local fixed drives; "
        "set SYNTEC_LAD_EDITOR or pass --editor"
    )


def ensure_checker(
    checker_path: Path | str | None = None,
    *,
    force: bool = False,
) -> Path:
    checker = Path(checker_path).resolve() if checker_path else DEFAULT_CHECKER
    if checker.is_file() and not force:
        if not CHECKER_SOURCE.exists() or checker.stat().st_mtime >= CHECKER_SOURCE.stat().st_mtime:
            return checker
    if not CHECKER_SOURCE.is_file():
        raise NativeValidationError(f"checker source is missing: {CHECKER_SOURCE}")
    csc = _first_existing(_SHARP_PATH_CANDIDATES)
    if csc is None:
        raise NativeValidationError(
            "C# compiler csc.exe was not found; install .NET Framework 4.x or build the checker manually"
        )
    checker.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [
            str(csc),
            "/nologo",
            "/target:exe",
            "/platform:x86",
            f"/out:{checker}",
            str(CHECKER_SOURCE),
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.returncode != 0 or not checker.is_file():
        detail = (process.stdout or "") + (process.stderr or "")
        raise NativeValidationError(
            f"failed to build native checker (exit {process.returncode}): {detail.strip()}"
        )
    return checker


def _parse_checker_output(output: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in output.splitlines():
        match = _NUMBER_RE.match(line.strip())
        if match:
            values[match.group(1)] = int(match.group(2))
    return values


def validate_native(
    path: Path | str,
    *,
    dll_path: Path | str | None = None,
    checker_path: Path | str | None = None,
    timeout: int = 30,
) -> dict[str, object]:
    """Run the real LadDLL syntax check against a saved ``.lad`` file."""

    lad = Path(path).resolve()
    if not lad.is_file():
        raise NativeValidationError(f"ladder file does not exist: {lad}")
    dll = find_lad_dll(dll_path)
    checker = ensure_checker(checker_path)
    try:
        process = subprocess.run(
            [str(checker), str(lad), str(dll)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise NativeValidationError(
            f"native checker timed out after {timeout} seconds"
        ) from exc
    except OSError as exc:
        raise NativeValidationError(
            f"native checker could not start: {exc}"
        ) from exc
    values = _parse_checker_output((process.stdout or "") + "\n" + (process.stderr or ""))
    if process.returncode not in (0, 1):
        detail = (process.stdout or "").strip() + " " + (process.stderr or "").strip()
        raise NativeValidationError(
            f"native checker could not run (exit {process.returncode}): {detail.strip()}"
        )
    required = {"syntax_ret", "error_count"}
    if not required.issubset(values):
        raise NativeValidationError(
            "native checker returned an incomplete result: "
            + ((process.stdout or "") + (process.stderr or "")).strip()
        )
    passed = values.get("syntax_ret") == 1 and values.get("error_count") == 0
    result: dict[str, object] = {
        "passed": passed,
        "syntax_ret": values.get("syntax_ret"),
        "error_count": values.get("error_count"),
        "error_line": values.get("error_line"),
        "error_type": values.get("error_type"),
        "checker": str(checker),
        "dll": str(dll),
    }
    if not passed:
        raise NativeValidationError(
            "LadDLL rejected the ladder: "
            f"error_count={result['error_count']} "
            f"error_line={result['error_line']} "
            f"error_type={result['error_type']}"
        )
    return result


def validate_document(
    document: LadDocument,
    *,
    dll_path: Path | str | None = None,
    checker_path: Path | str | None = None,
    timeout: int = 30,
) -> dict[str, object]:
    """Validate an in-memory document by writing it to a temporary file."""

    with tempfile.TemporaryDirectory(prefix="syntec-lad-native-") as directory:
        path = Path(directory) / "document.lad"
        path.write_bytes(document.to_bytes())
        result = validate_native(
            path,
            dll_path=dll_path,
            checker_path=checker_path,
            timeout=timeout,
        )
        result["temporary_path"] = str(path)
        return result


def open_in_editor(
    path: Path | str,
    *,
    editor_path: Path | str | None = None,
    wait: bool = False,
) -> subprocess.Popen[bytes]:
    """Launch MLCEdit with ``-f`` for human review; never writes the file."""

    lad = Path(path).resolve()
    if not lad.is_file():
        raise NativeValidationError(f"ladder file does not exist: {lad}")
    editor = find_editor(editor_path)
    process = subprocess.Popen(
        [str(editor), "-f", str(lad)],
        cwd=str(editor.parent),
    )
    if wait:
        process.wait()
    return process
