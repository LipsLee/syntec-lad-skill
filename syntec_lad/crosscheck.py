"""Cross-file consistency checks for a Syntec delivery.

A delivery usually ships four artefacts that must agree with each other:

* ``cnc.lad``            - the ladder program itself
* ``paramext.pxt``       - which extended parameters exist (and their ranges)
* ``ParamExt_*.xml``     - the three-language display text for those parameters
* ``AlarmPLC_*.xml``     - the alarm text for the alarm bits the ladder drives

``crosscheck`` reads them together and reports where they disagree, which is
the check that otherwise has to be written by hand for every delivery.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .inspection import inspect_document
from .model import LadDocument

CROSSCHECK_SCHEMA = "syntec-lad-crosscheck/v1"

PXT_SECTION_RE = re.compile(r"^\s*\[(\d+)\]", re.MULTILINE)
XML_PARAM_RE = re.compile(r'Message ID="Param(\d+)"\s+Content="([^"]*)"')
XML_R_RE = re.compile(r"\(R(\d+)\)")
ALARM_RE = re.compile(r'ID="AlarmMsg::(\w+)::ID=(\d+)"\s+Content="([^"]*)"')
ALARM_BIT_RE = re.compile(r"R\s*(\d+)\s*\.\s*(\d+)")
ADDR_TOKEN_RE = re.compile(r"@?\b(I|O|A|C|S|TM|CT)(\d{1,4})(?:\.(\d{1,2}))?\b")
REG_TOKEN_RE = re.compile(r"@?\bR(\d{1,5})(?:\.(\d{1,2}))?\b")
IO_FIELD_RE = re.compile(r"^\s*([IO])(\d{1,3})\s*$")

COIL_MNEMONICS = {"OUT", "SET", "RST", "PLS", "PLF"}
MACRO_PARAM_RANGE = (5001, 5054)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp950", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp950", errors="replace")


def parse_pxt(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    sections = [int(value) for value in PXT_SECTION_RE.findall(text)]
    return {
        "path": str(path),
        "sections": sorted(sections),
        "count": len(sections),
    }


def parse_param_xml(path: Path) -> dict[str, Any]:
    raw = _read_text(path)
    commented: set[str] = set()
    for block in re.findall(r"<!--(.*?)-->", raw, re.S):
        for match in XML_PARAM_RE.findall(block):
            commented.add(match[0])

    live_text = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    live: dict[str, dict[str, Any]] = {}
    for param, content in XML_PARAM_RE.findall(live_text):
        register = XML_R_RE.search(content)
        live[param] = {
            "param": int(param),
            "text": content,
            "register": int(register.group(1)) if register else None,
        }
    return {
        "path": str(path),
        "live": live,
        "commented": sorted(int(value) for value in commented),
        "count": len(live),
    }


def parse_alarm_xml(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    bits: dict[tuple[int, int], dict[str, Any]] = {}
    for kind, alarm_id, content in ALARM_RE.findall(text):
        match = ALARM_BIT_RE.search(content)
        if not match:
            continue
        key = (int(match.group(1)), int(match.group(2)))
        bits[key] = {
            "kind": kind,
            "id": int(alarm_id),
            "text": content.strip(),
        }
    return {"path": str(path), "bits": bits, "count": len(bits)}


def ladder_usage(document: LadDocument) -> dict[str, Any]:
    inspection = inspect_document(document)
    rows = {row["record_start"]: row for row in inspection["rows"]}
    registers: set[int] = set()
    discrete: dict[str, list[dict[str, Any]]] = {}
    coils: list[dict[str, Any]] = []

    for candidate in inspection["network_candidates"]:
        cells: list[dict[str, Any]] = []
        for record in range(candidate["record_start"], candidate["record_end"] + 1):
            row = rows.get(record)
            if row and row["kind"] == "code":
                cells.extend(row["cells"])
        where = {
            "network": candidate["id"],
            "program": candidate.get("program"),
            "record": candidate["record_start"],
        }
        for cell in cells:
            mnemonic = cell["mnemonic"]
            for operand in cell.get("operands") or []:
                value = str(operand.get("value") or "").strip()
                if not value:
                    continue
                reg = REG_TOKEN_RE.match(value)
                if reg:
                    registers.add(int(reg.group(1)))
                token = ADDR_TOKEN_RE.match(value.replace("@", ""))
                if token:
                    family = token.group(1)
                    address = f"{family}{token.group(2)}"
                    if token.group(3):
                        address = f"{address}.{token.group(3)}"
                    discrete.setdefault(address.upper(), []).append(
                        {**where, "mnemonic": mnemonic}
                    )
            if mnemonic in COIL_MNEMONICS and cell.get("operands"):
                target = str(cell["operands"][0].get("value") or "").strip()
                coils.append({**where, "target": target, "mnemonic": mnemonic})
            if mnemonic == "MOV" and len(cell.get("operands") or []) >= 2:
                target = str(cell["operands"][1].get("value") or "").strip()
                reg = REG_TOKEN_RE.match(target)
                if reg:
                    registers.add(int(reg.group(1)))

    commented_registers: set[int] = set()
    for comment in inspection["comments"]:
        for match in REG_TOKEN_RE.finditer(str(comment["text"])):
            commented_registers.add(int(match.group(1)))

    return {
        "registers": registers,
        "commented_registers": commented_registers,
        "discrete": discrete,
        "coils": coils,
        "inspection": inspection,
    }


def parse_io_table(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    points: dict[str, str] = {}

    if path.suffix.casefold() == ".xml":
        # IoMap_Customer.xml 之类的映射表：抽取其中的 I/O 点位与上下文
        for match in re.finditer(
            r"<([A-Za-z0-9_:]+)[^>]*>((?:(?!</\1>).){0,400})</\1>",
            text,
            re.S,
        ):
            block = match.group(0)
            tag = match.group(1)
            for token in re.finditer(r"\b([IO])(\d{1,3})\b", block):
                address = f"{token.group(1)}{token.group(2)}"
                context = re.sub(r"\s+", " ", block)[:120]
                points.setdefault(address.upper(), f"{tag}: {context}")
        for token in re.finditer(r"\b([IO])(\d{1,3})\b", text):
            address = f"{token.group(1)}{token.group(2)}"
            points.setdefault(address.upper(), address)
    else:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            field = re.split(r"[\t,;|]", stripped)[0].strip()
            match = IO_FIELD_RE.match(field)
            if match:
                points[field.upper()] = stripped
    return {"path": str(path), "points": points, "count": len(points)}


def _param_segment(param: int) -> str:
    low, high = MACRO_PARAM_RANGE
    if low <= param <= high:
        return "macro"
    if 3401 <= param <= 3460 or 5060 <= param <= 5113:
        return "plc"
    return "other"


def crosscheck(
    lad_path: Path,
    *,
    pxt_path: Path | None = None,
    param_xml_paths: list[Path] | None = None,
    alarm_xml_path: Path | None = None,
    io_path: Path | None = None,
) -> dict[str, Any]:
    document = LadDocument.load(lad_path)
    usage = ladder_usage(document)
    registers: set[int] = usage["registers"]

    result: dict[str, Any] = {
        "schema": CROSSCHECK_SCHEMA,
        "lad": {
            "path": str(Path(lad_path).resolve()),
            "sha256": document.sha256,
            "records": len(document.records),
            "networks": len(usage["inspection"]["network_candidates"]),
            "registers_referenced": len(registers),
        },
        "errors": [],
        "warnings": [],
        "info": [],
    }

    # ---- 参数表 / 三语 ----
    param_xmls = [parse_param_xml(path) for path in (param_xml_paths or [])]
    primary = param_xmls[0] if param_xmls else None

    if primary is not None:
        live = primary["live"]
        by_register = {
            info["register"]: info for info in live.values() if info["register"]
        }
        sections = parse_pxt(pxt_path)["sections"] if pxt_path else []

        missing_text = sorted(set(sections) - set(int(k) for k in live))
        for param in missing_text:
            result["errors"].append(
                {
                    "check": "param_text",
                    "message": f"Pr{param} 在 paramext.pxt 有节，但 {Path(primary['path']).name} 无生效文案",
                    "param": param,
                }
            )
        orphan_text = sorted(int(k) for k in live if pxt_path and int(k) not in set(sections))
        for param in orphan_text:
            result["warnings"].append(
                {
                    "check": "param_text",
                    "message": f"Pr{param} 有生效文案，但 paramext.pxt 无对应节（死文案，参数页不显示）",
                    "param": param,
                }
            )

        zero_ref = []
        for param in sorted(int(k) for k in live):
            info = live[str(param)]
            register = info["register"]
            if register is None or register in registers:
                continue
            zero_ref.append(
                {
                    "param": param,
                    "register": f"R{register}",
                    "segment": _param_segment(param),
                    "text": info["text"],
                }
            )
        for item in zero_ref:
            bucket = "warnings" if item["segment"] != "macro" else "info"
            result[bucket].append(
                {
                    "check": "param_zero_ref",
                    "message": (
                        f"Pr{item['param']}（{item['register']}）在梯图零引用"
                        f"［{item['segment']}］{item['text'][:40]}"
                    ),
                    **item,
                }
            )
        result["params"] = {
            "pxt_sections": len(sections),
            "xml_live": len(live),
            "xml_commented": primary["commented"],
            "missing_text": missing_text,
            "orphan_text": orphan_text,
            "zero_ref": zero_ref,
        }

        # 三语一致性
        if len(param_xmls) > 1:
            base = set(int(k) for k in primary["live"])
            languages = {}
            for other in param_xmls[1:]:
                other_ids = set(int(k) for k in other["live"])
                diff = sorted(base ^ other_ids)
                languages[Path(other["path"]).name] = {
                    "count": len(other_ids),
                    "diff": diff,
                }
                if diff:
                    result["warnings"].append(
                        {
                            "check": "param_language",
                            "message": (
                                f"{Path(other['path']).name} 与 "
                                f"{Path(primary['path']).name} 生效文案差集：{diff}"
                            ),
                            "diff": diff,
                        }
                    )
            result["languages"] = {
                "base": Path(primary["path"]).name,
                "base_count": len(base),
                "others": languages,
            }

    # ---- 报警位 ----
    if alarm_xml_path is not None:
        alarm = parse_alarm_xml(alarm_xml_path)
        documented = set(alarm["bits"])
        written: set[tuple[int, int]] = set()
        for coil in usage["coils"]:
            match = REG_TOKEN_RE.match(coil["target"])
            if not match or match.group(2) is None:
                continue
            register = int(match.group(1))
            if 44 <= register <= 49 or 590 <= register <= 591:
                written.add((register, int(match.group(2))))
        missing = sorted(written - documented)
        for register, bit in missing:
            # R44~R49 是报警位；R590/R591 是操作提示位，缺文案只算警告
            bucket = "errors" if register <= 49 else "warnings"
            kind = "报警" if register <= 49 else "提示"
            result[bucket].append(
                {
                    "check": "alarm_text",
                    "message": f"梯图写出 R{register}.{bit:02d}（{kind}位），但 {Path(alarm_xml_path).name} 无对应文案",
                    "register": f"R{register}.{bit:02d}",
                }
            )
        unused = sorted(documented - written)
        result["alarms"] = {
            "written": len(written),
            "documented": len(documented),
            "missing_text": [f"R{r}.{b:02d}" for r, b in missing],
            "unused_text": [f"R{r}.{b:02d}" for r, b in unused],
        }
        for register, bit in unused:
            result["info"].append(
                {
                    "check": "alarm_text",
                    "message": f"R{register}.{bit:02d} 有文案但梯图未写出（预留或废弃）：{alarm['bits'][(register, bit)]['text'][:40]}",
                    "register": f"R{register}.{bit:02d}",
                }
            )

    # ---- IO 表 ----
    if io_path is not None:
        table = parse_io_table(io_path)
        if not table["points"]:
            result["info"].append(
                {
                    "check": "io_table",
                    "message": (
                        f"{Path(io_path).name} 未解析出 I/O 点位，"
                        "可能是硬件端口映射表（IoMap）而不是点位表（地址<TAB>描述）"
                    ),
                }
            )
        ladder_io = {
            address
            for address in usage["discrete"]
            if ADDR_TOKEN_RE.match(address) and address[0] in {"I", "O"}
        }
        table_points = set(table["points"])
        only_table = sorted(table_points - ladder_io)
        only_ladder = sorted(ladder_io - table_points)
        result["io"] = {
            "table": str(io_path),
            "table_points": len(table_points),
            "ladder_points": len(ladder_io),
            "only_in_table": only_table,
            "only_in_ladder": only_ladder,
        }
        if table["points"]:
            for address in only_table:
                result["info"].append(
                    {
                        "check": "io_table",
                        "message": f"{address} 在 IO 表中有定义，梯图未使用：{table['points'][address][:50]}",
                        "address": address,
                    }
                )
            for address in only_ladder:
                result["warnings"].append(
                    {
                        "check": "io_table",
                        "message": f"{address} 梯图在用，但 IO 表无定义",
                        "address": address,
                    }
                )

    result["ok"] = not result["errors"]
    return result


def render_crosscheck(result: dict[str, Any], *, context: int = 12) -> str:
    lad = result["lad"]
    errors = result["errors"]
    warnings = result["warnings"]
    info = result["info"]
    lines = [
        "SYNTEC delivery crosscheck",
        f"Ladder : {lad['path']}",
        f"         sha256={lad['sha256'][:16]}…  records={lad['records']}  "
        f"networks={lad['networks']}  R referenced={lad['registers_referenced']}",
        f"Result : {'PASS' if result['ok'] else 'FAIL'}  "
        f"error {len(errors)} / warning {len(warnings)} / info {len(info)}",
    ]
    if "params" in result:
        params = result["params"]
        lines.append(
            f"Params : pxt {params['pxt_sections']} 节 | xml 生效 {params['xml_live']} 条 | "
            f"注释 {len(params['xml_commented'])} 条 | 零引用 {len(params['zero_ref'])}"
        )
    if "languages" in result:
        languages = result["languages"]
        others = ", ".join(
            f"{name} {data['count']} 条" for name, data in languages["others"].items()
        )
        lines.append(f"Langs  : {languages['base']} {languages['base_count']} 条 | {others}")
    if "alarms" in result:
        alarms = result["alarms"]
        lines.append(
            f"Alarms : 梯图写出 {alarms['written']} 位 | 文案 {alarms['documented']} 条 | "
            f"缺文案 {len(alarms['missing_text'])} | 未使用 {len(alarms['unused_text'])}"
        )
    if "io" in result:
        io = result["io"]
        lines.append(
            f"IO     : 表 {io['table_points']} 点 | 梯图 {io['ladder_points']} 点 | "
            f"仅表内有 {len(io['only_in_table'])} | 仅梯图有 {len(io['only_in_ladder'])}"
        )

    for title, items in (
        ("Errors", errors),
        ("Warnings", warnings),
        ("Info", info),
    ):
        if not items:
            continue
        lines.append("")
        lines.append(f"{title}:")
        for item in items[:context]:
            lines.append(f"  [{item['check']}] {item['message']}")
        if len(items) > context:
            lines.append(f"  ... {len(items) - context} more (raise --context or use --json)")
    return "\n".join(lines) + "\n"
