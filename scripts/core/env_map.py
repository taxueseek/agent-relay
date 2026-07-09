"""Environment ↔ workspace mapping (session-digger inspired, fully standalone).

Purpose
-------
Different coding agents store data under different home layouts, and each
encodes the *project / workspace path* differently. This module answers:

  - Which agent environments exist on this machine?
  - For *this* project folder, where does each env keep its data?
  - How many sessions / artifacts does that workspace have under each env?

No session-digger install required. Layout rules follow digger ENV_REGISTRY
conventions (Claude dash-encode, Grok URL-encode, Kimi nested project dirs, …).

Typical use
-----------
  scan_environments()           # machine-wide presence
  map_workspace(project_path)   # per-env binding for one folder
  resolve_env_project_dir(env, project)  # concrete data dir if any
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, unquote

# ---------------------------------------------------------------------------
# Registry (ported / aligned with session-digger ENV_REGISTRY + KNOWN_UNADAPTED)
# ---------------------------------------------------------------------------

# encode kinds:
#   dash     — Claude: /Users/a/b → -Users-a-b
#   url      — Grok: quote(path, safe="")
#   basename — match project name / hash dir loosely
#   none     — env is not project-partitioned (or unknown scheme)
#   pathfrag — path or encoded fragments appear in storage paths


@dataclass(frozen=True)
class EnvSpec:
    id: str
    name: str
    roots: tuple[str, ...]  # ~ relative roots
    format: str
    encode: str  # dash | url | basename | pathfrag | none
    session_globs: tuple[str, ...] = ("**/*.jsonl",)
    primary: bool = False


ENV_SPECS: tuple[EnvSpec, ...] = (
    EnvSpec(
        "claude",
        "Claude Code",
        ("~/.claude/projects",),
        "jsonl",
        "dash",
        ("*/*.jsonl", "**/*.jsonl"),
        primary=True,
    ),
    EnvSpec(
        "grok",
        "Grok Build",
        ("~/.grok/sessions",),
        "jsonl",
        "url",
        ("**/chat_history.jsonl",),
        primary=True,
    ),
    EnvSpec(
        "kimi_code",
        "Kimi Code",
        ("~/.kimi-code/sessions",),
        "jsonl",
        "basename",
        ("**/wire.jsonl", "**/agents/main/wire.jsonl"),
        primary=True,
    ),
    EnvSpec(
        "codex",
        "Codex (OpenAI)",
        ("~/.codex/sessions", "~/.codex"),
        "jsonl",
        "pathfrag",
        ("**/rollout-*.jsonl", "**/*.jsonl"),
    ),
    EnvSpec(
        "zcode",
        "ZCode",
        ("~/.zcode/cli/agents", "~/.zcode"),
        "jsonl-trace",
        "pathfrag",
        ("**/transcript.jsonl",),
    ),
    EnvSpec(
        "workbuddy",
        "WorkBuddy",
        ("~/.workbuddy/projects", "~/.workbuddy"),
        "jsonl",
        "basename",
        ("**/*.jsonl",),
    ),
    EnvSpec(
        "trae_cn",
        "Trae CN",
        ("~/.trae-cn/memory/projects", "~/.trae-cn"),
        "jsonl-summary",
        "pathfrag",
        ("**/*.jsonl",),
    ),
    EnvSpec(
        "mimo",
        "MiMo / mimocode",
        ("~/.mimo/projects", "~/.local/share/mimocode", "~/.mimocode"),
        "unknown",
        "basename",
        ("**/*.jsonl",),
    ),
    EnvSpec(
        "dim",
        "DIM",
        ("~/.dim/memory", "~/.dim"),
        "jsonl-summary",
        "none",
        ("**/*.jsonl",),
    ),
    EnvSpec(
        "reasonix",
        "Reasonix",
        ("~/.reasonix/sessions",),
        "jsonl",
        "none",
        ("**/*.jsonl",),
    ),
)

_SPEC_BY_ID: dict[str, EnvSpec] = {s.id: s for s in ENV_SPECS}


def expand_root(root: str, home: Path | None = None) -> Path:
    home = home or Path.home()
    p = Path(root).expanduser()
    if root.startswith("~/") or root == "~":
        p = home / root[2:] if root.startswith("~/") else home
    return p


def project_keys_for_env(env_id: str, project: Path) -> list[str]:
    """How this env names / encodes a workspace path under its root."""
    project = project.resolve()
    s = str(project)
    posix = project.as_posix()
    name = project.name
    enc = (_SPEC_BY_ID.get(env_id) or EnvSpec(env_id, env_id, (), "", "none")).encode
    keys: list[str] = []

    if enc == "dash":
        for raw in (s, posix):
            stripped = raw.lstrip("/").replace("\\", "/").lstrip("/")
            keys.append("-" + stripped.replace("/", "-").replace(":", ""))
            keys.append(stripped.replace("/", "-").replace(":", ""))
    elif enc == "url":
        for raw in (s, posix):
            keys.append(quote(raw, safe=""))
        if len(s) >= 2 and s[1] == ":":
            keys.append(quote(s.replace("\\", "/"), safe=""))
    elif enc == "basename":
        keys.append(name)
        # common hash / slug variants seen in the wild
        keys.append(name.lower())
        keys.append(name.replace("-", "_"))
        keys.append(f"wd_{name}")
        keys.append(f"wd_{name.lower()}")
    elif enc == "pathfrag":
        keys.extend([s, posix, name])
    else:  # none
        keys.append(name)

    # dedupe preserve order
    out: list[str] = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def _count_sessions(dir_path: Path, globs: tuple[str, ...], cap: int = 500) -> tuple[int, Path | None]:
    """Return (count, newest_file)."""
    if not dir_path.is_dir():
        return 0, None
    files: list[Path] = []
    for g in globs:
        try:
            files.extend([p for p in dir_path.glob(g) if p.is_file()])
        except OSError:
            continue
    # de-noise
    cleaned: list[Path] = []
    seen: set[str] = set()
    for p in files:
        name = p.name
        if name in ("prompt_history.jsonl", "history.jsonl", "backfill.jsonl"):
            continue
        if "subagents" in str(p):
            continue
        if name.endswith(".events.jsonl"):
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(p)
        if len(cleaned) >= cap:
            break
    if not cleaned:
        return 0, None
    newest = max(cleaned, key=lambda p: p.stat().st_mtime)
    return len(cleaned), newest


def _dir_matches_basename(dir_name: str, project: Path) -> bool:
    name = project.name
    dn = dir_name.lower()
    if name.lower() in dn or dn in name.lower():
        return True
    bits = [b for b in name.replace("-", "_").split("_") if len(b) >= 3]
    if bits and sum(1 for b in bits if b.lower() in dn) >= max(1, len(bits) // 2):
        return True
    return False


def resolve_env_project_dirs(
    env_id: str,
    project: Path,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Concrete storage directories for this workspace under one env (0..n)."""
    spec = _SPEC_BY_ID.get(env_id)
    if not spec:
        return []
    home = home or Path.home()
    project = project.resolve()
    keys = project_keys_for_env(env_id, project)
    found: list[Path] = []

    for root_s in spec.roots:
        root = expand_root(root_s, home)
        if not root.is_dir():
            continue

        if spec.encode in ("dash", "url"):
            for k in keys:
                cand = root / k
                if cand.is_dir():
                    found.append(cand)
            # fuzzy: decode dir names
            if not found:
                for child in root.iterdir():
                    if not child.is_dir():
                        continue
                    decoded = unquote(child.name)
                    if decoded in (str(project), project.as_posix()) or project.name in child.name:
                        found.append(child)
                        continue
                    # dash form contains path parts
                    if spec.encode == "dash":
                        enc = str(project).replace("/", "-").replace("\\", "-")
                        if enc in child.name or child.name.lstrip("-") in enc.lstrip("-"):
                            found.append(child)

        elif spec.encode == "basename":
            for child in root.iterdir():
                if child.is_dir() and _dir_matches_basename(child.name, project):
                    found.append(child)
            # also try exact keys
            for k in keys:
                cand = root / k
                if cand.is_dir() and cand not in found:
                    found.append(cand)

        elif spec.encode == "pathfrag":
            # sessions may embed cwd in path or metadata; scan one level + match
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                blob = child.name + str(child)
                if any(k in blob for k in keys if len(k) >= 3):
                    found.append(child)
            if not found:
                # whole root is the pool (not project-partitioned)
                found.append(root)

        else:  # none — not project-scoped; expose root if present
            found.append(root)

    # dedupe
    out: list[Path] = []
    seen: set[str] = set()
    for p in found:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


