"""Dry-run planner — codex-workflows --plan analogue for agent-relay.

No digger extract, no peer invoke. Sizes route + goal contract + risk only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .goal_lint import lint_goal
from .patterns import suggest_patterns
from .peers import probe_all, suggest_to_peer
from .result_protocol import complexity_route


def plan_action(
    *,
    task: str = "",
    goal: str = "",
    to_peer: str = "",
    from_peer: str = "auto",
    mode: str = "",
    verify_cmd: str = "",
    action: str = "delegate",  # pack | delegate | handoff | bridge
    cwd: Path | None = None,
) -> dict[str, Any]:
    text = (goal or task or "").strip()
    rows = probe_all(include_optional=True)
    present = {r.id: r for r in rows}
    route = complexity_route(text, present)
    if to_peer:
        rec = to_peer
        reason = f"用户指定 --to {to_peer}"
        complexity = route.get("complexity") or "unknown"
    else:
        rec = route.get("recommended_peer") or "any"
        reason = route.get("reason") or ""
        complexity = route.get("complexity") or "unknown"
        # fallback suggest
        if rec == "any":
            s = suggest_to_peer(text, from_peer=from_peer if from_peer != "auto" else "grok")
            rec = s.get("recommended_peer") or rec
            reason = s.get("reason") or reason

    contract = lint_goal(text, verify_cmd=verify_cmd, mode=mode, peer=rec)
    patterns = suggest_patterns(text, limit=2)

    peer_row = present.get(rec)
    peer_ok = bool(peer_row and peer_row.present and getattr(peer_row, "delegate", True))
    if action in ("pack", "bridge"):
        peer_ok = bool(peer_row and peer_row.present) if peer_row else True

    risks: list[str] = list(contract.get("warnings") or [])
    if action in ("delegate", "handoff") and rec and not peer_ok:
        risks.append(f"peer={rec} 不可 delegate（absent 或无 CLI）")
    if action in ("delegate", "handoff") and rec == "zcode":
        risks.append("zcode 无 CLI invoke：handoff 会降级为 resume 指令")
    if contract.get("grade") != "ready":
        risks.append("goal 未 hardened：建议先 goal-lint 再真跑")

    est = {
        "pack_evidence": "cheap" if action != "bridge" else "medium",
        "peer_tokens": "none (plan only)",
        "suggested_budget": "short" if contract.get("scale") == "quick" else "medium",
        "suggested_timeout_sec": 120 if contract.get("scale") == "quick" else 300,
        "suggested_max_turns": 4 if contract.get("scale") == "quick" else 8,
    }

    return {
        "schema": "agent-relay/plan/v1",
        "action": action,
        "task": text[:500],
        "route": {
            "from_peer": from_peer,
            "to_peer": rec,
            "reason": reason,
            "complexity": complexity,
            "peer_delegate_ok": peer_ok,
        },
        "contract": contract,
        "patterns": [{"id": p["id"], "name": p["name"]} for p in patterns],
        "estimates": est,
        "risks": risks,
        "next_commands": _next_cmds(action, rec, contract, text),
        "cwd": str(cwd or Path.cwd()),
    }


def _next_cmds(action: str, peer: str, contract: dict[str, Any], text: str) -> list[str]:
    RELAY = 'python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py'
    goal = contract.get("hardened_goal") or text
    v = contract.get("verify_cmd") or ""
    cmds = [f'{RELAY} goal-lint --goal {json_quote(goal)}']
    if action == "pack":
        cmds.append(f'{RELAY} pack --to {peer} --goal {json_quote(goal)}')
    elif action == "bridge":
        kw = text[:40] or "topic"
        cmds.append(f'{RELAY} bridge {json_quote(kw)}')
    elif action == "handoff":
        cmds.append(f'{RELAY} handoff --to {peer} --goal {json_quote(goal)} --wait')
    else:
        vflag = f" --verify-cmd {json_quote(v)}" if v else ""
        mode = contract.get("mode_hint") or "implement"
        cmds.append(
            f'{RELAY} delegate --to {peer} --mode {mode} --task {json_quote(goal)}{vflag}'
        )
    return cmds


def json_quote(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)


def format_plan(p: dict[str, Any], *, as_json: bool = False) -> str:
    if as_json:
        import json

        return json.dumps(p, ensure_ascii=False, indent=2)
    r = p.get("route") or {}
    c = p.get("contract") or {}
    e = p.get("estimates") or {}
    lines = [
        f"PLAN action={p.get('action')} grade={c.get('grade')} scale={c.get('scale')}",
        f"route: {r.get('from_peer')} → {r.get('to_peer')} ({r.get('complexity')}) "
        f"delegate_ok={r.get('peer_delegate_ok')}",
        f"reason: {r.get('reason')}",
        f"sandbox: {c.get('sandbox')} involvement: {c.get('involvement')}",
        f"verify_cmd: {c.get('verify_cmd') or '(none)'}",
        f"budget/timeout/turns: {e.get('suggested_budget')}/"
        f"{e.get('suggested_timeout_sec')}s/{e.get('suggested_max_turns')}",
        f"patterns: {', '.join(x.get('id', '') for x in (p.get('patterns') or []))}",
    ]
    risks = p.get("risks") or []
    if risks:
        lines.append("risks:")
        for x in risks:
            lines.append(f"  ! {x}")
    lines.append("next:")
    for cmd in p.get("next_commands") or []:
        lines.append(f"  $ {cmd}")
    return "\n".join(lines)
