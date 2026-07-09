"""Relay harness patterns — distilled from codex-workflows, mapped to agent-relay.

We do NOT re-host a multi-agent DAG. Patterns guide pack / bridge / delegate
composition for continuity + alignment.
"""

from __future__ import annotations

import re
from typing import Any

# id → pattern card
PATTERNS: dict[str, dict[str, Any]] = {
    "trust_loop": {
        "name": "信任闭环",
        "failure_mode": "vague goals + unsupported claims",
        "when": "昂贵 delegate/handoff 前后",
        "shape": "goal-lint → pack/delegate → job-status/VERIFY →（可选）claim 对照仓库",
        "commands": [
            'relay goal-lint --goal "…"',
            'relay delegate --to claude --task "… VERIFY: …"',
            "relay job-status --packet latest",
        ],
    },
    "fresh_context_review": {
        "name": "新鲜上下文审查",
        "failure_mode": "self-preferential judging（作者审自己）",
        "when": "实现后要 review，且实现方 ≠ 审查方",
        "shape": "implement peer A → pack → delegate --mode review --to peer B",
        "commands": [
            'relay pack --to claude --goal "实现完成，待审查"',
            'relay delegate --to kimi_code --mode review --task "审查 HANDOFF 中 primary 文件"',
        ],
    },
    "hedged_race": {
        "name": "对冲竞速",
        "failure_mode": "单路径死磕 / 付最慢路径的全价",
        "when": "根因不明，多假说可并行试",
        "shape": "同一 goal 开 2 个 delegate（不同 peer/假说）；先 VERIFY 通过者胜，另一份记 open",
        "commands": [
            'relay plan --task "… 假说A" --to claude',
            'relay plan --task "… 假说B" --to grok',
            "# 人工或主管 agent 先采纳先过 VERIFY 的结果",
        ],
    },
    "quota_handoff": {
        "name": "额度交接",
        "failure_mode": "上下文丢失 / 复述幻觉",
        "when": "额度将尽、PreCompact、换产品续跑",
        "shape": "pack（digger 证据）→ resume latest；文件+git 为真源",
        "commands": [
            'relay pack --goal "…" --budget short',
            "relay resume latest",
        ],
    },
    "cross_peer_align": {
        "name": "跨环境对齐",
        "failure_mode": "多 peer 各说各话 / provenance 不全",
        "when": "不知道另一产品做到哪",
        "shape": "bridge <keyword> → 读 HANDOFF provenance",
        "commands": [
            'relay bridge "关键词" --deep',
            "relay resume latest",
        ],
    },
    "supervisor_checkpoint": {
        "name": "主管门控",
        "failure_mode": "无人值守时盲写高风险区",
        "when": "支付/生产/密钥相关；goal-lint involvement=interactive",
        "shape": "plan 先出合同 → 用户确认 → 再 delegate；默认不自动写高风险路径",
        "commands": [
            'relay goal-lint --goal "…"',
            'relay plan --task "…" --to claude',
            "# 确认后再 delegate",
        ],
    },
    "anti_overbuild": {
        "name": "反过度建设",
        "failure_mode": "小任务开舰队 / deep pack 浪费",
        "when": "两行文件、typo、proof token",
        "shape": "quick scale + trivial peer（kimi/grok）+ short budget；无 --deep",
        "commands": [
            'relay plan --task "写两行 proof" --to kimi_code',
            'relay delegate --to kimi_code --task "… VERIFY: …" --budget short',
        ],
    },
}


def suggest_patterns(task: str, *, limit: int = 3) -> list[dict[str, Any]]:
    t = (task or "").lower()
    scores: list[tuple[int, str]] = []
    rules = [
        ("trust_loop", ("验证", "verify", "claim", "目标", "goal", "验收", "harden")),
        ("fresh_context_review", ("审查", "review", "code review", "互审")),
        ("hedged_race", ("根因", "假说", "race", "并行试", "flaky", "不确定")),
        ("quota_handoff", ("额度", "压缩", "compact", "交接", "handoff", "续跑", "resume")),
        ("cross_peer_align", ("对齐", "bridge", "另一边", "跨", "做到哪")),
        ("supervisor_checkpoint", ("支付", "生产", "密钥", "危险", "门控", "确认")),
        ("anti_overbuild", ("两行", "typo", "proof", "小改", "改文案", "token")),
    ]
    for pid, kws in rules:
        s = sum(2 for k in kws if k in t)
        if s:
            scores.append((s, pid))
    # always offer quota_handoff as baseline if nothing matched
    if not scores:
        scores = [(1, "quota_handoff"), (1, "trust_loop"), (1, "anti_overbuild")]
    scores.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for _, pid in scores[:limit]:
        card = dict(PATTERNS[pid])
        card["id"] = pid
        out.append(card)
    return out


def format_patterns(cards: list[dict[str, Any]]) -> str:
    lines = []
    for c in cards:
        lines.append(f"PATTERN {c.get('id')} · {c.get('name')}")
        lines.append(f"  failure_mode: {c.get('failure_mode')}")
        lines.append(f"  when: {c.get('when')}")
        lines.append(f"  shape: {c.get('shape')}")
        for cmd in c.get("commands") or []:
            lines.append(f"  $ {cmd}")
        lines.append("")
    return "\n".join(lines).rstrip()
