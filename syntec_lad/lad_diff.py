"""Segment-aligned diff for Syntec .lad documents.

The legacy diff compared records by list index, so a single inserted record
cascaded through every following position.  This module aligns the two files
by *segments* -- comment blocks, network candidates, and structural records --
using the longest common subsequence over segment content hashes, then groups
the result into unchanged / modified / added / removed / moved.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from typing import Any

from .inspection import inspect_document, _one_line
from .model import LadDocument

DIFF_SCHEMA = "syntec-lad-diff/v2"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _Segment:
    kind: str  # "comment" | "network" | "structural"
    start: int
    end: int
    sha256: str
    preview: str
    program: str | None = None
    comments: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.sha256

    def summary(self) -> str:
        if self.kind == "comment":
            return f"COMMENT {_one_line(self.preview)}"
        return self.preview


def _segments_for(document: LadDocument) -> list[_Segment]:
    inspection = inspect_document(document)
    records = document.records
    covered: set[int] = set()
    segments: list[_Segment] = []

    for comment in inspection["comments"]:
        start = int(comment["record_start"])
        end = int(comment["record_end"])
        covered.update(range(start, end + 1))
        segments.append(
            _Segment(
                kind="comment",
                start=start,
                end=end,
                sha256=_sha256(b"".join(records[start : end + 1])),
                preview=comment["text"],
            )
        )

    for candidate in inspection["network_candidates"]:
        start = int(candidate["record_start"])
        end = int(candidate["record_end"])
        covered.update(range(start, end + 1))
        leading = [
            _one_line(item["text"]) for item in candidate.get("leading_comments", [])
        ]
        segments.append(
            _Segment(
                kind="network",
                start=start,
                end=end,
                sha256=candidate["sha256"],
                preview=candidate["preview"],
                program=candidate.get("program"),
                comments=leading,
            )
        )

    # Anything left over is structural (END/LAB/RTS/...) or an unparsed gap;
    # emit it as a structural segment so alignment still covers every record.
    from .opcodes import opcode_name

    index = 0
    while index < len(records):
        if index in covered:
            index += 1
            continue
        run_end = index
        while run_end + 1 < len(records) and run_end + 1 not in covered:
            run_end += 1
        mnemonic = opcode_name(records[index][1])
        segments.append(
            _Segment(
                kind="structural",
                start=index,
                end=run_end,
                sha256=_sha256(b"".join(records[index : run_end + 1])),
                preview=f"STRUCT {mnemonic}",
            )
        )
        index = run_end + 1

    segments.sort(key=lambda seg: seg.start)
    return segments


def _operand_changes(
    left_records: list[bytes],
    right_records: list[bytes],
    encoding: str,
) -> list[dict[str, Any]]:
    """Symbol-level differences between paired rows of two networks."""

    from .inspection import _decode_code_row

    changes: list[dict[str, Any]] = []
    count = min(len(left_records), len(right_records))
    for index in range(count):
        left_row = left_records[index]
        right_row = right_records[index]
        if left_row == right_row:
            continue
        left_cells = _decode_code_row(left_row, index, encoding)["cells"]
        right_cells = _decode_code_row(right_row, index, encoding)["cells"]
        for position in range(min(len(left_cells), len(right_cells))):
            left_cell = left_cells[position]
            right_cell = right_cells[position]
            if left_cell["raw_hex"] == right_cell["raw_hex"]:
                continue
            left_text = f"{left_cell['mnemonic']} {left_cell['value']}".strip()
            right_text = f"{right_cell['mnemonic']} {right_cell['value']}".strip()
            same_opcode = left_cell["mnemonic"] == right_cell["mnemonic"]
            changes.append(
                {
                    "record": index,
                    "column": left_cell["column"],
                    "kind": "operand" if same_opcode else "instruction",
                    "left": left_text,
                    "right": right_text,
                    "left_raw": left_cell["raw_hex"],
                    "right_raw": right_cell["raw_hex"],
                }
            )
        if len(left_cells) != len(right_cells):
            changes.append(
                {
                    "record": index,
                    "column": min(len(left_cells), len(right_cells)),
                    "kind": "layout",
                    "left": f"{len(left_cells)} cells",
                    "right": f"{len(right_cells)} cells",
                }
            )
    return changes


def _cells_changed(left_records: list[bytes], right_records: list[bytes]) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    count = min(len(left_records), len(right_records))
    for index in range(count):
        a = left_records[index]
        b = right_records[index]
        for offset in range(0, 40, 4):
            if a[offset : offset + 4] != b[offset : offset + 4]:
                changed.append(
                    {
                        "record": index,
                        "column": offset // 4,
                        "left": a[offset : offset + 4].hex(" ").upper(),
                        "right": b[offset : offset + 4].hex(" ").upper(),
                    }
                )
    return changed


def _segment_payload(segment: _Segment) -> dict[str, Any]:
    return {
        "kind": segment.kind,
        "record_start": segment.start,
        "record_end": segment.end,
        "record_count": segment.end - segment.start + 1,
        "sha256": segment.sha256,
        "preview": _one_line(segment.preview),
        "program": segment.program,
        "comments": segment.comments,
    }


def diff_documents(
    left: LadDocument,
    right: LadDocument,
    *,
    context: int = 8,
) -> dict[str, Any]:
    """Align two documents by segment hashes and classify the differences."""

    left_segments = _segments_for(left)
    right_segments = _segments_for(right)

    matcher = difflib.SequenceMatcher(
        a=[seg.key for seg in left_segments],
        b=[seg.key for seg in right_segments],
        autojunk=False,
    )

    entries: list[dict[str, Any]] = []
    counts = {"unchanged": 0, "modified": 0, "added": 0, "removed": 0, "moved": 0}

    # First pass: walk SequenceMatcher opcodes, pairing replace blocks.
    pending_removed: list[_Segment] = []
    pending_added: list[_Segment] = []

    def flush_pending() -> None:
        nonlocal pending_removed, pending_added
        # Same-content segments on both sides are moves, not churn.
        removed_by_key: dict[str, _Segment] = {}
        for seg in pending_removed:
            removed_by_key.setdefault(seg.key, seg)
        moved_keys: set[str] = set()
        for seg in pending_added:
            stale = removed_by_key.get(seg.key)
            if stale is not None:
                counts["moved"] += 1
                moved_keys.add(seg.key)
                entries.append(
                    {
                        "status": "moved",
                        "left": _segment_payload(stale),
                        "right": _segment_payload(seg),
                    }
                )
                removed_by_key.pop(seg.key, None)
        for seg in pending_added:
            if seg.key in moved_keys:
                continue
            counts["added"] += 1
            entries.append({"status": "added", "right": _segment_payload(seg)})
        for seg in pending_removed:
            if seg.key in moved_keys:
                continue
            counts["removed"] += 1
            entries.append({"status": "removed", "left": _segment_payload(seg)})
        pending_removed = []
        pending_added = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            flush_pending()
            for offset in range(i2 - i1):
                counts["unchanged"] += 1
        elif tag == "replace":
            left_block = left_segments[i1:i2]
            right_block = right_segments[j1:j2]
            pair_count = min(len(left_block), len(right_block))
            for offset in range(pair_count):
                a = left_block[offset]
                b = right_block[offset]
                left_records = left.records[a.start : a.end + 1]
                right_records = right.records[b.start : b.end + 1]
                counts["modified"] += 1
                entries.append(
                    {
                        "status": "modified",
                        "left": _segment_payload(a),
                        "right": _segment_payload(b),
                        "operand_changes": _operand_changes(
                            left_records, right_records, left.encoding
                        ),
                        "changed_cells": _cells_changed(left_records, right_records),
                    }
                )
            pending_removed.extend(left_block[pair_count:])
            pending_added.extend(right_block[pair_count:])
        elif tag == "delete":
            pending_removed.extend(left_segments[i1:i2])
        elif tag == "insert":
            pending_added.extend(right_segments[j1:j2])
    flush_pending()

    same = (
        counts["modified"] == 0
        and counts["added"] == 0
        and counts["removed"] == 0
        and counts["moved"] == 0
    )
    byte_same = left.to_bytes() == right.to_bytes()
    if same and not byte_same:
        # Structure matches but raw bytes differ (e.g. trailer or padding).
        same = False
        entries.append(
            {
                "status": "modified",
                "note": "byte-level difference outside parsed segments",
                "left": {"sha256": left.sha256, "record_count": len(left.records)},
                "right": {"sha256": right.sha256, "record_count": len(right.records)},
            }
        )
        counts["modified"] += 1

    # Keep only meaningful entries in the payload; unchanged runs are summarized.
    shown = [entry for entry in entries if entry["status"] != "unchanged"]
    truncated = len(shown) > max(context, 0)
    result: dict[str, Any] = {
        "schema": DIFF_SCHEMA,
        "left": str(left.source_path) if left.source_path else None,
        "right": str(right.source_path) if right.source_path else None,
        "same": same,
        "left_sha256": left.sha256,
        "right_sha256": right.sha256,
        "left_records": len(left.records),
        "right_records": len(right.records),
        "left_segments": len(left_segments),
        "right_segments": len(right_segments),
        "summary": counts,
        "entries": shown[: max(context, 0)] if context else shown,
        "entries_total": len(shown),
        "truncated": truncated,
    }
    return result


def render_diff(result: dict[str, Any], *, context: int = 8) -> str:
    lines = [
        "SYNTEC LadEditor diff (segment-aligned)",
        f"left  : {result['left']}",
        f"right : {result['right']}",
        f"same  : {result['same']}",
        (
            f"records: {result['left_records']} -> {result['right_records']}  "
            f"segments: {result['left_segments']} -> {result['right_segments']}"
        ),
    ]
    summary = result["summary"]
    parts = [f"{name} {count}" for name, count in summary.items() if count]
    lines.append("changes: " + (", ".join(parts) if parts else "none"))
    if not result["entries"]:
        if result["same"]:
            lines.append("files are identical in structure and content")
        return "\n".join(lines) + "\n"

    status_titles = {
        "modified": "Modified",
        "added": "Added",
        "removed": "Removed",
        "moved": "Moved",
    }
    shown = result["entries"][: max(context, 0)]
    current_status = None
    for entry in shown:
        if entry["status"] != current_status:
            current_status = entry["status"]
            lines.append("")
            lines.append(f"{status_titles[current_status]}:")
        left = entry.get("left")
        right = entry.get("right")

        def describe(seg: dict[str, Any] | None) -> str:
            if seg is None:
                return "-"
            where = f"{seg['kind']} r{seg['record_start']}..{seg['record_end']}"
            if seg.get("program"):
                where += f" ({seg['program']})"
            return where

        if left is not None and right is not None:
            lines.append(f"  {describe(left)}  ->  {describe(right)}")
        elif left is not None:
            lines.append(f"  {describe(left)}")
        else:
            lines.append(f"  {describe(right)}")
        if left is not None and left.get("comments"):
            lines.append(f"    // {left['comments'][0]}")
        if (
            right is not None
            and right.get("comments")
            and right.get("comments") != (left or {}).get("comments")
        ):
            lines.append(f"    // {right['comments'][0]}")
        preview_left = left.get("preview") if left is not None else None
        preview_right = right.get("preview") if right is not None else None
        if not preview_left:
            preview_left = "(no instructions)" if left is not None else None
        if not preview_right:
            preview_right = "(no instructions)" if right is not None else None
        if preview_left != preview_right:
            if preview_left:
                lines.append(f"    left : {preview_left}")
            if preview_right:
                lines.append(f"    right: {preview_right}")
        else:
            lines.append(f"    {preview_left}")
        changes = entry.get("operand_changes")
        if not changes:
            changes = [
                {**item, "kind": "cell"}
                for item in entry.get("changed_cells", [])
            ]
        labels = {
            "operand": "op  ",
            "instruction": "ins ",
            "layout": "lay ",
            "cell": "cell",
        }
        for change in changes[:8]:
            kind = labels.get(str(change.get("kind")), "cell")
            lines.append(
                f"    {kind} r{change['record']:04d} c{change['column']}: "
                f"{change['left']} -> {change['right']}"
            )
        if len(changes) > 8:
            lines.append(f"    ... {len(changes) - 8} more cell changes")
    if result["truncated"]:
        lines.append("")
        lines.append(
            f"  ... {result['entries_total'] - len(shown)} more entries; "
            "raise --context or use --json for the full list"
        )
    return "\n".join(lines) + "\n"