@dataclass
class EnvPresence:
    env_id: str
    name: str
    present: bool
    roots: list[str]
    format: str
    encode: str
    primary: bool
    session_count_global: int = 0
    status: str = "missing"  # present | empty | missing

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceEnvBinding:
    env_id: str
    name: str
    present: bool
    encode: str
    project_keys: list[str]
    project_dirs: list[str]
    session_count: int
    latest_session: str
    bound: bool  # True if this workspace has data under the env
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_environments(*, home: Path | None = None) -> list[EnvPresence]:
    """Machine-wide: which agent envs exist (digger scan_all_environments analogue)."""
    home = home or Path.home()
    results: list[EnvPresence] = []
    for spec in ENV_SPECS:
        roots_exist: list[str] = []
        total = 0
        for r in spec.roots:
            rp = expand_root(r, home)
            if rp.exists():
                roots_exist.append(str(rp))
                n, _ = _count_sessions(rp, spec.session_globs)
                total += n
        present = bool(roots_exist)
        status = "missing"
        if present and total > 0:
            status = "present"
        elif present:
            status = "empty"
        results.append(
            EnvPresence(
                env_id=spec.id,
                name=spec.name,
                present=present,
                roots=roots_exist or [str(expand_root(spec.roots[0], home))],
                format=spec.format,
                encode=spec.encode,
                primary=spec.primary,
                session_count_global=total,
                status=status,
            )
        )
    return results


