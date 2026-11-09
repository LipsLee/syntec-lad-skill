"""Simultaneous, record-preserving PLC operand remapping."""

from __future__ import annotations

import hashlib
import struct
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import LadPatchError, LadValidationError
from .model import (
    LadDocument,
    encode_comment_payload,
    iter_comment_blocks,
    read_comment,
    read_comment_payload,
)
from .opcodes import (
    FLOW_OPCODES,
    OPERAND_PLC_BITS,
    STRUCTURAL_OPCODES,
    decode_plc_bits,
    is_comment_record,
    opcode_meta,
    opcode_name,
    parse_plc_device,
)


@dataclass(frozen=True)
class RemapChange:
    record: int
    column: int
    opcode: int
    mnemonic: str
    operand_index: int
    before: str
    after: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "column": self.column,
            "opcode": f"0x{self.opcode:02X}",
            "mnemonic": self.mnemonic,
            "operand_index": self.operand_index,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class RemapResult:
    document: LadDocument
    before_sha256: str
    after_sha256: str
    mapping: tuple[tuple[str, str], ...]
    changes: tuple[RemapChange, ...]
    source_counts: dict[str, int]
    changed_records: int
    comments_untouched: int
    unmatched_sources: tuple[str, ...]

    def as_dict(self, *, include_changes: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "mapping": dict(self.mapping),
            "source_counts": self.source_counts,
            "changed_records": self.changed_records,
            "changed_operands": len(self.changes),
            "comments_untouched": self.comments_untouched,
            "unmatched_sources": list(self.unmatched_sources),
        }
        if include_changes:
            result["changes"] = [change.as_dict() for change in self.changes]
        return result


@dataclass(frozen=True)
class CommentRemapChange:
    record: int
    record_count: int
    before: str
    after: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "record_count": self.record_count,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class CommentRemapResult:
    document: LadDocument
    before_sha256: str
    after_sha256: str
    mapping: tuple[tuple[str, str], ...]
    changes: tuple[CommentRemapChange, ...]
    source_counts: dict[str, int]
    changed_records: int
    comments_untouched: int
    unmatched_sources: tuple[str, ...]

    def as_dict(self, *, include_changes: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "mapping": dict(self.mapping),
            "source_counts": self.source_counts,
            "changed_blocks": len(self.changes),
            "changed_records": self.changed_records,
            "comments_untouched": self.comments_untouched,
            "unmatched_sources": list(self.unmatched_sources),
        }
        if include_changes:
            result["changes"] = [change.as_dict() for change in self.changes]
        return result


def _canonical(value: str) -> str:
    return decode_plc_bits(int(parse_plc_device(value).raw or 0))


def _normalize_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for source, target in mapping.items():
        source_key = _canonical(str(source))
        target_key = _canonical(str(target))
        previous = normalized.get(source_key)
        if previous is not None and previous != target_key:
            raise LadValidationError(
                f"conflicting remap targets for {source_key}: "
                f"{previous} and {target_key}"
            )
        normalized[source_key] = target_key
    return normalized


def _validate_expected_hash(document: LadDocument, expected: str | None) -> None:
    if expected is None:
        return
    expected = expected.strip().lower()
    if len(expected) != 64:
        raise LadValidationError(
            "expected_sha256 must contain exactly 64 hexadecimal characters"
        )
    try:
        int(expected, 16)
    except ValueError as exc:
        raise LadValidationError("expected_sha256 is not hexadecimal") from exc
    actual = document.sha256
    if actual.lower() != expected:
        raise LadPatchError(
            f"source hash mismatch: expected {expected}, got {actual.lower()}"
        )


def _register_display(canonical: str) -> str:
    if not canonical.startswith("R"):
        raise LadValidationError(
            f"comment remap requires register operands, got {canonical}"
        )
    return f"R{int(canonical[1:5])}.{int(canonical[6:])}"


def _comment_address_display(canonical: str) -> str:
    if canonical.startswith("R"):
        return _register_display(canonical)
    if canonical.startswith("O"):
        return f"O{int(canonical[1:])}"
    raise LadValidationError(
        f"comment remap requires O/R bit operands, got {canonical}"
    )


def _normalize_comment_mapping(
    mapping: Mapping[str, str],
    *,
    allowed_prefixes: tuple[str, ...] = ("R",),
    register_only: bool = True,
) -> dict[str, str]:
    normalized = _normalize_mapping(mapping)
    for source, target in normalized.items():
        if source[0] not in allowed_prefixes or target[0] not in allowed_prefixes:
            if register_only:
                raise LadValidationError(
                    "comment remap only accepts register-bit operands"
                )
            raise LadValidationError(
                "comment remap only accepts O/R bit operands"
            )
    if len(set(normalized.values())) != len(normalized):
        raise LadValidationError("comment remap has duplicate target addresses")
    return normalized


