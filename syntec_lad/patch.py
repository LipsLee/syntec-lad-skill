"""Hash-pinned, record-preserving patches for .lad files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compiler import compile_networks
from .errors import LadPatchError, LadValidationError
from .inspection import inspect_document, rows_connected
from .model import LadDocument, iter_comment_blocks
from .opcodes import is_comment_record


CHANGE_SCHEMA = "syntec-lad-change/v1"
_STRUCTURAL = {0xCF, 0xD0, 0xD5, 0xFD}


@dataclass(frozen=True)
class PatchOperation:
    op: str
    effective_start: int
    effective_end: int
    new_records: tuple[bytes, ...]
    order: int


@dataclass(frozen=True)
class PatchResult:
    document: LadDocument
    before_sha256: str
    after_sha256: str
    operations: tuple[dict[str, Any], ...]


def _hash_records(records: list[bytes], start: int, end: int) -> str:
    if start > end:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(b"".join(records[start:end + 1])).hexdigest()


def _record_range(operation: dict[str, Any], index: int) -> tuple[int, int]:
    if "record_start" not in operation or "record_end" not in operation:
        raise LadPatchError(
            f"operation {index} requires record_start and record_end"
        )
    try:
        start = int(operation["record_start"])
        end = int(operation["record_end"])
    except (TypeError, ValueError) as exc:
        raise LadPatchError(f"operation {index} has an invalid record range") from exc
    return start, end


def _expected_hash(operation: dict[str, Any], index: int) -> str:
    value = operation.get("expected_sha256", operation.get("target_sha256"))
    if not isinstance(value, str) or len(value) != 64:
        raise LadPatchError(
            f"operation {index} requires a 64-character expected_sha256"
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise LadPatchError(f"operation {index} has an invalid expected_sha256") from exc
    return value.lower()


def _comment_ranges(records: list[bytes], encoding: str) -> list[tuple[int, int, int]]:
    return [
        (comment.start_record, comment.end_record, comment.comment_type)
        for comment in iter_comment_blocks(records, encoding)
    ]


def _validate_target_range(
    records: list[bytes],
    start: int,
    end: int,
    *,
    encoding: str,
    index: int,
) -> None:
    if start < 0 or end < start or end >= len(records):
        raise LadPatchError(
            f"operation {index} range {start}..{end} is outside the document"
        )
    ranges = _comment_ranges(records, encoding)
    for comment_start, comment_end, _ in ranges:
        intersects = start <= comment_end and comment_start <= end
        if intersects and (start > comment_start or end < comment_end):
            raise LadPatchError(
                f"operation {index} partially selects comment block "
                f"{comment_start}..{comment_end}"
            )

    for record_index in range(start, end + 1):
        record = records[record_index]
        if is_comment_record(record):
            continue
        if record[1] in _STRUCTURAL:
            raise LadPatchError(
                f"operation {index} targets structural {record[1]:02X} record {record_index}"
            )

    code_indices = [
        record_index
        for record_index in range(start, end + 1)
        if not is_comment_record(records[record_index])
    ]
    if not code_indices:
        return
    first_code = code_indices[0]
    last_code = code_indices[-1]
    if first_code > 0 and not is_comment_record(records[first_code - 1]):
        if records[first_code - 1][1] not in _STRUCTURAL and rows_connected(
            records[first_code - 1], records[first_code]
        ):
            raise LadPatchError(
                f"operation {index} starts inside a vertically connected rung "
                f"(record {first_code})"
            )
    if last_code + 1 < len(records) and not is_comment_record(records[last_code + 1]):
        if records[last_code + 1][1] not in _STRUCTURAL and rows_connected(
            records[last_code], records[last_code + 1]
        ):
            raise LadPatchError(
                f"operation {index} ends inside a vertically connected rung "
                f"(record {last_code})"
            )


def _validate_boundary(
    records: list[bytes],
    boundary: int,
    *,
    encoding: str,
    index: int,
) -> None:
    if boundary < 0 or boundary > len(records):
        raise LadPatchError(f"operation {index} insertion boundary is outside the file")
    for comment_start, comment_end, _ in _comment_ranges(records, encoding):
        if comment_start < boundary <= comment_end:
            raise LadPatchError(
                f"operation {index} inserts inside comment block "
                f"{comment_start}..{comment_end}"
            )
    if 0 < boundary < len(records):
        left = records[boundary - 1]
        right = records[boundary]
        if not is_comment_record(left) and not is_comment_record(right):
            if left[1] not in _STRUCTURAL and right[1] not in _STRUCTURAL:
                if rows_connected(left, right):
                    raise LadPatchError(
                        f"operation {index} inserts inside a vertically connected rung"
                    )


def _new_network_records(
    operation: dict[str, Any],
    *,
    encoding: str,
    index: int,
    labels: set[str],
) -> list[bytes]:
    networks = operation.get("networks")
    if not isinstance(networks, list) or not networks:
        raise LadPatchError(f"operation {index} requires a non-empty networks array")
    for network in networks:
        if not isinstance(network, dict):
            continue
        actions = network.get("actions", [])
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            op = str(action.get("op", "")).upper()
            if op not in ("JMP", "JSR"):
                continue
            target = str(action.get("target", action.get("text", ""))).upper()
            if target not in labels:
                raise LadPatchError(
                    f"operation {index} {op} target {target!r} "
                    "does not name an existing LAB record"
                )
    try:
        return compile_networks(networks, encoding=encoding)
    except LadValidationError as exc:
        raise LadPatchError(f"operation {index} network compilation failed: {exc}") from exc


def _prepare_operations(
    document: LadDocument,
    change: dict[str, Any],
) -> list[PatchOperation]:
    operations_raw = change.get("operations")
    if not isinstance(operations_raw, list) or not operations_raw:
        raise LadPatchError("change.operations must be a non-empty array")
    records = document.records
    operations: list[PatchOperation] = []
    occupied: list[tuple[int, int, int]] = []
    labels = {
        str(label["name"]).upper()
        for label in inspect_document(document)["labels"]
    }

    for order, raw in enumerate(operations_raw):
        if not isinstance(raw, dict):
            raise LadPatchError(f"operation {order} must be an object")
        op = str(raw.get("op", "")).lower()
        if op not in ("insert", "replace", "delete"):
            raise LadPatchError(f"operation {order} has unsupported op {op!r}")
        expected = _expected_hash(raw, order)

        if op == "insert":
            new_records = _new_network_records(
                raw,
                encoding=document.encoding,
                index=order,
                labels=labels,
            )
            if "record_start" in raw or "record_end" in raw:
                start, end = _record_range(raw, order)
                _validate_target_range(
                    records,
                    start,
                    end,
                    encoding=document.encoding,
                    index=order,
                )
                actual = _hash_records(records, start, end)
                if actual != expected:
                    raise LadPatchError(
                        f"operation {order} target hash mismatch: expected {expected}, got {actual}"
                    )
                position = str(raw.get("position", "before")).lower()
                if position not in ("before", "after"):
                    raise LadPatchError(
                        f"operation {order} insert position must be before or after"
                    )
                boundary = start if position == "before" else end + 1
                effective_start, effective_end = boundary, boundary - 1
            else:
                if "at_record" not in raw:
                    raise LadPatchError(
                        f"operation {order} insert requires at_record or record_start/record_end"
                    )
                try:
                    boundary = int(raw["at_record"])
                except (TypeError, ValueError) as exc:
                    raise LadPatchError(
                        f"operation {order} has an invalid at_record"
                    ) from exc
                if boundary < 0 or boundary > len(records):
                    raise LadPatchError(
                        f"operation {order} at_record is outside the document"
                    )
                if boundary < len(records):
                    actual = _hash_records(records, boundary, boundary)
                elif records:
                    actual = _hash_records(records, len(records) - 1, len(records) - 1)
                else:
                    actual = hashlib.sha256(b"").hexdigest()
                if actual != expected:
                    raise LadPatchError(
                        f"operation {order} anchor hash mismatch: expected {expected}, got {actual}"
                    )
                effective_start, effective_end = boundary, boundary - 1
            _validate_boundary(
                records,
                effective_start,
                encoding=document.encoding,
                index=order,
            )
        else:
            start, end = _record_range(raw, order)
            _validate_target_range(
                records,
                start,
                end,
                encoding=document.encoding,
                index=order,
            )
            actual = _hash_records(records, start, end)
            if actual != expected:
                raise LadPatchError(
                    f"operation {order} target hash mismatch: expected {expected}, got {actual}"
                )
            effective_start, effective_end = start, end
            new_records = (
                _new_network_records(
                    raw,
                    encoding=document.encoding,
                    index=order,
                    labels=labels,
                )
                if op == "replace"
                else []
            )

        occupied.append((effective_start, effective_end, order))
        operations.append(
            PatchOperation(
                op=op,
                effective_start=effective_start,
                effective_end=effective_end,
                new_records=tuple(new_records),
                order=order,
            )
        )

    for left_index, left in enumerate(occupied):
        for right in occupied[left_index + 1:]:
            if left[0] <= right[1] and right[0] <= left[1]:
                raise LadPatchError(
                    f"operations {left[2]} and {right[2]} overlap"
                )
    return operations


def apply_change_plan(
    document: LadDocument | Path | str,
    change: dict[str, Any],
    *,
    return_result: bool = False,
) -> LadDocument | PatchResult:
    """Apply a reviewed, hash-pinned change document.

    ``base_sha256`` is mandatory.  Every operation also carries its own
    ``expected_sha256``.  The original records outside those exact ranges are
    never reconstructed or re-encoded, so they remain byte-for-byte identical.
    """

    if isinstance(document, (Path, str)):
        document = LadDocument.load(document)
    if not isinstance(document, LadDocument):
        raise LadValidationError("document must be a LadDocument or path")
    if not isinstance(change, dict):
        raise LadPatchError("change must be a JSON object")
    if change.get("schema") != CHANGE_SCHEMA:
        raise LadPatchError(
            f"unsupported change schema: {change.get('schema')!r}; expected {CHANGE_SCHEMA}"
        )
    base_hash = change.get("base_sha256")
    if not isinstance(base_hash, str) or len(base_hash) != 64:
        raise LadPatchError("change.base_sha256 is required")
    before = document.sha256
    if before.lower() != base_hash.lower():
        raise LadPatchError(
            f"change base hash mismatch: expected {base_hash.lower()}, got {before}"
        )

    operations = _prepare_operations(document, change)

    # Rebuild from original record coordinates.  Mutating a list bottom-up is
    # not sufficient when an insertion and a replacement share the same
    # boundary: processing the replacement second would delete the insertion.
    insertions: dict[int, list[PatchOperation]] = {}
    replacements: dict[int, PatchOperation] = {}
    for operation in operations:
        if operation.op == "insert":
            insertions.setdefault(operation.effective_start, []).append(operation)
        else:
            replacements[operation.effective_start] = operation

    records: list[bytes] = []
    index = 0
    record_count = len(document.records)
    while index <= record_count:
        for operation in sorted(
            insertions.get(index, []),
            key=lambda item: item.order,
        ):
            records.extend(operation.new_records)
        if index == record_count:
            break
        operation = replacements.get(index)
        if operation is None:
            records.append(document.records[index])
            index += 1
            continue
        records.extend(operation.new_records)
        index = operation.effective_end + 1

    result_document = LadDocument(
        records,
        encoding=document.encoding,
        source_path=document.source_path,
    )
    result_document.original_sha256 = before
    after = result_document.sha256
    result = PatchResult(
        document=result_document,
        before_sha256=before,
        after_sha256=after,
        operations=tuple(
            {
                "op": operation.op,
                "effective_start": operation.effective_start,
                "effective_end": operation.effective_end,
                "record_count": len(operation.new_records),
            }
            for operation in operations
        ),
    )
    return result if return_result else result.document


def apply_change_plan_file(
    source_path: Path | str,
    change: dict[str, Any],
    output_path: Path | str,
    *,
    overwrite: bool = False,
    in_place: bool = False,
) -> dict[str, Any]:
    document = LadDocument.load(source_path)
    result = apply_change_plan(document, change, return_result=True)
    assert isinstance(result, PatchResult)
    save_result = result.document.save(
        output_path,
        overwrite=overwrite,
        in_place=in_place,
    )
    save_result.update(
        {
            "before_sha256": result.before_sha256,
            "after_sha256": result.after_sha256,
            "operations": result.operations,
        }
    )
    return save_result
