"""Peer registry — product-agnostic, aligned with session-digger ENV_REGISTRY."""

from __future__ import annotations

import shutil
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .paths import discover_sd_root

# Primary first-class peers for this skill
# ZCode 无 CLI invoke，保留在 KNOWN_PEERS 中但降级为非 primary
PRIMARY_PEERS = ("claude", "grok", "kimi_code")

# Mirrors digger ENV_REGISTRY keys (soft sync; doctor warns on drift)
KNOWN_PEERS = (
    "claude",
    "grok",
    "zcode",
    "kimi_code",
    "mimo",
    "codex",
    "workbuddy",
    "trae_cn",
    "dim",
    "reasonix",
)

PEER_MARKERS: dict[str, list[Path]] = {
    "claude": [Path.home() / ".claude" / "projects"],
    "grok": [Path.home() / ".grok" / "sessions"],
    "zcode": [
        Path.home() / ".zcode" / "cli" / "agents",
        Path.home() / ".zcode" / "cli" / "db" / "db.sqlite",
        Path.home() / ".zcode",
    ],
    "kimi_code": [Path.home() / ".kimi-code" / "sessions"],
    "mimo": [
        Path.home() / ".mimocode" / "bin" / "mimo",
        Path.home() / ".local" / "share" / "mimocode",
        Path.home() / ".mimo" / "projects",
        Path.home() / ".config" / "mimocode",
    ],
    "codex": [Path.home() / ".codex" / "sessions", Path.home() / ".codex"],
    "workbuddy": [Path.home() / ".workbuddy"],
    "trae_cn": [Path.home() / ".trae-cn"],
    "dim": [Path.home() / ".dim" / "memory", Path.home() / ".dim"],
    "reasonix": [Path.home() / ".reasonix" / "sessions"],
}

# Optional CLI binaries for L2 (stub probe only in v0.1)
PEER_BINARIES: dict[str, list[str]] = {
    "claude": ["claude"],
    "grok": ["grok"],
    "zcode": ["zcode", "z-code"],
    "codex": ["codex"],
    "kimi_code": ["kimi", "kimi-cli"],
    "mimo": ["mimo", "mimocode"],
}

# App-bundled zcode.cjs is experimental; invoke disabled unless env opt-in.
# Detection still lists presence of ~/.zcode for pack/resume evidence.


@dataclass
class PeerStatus:
    id: str
    present: bool
    evidence: bool
    resume: bool
    pack: bool
    delegate: bool
    primary: bool
    markers_found: list[str]
    binaries_found: list[str]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _marker_exists(paths: list[Path]) -> list[str]:
    found = []
    for p in paths:
        if p.exists():
            found.append(str(p))
    return found


