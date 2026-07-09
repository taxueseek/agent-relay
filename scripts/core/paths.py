"""Path discovery for agent-relay and session-digger."""

from __future__ import annotations

import os
import re
from pathlib import Path

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
