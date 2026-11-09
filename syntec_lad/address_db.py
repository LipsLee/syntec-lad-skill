"""Bundled engineering address lookup.

The lookup data is a reverse index from the MIT-licensed
``panming/syntec-plc-ladder`` knowledge base. It is a planning aid, not a
substitute for the target machine's reviewed point table or the native
LadDLL syntax check.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import LadValidationError

_ADDRESS_RE = re.compile(r"^([CSR])(\d{1,5})(?:\.(\d{1,2}))?$")


def _candidate_paths() -> list[Path]:
    package_root = Path(__file__).resolve().parent
    return [
        package_root / "data" / "address-db.json",
        package_root.parent / "references" / "panming" / "address-db.json",
        Path.cwd() / "references" / "panming" / "address-db.json",
    ]


def default_address_db_path() -> Path:
    """Return the first bundled address database found."""

    for path in _candidate_paths():
        if path.is_file():
            return path
    looked_at = "\n".join(f"  - {path}" for path in _candidate_paths())
    raise LadValidationError(
        "bundled address database not found; looked at:\n" + looked_at
    )


def _normalise_descriptions(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    return [str(value)] if str(value).strip() else []


@lru_cache(maxsize=4)
def _load_cached(path_text: str) -> dict[str, list[str]]:
    path = Path(path_text)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise LadValidationError(f"cannot read address database {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LadValidationError(f"invalid address database JSON {path}: {exc}") from exc

    index = raw.get("reverse_index") if isinstance(raw, dict) else None
    if not isinstance(index, dict):
        raise LadValidationError(
            f"address database {path} has no reverse_index object"
        )

    result: dict[str, list[str]] = {}
    for key, value in index.items():
        address = str(key).strip()
        descriptions = _normalise_descriptions(value)
        if address and descriptions:
            result[address] = descriptions
    return result


def load_address_db(path: str | Path | None = None) -> dict[str, list[str]]:
    """Load the reverse address index."""

    selected = Path(path) if path is not None else default_address_db_path()
    return _load_cached(str(selected.resolve()))


def _canonical_address(value: str) -> tuple[str, int, int | None] | None:
    match = _ADDRESS_RE.fullmatch(value.strip().upper())
    if not match:
        return None
    bit_text = match.group(3)
    return (
        match.group(1),
        int(match.group(2)),
        int(bit_text) if bit_text is not None else None,
    )


def _sort_key(hit: dict[str, Any]) -> tuple[int, int, int]:
    order = {"C": 0, "S": 1, "R": 2}
    canonical = _canonical_address(str(hit["address"]))
    if canonical is None:
        return (99, 0, 0)
    return (
        order.get(canonical[0], 99),
        canonical[1],
        -1 if canonical[2] is None else canonical[2],
    )


def _make_hit(address: str, descriptions: list[str], match: str) -> dict[str, Any]:
    canonical = _canonical_address(address)
    return {
        "address": address,
        "type": canonical[0] if canonical else address[:1].upper(),
        "match": match,
        "descriptions": descriptions,
    }


def lookup_address(
    query: str,
    *,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Look up an exact C/S/R address or search descriptions by keyword.

    Exact queries accept short or padded forms, for example ``C6``, ``C006``,
    ``R600.1``, and ``R0600.01``. Keyword search is case-insensitive and
    matches both address and description text.
    """

    text = query.strip()
    if not text:
        raise LadValidationError("lookup query must not be empty")
    if limit < 1:
        raise LadValidationError("lookup --limit must be at least 1")

    index = load_address_db(db_path)
    canonical = _canonical_address(text)
    hits: list[dict[str, Any]] = []

    if canonical is not None:
        for address, descriptions in index.items():
            current = _canonical_address(address)
            if current is None:
                continue
            if current[0] != canonical[0] or current[1] != canonical[1]:
                continue
            if canonical[2] is not None and current[2] != canonical[2]:
                continue
            hits.append(_make_hit(address, descriptions, "address"))
    else:
        needle = text.casefold()
        for address, descriptions in index.items():
            haystack = [address.casefold(), *(item.casefold() for item in descriptions)]
            if any(needle in item for item in haystack):
                hits.append(_make_hit(address, descriptions, "keyword"))

    hits.sort(key=_sort_key)
    return hits[:limit]


__all__ = [
    "default_address_db_path",
    "load_address_db",
    "lookup_address",
]
