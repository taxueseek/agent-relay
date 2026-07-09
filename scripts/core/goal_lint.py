"""Goal hardening — inspired by codex-workflows GoalLint.

Turns a vague handoff goal into a falsifiable task contract before pack/delegate
spends peer quota. Pure heuristics (no model call).
"""

from __future__ import annotations

import re
from typing import Any

from .result_protocol import extract_verify_cmd

GOAL_CONTRACT_SCHEMA = "agent-relay/goal-contract/v1"

_VAGUE = re.compile(
    r"^(帮我|请|麻烦|看看|优化一下|改进|处理一下|弄一下|搞定|完善|调整).{0,8}$"
    r"|^(fix|improve|handle|look at|update)\s+it\.?$"
    r"|看起来|差不多|尽量|适当|更好|更好一点",
    re.I,
)

_WRITE_HINT = re.compile(
    r"(写|创建|改|修|实现|迁移|删除|重命名|apply|write|implement|fix|migrate|delete)",
    re.I,
)
_READ_HINT = re.compile(
    r"(审查|review|审计|audit|分析|对齐|bridge|读|总结|调研|research|检查|verify|claim)",
    re.I,
)
_RISK_HINT = re.compile(
    r"(支付|payments?|生产|prod|生产库|drop |rm -rf|密钥|secret|token|credential|迁移全量)",
    re.I,
)


def lint_goal(
    goal: str,
    *,
    verify_cmd: str = "",
    mode: str = "",
    peer: str = "",
) -> dict[str, Any]:
    """Return a hardened goal contract + warnings. Never raises on empty input."""
    raw = (goal or "").strip()
    warnings: list[str] = []
    scores: dict[str, int] = {}

    if not raw:
        warnings.append("goal 为空：pack/delegate 前必须有可判定完成标准")
        scores["specificity"] = 0
    elif len(raw) < 12 or _VAGUE.search(raw):
        warnings.append("goal 过泛：缺路径、验收命令或明确产物")
        scores["specificity"] = 1
    elif len(raw) < 40:
        scores["specificity"] = 2
    else:
        scores["specificity"] = 3

    vcmd = extract_verify_cmd(raw, verify_cmd)
    if vcmd:
        scores["verify"] = 3
    else:
        warnings.append("缺 VERIFY：建议 goal 内写 VERIFY: test -f path && rg -q TOKEN path")
        scores["verify"] = 0

    # sandbox / write surface
    wants_write = bool(_WRITE_HINT.search(raw))
    wants_read = bool(_READ_HINT.search(raw))
    if wants_write and not wants_read:
        sandbox = "workspace-write"
        involvement = "checkpointed"
    elif wants_read and not wants_write:
        sandbox = "read-only"
        involvement = "hands_off"
    else:
        sandbox = "workspace-write" if wants_write else "read-only"
        involvement = "checkpointed"

    if _RISK_HINT.search(raw):
        warnings.append("高风险关键词：建议 human 门控或先 --plan 再 delegate")
        involvement = "interactive"
        scores["risk"] = 3
    else:
        scores["risk"] = 1 if wants_write else 0

    # objective vs non-goals heuristics
    objective = raw
    non_goals: list[str] = []
    if "不要" in raw or "勿" in raw or "not " in raw.lower() or "don't" in raw.lower():
        for part in re.split(r"[;；。\n]", raw):
            if re.search(r"不要|勿|not |don't|do not", part, re.I):
                non_goals.append(part.strip()[:120])
    if not non_goals:
        non_goals = [
            "不扩 scope 到未点名的子系统",
            "不写密钥/token 进 packet",
            "不把摘要幻觉写成决策",
        ]

    success = []
    if vcmd:
        success.append(f"本地验收通过：{vcmd}")
    success.append("result.json 有 done/files；verify 为 pass 或 completed_unverified")
    if wants_write:
        success.append("目标路径存在且 diff 最小")
    else:
        success.append("结论可追溯到文件路径或证据来源")

    failure = [
        "无 VERIFY 且无产物路径",
        "改了 do_not_touch 或无关大范围重构",
        "仅口头「看起来没问题」无证据",
    ]

    stop = [
        "VERIFY 通过",
        "blocked 且 open 写清阻塞原因",
        "达到 max-turns / timeout",
    ]

    # scale hint (anti-overbuild)
    scale = "quick"
    if len(raw) > 180 or re.search(r"全量|整个|所有|audit all|every ", raw, re.I):
        scale = "deep"
        warnings.append("规模偏大：优先 quick 切片或 bridge 对齐，再 deep")
    elif scores.get("specificity", 0) >= 2 and scores.get("verify", 0) >= 3:
        scale = "standard" if wants_write else "quick"

    # hardened one-liner for packet.goal
    hardened = raw
    if raw and vcmd and "VERIFY" not in raw.upper() and "验收" not in raw:
        hardened = f"{raw.rstrip()}  VERIFY: {vcmd}"

    grade = "ready"
    if scores.get("specificity", 0) < 2 or scores.get("verify", 0) < 3:
        grade = "needs_work"
    if not raw:
        grade = "blocked"

    return {
        "schema": GOAL_CONTRACT_SCHEMA,
        "grade": grade,  # ready | needs_work | blocked
        "raw_goal": raw,
        "hardened_goal": hardened,
        "objective": objective[:500],
        "non_goals": non_goals[:8],
        "verify_cmd": vcmd,
        "success_criteria": success[:8],
        "failure_criteria": failure[:8],
        "stop_conditions": stop[:6],
        "sandbox": sandbox,
        "involvement": involvement,  # hands_off | checkpointed | interactive
        "scale": scale,  # quick | standard | deep
        "mode_hint": mode or ("implement" if wants_write else "review" if wants_read else "continue"),
        "peer_hint": peer or "",
        "warnings": warnings,
        "scores": scores,
        "artifacts": [
            "packet.json",
            "HANDOFF.md",
            "result.json",
        ],
    }


def format_contract(c: dict[str, Any], *, as_json: bool = False) -> str:
    if as_json:
        import json

        return json.dumps(c, ensure_ascii=False, indent=2)
    lines = [
        f"GOAL_LINT grade={c.get('grade')} scale={c.get('scale')} sandbox={c.get('sandbox')} "
        f"involvement={c.get('involvement')}",
        f"objective: {c.get('objective', '')[:300]}",
        f"hardened: {c.get('hardened_goal', '')[:400]}",
        f"verify_cmd: {c.get('verify_cmd') or '(none)'}",
        f"mode_hint: {c.get('mode_hint')}",
        "success:",
    ]
    for s in c.get("success_criteria") or []:
        lines.append(f"  - {s}")
    lines.append("failure:")
    for s in c.get("failure_criteria") or []:
        lines.append(f"  - {s}")
    lines.append("non_goals:")
    for s in c.get("non_goals") or []:
        lines.append(f"  - {s}")
    warns = c.get("warnings") or []
    if warns:
        lines.append("warnings:")
        for w in warns:
            lines.append(f"  ! {w}")
    return "\n".join(lines)