def _comment_name(
    payload: bytes,
    *,
    record: int,
) -> tuple[bytes, bytes, str | None]:
    tab = payload.find(b"\t")
    if tab < 0:
        return payload, b"", None
    prefix = payload[:tab]
    try:
        name = prefix.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        return prefix, payload[tab:], None
    try:
        canonical = _canonical(name)
    except LadValidationError:
        return prefix, payload[tab:], None
    if not canonical.startswith(("R", "O")):
        return prefix, payload[tab:], None
    return prefix, payload[tab:], canonical


def _replace_comment_name(prefix: bytes, target: str) -> bytes:
    text = prefix.decode("ascii")
    leading = text[:len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()):]
    return f"{leading}{target}{trailing}".encode("ascii")


def _remap_variable_comment_addresses(
    document: LadDocument,
    mapping: Mapping[str, str],
    *,
    expected_sha256: str | None = None,
    require_all_sources: bool = False,
    allowed_prefixes: tuple[str, ...],
    register_only: bool,
    allow_resize: bool,
) -> CommentRemapResult:
    """Rename addresses inside type-4 variable comments.

    The comment short text, long text, whitespace, type-2 header, line
    comments, and all code records remain byte-for-byte unchanged.  The source
    and target names must have the same encoded length unless allow_resize is
    explicitly enabled.
    """

    _validate_expected_hash(document, expected_sha256)
    normalized = _normalize_comment_mapping(
        mapping,
        allowed_prefixes=allowed_prefixes,
        register_only=register_only,
    )
    if not normalized:
        raise LadValidationError("comment remap mapping must contain at least one entry")

    blocks = iter_comment_blocks(document.records, document.encoding)
    existing_names: set[str] = set()
    for block in blocks:
        if block.comment_type != 4:
            continue
        payload, _ = read_comment_payload(document.records, block.start_record)
        _, _, canonical = _comment_name(payload, record=block.start_record)
        if canonical is not None:
            existing_names.add(canonical)
    for target in normalized.values():
        if target in existing_names and target not in normalized:
            raise LadValidationError(
                f"comment target already exists and is not a source: {target}"
            )

    counts: Counter[str] = Counter()
    changes: list[CommentRemapChange] = []
    records: list[bytes] = []
    changed_records = 0
    comments_untouched = 0
    index = 0
    while index < len(document.records):
        if not is_comment_record(document.records[index]):
            records.append(document.records[index])
            index += 1
            continue

        block = read_comment(document.records, index, document.encoding)
        if block.comment_type != 4:
            records.extend(document.records[index:index + block.record_count])
            comments_untouched += 1
            index += block.record_count
            continue

        payload, count = read_comment_payload(document.records, index)
        prefix, suffix, source = _comment_name(payload, record=index)
        target = normalized.get(source) if source is not None else None
        if target is None:
            records.extend(document.records[index:index + block.record_count])
            comments_untouched += 1
            index += block.record_count
            continue

        target_display = _comment_address_display(target)
        new_prefix = _replace_comment_name(prefix, target_display)
        new_payload = new_prefix + suffix
        new_records = encode_comment_payload(new_payload, 4)
        if not allow_resize and len(new_payload) != len(payload):
            raise LadValidationError(
                f"comment address rename changes payload length at record {index}"
            )
        if not allow_resize and len(new_records) != count:
            raise LadValidationError(
                f"comment address rename changes record count at record {index}"
            )
        changed_records += sum(
            old != new
            for old, new in zip(
                document.records[index:index + count],
                new_records,
            )
        )
        records.extend(new_records)
        counts[source] += 1
        changes.append(
            CommentRemapChange(
                record=index,
                record_count=count,
                before=prefix.decode("ascii").strip(),
                after=target_display,
            )
        )
        index += block.record_count

    unmatched = tuple(source for source in normalized if counts[source] == 0)
    if require_all_sources and unmatched:
        raise LadValidationError(
            "comment remap sources not found: " + ", ".join(unmatched)
        )

    new_document = LadDocument(
        records,
        encoding=document.encoding,
        source_path=document.source_path,
        original_sha256=document.original_sha256,
    )
    return CommentRemapResult(
        document=new_document,
        before_sha256=document.sha256,
        after_sha256=new_document.sha256,
        mapping=tuple(normalized.items()),
        changes=tuple(changes),
        source_counts={source: counts[source] for source in normalized},
        changed_records=changed_records,
        comments_untouched=comments_untouched,
        unmatched_sources=unmatched,
    )


def remap_variable_comments(
    document: LadDocument,
    mapping: Mapping[str, str],
    *,
    expected_sha256: str | None = None,
    require_all_sources: bool = False,
) -> CommentRemapResult:
    """Rename register addresses inside type-4 variable comments."""

    return _remap_variable_comment_addresses(
        document,
        mapping,
        expected_sha256=expected_sha256,
        require_all_sources=require_all_sources,
        allowed_prefixes=("R",),
        register_only=True,
        allow_resize=False,
    )


