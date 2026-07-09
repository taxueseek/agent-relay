"""Packet schema, validation, and HANDOFF.md rendering."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "agent-relay/v1"
REQUIRED_TOP = ("schema", "id", "goal", "status", "next_actions", "files", "provenance", "routing")


def new_packet_id() -> str:
    import secrets

    now = datetime.now(timezone.utc).astimezone()
    return now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def empty_packet(
    *,
    goal: str,
    from_peer: str,
    to_peer: str,
    packet_id: str | None = None,
) -> dict[str, Any]:
    pid = packet_id or new_packet_id()
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "schema": SCHEMA,
        "id": pid,
        "goal": goal or "(未填写目标 — 请在 resume 后向用户确认)",
        "status": "ready_for_handoff",
        "done": [],
        "rejected": [],
        "open": [],
        "next_actions": [],
        "files": {"primary": [], "touched": [], "do_not_touch": []},
        "verification": [],
        "verify_cmd": "",
        "fingerprints": [],
        "delta": {"parent_id": None, "mode": "full"},
        "decisions": [],
        "constraints": [],
        "suggested_skills": [],
        "provenance": {
            "sources": [],
            "env_detected": [],
            "conflicts": [],
        },
        "routing": {
            "from_peer": from_peer,
            "to_peer": to_peer or "any",
            "recommended_peer": to_peer or "any",
            "reason": "",
            "complexity": "",
            "handoff_phrase": "Read .relay/CURRENT.md (or this HANDOFF.md) and continue next_actions in order",
        },
        "workspace": {"cwd": "", "git_head": "", "dirty": False},
        "created_at": now,
        "expires_hint_hours": 72,
    }


def validate_packet(pkt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(pkt, dict):
        return ["packet is not an object"]
    for k in REQUIRED_TOP:
        if k not in pkt:
            errors.append(f"missing field: {k}")
    if pkt.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    files = pkt.get("files")
    if files is not None and not isinstance(files, dict):
        errors.append("files must be object")
    elif isinstance(files, dict):
        for fk in ("primary", "touched"):
            if fk in files and not isinstance(files[fk], list):
                errors.append(f"files.{fk} must be list")
    prov = pkt.get("provenance")
    if prov is not None and not isinstance(prov, dict):
        errors.append("provenance must be object")
    return errors


def load_packet(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    errs = validate_packet(data)
    if errs:
        raise ValueError("invalid packet: " + "; ".join(errs))
    return data


def save_packet(dir_path: Path, pkt: dict[str, Any], sources: dict[str, Any] | None = None) -> None:
    errs = validate_packet(pkt)
    if errs:
        raise ValueError("invalid packet: " + "; ".join(errs))
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "packet.json").write_text(
        json.dumps(pkt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dir_path / "HANDOFF.md").write_text(render_handoff_md(pkt), encoding="utf-8")
    if sources is not None:
        (dir_path / "sources.json").write_text(
            json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def render_handoff_md(pkt: dict[str, Any]) -> str:
    files = pkt.get("files") or {}
    primary = files.get("primary") or []
    touched = files.get("touched") or []
    routing = pkt.get("routing") or {}
    prov = pkt.get("provenance") or {}
    lines = [
        f"# 接力：{pkt.get('goal', '')[:80]}",
        "",
        f"- **id**: `{pkt.get('id')}`",
        f"- **schema**: `{pkt.get('schema')}`",
        f"- **status**: {pkt.get('status')}",
        f"- **from → to**: {routing.get('from_peer')} → {routing.get('to_peer')}",
        f"- **recommended**: {routing.get('recommended_peer')} ({routing.get('reason', '')})",
        f"- **created_at**: {pkt.get('created_at')}",
        f"- **完成标准**: {pkt.get('goal')}",
        "",
        "## 已完成",
    ]
    done = pkt.get("done") or []
    lines.extend([f"- {x}" for x in done] if done else ["- （无）"])
    lines += ["", "## 已否决"]
    rej = pkt.get("rejected") or []
    lines.extend([f"- {x}" for x in rej] if rej else ["- （无）"])
    lines += ["", "## 未决"]
    op = pkt.get("open") or []
    lines.extend([f"- {x}" for x in op] if op else ["- （无）"])
    lines += ["", "## 下一步（按序）"]
    nxt = pkt.get("next_actions") or []
    if nxt:
        for i, x in enumerate(nxt, 1):
            lines.append(f"{i}. {x}")
    else:
        lines.append("1. 阅读 primary 文件，向用户确认下一步")
    lines += ["", "## 先读这些文件"]
    if primary:
        lines.extend([f"- `{p}`" for p in primary])
    elif touched:
        lines.extend([f"- `{p}`" for p in touched[:12]])
    else:
        lines.append("- （会话未记录文件改动；以 git status 为准）")
    if touched and primary:
        lines += ["", "### 其它触及"]
        for p in touched[:20]:
            if p not in primary:
                lines.append(f"- `{p}`")
    lines += ["", "## 验证"]
    if pkt.get("verify_cmd"):
        lines.append(f"- **verify_cmd**: `{pkt.get('verify_cmd')}`")
    ver = pkt.get("verification") or []
    if ver:
        for v in ver:
            if isinstance(v, dict):
                lines.append(f"- `{v.get('cmd', '')}` → {v.get('result', 'unknown')} {v.get('note', '')}")
            else:
                lines.append(f"- {v}")
    elif not pkt.get("verify_cmd"):
        lines.append("- （无记录）")
    fps = pkt.get("fingerprints") or []
    if fps:
        lines += ["", "## 文件指纹"]
        for fp in fps[:8]:
            if isinstance(fp, dict):
                lines.append(
                    f"- `{fp.get('path','')}` exists={fp.get('exists')} sha={fp.get('sha256','')[:12]} size={fp.get('size')}"
                )
    delta = pkt.get("delta") or {}
    if delta.get("parent_id"):
        lines += ["", f"## Delta\n- parent: `{delta.get('parent_id')}` mode={delta.get('mode')}"]
    lines += ["", "## 决策与约束"]
    for d in pkt.get("decisions") or []:
        if isinstance(d, dict):
            lines.append(f"- **{d.get('what', '')}** — {d.get('why', '')}")
        else:
            lines.append(f"- {d}")
    for c in pkt.get("constraints") or []:
        lines.append(f"- 约束：{c}")
    if not (pkt.get("decisions") or pkt.get("constraints")):
        lines.append("- （无）")
    lines += ["", "## 来源会话（provenance）"]
    for s in prov.get("sources") or []:
        if isinstance(s, dict):
            lines.append(f"- **{s.get('peer')}** [{s.get('role', '')}] `{s.get('path', '')}`")
        else:
            lines.append(f"- {s}")
    lines += [
        "",
        "## Resume 指令",
        "",
        routing.get("handoff_phrase")
        or "Read this HANDOFF.md, open primary files, continue next_actions. Do not re-ask for background already listed.",
        "",
    ]

    # ZCode-specific continuation guide
    to_peer = (routing.get("to_peer") or "").lower().strip()
    if to_peer == "zcode":
        lines += [
            "## ZCode 续跑",
            "",
            "ZCode 无 CLI invoke，需手动读档续跑：",
            "",
            "1. Read 本 HANDOFF.md 与 primary 文件",
            "2. 按 next_actions 顺序推进",
            "3. 完成后回包：",
            "   ```",
            f"   python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py pack --from zcode --to {routing.get('from_peer', '?')}",
            "   ```",
            "4. 如有 VERIFY 命令，手动运行验收",
            "",
        ]

    return "\n".join(lines) + "\n"


def is_expired(pkt: dict[str, Any]) -> bool:
    hours = int(pkt.get("expires_hint_hours") or 72)
    created = pkt.get("created_at")
    if not created:
        return False
    try:
        # support offset-aware iso
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except Exception:
        return False
    now = datetime.now(timezone.utc).astimezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    return now - dt > timedelta(hours=hours)


def write_current_pointer(project_relay: Path, pkt: dict[str, Any], packet_abs: Path) -> None:
    project_relay.mkdir(parents=True, exist_ok=True)
    content = (
        f"# agent-relay CURRENT\n\n"
        f"- packet_id: `{pkt.get('id')}`\n"
        f"- path: `{packet_abs}`\n"
        f"- goal: {pkt.get('goal')}\n"
        f"- from → to: {(pkt.get('routing') or {}).get('from_peer')} → {(pkt.get('routing') or {}).get('to_peer')}\n"
        f"- handoff: `{packet_abs / 'HANDOFF.md'}`\n\n"
        f"Resume: Read `{packet_abs / 'HANDOFF.md'}` and continue next_actions.\n"
    )
    (project_relay / "CURRENT.md").write_text(content, encoding="utf-8")
