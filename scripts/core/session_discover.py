"""Standalone multi-env session discovery (uses env_map workspace binding).

Session listing is a *consumer* of environment↔workspace mapping
(``core.env_map``). The important capability is: any project folder can be
bound to Claude/Grok/Kimi/… storage layouts without installing session-digger.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote

from .env_map import resolve_env_project_dirs

# Non-session noise under Claude project dirs (from digger)
_GLOBAL_EXCLUDES = frozenset(
    {
        "prompt_history.jsonl",
        "history.jsonl",
        "backfill.jsonl",
    }
)

_AGENT_MAP = {
    "claude": "claude",
    "grok": "grok",
    "kimi": "kimi_code",
    "kimi_code": "kimi_code",
    "codex": "codex",
    "zcode": "zcode",
    "cross": "cross",
    "auto": "cross",
}


def _home() -> Path:
    return Path.home()


def _mtime(path: str | Path) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def session_in_project(
    path: str | Path,
    agent: str,
    project: Path,
) -> bool:
    """Whether a session file belongs to ``project`` (digger-compatible rules + extras)."""
    ps = str(Path(path))
    try:
        ps_res = str(Path(path).resolve())
    except OSError:
        ps_res = ps
    cwd = project.resolve()
    cwd_s = str(cwd)
    cwd_posix = cwd.as_posix()
    name = cwd.name
    at = (agent or "").lower()
    if at == "kimi":
        at = "kimi_code"

    # Claude: dash-encoded cwd in path (digger: encoded = cwd.replace("/", "-"))
    if at == "claude":
        encoded = cwd_s.replace("/", "-").replace("\\", "-")
        # also strip drive colon for Windows-ish encodings
        encoded2 = cwd_posix.replace("/", "-")
        return (
            encoded in ps
            or encoded in ps_res
            or encoded2 in ps
            or cwd_s in ps_res
            or cwd_posix in ps_res
        )

    # Grok: URL-encoded cwd (digger: quote(cwd, safe=""))
    if at == "grok":
        for raw in (cwd_s, cwd_posix):
            enc = quote(raw, safe="")
            if enc in ps or enc in ps_res:
                return True
        # decoded segment match
        try:
            for part in Path(path).parts:
                if unquote(part) in (cwd_s, cwd_posix) or unquote(part) == cwd_s:
                    return True
                try:
                    if Path(unquote(part)).resolve() == cwd:
                        return True
                except OSError:
                    pass
        except Exception:
            pass
        return name in ps and ("%2F" in ps or "%2f" in ps or "sessions" in ps)

    # Kimi: digger only checks basename; we also match hashed project dir loosely
    if at in ("kimi", "kimi_code"):
        if name and name.lower() in ps.lower():
            return True
        # common hash prefix embeds workspace hint: wd_gpt_… / project slug fragments
        slug_bits = [b for b in name.replace("-", "_").split("_") if len(b) >= 3]
        hit = sum(1 for b in slug_bits if b.lower() in ps.lower())
        if slug_bits and hit >= max(1, len(slug_bits) // 2):
            return True
        # parent project dir name exact
        try:
            # .../sessions/<project_key>/session_*/agents/main/wire.jsonl
            p = Path(path)
            for parent in p.parents:
                if parent.name and parent.name not in ("sessions", "agents", "main", "session"):
                    if name.lower() in parent.name.lower() or parent.name.lower() in name.lower():
                        return True
                    break
        except Exception:
            pass
        return False

    # Codex / zcode / generic: path contains project path or name
    if cwd_s in ps_res or cwd_posix in ps_res or cwd_s in ps:
        return True
    return bool(name) and name in ps


def _scan_claude(base: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if not d.is_dir():
            continue
        for jf in d.glob("*.jsonl"):
            if "subagents" in str(jf):
                continue
            if ".jsonl." in jf.name:
                continue
            if jf.name in _GLOBAL_EXCLUDES:
                continue
            if jf.name.endswith(".events.jsonl"):
                continue
            out.append((jf.stem, str(jf), "claude"))
    return out


def _scan_grok(base: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if not base.is_dir():
        return out
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        # skip sqlite / non-project files
        if project_dir.suffix in (".sqlite", ".db"):
            continue
        for session_dir in project_dir.iterdir():
            if not session_dir.is_dir():
                continue
            chat = session_dir / "chat_history.jsonl"
            if chat.is_file():
                out.append((session_dir.name, str(chat), "grok"))
    return out


def _scan_kimi(base: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if not base.is_dir():
        return out
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        for session_dir in project_dir.iterdir():
            if not session_dir.is_dir():
                continue
            wire = session_dir / "agents" / "main" / "wire.jsonl"
            if wire.is_file():
                sid = session_dir.name.replace("session_", "")
                out.append((sid, str(wire), "kimi_code"))
    return out


def _scan_codex(base: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    if not base.is_dir():
        return out
    for jf in base.rglob("rollout-*.jsonl"):
        out.append((jf.stem, str(jf), "codex"))
    if not out:
        for jf in base.rglob("*.jsonl"):
            if jf.name.startswith("."):
                continue
            out.append((jf.stem, str(jf), "codex"))
    return out


def _scan_zcode(bases: Iterable[Path]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for base in bases:
        if not base.is_dir():
            continue
        for sess in base.iterdir():
            if not sess.is_dir():
                continue
            if not (sess.name.startswith("sess_") or len(sess.name) > 8):
                continue
            for agent_dir in sess.rglob("transcript.jsonl"):
                out.append((sess.name, str(agent_dir), "zcode"))
    return out


def find_sessions(
    *,
    agent: str = "cross",
    scope: str = "current",
    limit: int = 50,
    project: Path | None = None,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Find session transcripts. Returns list of {id, path, agent, source}.

    ``scope=current`` filters to ``project`` (default cwd) using digger-style rules.
    Fully standalone — no session-digger import.
    """
    home = home or _home()
    project = (project or Path.cwd()).resolve()
    agent_key = _AGENT_MAP.get((agent or "cross").lower(), (agent or "cross").lower())

    if agent_key == "cross":
        to_scan = ["claude", "grok", "kimi_code", "codex", "zcode"]
    else:
        to_scan = [agent_key]

    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for at in to_scan:
        batch: list[tuple[str, str, str]] = []
        # Prefer env_map: only scan dirs bound to this workspace when scope=current
        if scope == "current":
            proj_dirs = resolve_env_project_dirs(at, project, home=home)
            for d in proj_dirs:
                if at == "claude":
                    batch.extend(_scan_claude(d if d.name != "projects" else d))
                    # resolve_env returns project subdir for claude — scan that dir's jsonl
                    if d.is_dir():
                        for jf in d.glob("*.jsonl"):
                            if jf.name in _GLOBAL_EXCLUDES or "subagents" in str(jf):
                                continue
                            if jf.name.endswith(".events.jsonl"):
                                continue
                            batch.append((jf.stem, str(jf), "claude"))
                elif at == "grok":
                    for session_dir in d.iterdir() if d.is_dir() else []:
                        if not session_dir.is_dir():
                            continue
                        chat = session_dir / "chat_history.jsonl"
                        if chat.is_file():
                            batch.append((session_dir.name, str(chat), "grok"))
                elif at in ("kimi", "kimi_code"):
                    for session_dir in d.iterdir() if d.is_dir() else []:
                        if not session_dir.is_dir():
                            continue
                        wire = session_dir / "agents" / "main" / "wire.jsonl"
                        if wire.is_file():
                            batch.append(
                                (session_dir.name.replace("session_", ""), str(wire), "kimi_code")
                            )
                elif at == "codex":
                    batch.extend(_scan_codex(d))
                elif at == "zcode":
                    batch.extend(_scan_zcode([d]))
        else:
            if at == "claude":
                batch = _scan_claude(home / ".claude" / "projects")
            elif at == "grok":
                batch = _scan_grok(home / ".grok" / "sessions")
            elif at in ("kimi", "kimi_code"):
                batch = _scan_kimi(home / ".kimi-code" / "sessions")
            elif at == "codex":
                batch = _scan_codex(home / ".codex" / "sessions")
                if not batch:
                    batch = _scan_codex(home / ".codex")
            elif at == "zcode":
                batch = _scan_zcode(
                    [
                        home / ".zcode" / "cli" / "agents",
                        home / ".zcode" / "agents",
                    ]
                )

        for sid, path, label in batch:
            try:
                key = str(Path(path).resolve())
            except OSError:
                key = path
            if key in seen:
                continue
            seen.add(key)
            entries.append((sid, path, label))

    # Safety filter when scope=current (in case env_map returned broad roots)
    if scope == "current":
        entries = [e for e in entries if session_in_project(e[1], e[2], project)]

    entries.sort(key=lambda e: _mtime(e[1]), reverse=True)

    rows: list[dict[str, Any]] = []
    for sid, path, label in entries[: max(1, limit)]:
        rows.append(
            {
                "id": sid,
                "path": path,
                "agent": label,
                "source": "native",
            }
        )
    return rows


