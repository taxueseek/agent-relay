"""Job digest — simplified fleet-protocol ideas for agent-relay packets.

States derived from LAST_JOB.json + result.json + meta sidecars.
Does not implement multi-run fleet supervision; single-packet job view only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def inspect_packet_job(packet_dir: Path) -> dict[str, Any]:
    """Derive job state for one packet directory."""
    packet_dir = Path(packet_dir)
    inv = packet_dir / "invoke"
    last_path = inv / "LAST_JOB.json"
    result_path = packet_dir / "result.json"
    if not result_path.exists():
        result_path = inv / "result.json"
    packet_path = packet_dir / "packet.json"

    job: dict[str, Any] = {}
    if last_path.exists():
        try:
            job = json.loads(last_path.read_text(encoding="utf-8"))
        except Exception as e:
            job = {"status": "error", "error": str(e)}

    result: dict[str, Any] = {}
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            result = {}

    pkt: dict[str, Any] = {}
    if packet_path.exists():
        try:
            pkt = json.loads(packet_path.read_text(encoding="utf-8"))
        except Exception:
            pkt = {}

    raw_st = (job.get("status") or "").lower()
    # normalize to fleet-like states
    if result and result.get("status") in ("verified_pass", "verified_fail", "completed_unverified", "failed"):
        if result.get("status") == "verified_pass":
            state = "completed"
        elif result.get("status") == "verified_fail":
            state = "completed"  # finished but verify failed
        elif result.get("status") == "failed":
            state = "stopped"
        else:
            state = "completed"
    elif raw_st in ("done", "completed", "ok"):
        state = "completed"
    elif raw_st in ("running", "queued", "started"):
        state = "running"
    elif raw_st in ("failed", "error", "killed"):
        state = "stopped"
    elif last_path.exists():
        state = "idle"
    else:
        state = "idle"

    # stall heuristic: meta mtime old + still running
    stalled = False
    progress_age = None
    if state == "running":
        mtimes = []
        for p in (last_path, inv):
            if p.exists():
                try:
                    mtimes.append(p.stat().st_mtime if p.is_file() else max(
                        (x.stat().st_mtime for x in p.glob("*")), default=0
                    ))
                except Exception:
                    pass
        if mtimes:
            progress_age = round(time.time() - max(mtimes), 1)
            if progress_age > 900:  # 15 min no touch
                stalled = True

    verify = (result.get("verify") or job.get("verify") or {})
    if isinstance(verify, str):
        verify = {"result": verify}

    return {
        "schema": "agent-relay/job/v1",
        "packet_id": packet_dir.name,
        "packet_dir": str(packet_dir),
        "state": state,  # running | completed | stopped | idle
        "stalled": stalled,
        "progress_age_sec": progress_age,
        "raw_job_status": job.get("status"),
        "peer": result.get("peer") or job.get("peer") or (pkt.get("routing") or {}).get("to_peer"),
        "goal": (pkt.get("goal") or "")[:200],
        "verify": verify.get("result") or "unknown",
        "verify_cmd": (pkt.get("verify_cmd") or verify.get("cmd") or "")[:200],
        "duration_sec": result.get("duration_sec") or job.get("duration_sec"),
        "session_id": result.get("session_id") or (job.get("result") or {}).get("session_id") or "",
        "resume_cmd": (job.get("result") or {}).get("resume_cmd") or "",
        "done": result.get("done") or [],
        "files": result.get("files") or [],
        "open": result.get("open") or [],
        "result_status": result.get("status") or "",
        "paths": {
            "packet": str(packet_path) if packet_path.exists() else "",
            "handoff": str(packet_dir / "HANDOFF.md"),
            "last_job": str(last_path) if last_path.exists() else "",
            "result": str(result_path) if result_path.exists() else "",
        },
    }


def format_job_digest(d: dict[str, Any]) -> str:
    lines = [
        f"JOB packet={d.get('packet_id')} state={d.get('state')}"
        f"{' STALLED' if d.get('stalled') else ''}",
        f"peer={d.get('peer')} verify={d.get('verify')} result={d.get('result_status') or '-'}",
        f"goal: {d.get('goal') or '(none)'}",
    ]
    if d.get("duration_sec") is not None:
        lines.append(f"duration_sec: {d.get('duration_sec')}")
    if d.get("progress_age_sec") is not None:
        lines.append(f"progress_age_sec: {d.get('progress_age_sec')}")
    if d.get("session_id"):
        lines.append(f"session_id: {d.get('session_id')}")
    if d.get("resume_cmd"):
        lines.append(f"resume_cmd: {d.get('resume_cmd')}")
    if d.get("done"):
        lines.append(f"done: {'; '.join(str(x) for x in d['done'][:5])}")
    if d.get("files"):
        lines.append(f"files: {' '.join(str(x) for x in d['files'][:8])}")
    if d.get("open"):
        lines.append(f"open: {'; '.join(str(x) for x in d['open'][:3])}")
    paths = d.get("paths") or {}
    if paths.get("result"):
        lines.append(f"result_json: {paths['result']}")
    if paths.get("handoff"):
        lines.append(f"handoff: {paths['handoff']}")
    return "\n".join(lines)
