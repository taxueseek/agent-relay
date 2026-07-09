"""L0 evidence collection via session-digger echolib / sd-recall."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .paths import discover_sd_root  # optional digger root

_SD_RECALL_MOD = None  # cached import of session-digger sd-recall.py


def _ensure_echolib():
    sd = discover_sd_root()
    if not sd:
        raise RuntimeError("session-digger not found; set SESSION_DIGGER_ROOT")
    scripts = str(sd / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import echolib  # type: ignore

    return echolib, sd


def sd_recall_py(sd: Path) -> Path:
    return sd / "scripts" / "sd-recall.py"


def load_sd_recall_module():
    """Import session-digger's sd-recall.py (hyphen name) via importlib."""
    global _SD_RECALL_MOD
    if _SD_RECALL_MOD is not None:
        return _SD_RECALL_MOD
    sd = discover_sd_root()
    if not sd:
        return None
    path = sd_recall_py(sd)
    if not path.is_file():
        return None
    # ensure echolib importable for sd-recall
    scripts = str(sd / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("agent_relay_sd_recall", path)
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    _SD_RECALL_MOD = mod
    return mod


def run_sd_recall(
    args: list[str],
    timeout: int = 120,
    cwd: Path | str | None = None,
) -> tuple[int, str, str]:
    sd = discover_sd_root()
    if not sd:
        return 1, "", "session-digger not found"
    cmd = [sys.executable, str(sd_recall_py(sd)), *args]
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def git_workspace(cwd: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cwd": str(cwd.resolve()),
        "git_head": "",
        "dirty": False,
        "diff_stat": "",
        "dirty_files": [],
    }
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if head.returncode == 0:
            out["git_head"] = head.stdout.strip()
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if st.returncode == 0:
            out["dirty"] = bool(st.stdout.strip())
            dirty: list[str] = []
            for line in st.stdout.splitlines():
                # XY PATH or XY ORIG -> PATH
                raw = line[3:] if len(line) > 3 else line
                if " -> " in raw:
                    raw = raw.split(" -> ", 1)[1]
                raw = raw.strip().strip('"')
                if not raw:
                    continue
                p = (cwd / raw).resolve()
                dirty.append(str(p))
            out["dirty_files"] = dirty[:40]
        ds = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if ds.returncode == 0:
            out["diff_stat"] = ds.stdout.strip()[:2000]
    except Exception:
        pass
    return out


def list_sessions(
    peer: str = "cross",
    limit: int = 10,
    scope: str = "current",
    cwd: Path | str | None = None,
    *,
    prefer_native: bool = True,
) -> list[dict[str, Any]]:
    """List sessions for a peer / project.

    Default: **native** multi-env discovery (digger-inspired, no digger install).
    If digger is installed and prefer_native=False, may use digger adapters.
    ``cwd`` scopes ``current`` to that project root.
    """
    project = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()

    agent = peer
    if peer == "kimi":
        agent = "kimi_code"
    if peer in ("auto",):
        agent = "cross"
    if peer not in (
        "claude",
        "grok",
        "kimi",
        "kimi_code",
        "cross",
        "auto",
        "codex",
        "zcode",
    ):
        agent = "cross"

    # 1) Native first (standalone)
    if prefer_native:
        from .session_discover import find_sessions as native_find

        rows = native_find(
            agent=agent,
            scope=scope,
            limit=limit,
            project=project,
        )
        if rows:
            return rows
        # empty native is authoritative for "no sessions in scope"
        # still try digger only when digger is available and native found nothing
        # with scope=all we already scanned; return empty unless digger can help zcode db

    # 2) Optional digger / echolib (when installed)
    if peer == "zcode" or agent == "zcode":
        try:
            echolib, _sd = _ensure_echolib()
        except RuntimeError:
            echolib = None
        if echolib is not None:
            try:
                rows = echolib.zcode_db_list_sessions(limit=limit, keyword="")
                if isinstance(rows, list):
                    return _normalize_session_rows(rows, default_agent="zcode")
                if isinstance(rows, dict) and "sessions" in rows:
                    return _normalize_session_rows(rows["sessions"], default_agent="zcode")
            except Exception:
                try:
                    return _normalize_session_rows(
                        echolib.zcode_list_sessions(limit=limit) or [],
                        default_agent="zcode",
                    )
                except Exception:
                    pass

    if not prefer_native or discover_sd_root():
        mod = load_sd_recall_module()
        if mod and hasattr(mod, "find_sessions"):
            old = os.getcwd()
            try:
                os.chdir(project)
                entries = mod.find_sessions(scope=scope, limit=limit, agent=agent)
            except Exception:
                entries = None
            finally:
                try:
                    os.chdir(old)
                except OSError:
                    pass
            if entries:
                rows = []
                for item in entries:
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        sid, path, at = item[0], item[1], item[2]
                    elif isinstance(item, dict):
                        sid = item.get("id") or item.get("session_id") or ""
                        path = item.get("path") or item.get("jsonl_path") or ""
                        at = item.get("agent") or item.get("peer") or agent
                    else:
                        continue
                    if not path:
                        continue
                    at_n = str(at).lower()
                    if at_n == "kimi":
                        at_n = "kimi_code"
                    rows.append(
                        {
                            "id": str(sid),
                            "path": str(path),
                            "agent": at_n,
                            "source": "session-digger",
                        }
                    )
                if rows:
                    return rows

        code, stdout, _stderr = run_sd_recall(
            ["sessions", "--scope", scope, "--limit", str(limit), "--agent", agent],
            cwd=project,
        )
        if code == 0 and stdout.strip():
            rows = _parse_sessions_table(stdout)
            for r in rows:
                r["source"] = "session-digger-cli"
            if rows:
                return rows

    # 3) Native again if we skipped it
    if not prefer_native:
        from .session_discover import find_sessions as native_find

        return native_find(agent=agent, scope=scope, limit=limit, project=project)

    return []


def _normalize_session_rows(
    rows: list[Any],
    default_agent: str = "unknown",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if isinstance(r, dict):
            path = r.get("path") or r.get("jsonl_path") or r.get("file") or ""
            sid = r.get("id") or r.get("session_id") or Path(str(path)).stem
            agent = (r.get("agent") or r.get("peer") or default_agent).lower()
            if agent == "kimi":
                agent = "kimi_code"
            if path:
                out.append(
                    {
                        "id": str(sid),
                        "path": str(path),
                        "agent": agent,
                        "source": r.get("source") or "session-digger",
                    }
                )
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            out.append(
                {
                    "id": str(r[0]),
                    "path": str(r[1]),
                    "agent": str(r[2] if len(r) > 2 else default_agent).lower(),
                    "source": "session-digger",
                }
            )
    return out


def _parse_sessions_table(text: str) -> list[dict[str, Any]]:
    """Parse sd-recall sessions text table into dicts."""
    rows = []
    lines = text.strip().splitlines()
    for line in lines:
        if not line.strip() or line.startswith("SESSION") or line.startswith("---"):
            continue
        # path is last column often absolute
        m = re.search(r"(\S+\.jsonl)\s*$", line)
        path = m.group(1) if m else ""
        agent = ""
        for a in ("claude", "grok", "kimi", "zcode", "codex"):
            if re.search(rf"\b{a}\b", line, re.I):
                agent = a
                break
        sid = line.split()[0] if line.split() else ""
        rows.append({"id": sid, "path": path, "agent": agent, "raw": line})
    return rows


def _native_tail_messages(session_path: str, limit: int = 20) -> str:
    """Best-effort message skim from JSONL without session-digger."""
    p = Path(session_path)
    if not p.is_file():
        return ""
    lines: list[str] = []
    try:
        # read last ~200KB for speed
        data = p.read_bytes()
        if len(data) > 200_000:
            data = data[-200_000:]
        text = data.decode("utf-8", errors="replace")
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            role = (
                obj.get("role")
                or (obj.get("message") or {}).get("role")
                if isinstance(obj.get("message"), dict)
                else None
            ) or obj.get("type") or "msg"
            content = obj.get("content") or obj.get("text")
            if content is None and isinstance(obj.get("message"), dict):
                content = obj["message"].get("content") or obj["message"].get("text")
            if isinstance(content, list):
                bits = []
                for b in content:
                    if isinstance(b, dict):
                        bits.append(str(b.get("text") or b.get("content") or ""))
                    else:
                        bits.append(str(b))
                content = " ".join(bits)
            if content is None:
                continue
            content = str(content).strip()
            if not content:
                continue
            lines.append(f"[{str(role).upper()}] {content[:500]}")
    except OSError:
        return ""
    return "\n".join(lines[-limit:])


def _native_scan_file_paths(session_path: str, limit: int = 40) -> list[str]:
    """Heuristic path harvest from transcript when digger unavailable."""
    p = Path(session_path)
    if not p.is_file():
        return []
    try:
        data = p.read_bytes()
        if len(data) > 300_000:
            data = data[-300_000:]
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return []
    found = re.findall(
        r"(?:/Users|/home|[A-Za-z]:[\\/])[^\s\"']+\.[A-Za-z0-9]{1,8}",
        text,
    )
    out: list[str] = []
    seen: set[str] = set()
    for f in found:
        if f in seen:
            continue
        seen.add(f)
        out.append(f)
        if len(out) >= limit:
            break
    return out


def extract_files(session_path: str) -> list[str]:
    if discover_sd_root():
        code, stdout, stderr = run_sd_recall(["files", session_path])
        if code == 0:
            files = []
            for line in stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("("):
                    continue
                path = line.split("\t")[0].strip()
                if path:
                    files.append(path)
            if files:
                return files
        try:
            echolib, _ = _ensure_echolib()
            files = echolib.extract_files_changed(session_path)
            out = []
            for entry in files or []:
                if isinstance(entry, (list, tuple)) and entry:
                    out.append(str(entry[0]))
                elif isinstance(entry, str):
                    out.append(entry)
            if out:
                return out
        except Exception:
            pass
    return _native_scan_file_paths(session_path)


def extract_messages(session_path: str, role: str = "both", limit: int = 20) -> str:
    if discover_sd_root():
        code, stdout, stderr = run_sd_recall(
            ["messages", session_path, "--role", role, "--no-tools", "--limit", str(limit)]
        )
        if code == 0 and stdout.strip():
            return stdout.strip()
        try:
            echolib, _ = _ensure_echolib()
            msgs = echolib.extract_messages(session_path, role=role, limit=limit)
            parts = []
            for m in msgs or []:
                if isinstance(m, dict):
                    parts.append(
                        f"[{m.get('role', '')}] {m.get('content', m.get('text', ''))[:500]}"
                    )
                else:
                    parts.append(str(m)[:500])
            if parts:
                return "\n".join(parts)
        except Exception:
            pass
    return _native_tail_messages(session_path, limit=limit)


def extract_knowledge(session_path: str) -> list[dict[str, Any]]:
    code, stdout, stderr = run_sd_recall(["extract-knowledge", session_path])
    if code != 0 or not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        # try line by line
        pass
    return []


def search_sessions(keyword: str, limit: int = 10, agent: str = "cross") -> str:
    code, stdout, stderr = run_sd_recall(
        ["search", keyword, "--scope", "all", "--limit", str(limit), "--agent", agent]
    )
    if code != 0:
        return stderr or stdout
    return stdout


def resolve_session(
    *,
    session: str | None = None,
    peer: str = "auto",
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Return {path, peer, id} for packing. Project-scoped when cwd is set."""
    project = (cwd or Path.cwd()).resolve()
    if session:
        p = Path(session).expanduser()
        if p.exists():
            detected = _detect_peer_from_path(str(p))
            return {
                "path": str(p.resolve()),
                "peer": detected or peer,
                "id": p.stem,
                "source": "explicit",
            }
        # zcode:// id
        if session.startswith("zcode://"):
            return {
                "path": session,
                "peer": "zcode",
                "id": session.replace("zcode://", ""),
                "source": "explicit",
            }
        return {
            "path": session,
            "peer": peer if peer != "auto" else "unknown",
            "id": session,
            "source": "explicit",
        }

    # Native multi-env discovery first (works without session-digger)
    use_peer = peer if peer != "auto" else "cross"
    rows = list_sessions(
        use_peer if use_peer != "auto" else "cross",
        limit=5,
        cwd=project,
        prefer_native=True,
    )
    if not rows and use_peer == "cross":
        for try_peer in ("grok", "claude", "kimi_code", "codex", "zcode"):
            rows = list_sessions(try_peer, limit=5, cwd=project, prefer_native=True)
            if rows:
                break
    if not rows:
        rows = list_sessions(
            use_peer if use_peer != "auto" else "cross",
            limit=5,
            scope="all",
            cwd=project,
            prefer_native=True,
        )

    if not rows:
        raise FileNotFoundError("no sessions found; pass --session PATH")

    row = rows[0]
    path = row.get("path") or ""
    agent = (row.get("agent") or use_peer or "unknown").lower()
    if agent in ("cross", "auto", ""):
        agent = _detect_peer_from_path(path) or "unknown"
    if agent == "kimi":
        agent = "kimi_code"
    return {
        "path": path,
        "peer": agent,
        "id": row.get("id") or Path(path).stem,
        "source": row.get("source") or "native",
    }


def _detect_peer_from_path(path: str) -> str:
    p = path.lower()
    if "zcode" in p:
        return "zcode"
    if "/.grok/" in p or "grok" in p:
        return "grok"
    if "/.claude/" in p:
        return "claude"
    if "kimi" in p:
        return "kimi_code"
    if "codex" in p:
        return "codex"
    return "unknown"


def _normalize_message_line(m: Any) -> str:
    """Normalize echolib / zcode message blobs to [ROLE] text."""
    if isinstance(m, dict):
        role = str(m.get("role") or m.get("type") or "msg").upper()
        text = m.get("text") or m.get("content") or m.get("message") or ""
        if isinstance(text, list):
            parts = []
            for b in text:
                if isinstance(b, dict):
                    parts.append(str(b.get("text") or b.get("content") or ""))
                else:
                    parts.append(str(b))
            text = " ".join(parts)
        text = str(text).strip()
        return f"[{role}] {text[:500]}"
    s = str(m).strip()
    # sometimes str(dict)
    if s.startswith("{") and "role" in s:
        try:
            # rough extract text=
            tm = re.search(r"['\"]text['\"]\s*:\s*['\"]([^'\"]+)", s)
            rm = re.search(r"['\"]role['\"]\s*:\s*['\"]([^'\"]+)", s)
            if tm:
                return f"[{(rm.group(1) if rm else 'MSG').upper()}] {tm.group(1)[:500]}"
        except Exception:
            pass
    return s[:500]


def collect_evidence(
    session_path: str,
    peer: str,
    cwd: Path,
    *,
    deep: bool = False,
    msg_limit: int | None = None,
) -> dict[str, Any]:
    """Collect session evidence.

    deep=False (default): skip extract-knowledge and keep message window small (fast/lean).
    deep=True: full extract-knowledge + larger message window.
    """
    files: list[str] = []
    messages = ""
    knowledge: list[dict[str, Any]] = []
    if msg_limit is None:
        msg_limit = 20 if deep else 8
    if session_path.startswith("zcode://"):
        # zcode db path
        try:
            echolib, _ = _ensure_echolib()
            sid = session_path.replace("zcode://", "")
            msgs = echolib.zcode_db_extract_messages(sid, role="both", limit=msg_limit)
            if isinstance(msgs, list):
                parts = [_normalize_message_line(m) for m in msgs]
                messages = "\n".join(parts)
            tools = []
            try:
                tools = echolib.zcode_db_extract_tools(sid, limit=30 if deep else 12) or []
            except Exception:
                pass
            for t in tools:
                if isinstance(t, dict):
                    blob = json.dumps(t, ensure_ascii=False)
                    for m in re.findall(
                        r"(/Users/[^\"'\s]+|/home/[^\"'\s]+|[A-Za-z0-9_./-]+\.[a-zA-Z0-9]{1,8})",
                        blob,
                    ):
                        if len(m) > 3 and not m.startswith("http"):
                            files.append(m)
        except Exception as e:
            messages = f"(zcode extract failed: {e})"
    else:
        files = extract_files(session_path)
        messages = extract_messages(session_path, role="both", limit=msg_limit)
        # expensive: only in deep mode
        if deep:
            knowledge = extract_knowledge(session_path)

    ws = git_workspace(cwd)
    # merge git dirty files so pack always has workspace truth
    for f in ws.get("dirty_files") or []:
        files.append(f)

    # dedupe files preserve order; drop non-existing absolute noise
    seen = set()
    uniq_files = []
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        # keep relative-looking or existing paths
        if f.startswith("/") or f.startswith("~"):
            p = Path(f).expanduser()
            if p.exists() or f.endswith((".md", ".py", ".ts", ".tsx", ".json", ".toml", ".sh")):
                uniq_files.append(str(p) if p.exists() else f)
        else:
            cand = (cwd / f).resolve()
            uniq_files.append(str(cand) if cand.exists() else f)

    return {
        "session_path": session_path,
        "peer": peer,
        "files": uniq_files,
        "messages": messages,
        "knowledge": knowledge,
        "workspace": ws,
    }
