"""Path discovery for agent-relay and session-digger."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote, unquote

RELAY_HOME = Path(os.environ.get("AGENT_RELAY_HOME", Path.home() / ".agents" / "relay")).expanduser()
SKILL_ROOT = Path(__file__).resolve().parents[2]


def discover_sd_root() -> Path | None:
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


# --- peer session roots & project-scoped discovery (portable) ---

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
    return [p for p in mapping.get(peer, []) if True]


def grok_project_keys(cwd: Path) -> list[str]:
    """URL-encoded project keys Grok may use under ~/.grok/sessions/."""
    cwd = cwd.resolve()
    keys: list[str] = []
    for raw in (str(cwd), cwd.as_posix()):
        k = quote(raw, safe="")
        if k not in keys:
            keys.append(k)
    # Windows drive letter variants if present
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
    s = str(cwd)
    posix = cwd.as_posix()
    keys: list[str] = []
    for raw in (s, posix):
        # typical: -Users-foo-project
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


def _path_matches_project(path: Path, project: Path) -> bool:
    """Best-effort: does this session path belong to project?"""
    project = project.resolve()
    ps = str(path)
    proj_s = str(project)
    proj_posix = project.as_posix()
    name = project.name
    if proj_s in ps or proj_posix in ps:
        return True
    try:
        decoded_parts = [unquote(part) for part in path.parts]
        joined = "/".join(decoded_parts)
        if proj_s in joined or proj_posix in joined or name in joined:
            # stronger: resolve decoded dir if looks absolute
            for part in decoded_parts:
                if not part or part in (path.anchor, "/"):
                    continue
                if part.startswith("/") or (len(part) >= 2 and part[1] == ":"):
                    try:
                        if Path(part).resolve() == project:
                            return True
                    except OSError:
                        pass
            if quote(proj_s, safe="") in ps or quote(proj_posix, safe="") in ps:
                return True
            if name and name in ps:
                return True
    except Exception:
        pass
    return name in ps


def find_latest_session(
    peer: str = "grok",
    project: Path | None = None,
    explicit: str | Path | None = None,
    *,
    allow_global_fallback: bool = True,
) -> Path | None:
    """Locate a session transcript for packing, scoped to a project when possible.

    Resolution order:
      1. explicit path (arg)
      2. AGENT_RELAY_EVAL_SESSION / GROK_SESSION / CLAUDE_SESSION env
      3. peer store filtered by project encoding (Grok URL-encode / Claude dash-encode)
      4. peer store fuzzy match on project path/name
      5. optional global newest transcript (if allow_global_fallback)
    """
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.is_file():
            return p.resolve()
        if p.is_dir():
            for name in ("chat_history.jsonl", "wire.jsonl"):
                hit = p / name
                if hit.is_file():
                    return hit.resolve()
            nested = list(p.rglob("chat_history.jsonl")) + list(p.rglob("wire.jsonl"))
            hit = _newest(nested)
            if hit:
                return hit.resolve()
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
            found = find_latest_session(peer, project, val, allow_global_fallback=False)
            if found:
                return found

    project = (project or Path.cwd()).resolve()
    cands: list[Path] = []

    if peer_l == "grok":
        for root in peer_session_roots("grok"):
            if not root.is_dir():
                continue
            for key in grok_project_keys(project):
                proj_dir = root / key
                if proj_dir.is_dir():
                    cands.extend(proj_dir.glob("*/chat_history.jsonl"))
            if not cands:
                for d in root.iterdir():
                    if not d.is_dir():
                        continue
                    if _path_matches_project(d, project):
                        cands.extend(d.glob("*/chat_history.jsonl"))
            if not cands and allow_global_fallback:
                cands.extend(root.glob("*/**/chat_history.jsonl"))

    elif peer_l == "claude":
        for root in peer_session_roots("claude"):
            if not root.is_dir():
                continue
            for key in claude_project_keys(project):
                proj_dir = root / key
                if proj_dir.is_dir():
                    cands.extend(proj_dir.glob("*.jsonl"))
            if not cands:
                for d in root.iterdir():
                    if d.is_dir() and _path_matches_project(d, project):
                        cands.extend(d.glob("*.jsonl"))
            if not cands and allow_global_fallback:
                cands.extend(root.glob("*/*.jsonl"))
                cands.extend(root.glob("*.jsonl"))

    elif peer_l in ("kimi", "kimi_code"):
        for root in peer_session_roots("kimi_code"):
            if not root.is_dir():
                continue
            # wire.jsonl under session_*/agents/main/
            wires = list(root.glob("**/wire.jsonl"))
            scoped = [w for w in wires if _path_matches_project(w, project)]
            cands.extend(scoped or (wires if allow_global_fallback else []))

    else:
        # generic: any chat_history / jsonl under peer roots
        for root in peer_session_roots(peer_l):
            if not root.is_dir():
                continue
            found = list(root.rglob("chat_history.jsonl")) + list(root.rglob("wire.jsonl"))
            scoped = [f for f in found if _path_matches_project(f, project)]
            cands.extend(scoped or (found if allow_global_fallback else []))

    return _newest(cands)


def portable_file_token_verify(path: Path | str, token: str) -> str:
    """Shell-safe VERIFY command using the current Python — works on macOS/Linux/Windows.

    Prefer this over `test -f && rg -q` which needs Unix + ripgrep.
    """
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
