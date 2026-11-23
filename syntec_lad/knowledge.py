"""Keyword search over the bundled engineering knowledge base.

The 602-address index only covers C/S/R status semantics, so words such as
「润滑」「刀库」「安全门」 return nothing there. This module falls back to the
markdown reference library so a failed address lookup still points at the
relevant section instead of a dead end.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .zhconv import to_simplified

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

# 工艺词 -> 知识库检索建议（索引里没有这些词，但文档里有对应章节）
DOMAIN_HINTS = {
    "润滑": ["function-library", "patterns"],
    "打油": ["function-library"],
    "液压": ["function-library"],
    "排屑": ["function-library"],
    "冷却": ["function-library"],
    "刀库": ["function-library", "patterns"],
    "刀塔": ["function-library"],
    "主轴": ["function-library"],
    "安全门": ["project-conventions", "function-library"],
    "门锁": ["project-conventions", "function-library"],
    "报警": ["patterns", "project-conventions"],
    "原点": ["patterns", "element-grammar"],
    "换刀": ["function-library", "patterns"],
    "对刀": ["function-library"],
    "M码": ["function-library"],
    "注释": ["comment-encoding", "project-conventions"],
    "编码": ["comment-encoding"],
    "格式": ["lad-format"],
    "寄存器": ["address-map"],
    "权限": ["R512-R1023-权限表", "address-map"],
}


def _reference_root() -> Path:
    package_root = Path(__file__).resolve().parent
    candidates = [
        package_root.parent / "references",
        Path.cwd() / "references",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0]


@lru_cache(maxsize=8)
def _load_documents(root_text: str) -> tuple[tuple[str, str], ...]:
    root = Path(root_text)
    documents: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.md")):
        try:
            documents.append((path.relative_to(root).as_posix(), path.read_text(encoding="utf-8-sig", errors="replace")))
        except OSError:
            continue
    return tuple(documents)


def _sections(text: str) -> list[tuple[str, int, str]]:
    """Split markdown into (heading, offset, body) tuples."""

    matches = list(HEADING_RE.finditer(text))
    sections: list[tuple[str, int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), start, text[start:end]))
    return sections


def search_knowledge(query: str, *, limit: int = 8) -> dict[str, Any]:
    """Search headings and paragraphs of the bundled references."""

    text = query.strip()
    if not text:
        return {"query": query, "hits": [], "hints": []}
    needle = to_simplified(text).casefold()
    root = _reference_root()
    hits: list[dict[str, Any]] = []

    for relative, document in _load_documents(str(root.resolve())):
        if needle in relative.casefold() and len(hits) < limit:
            hits.append(
                {
                    "file": relative,
                    "heading": "(文件名匹配)",
                    "line": 1,
                    "excerpt": relative,
                }
            )
        for heading, offset, body in _sections(document):
            heading_hit = needle in heading.casefold()
            body_hit = needle in body.casefold()
            if not (heading_hit or body_hit):
                continue
            line = document.count("\n", 0, offset) + 1
            excerpt = ""
            if body_hit:
                position = body.casefold().find(needle)
                snippet = body[max(0, position - 60) : position + 80]
                excerpt = " ".join(snippet.split())
            hits.append(
                {
                    "file": relative,
                    "heading": heading,
                    "line": line,
                    "weight": 0 if heading_hit else 1,
                    "excerpt": excerpt,
                }
            )
        if len(hits) >= limit * 4:
            break

    hits.sort(key=lambda item: (item.get("weight", 1), item["file"], item["line"]))
    for item in hits:
        item.pop("weight", None)

    hints = []
    for keyword, files in DOMAIN_HINTS.items():
        if keyword.casefold() in needle:
            hints.extend(f"references/panming/{name}.md" for name in files)

    return {
        "query": query,
        "hits": hits[:limit],
        "hints": sorted(set(hints)),
    }


__all__ = ["search_knowledge", "DOMAIN_HINTS"]