def digger_env_ids() -> list[str] | None:
    sd = discover_sd_root()
    if not sd:
        return None
    try:
        import sys

        scripts = str(sd / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import echolib  # type: ignore

        reg = getattr(echolib, "ENV_REGISTRY", None) or getattr(echolib, "ADAPTER_REGISTRY", None)
        if isinstance(reg, dict):
            return list(reg.keys())
    except Exception:
        return None
    return None


def probe_peer(peer_id: str) -> PeerStatus:
    markers = PEER_MARKERS.get(peer_id, [])
    found = _marker_exists(markers)
    bins = []
    for b in PEER_BINARIES.get(peer_id, []):
        if shutil.which(b):
            bins.append(b)
    if peer_id == "kimi_code":
        for c in (
            Path.home() / ".kimi-code" / "bin" / "kimi",
            Path.home() / ".local" / "bin" / "kimi-cli",
        ):
            if c.is_file() and os.access(c, os.X_OK):
                bins.append(str(c))
                break
    if peer_id == "mimo":
        for c in (
            Path.home() / ".mimocode" / "bin" / "mimo",
            Path.home() / ".local" / "bin" / "mimo",
        ):
            if c.is_file() and os.access(c, os.X_OK):
                bins.append(str(c))
                break
    present = bool(found) or bool(bins)
    # evidence if digger knows it or local markers exist
    digger_ids = digger_env_ids()
    evidence = present and (
        digger_ids is None
        or peer_id in digger_ids
        or peer_id in PRIMARY_PEERS
        or peer_id == "mimo"  # digger 登记为 mimo 根目录；CLI 为 mimocode
    )
    if digger_ids and peer_id in digger_ids and found:
        evidence = True
    # ZCode: pack/resume yes when data dir present; invoke/delegate only if PATH CLI
    # (app-bundled headless is opt-in elsewhere, not advertised as delegate)
    notes = ""
    if not present:
        notes = "not detected on this machine"
    elif peer_id == "zcode" and not bins:
        notes = "sessions OK; invoke 默认关闭（无 PATH CLI）"
    elif peer_id == "mimo" and bins:
        notes = "mimocode CLI (mimo run)"
    return PeerStatus(
        id=peer_id,
        present=present,
        evidence=evidence,
        resume=True,  # any agent can read HANDOFF.md
        pack=present,
        delegate=bool(bins) if peer_id != "zcode" else bool(bins),  # only real PATH zcode
        primary=peer_id in PRIMARY_PEERS,
        markers_found=found,
        binaries_found=bins,
        notes=notes,
    )


def probe_all(include_optional: bool = True) -> list[PeerStatus]:
    ids = list(KNOWN_PEERS) if include_optional else list(PRIMARY_PEERS)
    # merge digger extras
    digger_ids = digger_env_ids() or []
    for d in digger_ids:
        if d not in ids and d != "universal":
            ids.append(d)
    return [probe_peer(i) for i in ids]


def detect_host_peer(cwd: Path | None = None) -> str:
    """Best-effort: which product is hosting the current process."""
    # env hints
    import os

    for key, peer in (
        ("CLAUDE_CODE", "claude"),
        ("CLAUDE_PROJECT_DIR", "claude"),
        ("GROK_SESSION", "grok"),
        ("ZCODE_SESSION", "zcode"),
    ):
        if os.environ.get(key):
            return peer
    # parent process name
    try:
        import psutil  # optional

        p = psutil.Process()
        names = []
        for _ in range(4):
            names.append((p.name() or "").lower())
            p = p.parent()
            if p is None:
                break
        joined = " ".join(names)
        if "claude" in joined:
            return "claude"
        if "grok" in joined:
            return "grok"
        if "zcode" in joined or "z-code" in joined:
            return "zcode"
    except Exception:
        pass
    # fallback: prefer primary present
    for pid in PRIMARY_PEERS:
        st = probe_peer(pid)
        if st.present:
            # grok often used in this workspace; still return first present primary
            return pid
    return "unknown"


def suggest_to_peer(task: str = "", from_peer: str = "") -> dict[str, str]:
    from .result_protocol import complexity_route

    present = {p.id: p for p in probe_all() if p.present}
    # complexity-first routing
    cr = complexity_route(task, present)
    rec = cr.get("recommended_peer") or "any"
    if rec != from_peer and rec in present:
        return {
            "recommended_peer": rec,
            "reason": cr.get("reason") or "",
            "complexity": cr.get("complexity") or "",
        }
    # fallback: switch away from current
    for cand in ("claude", "grok", "kimi_code", "zcode"):
        if cand in present and cand != from_peer:
            reason = "换环境续跑"
            if cand == "zcode":
                reason = "换环境续跑（zcode 仅 pack/resume）"
            return {"recommended_peer": cand, "reason": reason, "complexity": cr.get("complexity") or ""}
    if present:
        return {
            "recommended_peer": next(iter(present)),
            "reason": "仅检测到该 peer",
            "complexity": cr.get("complexity") or "",
        }
    return {"recommended_peer": "any", "reason": "未探测到本地 peer", "complexity": "unknown"}
