"""Plan-level checks that run before a ladder is compiled.

The native ``lint`` command works on a generated ``.lad``.  This module checks
the reviewed JSON plan instead, so repeated conditions are reported while the
model can still merge them into one ``actions`` array.  Conditions are
compared after DNF normalization, which ignores commutative ordering and
equivalent ``all``/``any`` nesting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .compiler import _condition_dnf, _parse_condition_node
from .errors import LadError

PLAN_LINT_SCHEMA = "syntec-lad-plan-lint/v1"

ONE_CELL_ACTIONS = {"OUT", "OUTI", "PLS", "PLF", "SET", "RST"}
FLOW_ACTIONS = {"JMP", "JSR", "MSG"}


def _operand_key(operand: Any) -> tuple[Any, ...] | None:
    if operand is None:
        return None
    return (operand.kind, operand.raw, operand.indirect, operand.text)


def _leaf_key(leaf: Any) -> tuple[Any, ...]:
    # ``Leaf.key`` includes the original source spelling (``I0`` vs ``I000``).
    # Use the decoded operands instead so both spellings compare equal.
    return (
        leaf.kind,
        leaf.opcode,
        leaf.width,
        _operand_key(leaf.op1),
        _operand_key(leaf.op2),
    )


def _reduce_terms(terms: Iterable[frozenset[tuple[Any, ...]]]) -> frozenset[
    frozenset[tuple[Any, ...]]
]:
    unique = set(terms)
    if frozenset() in unique:
        return frozenset({frozenset()})
    # Absorption: A OR (A AND B) is still A.
    reduced = {
        term
        for term in unique
        if not any(other < term for other in unique)
    }
    return frozenset(reduced)


def condition_signature(condition: Any) -> frozenset[frozenset[tuple[Any, ...]]]:
    """Return a commutative DNF signature for a plan condition."""

    terms = _condition_dnf(_parse_condition_node(condition))
    return _reduce_terms(
        frozenset(_leaf_key(leaf) for leaf in term) for term in terms
    )


def _condition_width(condition: Any) -> int:
    terms = _condition_dnf(_parse_condition_node(condition))
    return max((sum(leaf.width for leaf in term) for term in terms), default=0)


def _action_width(action: Any) -> int:
    if not isinstance(action, dict):
        return 2
    return 1 if str(action.get("op", "")).upper() in ONE_CELL_ACTIONS else 2


def _network_widths(network: dict[str, Any]) -> set[int]:
    actions = network.get("actions")
    if not isinstance(actions, list):
        return set()
    return {_action_width(action) for action in actions}


def _has_flow_action(network: dict[str, Any]) -> bool:
    actions = network.get("actions")
    if not isinstance(actions, list):
        return False
    return any(
        isinstance(action, dict)
        and str(action.get("op", "")).upper() in FLOW_ACTIONS
        for action in actions
    )


def _issue(
    rule: str,
    severity: str,
    detail: str,
    *,
    program: str | None,
    networks: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "rule": rule,
        "severity": severity,
        "detail": detail,
        "program": program,
        "network": networks[-1] if networks else None,
        "networks": networks,
    }
    if extra:
        item.update(extra)
    return item


def lint_plan(
    plan: dict[str, Any],
    *,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit one ``syntec-lad-plan/v1`` document for missed merges."""

    issues: list[dict[str, Any]] = []
    programs = plan.get("programs")
    if not isinstance(programs, list):
        programs = []

    for program in programs:
        if not isinstance(program, dict):
            continue
        program_name = str(program.get("name") or "?")
        networks = program.get("networks")
        if not isinstance(networks, list):
            continue

        previous: (
            tuple[
                str,
                frozenset[frozenset[tuple[Any, ...]]],
                set[int],
                int,
                bool,
            ]
            | None
        ) = None
        for index, network in enumerate(networks, start=1):
            if not isinstance(network, dict):
                continue
            network_id = f"N{index:04d}"
            widths = _network_widths(network)

            try:
                signature = condition_signature(network.get("condition"))
                condition_width = _condition_width(network.get("condition"))
            except LadError as exc:
                issues.append(
                    _issue(
                        "invalid_condition",
                        "error",
                        f"{network_id} 的条件无法规范化：{exc}",
                        program=program_name,
                        networks=[network_id],
                    )
                )
                previous = None
                continue

            action_start = 8 if 2 in widths else 9
            if len(widths) > 1 and condition_width > action_start:
                issues.append(
                    _issue(
                        "mixed_action_too_wide",
                        "info",
                        f"{network_id} 的 actions 同时含 1 格和 2 格指令，"
                        f"但条件宽 {condition_width} 列，超过第 {action_start} 列"
                        "的动作母线起点；生成器会退回逐动作布局并重复条件。"
                        "缩短串联条件，或把动作拆到不同 network",
                        program=program_name,
                        networks=[network_id],
                    )
                )

            if previous is not None:
                (
                    previous_id,
                    previous_signature,
                    previous_widths,
                    previous_width,
                    previous_flow,
                ) = previous
                if signature == previous_signature and widths and previous_widths:
                    if previous_flow or _has_flow_action(network):
                        issues.append(
                            _issue(
                                "flow_action_not_mergeable",
                                "info",
                                f"{previous_id} 与 {network_id} 条件逻辑等价，"
                                "但包含 JMP/JSR/MSG；流程指令不能与其它动作共用"
                                "动作母线，保持独立 network",
                                program=program_name,
                                networks=[previous_id, network_id],
                            )
                        )
                        previous = (
                            network_id,
                            signature,
                            widths,
                            condition_width,
                            _has_flow_action(network),
                        )
                        continue
                    action_start = (
                        8
                        if 2 in previous_widths or 2 in widths
                        else 9
                    )
                    max_width = max(previous_width, condition_width)
                    if max_width <= action_start:
                        unconditional = signature == frozenset({frozenset()})
                        suffix = "（两者都无条件）" if unconditional else ""
                        issues.append(
                            _issue(
                                "mergeable_condition",
                                "warning",
                                f"{previous_id} 与 {network_id} 的条件逻辑等价"
                                f"{suffix}；把 {network_id} 的 actions 并入 "
                                f"{previous_id}，条件只画一次",
                                program=program_name,
                                networks=[previous_id, network_id],
                            )
                        )
                    else:
                        issues.append(
                            _issue(
                                "mergeable_condition_too_wide",
                                "info",
                                f"{previous_id} 与 {network_id} 的条件逻辑等价，"
                                f"但条件宽 {max_width} 列，超过共用动作母线的"
                                f"第 {action_start} 列；合并后仍会重复条件。"
                                "保持两个 network，或缩短串联条件",
                                program=program_name,
                                networks=[previous_id, network_id],
                            )
                        )

            previous = (
                network_id,
                signature,
                widths,
                condition_width,
                _has_flow_action(network),
            )

    counts = {"error": 0, "warning": 0, "info": 0}
    for item in issues:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1

    network_count = sum(
        len(program.get("networks") or [])
        for program in programs
        if isinstance(program, dict)
    )
    return {
        "schema": PLAN_LINT_SCHEMA,
        "source": {
            "path": str(source_path) if source_path is not None else None,
            "programs": len(programs),
            "networks": network_count,
        },
        "summary": counts,
        "ok": counts["error"] == 0,
        "issues": issues,
    }


def render_plan_lint(result: dict[str, Any], *, limit: int | None = None) -> str:
    source = result["source"]
    summary = result["summary"]
    lines = [
        "SYNTEC ladder plan lint",
        f"Plan    : {source['path'] or '<memory>'}",
        f"Programs: {source['programs']}  Networks: {source['networks']}",
        f"Result  : {'PASS' if result['ok'] else 'FAIL'}  "
        f"error {summary.get('error', 0)} / warning {summary.get('warning', 0)} / "
        f"info {summary.get('info', 0)}",
    ]
    if not result["issues"]:
        return "\n".join(lines) + "\n"
    lines.append("")
    issues = result["issues"]
    shown = issues if limit is None else issues[:limit]
    for item in shown:
        where = item.get("program") or ""
        networks = item.get("networks") or []
        if networks:
            where = f"{where} {'/'.join(networks)}".strip()
        location = f"  @{where}" if where else ""
        lines.append(
            f"[{item['severity'].upper():7}] {item['rule']}: "
            f"{item['detail']}{location}"
        )
    if limit is not None and len(issues) > limit:
        lines.append(
            f"... {len(issues) - limit} more issues (use --limit 0 to show all)"
        )
    return "\n".join(lines) + "\n"
