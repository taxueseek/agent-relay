"""Synthesize packet from evidence (template fill, no free hallucination of paths)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .packet import empty_packet
from .peers import probe_all, suggest_to_peer
from .result_protocol import (
    apply_delta_from_previous,
    extract_verify_cmd,
    fingerprint_paths,
)

_NOISE_RE = re.compile(
    r"(command-message|command-name|command-args|This session is being continued|"
    r"summary below covers|<\s*/?\s*command)",
    re.I,
)


def _clean_text(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_noise_user_text(text: str) -> bool:
    if not text or len(text.strip()) < 4:
        return True
    if _NOISE_RE.search(text):
        return True
    if text.strip().startswith("{") and "timestamp" in text:
        return True
    return False


def _user_message_snippets(messages: str, limit: int = 5) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    role = None
    for line in (messages or "").splitlines():
        if re.match(r"^\[USER\]", line, re.I) or re.match(r"^\[user\]", line):
            if current and role == "user":
                chunks.append("\n".join(current).strip())
            role = "user"
            current = []
            # inline text after tag
            rest = re.sub(r"^\[USER\]\s*", "", line, flags=re.I).strip()
            if rest and not re.match(r"^\d{4}-", rest):
                current.append(rest)
            continue
        if line.startswith("[") and "]" in line[:24]:
            if current and role == "user":
                chunks.append("\n".join(current).strip())
            role = "other"
            current = []
            continue
        if role == "user":
            current.append(line)
    if current and role == "user":
        chunks.append("\n".join(current).strip())

    if not chunks:
        for m in re.finditer(r"\[user\]\s*(.+?)(?=\[|$)", messages or "", re.I | re.S):
            chunks.append(m.group(1).strip()[:500])

    cleaned = []
    for c in chunks:
        c2 = _clean_text(c)
        if not _is_noise_user_text(c2):
            cleaned.append(c2[:400])
    return cleaned[:limit]


def _infer_goal(goal_arg: str, messages: str) -> str:
    if goal_arg and goal_arg.strip():
        return goal_arg.strip()
    users = _user_message_snippets(messages, limit=5)
    if users:
        text = users[-1]
        if len(text) > 200:
            text = text[:200] + "…"
        return text
    return "（从会话未能提取明确目标 — resume 后请向用户确认完成标准）"


def _decisions_from_knowledge(knowledge: list[dict[str, Any]], budget: str) -> list[dict[str, str]]:
    out = []
    max_n = 5 if budget == "short" else 12
    for item in knowledge or []:
        if not isinstance(item, dict):
            continue
        cat = (item.get("category") or "").lower()
        content = str(item.get("content") or item.get("summary") or "").strip()
        if cat == "lesson" and (
            content.startswith("Tool ") or "Traceback" in content or len(content) > 240
        ):
            continue
        if cat in ("decision", "value", "pattern", "correction") or (cat == "lesson" and content):
            out.append(
                {
                    "what": content[:300],
                    "why": cat or "extracted",
                    "evidence": "extract-knowledge",
                }
            )
        elif content and cat not in ("lesson",):
            out.append(
                {
                    "what": content[:300],
                    "why": cat or "extracted",
                    "evidence": "extract-knowledge",
                }
            )
        if len(out) >= max_n:
            break
    return out


def _next_actions(goal: str, files: list[str], messages: str, *, budget: str = "short") -> list[str]:
    """short: max 3 actions; medium: max 5."""
    cap = 3 if budget == "short" else 5
    actions: list[str] = []
    if files:
        actions.append(f"阅读 primary（≤{min(len(files), 3 if budget == 'short' else 5)} 个）")
    actions.append(f"按完成标准推进：{_clean_text(goal)[:120]}")
    if budget != "short":
        users = _user_message_snippets(messages, limit=2)
        if users:
            last = users[-1]
            if last[:40] not in goal:
                actions.append("结合最近有效意图：" + last[:100])
        actions.append("验证后 pack/invoke 回报")
    return actions[:cap]


def synthesize(
    evidence: dict[str, Any],
    *,
    goal: str = "",
    from_peer: str = "unknown",
    to_peer: str = "",
    budget: str = "short",
    previous_packet: dict[str, Any] | None = None,
    verify_cmd: str = "",
) -> dict[str, Any]:
    messages = evidence.get("messages") or ""
    files = list(evidence.get("files") or [])
    knowledge = evidence.get("knowledge") or []
    ws = evidence.get("workspace") or {}
    session_path = evidence.get("session_path") or ""
    peer = evidence.get("peer") or from_peer

    g = _infer_goal(goal, messages)
    sug = suggest_to_peer(g if not to_peer else "", from_peer=peer)
    dest = to_peer or sug["recommended_peer"]

    # short: primary≤3 touched≤5; medium: primary≤5 touched≤12
    max_primary = 3 if budget == "short" else 5
    max_touched = 5 if budget == "short" else 12
    # pin only the lean entry when task is about this skill (avoid dumping many skill files)
    blob = (g + " " + (session_path or "") + " " + messages[:300]).lower()
    if any(k in blob for k in ("agent-relay", "接力", "handoff", "协作 skill", "跨环境")):
        skill_md = Path.home() / ".agents" / "skills" / "agent-relay" / "SKILL.md"
        if skill_md.exists():
            sp = str(skill_md)
            if sp not in files:
                files.insert(0, sp)
        if budget != "short":
            for rel in ("scripts/relay_cli.py", "scripts/core/invoke.py"):
                p = Path.home() / ".agents" / "skills" / "agent-relay" / rel
                if p.exists():
                    sp = str(p)
                    if sp not in files:
                        files.insert(1, sp)

    # prefer existing files for primary; short budget: drop non-existing noise early
    existing = []
    missing = []
    for f in files:
        if Path(f).expanduser().exists():
            existing.append(f)
        else:
            missing.append(f)
    ordered = existing + (missing if budget != "short" else missing[:2])
    primary = ordered[:max_primary]
    touched = ordered[:max_touched]

    env_detected = [p.id for p in probe_all() if p.present]

    pkt = empty_packet(goal=g, from_peer=peer, to_peer=dest)
    pkt["done"] = []
    pkt["open"] = []
    if "（从会话未能提取" in g:
        pkt["open"].append("完成标准未在会话中明确，需用户确认")
    pkt["next_actions"] = _next_actions(g, ordered, messages, budget=budget)
    pkt["files"] = {"primary": primary, "touched": touched, "do_not_touch": []}
    # short: at most 3 decisions
    decs = _decisions_from_knowledge(knowledge, budget)
    pkt["decisions"] = decs[:3] if budget == "short" else decs
    pkt["provenance"]["sources"] = [
        {"peer": peer, "path": session_path, "role": "primary"},
    ]
    pkt["provenance"]["env_detected"] = env_detected
    pkt["routing"]["from_peer"] = peer
    pkt["routing"]["to_peer"] = dest
    pkt["routing"]["recommended_peer"] = dest
    pkt["routing"]["reason"] = sug.get("reason") or f"pack from {peer}"
    if sug.get("complexity"):
        pkt["routing"]["complexity"] = sug["complexity"]

    # ZCode-specific handoff_phrase: no CLI invoke, manual resume
    if dest == "zcode":
        pkt["routing"]["handoff_phrase"] = (
            f"ZCode 需手动接手。Read HANDOFF.md 与 primary 文件，按 next_actions 顺序推进。"
            f"完成后运行: python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py pack --from zcode --to {peer}"
        )
    pkt["workspace"] = {
        "cwd": ws.get("cwd") or "",
        "git_head": ws.get("git_head") or "",
        "dirty": bool(ws.get("dirty")),
    }
    if ws.get("diff_stat"):
        pkt["open"].append("工作区有未提交变更，resume 后先 git status")
    if ws.get("dirty_files"):
        pkt["open"].append(f"git 脏文件 {len(ws['dirty_files'])} 个已并入 files 线索")

    # verify_cmd + fingerprints
    vcmd = extract_verify_cmd(g, verify_cmd)
    pkt["verify_cmd"] = vcmd
    if vcmd:
        pkt["verification"] = [{"cmd": vcmd, "result": "unknown", "note": "to be run after invoke"}]
        pkt["open"] = [x for x in (pkt.get("open") or []) if "完成标准" not in x]
    else:
        pkt["open"].append("未提供 VERIFY: 命令 — 建议 goal 含可脚本验收")

    pkt["fingerprints"] = fingerprint_paths(primary, limit=max_primary)

    skills = []
    gl = g.lower()
    if any(x in gl for x in ("skill", "协作", "relay", "handoff")):
        skills.append("agent-relay")
    if "session" in gl or "会话" in g:
        skills.append("session-digger")
    pkt["suggested_skills"] = skills

    # incremental delta from previous packet
    pkt = apply_delta_from_previous(pkt, previous_packet)
    return pkt


def merge_bridge_sources(
    base_pkt: dict[str, Any],
    search_text: str,
    keyword: str,
) -> dict[str, Any]:
    """Attach bridge search hits into provenance."""
    sources = list((base_pkt.get("provenance") or {}).get("sources") or [])
    peers_hit: set[str] = set()
    hit_n = 0

    paths = re.findall(r"(/\S+\.jsonl)", search_text or "")
    for p in paths[:15]:
        peer = "unknown"
        pl = p.lower()
        if "grok" in pl:
            peer = "grok"
        elif "claude" in pl:
            peer = "claude"
        elif "zcode" in pl:
            peer = "zcode"
        elif "kimi" in pl:
            peer = "kimi_code"
        elif "codex" in pl:
            peer = "codex"
        peers_hit.add(peer)
        hit_n += 1
        if not any(s.get("path") == p for s in sources if isinstance(s, dict)):
            sources.append({"peer": peer, "path": p, "role": "related"})

    for m in re.finditer(
        r"---\s*\[\d+/\d+\]\s*([^\s(]+)\s*\((claude|grok|codex|zcode|kimi[^,)]*|kimi_code)[^)]*\)",
        search_text or "",
        re.I,
    ):
        label = m.group(1).strip()
        peer = m.group(2).lower()
        if peer.startswith("kimi"):
            peer = "kimi_code"
        peers_hit.add(peer)
        hit_n += 1
        path = f"search://{peer}/{label}"
        if not any(s.get("path") == path for s in sources if isinstance(s, dict)):
            sources.append({"peer": peer, "path": path, "role": "related"})

    if hit_n == 0:
        for peer in ("claude", "grok", "codex", "zcode", "kimi_code"):
            if re.search(rf"\({peer}\b", search_text or "", re.I):
                peers_hit.add(peer)
                hit_n += 1
                sources.append(
                    {"peer": peer, "path": f"search://keyword/{keyword}", "role": "related"}
                )

    base_pkt.setdefault("provenance", {})["sources"] = sources
    base_pkt["open"] = list(base_pkt.get("open") or [])
    base_pkt["open"].append(f"bridge 关键词「{keyword}」命中约 {hit_n} 条会话线索")
    if len(peers_hit) > 1:
        base_pkt["open"].append(f"多 peer 相关：{', '.join(sorted(peers_hit))}")
    return base_pkt
