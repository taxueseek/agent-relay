"""Path discovery for agent-relay (standalone; session-digger optional)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .session_discover import find_sessions as native_find_sessions
from .session_discover import resolve_latest as native_resolve_latest
from .session_discover import session_in_project

RELAY_HOME = Path(os.environ.get("AGENT_RELAY_HOME", Path.home() / ".agents" / "relay")).expanduser()
SKILL_ROOT = Path(__file__).resolve().parents[2]


def discover_sd_root() -> Path | None:
    """Optional session-digger root (evidence extraction only; discovery is native)."""
    env = os.environ.get("SESSION_DIGGER_ROOT") or os.environ.get("SD_ROOT")
    if env:
        p = Path(env).expanduser()
        if (p / "scripts" / "echolib.py").exists() or (p / "scripts" / "sd-recall.py").exists():
            return p
    candidates = [
        Path.home() / ".agents" / "skills" / "session-digger",
        Path.home() / ".claude" / "plugins" / "session-digger",
        SKILL_ROOT.parent / "session-digger",
    ]
    for c in candidates:
        if (c / "scripts" / "echolib.py").exists():
            return c
        if (c / "scripts" / "sd-recall.py").exists():
            return c
    return None


def project_slug(cwd: Path | None = None) -> str:
    cwd = (cwd or Path.cwd()).resolve()
    raw = str(cwd)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    if len(slug) > 80:
        slug = slug[-80:]
    return slug or "workspace"


def packet_dir(slug: str, packet_id: str) -> Path:
    return RELAY_HOME / slug / packet_id


def latest_packet_dir(slug: str) -> Path | None:
    base = RELAY_HOME / slug
    if not base.is_dir():
        return None
    dirs = [p for p in base.iterdir() if p.is_dir() and (p / "packet.json").exists()]
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def project_relay_dir(project: Path | None = None) -> Path:
    return (project or Path.cwd()).resolve() / ".relay"


def peer_session_roots(peer: str) -> list[Path]:
    """Known session root directories per peer (home-relative, OS-agnostic)."""
    home = Path.home()
    peer = (peer or "").lower().strip()
    mapping: dict[str, list[Path]] = {
        "grok": [home / ".grok" / "sessions"],
        "claude": [home / ".claude" / "projects"],
        "kimi_code": [home / ".kimi-code" / "sessions"],
        "kimi": [home / ".kimi-code" / "sessions"],
        "codex": [home / ".codex" / "sessions", home / ".codex"],
        "zcode": [home / ".zcode" / "cli" / "agents", home / ".zcode"],
    }
    return list(mapping.get(peer, []))


def grok_project_keys(cwd: Path) -> list[str]:
    """URL-encoded project keys Grok may use under ~/.grok/sessions/."""
    cwd = cwd.resolve()
    keys: list[str] = []
    for raw in (str(cwd), cwd.as_posix()):
        k = quote(raw, safe="")
        if k not in keys:
            keys.append(k)
    s = str(cwd)
    if len(s) >= 2 and s[1] == ":":
        alt = s.replace("\\", "/")
        k = quote(alt, safe="")
        if k not in keys:
            keys.append(k)
    return keys


def claude_project_keys(cwd: Path) -> list[str]:
    """Claude project dir name variants under ~/.claude/projects/."""
    cwd = cwd.resolve()
    keys: list[str] = []
    for raw in (str(cwd), cwd.as_posix()):
        stripped = raw.lstrip("/").replace("\\", "/").lstrip("/")
        enc = "-" + stripped.replace("/", "-").replace(":", "")
        if enc not in keys:
            keys.append(enc)
        bare = stripped.replace("/", "-").replace(":", "")
        if bare not in keys:
            keys.append(bare)
    return keys


def _newest(paths: list[Path]) -> Path | None:
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


def find_latest_session_info(
    peer: str = "grok",
    project: Path | None = None,
    explicit: str | Path | None = None,
    *,
    allow_global_fallback: bool = True,
    prefer_digger: bool = False,
) -> dict[str, Any] | None:
    """Locate a session transcript; return {path, source, peer, id} or None.

    Resolution order (standalone by default):
      1. explicit path
      2. env overrides
      3. **native multi-env discovery** (digger-inspired, no digger install)
      4. optional session-digger if prefer_digger=True and installed
    """
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.is_file():
            return {
                "path": p.resolve(),
                "source": "explicit",
                "peer": peer,
                "id": p.stem,
            }
        if p.is_dir():
            for name in ("chat_history.jsonl", "wire.jsonl", "transcript.jsonl"):
                hit = p / name
                if hit.is_file():
                    return {
                        "path": hit.resolve(),
                        "source": "explicit",
                        "peer": peer,
                        "id": p.name,
                    }
            nested = (
                list(p.rglob("chat_history.jsonl"))
                + list(p.rglob("wire.jsonl"))
                + list(p.rglob("transcript.jsonl"))
            )
            hit = _newest(nested)
            if hit:
                return {
                    "path": hit.resolve(),
                    "source": "explicit",
                    "peer": peer,
                    "id": hit.parent.name,
                }
        return None

    peer_l = (peer or "grok").lower().strip()
    env_keys = [
        "AGENT_RELAY_EVAL_SESSION",
        "RELAY_EVAL_SESSION",
    ]
    if peer_l == "grok":
        env_keys.append("GROK_SESSION")
    if peer_l == "claude":
        env_keys.append("CLAUDE_SESSION")
    if peer_l in ("kimi", "kimi_code"):
        env_keys.append("KIMI_SESSION")
    for key in env_keys:
        val = (os.environ.get(key) or "").strip()
        if val:
            found = find_latest_session_info(
                peer, project, val, allow_global_fallback=False, prefer_digger=False
            )
            if found:
                found["source"] = f"env:{key}"
                return found

    project = (project or Path.cwd()).resolve()

    # --- native (primary): digger-inspired, no install required ---
    native = native_resolve_latest(
        peer_l,
        project=project,
        allow_global_fallback=allow_global_fallback,
    )
    if native:
        return native

    # --- optional digger (only if asked + installed) ---
    if prefer_digger and discover_sd_root() is not None:
        try:
            from .evidence import list_sessions  # type: ignore

            digger_peer = "cross" if peer_l in ("auto",) else peer_l
            rows = list_sessions(
                digger_peer,
                limit=5,
                scope="current",
                cwd=project,
                prefer_native=False,
            )
            if rows:
                row = rows[0]
                path = Path(row["path"])
                if path.is_file():
                    agent = (row.get("agent") or peer_l).lower()
                    if agent == "kimi":
                        agent = "kimi_code"
                    return {
                        "path": path.resolve(),
                        "source": row.get("source") or "session-digger",
                        "peer": agent,
                        "id": row.get("id") or path.stem,
                    }
        except Exception:
            pass

    return None


def find_latest_session(
    peer: str = "grok",
    project: Path | None = None,
    explicit: str | Path | None = None,
    *,
    allow_global_fallback: bool = True,
    prefer_digger: bool = False,
) -> Path | None:
    """Locate a session transcript path (see find_latest_session_info)."""
    info = find_latest_session_info(
        peer,
        project,
        explicit,
        allow_global_fallback=allow_global_fallback,
        prefer_digger=prefer_digger,
    )
    if not info:
        return None
    return Path(info["path"])


def portable_file_token_verify(path: Path | str, token: str) -> str:
    """Shell-safe VERIFY command using the current Python — works on macOS/Linux/Windows."""
    import json
    import sys

    p = str(Path(path).expanduser())
    code = (
        "from pathlib import Path; "
        f"p=Path({json.dumps(p)}); "
        "t=p.read_text(encoding='utf-8',errors='replace') if p.is_file() else ''; "
        f"raise SystemExit(0 if p.is_file() and {json.dumps(token)} in t else 1)"
    )
    return f"{sys.executable} -c {json.dumps(code)}"


# re-export for callers / tests
__all__ = [
    "RELAY_HOME",
    "SKILL_ROOT",
    "discover_sd_root",
    "project_slug",
    "packet_dir",
    "latest_packet_dir",
    "project_relay_dir",
    "peer_session_roots",
    "grok_project_keys",
    "claude_project_keys",
    "find_latest_session",
    "find_latest_session_info",
    "portable_file_token_verify",
    "native_find_sessions",
    "session_in_project",
]
