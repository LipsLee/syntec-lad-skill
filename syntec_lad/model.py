"""Container model and exact record/comment codec for .lad files."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import LadFormatError, LadValidationError
from .opcodes import is_comment_record


LAD_TRAILER = b"\x00\xFE"
VERSION_TEXT = "If you see this message, please upgrade your ladder editor."
COMMENT_TYPE_NAMES = {
    1: "line",
    2: "version",
    3: "program",
    4: "variable",
}


@dataclass(frozen=True)
class CommentBlock:
    start_record: int
    record_count: int
    comment_type: int
    length: int
    text: str
    raw: bytes

    @property
    def end_record(self) -> int:
        return self.start_record + self.record_count - 1


def _decode(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace").rstrip("\x00")


def _comment_payload(records: list[bytes], start_index: int) -> tuple[bytes, int]:
    header = records[start_index]
    length = header[2]
    output = bytearray()
    record_index = start_index
    current = header
    position = 6

    while len(output) < length:
        output.append(current[position])
        position = position + 1 if position % 4 != 3 else position + 3
        if len(output) < length and position >= 40:
            record_index += 1
            if record_index >= len(records):
                raise LadFormatError(
                    f"comment length {length} exceeds available records"
                )
            current = records[record_index]
            position = 2
    return bytes(output), record_index - start_index + 1


def read_comment(
    records: list[bytes],
    start_index: int,
    encoding: str,
) -> CommentBlock:
    if start_index >= len(records) or not is_comment_record(records[start_index]):
        raise LadFormatError(f"record {start_index} is not a comment header")
    header = records[start_index]
    payload, count = _comment_payload(records, start_index)
    return CommentBlock(
        start_record=start_index,
        record_count=count,
        comment_type=header[3],
        length=header[2],
        text=_decode(payload, encoding),
        raw=b"".join(records[start_index:start_index + count]),
    )


def read_comment_payload(
    records: list[bytes],
    start_index: int,
) -> tuple[bytes, int]:
    """Return the raw comment payload and physical record count."""

    return _comment_payload(records, start_index)


def iter_comment_blocks(
    records: list[bytes],
    encoding: str,
) -> list[CommentBlock]:
    result: list[CommentBlock] = []
    record_index = 0
    while record_index < len(records):
        if is_comment_record(records[record_index]):
            block = read_comment(records, record_index, encoding)
            result.append(block)
            record_index += block.record_count
        else:
            record_index += 1
    return result


def encode_comment(text: str, comment_type: int, encoding: str) -> list[bytes]:
    if comment_type not in COMMENT_TYPE_NAMES:
        raise LadValidationError(f"unsupported comment type: {comment_type}")
    try:
        "".encode(encoding)
        payload = text.encode(encoding)
    except LookupError as exc:
        raise LadValidationError(f"unknown comment encoding: {encoding}") from exc
    except UnicodeEncodeError as exc:
        raise LadValidationError(
            f"comment cannot be encoded as {encoding}: {exc}"
        ) from exc
    if len(payload) > 255:
        raise LadValidationError(
            f"comment is {len(payload)} bytes; the format limit is 255"
        )

    return encode_comment_payload(payload, comment_type)


def encode_comment_payload(payload: bytes, comment_type: int) -> list[bytes]:
    """Encode an already encoded comment payload into physical records."""

    if comment_type not in COMMENT_TYPE_NAMES:
        raise LadValidationError(f"unsupported comment type: {comment_type}")
    if len(payload) > 255:
        raise LadValidationError(
            f"comment is {len(payload)} bytes; the format limit is 255"
        )

    first = bytearray(40)
    first[2] = len(payload)
    first[3] = comment_type
    records = [first]
    position = 6
    for byte in payload:
        if position >= 40:
            records.append(bytearray(40))
            position = 2
        records[-1][position] = byte
        position = position + 1 if position % 4 != 3 else position + 3
    return [bytes(record) for record in records]


def _comment_payloads(records: list[bytes]) -> bytes:
    chunks: list[bytes] = []
    index = 0
    while index < len(records):
        if is_comment_record(records[index]):
            payload, count = _comment_payload(records, index)
            chunks.append(payload)
            index += count
        else:
            index += 1
    return b"\n".join(chunks)


def detect_encoding(records: list[bytes]) -> str:
    """Detect Big5 vs GB-style comments using the same intent as MLCEdit.

    A new file uses GB2312.  Existing files are examined using the heuristic
    recovered from ``mainForm.GetCommentEncodingType``.
    """

    payload = _comment_payloads(records)
    if not any(byte > 0x7F for byte in payload):
        return "gb2312"

    big5_score = 0
    gb_score = 0
    index = 0
    while index < len(payload):
        if payload[index] <= 0x7F:
            index += 1
            continue
        if index + 1 >= len(payload):
            break
        lead = payload[index]
        trail = payload[index + 1]
        if 0xAA <= lead <= 0xAF:
            short_big5 = 10
        else:
            short_big5 = 0
        if 0xA4 <= lead <= 0xA9 and 0xA1 <= trail <= 0xFE:
            short_big5 += 1
        if 0x40 <= trail <= 0x7E:
            short_big5 += 1
        big5_score += short_big5
        if 0xC6 <= lead <= 0xD7 and 0xA1 <= trail <= 0xFE:
            gb_score += 2
        index += 2

    if big5_score > gb_score:
        return "big5"
    # MLCEdit's fallback for non-Big5 comments is GB2312.  Prefer that
    # codec when the payload is representable; otherwise retain GB18030 so
    # newer extended characters are not lost.
    try:
        payload.decode("gb2312")
    except UnicodeDecodeError:
        return "gb18030"
    return "gb2312"


class LadDocument:
    """A lossless in-memory representation of one .lad file."""

    def __init__(
        self,
        records: list[bytes],
        *,
        encoding: str = "gb2312",
        source_path: Path | str | None = None,
        original_sha256: str | None = None,
    ) -> None:
        if any(len(record) != 40 for record in records):
            raise LadFormatError("every physical record must be exactly 40 bytes")
        self.records = [bytes(record) for record in records]
        self.encoding = encoding
        self.source_path = Path(source_path).resolve() if source_path else None
        self.original_sha256 = original_sha256

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        encoding: str | None = None,
        source_path: Path | str | None = None,
    ) -> "LadDocument":
        if len(data) < 2 or data[-2:] != LAD_TRAILER:
            if len(data) % 40 == 0:
                raise LadFormatError("file is missing the required 00 FE trailer")
            raise LadFormatError("invalid .lad size or trailer")
        body = data[:-2]
        if len(body) % 40:
            raise LadFormatError("ladder body is not a multiple of 40 bytes")
        records = [body[offset:offset + 40] for offset in range(0, len(body), 40)]
        selected_encoding = encoding if encoding and encoding != "auto" else detect_encoding(records)
        try:
            "".encode(selected_encoding)
        except LookupError as exc:
            raise LadValidationError(
                f"unknown ladder comment encoding: {selected_encoding}"
            ) from exc
        # Decode once to catch an explicitly wrong encoding before any write.
        iter_comment_blocks(records, selected_encoding)
        return cls(
            records,
            encoding=selected_encoding,
            source_path=source_path,
            original_sha256=hashlib.sha256(data).hexdigest(),
        )

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        encoding: str | None = "auto",
    ) -> "LadDocument":
        path = Path(path)
        data = path.read_bytes()
        return cls.from_bytes(data, encoding=encoding, source_path=path)

    @property
    def trailer(self) -> bytes:
        return LAD_TRAILER

    @property
    def version(self) -> int | None:
        for comment in iter_comment_blocks(self.records, self.encoding):
            if comment.comment_type != 2:
                continue
            first = comment.text.split("\t", 1)[0].strip()
            try:
                return int(first)
            except ValueError:
                continue
        return None

    @property
    def variable_comments(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for comment in iter_comment_blocks(self.records, self.encoding):
            if comment.comment_type != 4:
                continue
            fields = [field.strip() for field in comment.text.split("\t")]
            while len(fields) < 3:
                fields.append("")
            result.append({
                "name": fields[0],
                "short": fields[1],
                "long": fields[2],
            })
        return result

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def to_bytes(self) -> bytes:
        return b"".join(self.records) + LAD_TRAILER

    def expected_hash(self, start_record: int, end_record: int) -> str:
        records = self.records[start_record:end_record + 1]
        return hashlib.sha256(b"".join(records)).hexdigest()

    def append_variable_comments(self, variables: list[dict[str, str]]) -> None:
        """Replace existing type 2/4 comments and optionally append new ones."""

        filtered: list[bytes] = []
        index = 0
        while index < len(self.records):
            if is_comment_record(self.records[index]):
                block = read_comment(self.records, index, self.encoding)
                if block.comment_type not in (2, 4):
                    filtered.extend(self.records[index:index + block.record_count])
                index += block.record_count
                continue
            filtered.append(self.records[index])
            index += 1
        self.records = filtered

        if not variables:
            return
        version_text = f"2\t{VERSION_TEXT}"
        self.records.extend(encode_comment(version_text, 2, self.encoding))
        for variable in variables:
            name = str(variable.get("name", "")).strip()
            short = str(variable.get("short", "")).strip()
            long = str(variable.get("long", "")).strip()
            if not name:
                raise LadValidationError("variable comment name cannot be empty")
            text = f"{name} \t{short} \t{long}"
            self.records.extend(encode_comment(text, 4, self.encoding))

    def _sidecar_path(self, path: Path) -> Path | None:
        candidates = [path.with_suffix(".LC"), path.with_suffix(".lc"), path.with_suffix(".Lc")]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def save(
        self,
        path: Path | str,
        *,
        overwrite: bool = False,
        in_place: bool = False,
        atomic: bool = True,
        manage_sidecar: bool = True,
    ) -> dict[str, object]:
        target = Path(path).resolve()
        if in_place:
            overwrite = True
        if target.exists() and not overwrite:
            raise LadValidationError(
                f"output exists: {target}; use overwrite=True or an explicit new path"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        sidecar = self._sidecar_path(target) if manage_sidecar else None
        backup_path: Path | None = None
        sidecar_backup: Path | None = None
        if in_place and target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_path = target.with_name(f"{target.name}.{stamp}.bak")
            shutil.copy2(target, backup_path)
        if sidecar and in_place:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            sidecar_backup = sidecar.with_name(f"{sidecar.name}.{stamp}.bak")
            shutil.copy2(sidecar, sidecar_backup)
        elif sidecar:
            # A new output must not inherit a same-named .LC sidecar: the
            # editor gives the sidecar precedence over embedded type 4
            # comments.  Preserve it under a timestamped backup and remove the
            # original name only after the new file has been written.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            sidecar_backup = sidecar.with_name(f"{sidecar.name}.{stamp}.bak")
            shutil.copy2(sidecar, sidecar_backup)

        data = self.to_bytes()
        if atomic:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, target)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
        else:
            target.write_bytes(data)

        if sidecar:
            sidecar.unlink(missing_ok=True)

        return {
            "path": str(target),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "backup": str(backup_path) if backup_path else None,
            "sidecar": str(sidecar) if sidecar else None,
            "sidecar_backup": str(sidecar_backup) if sidecar_backup else None,
        }

    def inspect(self) -> dict[str, object]:
        from .inspection import inspect_document

        return inspect_document(self)