def resolve_latest(
    peer: str = "cross",
    project: Path | None = None,
    *,
    allow_global_fallback: bool = True,
    home: Path | None = None,
) -> dict[str, Any] | None:
    """Latest session for peer under project; optional global fallback."""
    project = (project or Path.cwd()).resolve()
    peer_l = (peer or "cross").lower()
    if peer_l in ("auto",):
        peer_l = "cross"

    rows = find_sessions(
        agent=peer_l,
        scope="current",
        limit=5,
        project=project,
        home=home,
    )
    if not rows and allow_global_fallback:
        rows = find_sessions(
            agent=peer_l if peer_l != "cross" else "cross",
            scope="all",
            limit=5,
            project=project,
            home=home,
        )
        # keep peer filter when not cross
        if peer_l not in ("cross", "auto") and rows:
            want = {peer_l, "kimi" if peer_l == "kimi_code" else peer_l, "kimi_code"}
            rows = [r for r in rows if r.get("agent") in want]

    if not rows:
        return None
    row = rows[0]
    path = Path(row["path"])
    if not path.is_file():
        return None
    agent = row.get("agent") or peer_l
    if agent == "kimi":
        agent = "kimi_code"
    return {
        "path": path.resolve(),
        "source": "native",
        "peer": agent,
        "id": row.get("id") or path.stem,
    }