def map_workspace(
    project: Path | str | None = None,
    *,
    home: Path | None = None,
    env_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Map one project/workspace folder onto every known agent environment.

    Returns a structured report usable by doctor / pack / humans.
    """
    home = home or Path.home()
    project = Path(project or Path.cwd()).expanduser().resolve()
    specs = (
        [_SPEC_BY_ID[i] for i in env_ids if i in _SPEC_BY_ID]
        if env_ids
        else list(ENV_SPECS)
    )

    bindings: list[WorkspaceEnvBinding] = []
    for spec in specs:
        roots_ok = any(expand_root(r, home).exists() for r in spec.roots)
        keys = project_keys_for_env(spec.id, project)
        dirs = resolve_env_project_dirs(spec.id, project, home=home) if roots_ok else []
        # For pathfrag/none, dirs may be whole root — only count files that match project
        sess_count = 0
        latest: Path | None = None
        for d in dirs:
            n, newest = _count_sessions(d, spec.session_globs)
            if spec.encode in ("pathfrag", "none") and d in [
                expand_root(r, home) for r in spec.roots
            ]:
                # filter files by project key presence in path
                files = []
                for g in spec.session_globs:
                    try:
                        files.extend(list(d.glob(g)))
                    except OSError:
                        pass
                matched = [
                    f
                    for f in files
                    if f.is_file()
                    and any(k in str(f) for k in keys if len(str(k)) >= 3)
                ]
                if matched:
                    n = len(matched)
                    newest = max(matched, key=lambda p: p.stat().st_mtime)
                elif spec.encode == "none":
                    n, newest = 0, None  # not project-bound
                else:
                    # no path match — treat unbound
                    n, newest = 0, None
            sess_count += n
            if newest and (latest is None or newest.stat().st_mtime > latest.stat().st_mtime):
                latest = newest

        bound = sess_count > 0 or (
            bool(dirs)
            and spec.encode in ("dash", "url", "basename")
            and any(
                d not in [expand_root(r, home) for r in spec.roots] for d in dirs
            )
        )
        # refine bound: dash/url project dir exists even if empty
        if not bound and dirs and spec.encode in ("dash", "url", "basename"):
            bound = any(
                d.name in keys or any(k in d.name for k in keys if len(k) >= 3)
                for d in dirs
            )

        notes = ""
        if not roots_ok:
            notes = "env root not on this machine"
        elif not bound:
            notes = "no project-scoped data under this workspace"
        elif sess_count == 0:
            notes = "project dir exists but no session files matched"

        bindings.append(
            WorkspaceEnvBinding(
                env_id=spec.id,
                name=spec.name,
                present=roots_ok,
                encode=spec.encode,
                project_keys=keys[:6],
                project_dirs=[str(d) for d in dirs],
                session_count=sess_count,
                latest_session=str(latest) if latest else "",
                bound=bound and roots_ok,
                notes=notes,
            )
        )

    bound_envs = [b.env_id for b in bindings if b.bound]
    return {
        "schema": "agent-relay/workspace-map/v1",
        "project": str(project),
        "project_name": project.name,
        "home": str(home),
        "bound_envs": bound_envs,
        "bound_count": len(bound_envs),
        "environments": [b.to_dict() for b in bindings],
    }


def format_workspace_map(report: dict[str, Any]) -> str:
    lines = [
        f"WORKSPACE {report.get('project')}",
        f"bound: {report.get('bound_count')} env(s) → {', '.join(report.get('bound_envs') or []) or '(none)'}",
        "",
        f"{'ENV':<12} {'ON':<4} {'BOUND':<6} {'SESS':<6} {'ENCODE':<10} NOTES",
        "-" * 72,
    ]
    for e in report.get("environments") or []:
        lines.append(
            f"{e.get('env_id',''):<12} "
            f"{'Y' if e.get('present') else '.':<4} "
            f"{'Y' if e.get('bound') else '.':<6} "
            f"{e.get('session_count', 0):<6} "
            f"{e.get('encode',''):<10} "
            f"{(e.get('notes') or '')[:40]}"
        )
        if e.get("bound") and e.get("project_dirs"):
            lines.append(f"             dirs: {e['project_dirs'][0]}")
        if e.get("latest_session"):
            lines.append(f"             latest: {e['latest_session']}")
    return "\n".join(lines)


def format_env_scan(rows: list[EnvPresence]) -> str:
    lines = [
        f"{'ENV':<12} {'STATUS':<10} {'SESS':<8} ROOT",
        "-" * 72,
    ]
    for r in rows:
        root = (r.roots[0] if r.roots else "")[:48]
        lines.append(
            f"{r.env_id:<12} {r.status:<10} {r.session_count_global:<8} {root}"
        )
    return "\n".join(lines)
