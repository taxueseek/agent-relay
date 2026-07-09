"""Wave-2 protocol: verify_cmd, one-screen result.json, fingerprints, delta, routing."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESULT_SCHEMA = "agent-relay/result/v1"

# goal 里可写：VERIFY: <shell>  或  验收：...
_VERIFY_RE = re.compile(
    r"(?:VERIFY|验收命令|验收)\s*[:：]\s*(.+?)(?:\n|$)",
    re.I | re.M,
)


def extract_verify_cmd(goal: str, explicit: str = "") -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    m = _VERIFY_RE.search(goal or "")
    if m:
        return m.group(1).strip()
    # heuristic: file path + TOKEN in goal → build grep check
    token_m = re.search(r"(TOKEN[=:：]\s*|token[=:：]\s*)([A-Za-z0-9_.-]+)", goal or "", re.I)
    path_m = re.search(
        r"((?:/Users|/home|~)[^\s]+?\.(?:md|py|txt|json)|docs/[^\s]+?\.(?:md|py|txt))",
        goal or "",
    )
    if token_m and path_m:
        tok = token_m.group(2)
        path = path_m.group(1)
        return f"test -f {path} && rg -q {json.dumps(tok)} {path}"
    if path_m and re.search(r"(存在|写|Write|创建)", goal or "", re.I):
        path = path_m.group(1)
        return f"test -f {path}"
    return ""


def run_verify(cmd: str, *, cwd: Path, timeout: int = 30) -> dict[str, Any]:
    if not cmd:
        return {"cmd": "", "result": "skipped", "note": "no verify_cmd", "exit_code": None}
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "cmd": cmd,
            "result": "pass" if p.returncode == 0 else "fail",
            "exit_code": p.returncode,
            "stdout": (p.stdout or "")[:500],
            "stderr": (p.stderr or "")[:500],
            "note": "",
        }
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "result": "fail", "exit_code": 124, "note": "verify timeout"}
    except Exception as e:
        return {"cmd": cmd, "result": "fail", "exit_code": 1, "note": str(e)}


def file_fingerprint(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_file():
        return {"path": str(path), "exists": False, "sha256": "", "size": 0, "mtime": None}
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        st = p.stat()
        return {
            "path": str(p.resolve()),
            "exists": True,
            "sha256": h.hexdigest()[:16],
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        }
    except Exception as e:
        return {"path": str(path), "exists": False, "sha256": "", "size": 0, "error": str(e)}


def fingerprint_paths(paths: list[str], *, limit: int = 12) -> list[dict[str, Any]]:
    out = []
    for p in paths[:limit]:
        out.append(file_fingerprint(p))
    return out


def parse_one_screen_result(stdout: str) -> dict[str, Any]:
    """Extract one-screen result from callee stdout (JSON block or 三行报告)."""
    text = stdout or ""
    # fenced json
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return _normalize_result(data, source="json_fence")
        except json.JSONDecodeError:
            pass
    # bare json line
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and "done" in line:
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return _normalize_result(data, source="json_line")
            except json.JSONDecodeError:
                continue
    # 三行报告 heuristic
    done, files, open_items = [], [], []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^(做了什么|done)\s*[:：]", s, re.I):
            done.append(re.sub(r"^(做了什么|done)\s*[:：]\s*", "", s, flags=re.I)[:200])
        if re.match(r"^(改了哪些路径|files|路径)\s*[:：]", s, re.I):
            rest = re.sub(r"^(改了哪些路径|files|路径)\s*[:：]\s*", "", s, flags=re.I)
            files.extend([x.strip() for x in re.split(r"[,，;；]", rest) if x.strip()][:8])
        if re.match(r"^(未决|open)\s*[:：]", s, re.I):
            open_items.append(re.sub(r"^(未决|open)\s*[:：]\s*", "", s, flags=re.I)[:200])
    if done or files:
        return _normalize_result(
            {"done": done, "files": files, "open": open_items, "verify": "unknown"},
            source="three_line",
        )
    # fallback: first 400 chars as summary
    return _normalize_result(
        {
            "done": [text.strip()[:300]] if text.strip() else [],
            "files": [],
            "open": [],
            "verify": "unknown",
        },
        source="raw_summary",
    )


def _normalize_result(data: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "done": list(data.get("done") or [])[:12],
        "files": list(data.get("files") or data.get("files_changed") or [])[:20],
        "open": list(data.get("open") or data.get("open_items") or [])[:12],
        "verify": data.get("verify") or data.get("verification") or "unknown",
        "source": source,
    }


def write_result_json(
    packet_dir: Path,
    *,
    peer: str,
    parsed: dict[str, Any],
    verify: dict[str, Any],
    duration_sec: float,
    session_id: str = "",
    exit_code: int = 0,
) -> Path:
    out = {
        "schema": RESULT_SCHEMA,
        "packet_id": packet_dir.name,
        "peer": peer,
        "exit_code": exit_code,
        "duration_sec": round(duration_sec, 3),
        "session_id": session_id,
        "done": parsed.get("done") or [],
        "files": parsed.get("files") or [],
        "open": parsed.get("open") or [],
        "verify": verify,
        "parse_source": parsed.get("source") or "",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    # final verify status: local script wins
    if verify.get("result") in ("pass", "fail"):
        out["status"] = "verified_pass" if verify["result"] == "pass" else "verified_fail"
    elif exit_code == 0:
        out["status"] = "completed_unverified"
    else:
        out["status"] = "failed"
    path = packet_dir / "invoke" / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # also root for easy find
    (packet_dir / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def apply_delta_from_previous(
    pkt: dict[str, Any],
    prev: dict[str, Any] | None,
) -> dict[str, Any]:
    """Incremental pack: carry done/rejected, shrink open, mark parent."""
    if not prev:
        pkt["delta"] = {"parent_id": None, "mode": "full"}
        return pkt
    parent_id = prev.get("id")
    prev_done = list(prev.get("done") or [])
    prev_rej = list(prev.get("rejected") or [])
    # merge without dup
    done = list(dict.fromkeys(prev_done + list(pkt.get("done") or [])))[-20:]
    rej = list(dict.fromkeys(prev_rej + list(pkt.get("rejected") or [])))[-20:]
    pkt["done"] = done
    pkt["rejected"] = rej
    # open: prefer new open, drop items already done
    done_set = set(done)
    new_open = [x for x in (pkt.get("open") or []) if x not in done_set][:8]
    pkt["open"] = new_open
    pkt["delta"] = {
        "parent_id": parent_id,
        "mode": "delta",
        "carried_done": len(prev_done),
        "carried_rejected": len(prev_rej),
    }
    # inherit fingerprints for unchanged note
    if prev.get("fingerprints") and not pkt.get("fingerprints"):
        pkt["fingerprints"] = prev.get("fingerprints")
    return pkt


def complexity_route(task: str, present: dict[str, Any]) -> dict[str, str]:
    """Route by complexity: trivial→kimi/grok small; hard→claude."""
    t = (task or "").lower()
    trivial_kw = (
        "写文件",
        "两行",
        "一行",
        "token",
        "proof",
        "验收",
        "可见印证",
        "创建文件",
        "rename",
        "typo",
        "改文案",
        "只写",
    )
    hard_kw = (
        "架构",
        "重构",
        "设计",
        "多文件",
        "迁移",
        "并发",
        "安全",
        "性能",
        "全量",
        "协议",
        "插件",
        "review",
        "审查",
    )
    score = 0
    for k in trivial_kw:
        if k in t:
            score -= 2
    for k in hard_kw:
        if k in t:
            score += 3
    if len(t) < 40:
        score -= 1
    if len(t) > 200:
        score += 1

    def has(p: str) -> bool:
        return p in present and getattr(present[p], "present", True)

    # present may be PeerStatus or dict
    def on(p: str) -> bool:
        v = present.get(p)
        if v is None:
            return False
        if hasattr(v, "present"):
            return bool(v.present)
        return True

    if score <= -2:
        for cand in ("kimi_code", "mimo", "grok", "claude"):
            if on(cand):
                return {
                    "recommended_peer": cand,
                    "reason": f"trivial 任务 score={score}，优先小成本 peer",
                    "complexity": "trivial",
                }
    if score >= 3:
        for cand in ("claude", "grok", "kimi_code", "mimo"):
            if on(cand):
                return {
                    "recommended_peer": cand,
                    "reason": f"hard 任务 score={score}，优先旗舰/强推理",
                    "complexity": "hard",
                }
    for cand in ("claude", "grok", "kimi_code", "mimo"):
        if on(cand):
            return {
                "recommended_peer": cand,
                "reason": f"medium 任务 score={score}",
                "complexity": "medium",
            }
    return {"recommended_peer": "any", "reason": "无可用 peer", "complexity": "unknown"}


def mode_instruction(mode: str, lean: bool = True) -> str:
    mode = (mode or "continue").lower()
    if mode == "implement":
        base = (
            "实现模式：最小 diff；禁止大范围探索/全仓搜索；"
            "最多 Read primary 中的文件；完成后输出一屏 JSON："
            '{"done":[],"files":[],"open":[],"verify":"pass|fail|unknown"}'
        )
    elif mode == "review":
        base = (
            "审查模式：只读；最多 1-3 条问题；禁止改业务代码（除非阻塞 bug）；"
            '输出 JSON：{"done":["审查结论"],"files":[],"open":[],"verify":"unknown"}'
        )
    elif mode == "fix":
        base = (
            "修复模式：只改与 bug 相关文件；先复现再改；"
            '输出 JSON：{"done":[],"files":[],"open":[],"verify":"pass|fail"}'
        )
    else:
        base = (
            "继续模式：按 goal/next 推进；"
            '结束输出 JSON：{"done":[],"files":[],"open":[],"verify":"unknown"}'
        )
    if lean:
        base += " 禁止冗长复述背景。"
    return base