def remap_variable_comment_addresses(
    document: LadDocument,
    mapping: Mapping[str, str],
    *,
    expected_sha256: str | None = None,
    require_all_sources: bool = False,
    allow_resize: bool = True,
) -> CommentRemapResult:
    """Rename O/R addresses inside type-4 comments, optionally resizing them."""

    return _remap_variable_comment_addresses(
        document,
        mapping,
        expected_sha256=expected_sha256,
        require_all_sources=require_all_sources,
        allowed_prefixes=("O", "R"),
        register_only=False,
        allow_resize=allow_resize,
    )


def remap_plc_bits(
    document: LadDocument,
    mapping: Mapping[str, str],
    *,
    expected_sha256: str | None = None,
    require_all_sources: bool = False,
) -> RemapResult:
    """Remap PLC-bit operands in one simultaneous pass.

    Every operand is resolved from the original record, so a mapping such as
    ``I4 -> I10`` and ``I10 -> I38`` never cascades.  Comments, labels, strings,
    and unknown opcodes are left byte-for-byte unchanged.
    """

    _validate_expected_hash(document, expected_sha256)
    normalized = _normalize_mapping(mapping)
    if not normalized:
        raise LadValidationError("remap mapping must contain at least one entry")

    raw_targets: dict[str, int] = {
        source: int(parse_plc_device(target).raw or 0)
        for source, target in normalized.items()
    }
    counts: Counter[str] = Counter()
    changes: list[RemapChange] = []
    records: list[bytes] = []
    comment_record_indices: set[int] = set()
    for block in iter_comment_blocks(document.records, document.encoding):
        comment_record_indices.update(
            range(block.start_record, block.start_record + block.record_count)
        )

    for record_index, record in enumerate(document.records):
        if (
            record_index in comment_record_indices
            or record[1] in STRUCTURAL_OPCODES
        ):
            records.append(record)
            continue

        mutable = bytearray(record)
        offset = 0
        while offset < 40:
            raw_opcode = record[offset + 1]
            meta = opcode_meta(raw_opcode)
            width = 8 if meta.is_bicell else 4
            if offset + width > 40:
                break
            if (
                raw_opcode not in (0x00, 0x0D)
                and raw_opcode not in FLOW_OPCODES.values()
                and meta.mapped != 0
            ):
                if meta.op1_type == OPERAND_PLC_BITS:
                    _replace_operand(
                        mutable,
                        record_index=record_index,
                        offset=offset,
                        raw_opcode=raw_opcode,
                        operand_index=0,
                        operand_offset=offset + 2,
                        mapping=normalized,
                        raw_targets=raw_targets,
                        counts=counts,
                        changes=changes,
                    )
                if width == 8 and meta.op2_type == OPERAND_PLC_BITS:
                    _replace_operand(
                        mutable,
                        record_index=record_index,
                        offset=offset,
                        raw_opcode=raw_opcode,
                        operand_index=1,
                        operand_offset=offset + 4,
                        mapping=normalized,
                        raw_targets=raw_targets,
                        counts=counts,
                        changes=changes,
                    )
            offset += width
        records.append(bytes(mutable))

    changed_records = len({change.record for change in changes})
    unmatched = tuple(source for source in normalized if counts[source] == 0)
    if require_all_sources and unmatched:
        raise LadValidationError(
            "remap sources not found in operands: " + ", ".join(unmatched)
        )

    new_document = LadDocument(
        records,
        encoding=document.encoding,
        source_path=document.source_path,
        original_sha256=document.original_sha256,
    )
    after_sha = hashlib.sha256(new_document.to_bytes()).hexdigest()
    return RemapResult(
        document=new_document,
        before_sha256=document.sha256,
        after_sha256=after_sha,
        mapping=tuple(normalized.items()),
        changes=tuple(changes),
        source_counts={
            source: counts[source] for source in normalized
        },
        changed_records=changed_records,
        comments_untouched=len(
            iter_comment_blocks(document.records, document.encoding)
        ),
        unmatched_sources=unmatched,
    )


def _replace_operand(
    mutable: bytearray,
    *,
    record_index: int,
    offset: int,
    raw_opcode: int,
    operand_index: int,
    operand_offset: int,
    mapping: Mapping[str, str],
    raw_targets: Mapping[str, int],
    counts: Counter[str],
    changes: list[RemapChange],
) -> None:
    current_raw = struct.unpack_from("<H", mutable, operand_offset)[0]
    current = decode_plc_bits(current_raw)
    target = mapping.get(current)
    if target is None:
        return
    counts[current] += 1
    if current == target:
        return
    struct.pack_into("<H", mutable, operand_offset, raw_targets[current])
    changes.append(
        RemapChange(
            record=record_index,
            column=offset // 4,
            opcode=raw_opcode,
            mnemonic=opcode_name(raw_opcode),
            operand_index=operand_index,
            before=current,
            after=target,
        )
    )
