#!/usr/bin/env python3
"""Wave-2 evaluation: measure pack/prompt/verify/result vs baseline-like metrics.

Portable across projects and OSes:
  - session path discovered from project cwd (no hard-coded user paths)
  - VERIFY via portable Python check (no Unix-only test/rg)
  - outputs under <project>/.relay/eval/ by default

Env overrides:
  AGENT_RELAY_EVAL_PROJECT   project root (default: cwd)
  AGENT_RELAY_EVAL_SESSION   explicit session transcript path
  AGENT_RELAY_EVAL_FROM      pack --from peer (default: grok)
  AGENT_RELAY_EVAL_OUT       report/output directory
  AGENT_RELAY_HOME           packet store root
  GROK_SESSION / CLAUDE_SESSION / KIMI_SESSION  peer-specific session path

CLI:
  python3 scripts/eval_wave2.py [--project DIR] [--session PATH] [--from PEER]
                                [--to PEER ...] [--out DIR] [--no-global-session]
                                [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.paths import (  # noqa: E402
    RELAY_HOME,
    SKILL_ROOT,
    find_latest_session,
    latest_packet_dir,
    portable_file_token_verify,
    project_relay_dir,
    project_slug,
)

RELAY = Path(__file__).resolve().parent / "relay_cli.py"

TERMINAL_JOB_STATES = frozenset(
    {"completed", "stopped", "done", "failed", "timeout", "error", "ok", "idle"}
)


def run(cmd: list[str], timeout: int = 120, cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_packet_id(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("packet_id:"):
            return line.split(":", 1)[1].strip()
    return ""


def resolve_packet_dir(pid: str, project: Path) -> Path | None:
    if not pid:
        return latest_packet_dir(project_slug(project))
    # Prefer project-scoped slug
    slug = project_slug(project)
    cand = RELAY_HOME / slug / pid
    if cand.is_dir() and (cand / "packet.json").exists():
        return cand
    # Fallback: search under RELAY_HOME (bounded)
    if not RELAY_HOME.is_dir():
        return None
    for d in RELAY_HOME.iterdir():
        if not d.is_dir():
            continue
        hit = d / pid
        if hit.is_dir() and (hit / "packet.json").exists():
            return hit
    return None


def poll_job(packet_dir: Path | None, timeout: int = 150) -> dict:
    t0 = time.time()
    last: dict = {}
    while time.time() - t0 < timeout:
        cmd = [sys.executable, str(RELAY), "job-status", "--verbose"]
        if packet_dir:
            cmd.extend(["--packet", packet_dir.name])
        code, out = run(cmd, timeout=30)
        st = ""
        for line in out.splitlines():
            if line.startswith("JOB_STATUS="):
                st = line.split("=", 1)[1].strip().lower()
            if line.startswith("JOB ") and "state=" in line:
                # JOB packet=... state=completed
                for part in line.split():
                    if part.startswith("state="):
                        st = part.split("=", 1)[1].strip().lower()
        if st in TERMINAL_JOB_STATES and st not in ("idle",):
            # idle alone is not terminal if we just started
            pass
        if st in ("completed", "stopped", "done", "failed", "timeout", "error", "ok"):
            try:
                if packet_dir and (packet_dir / "invoke" / "LAST_JOB.json").exists():
                    last = json.loads(
                        (packet_dir / "invoke" / "LAST_JOB.json").read_text(encoding="utf-8")
                    )
                elif packet_dir and (packet_dir / "result.json").exists():
                    last = {
                        "status": st,
                        "result": json.loads(
                            (packet_dir / "result.json").read_text(encoding="utf-8")
                        ),
                    }
                else:
                    # newest LAST_JOB under this project's slug only
                    jobs = []
                    if packet_dir:
                        lj = packet_dir / "invoke" / "LAST_JOB.json"
                        if lj.exists():
                            jobs = [lj]
                    if not jobs:
                        slug_base = RELAY_HOME / project_slug(Path.cwd())
                        if slug_base.is_dir():
                            jobs = list(slug_base.rglob("LAST_JOB.json"))
                    if jobs:
                        jobs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                        last = json.loads(jobs[0].read_text(encoding="utf-8"))
                    else:
                        last = {"status": st, "raw": out[-2000:]}
            except Exception:
                last = {"status": st, "raw": out[-2000:]}
            last["_poll_sec"] = round(time.time() - t0, 2)
            last["_job_status_line"] = st
            return last
        time.sleep(3)
    return {"status": "timeout_poll", "_poll_sec": timeout}


def build_scenarios(
    out_dir: Path,
    peers: list[str],
    ts: str,
) -> list[dict]:
    scenarios = []
    for peer in peers:
        safe = peer.replace("/", "_")
        path = out_dir / f"WAVE2_{safe.upper()}.md"
        token = f"W2_{safe[:4].upper()}_{ts}"
        scenarios.append(
            {
                "name": f"{safe}_wave2",
                "to": peer,
                "token": token,
                "path": path,
            }
        )
    return scenarios


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="agent-relay wave2 portable eval")
    ap.add_argument(
        "--project",
        default=os.environ.get("AGENT_RELAY_EVAL_PROJECT") or "",
        help="project root (default: cwd or AGENT_RELAY_EVAL_PROJECT)",
    )
    ap.add_argument(
        "--session",
        default=os.environ.get("AGENT_RELAY_EVAL_SESSION")
        or os.environ.get("GROK_SESSION")
        or "",
        help="explicit session transcript path",
    )
    ap.add_argument(
        "--from",
        dest="from_peer",
        default=os.environ.get("AGENT_RELAY_EVAL_FROM") or "grok",
        help="pack --from peer (default: grok)",
    )
    ap.add_argument(
        "--to",
        dest="to_peers",
        nargs="+",
        default=["claude", "kimi_code"],
        help="peers to invoke (default: claude kimi_code)",
    )
    ap.add_argument(
        "--out",
        default=os.environ.get("AGENT_RELAY_EVAL_OUT") or "",
        help="output dir for proofs/report (default: <project>/.relay/eval)",
    )
    ap.add_argument(
        "--no-global-session",
        action="store_true",
        help="do not fall back to newest session outside this project",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="only resolve paths and print plan; no pack/invoke",
    )
    ap.add_argument("--poll-timeout", type=int, default=150)
    args = ap.parse_args(argv)

    project = Path(args.project).expanduser().resolve() if args.project else Path.cwd().resolve()
    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else (project_relay_dir(project) / "eval")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    session = find_latest_session(
        peer=args.from_peer,
        project=project,
        explicit=args.session or None,
        allow_global_fallback=not args.no_global_session,
    )

    meta = {
        "project": str(project),
        "project_slug": project_slug(project),
        "skill_root": str(SKILL_ROOT),
        "relay_home": str(RELAY_HOME),
        "from_peer": args.from_peer,
        "session": str(session) if session else None,
        "out_dir": str(out_dir),
        "platform": sys.platform,
        "python": sys.executable,
    }
    print(json.dumps({"resolve": meta}, ensure_ascii=False, indent=2))

    if not session:
        print(
            "ERROR: no session found for this project.\n"
            "  Pass --session PATH, or set AGENT_RELAY_EVAL_SESSION,\n"
            "  or open a chat under this project so peer stores a session.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(json.dumps({"dry_run": True, "to": args.to_peers}, ensure_ascii=False))
        return 0

    ts = time.strftime("%H%M%S")
    scenarios = build_scenarios(out_dir, args.to_peers, ts)
    results = []

    for sc in scenarios:
        path = sc["path"]
        tok = sc["token"]
        verify = portable_file_token_verify(path, tok)
        goal = f"写文件 {path} 两行：{tok} 与 WAVE2_OK。VERIFY: {verify}"

        t_pack0 = time.time()
        code, out = run(
            [
                sys.executable,
                str(RELAY),
                "pack",
                "--from",
                args.from_peer,
                "--to",
                sc["to"],
                "--session",
                str(session),
                "--goal",
                goal,
            ],
            timeout=60,
            cwd=project,
        )
        pack_sec = round(time.time() - t_pack0, 3)
        pid = parse_packet_id(out)
        pdir = resolve_packet_dir(pid, project)

        handoff_bytes = 0
        verify_cmd = ""
        delta_mode = ""
        if pdir and (pdir / "HANDOFF.md").exists():
            handoff_bytes = (pdir / "HANDOFF.md").stat().st_size
            try:
                pkt = json.loads((pdir / "packet.json").read_text(encoding="utf-8"))
                verify_cmd = pkt.get("verify_cmd") or ""
                delta_mode = (pkt.get("delta") or {}).get("mode") or ""
            except Exception:
                pass

        t_inv0 = time.time()
        inv_cmd = [
            sys.executable,
            str(RELAY),
            "invoke",
            "--to",
            sc["to"],
            "--packet",
            pid or "latest",
            "--mode",
            "implement",
            "--visible",
        ]
        code2, out2 = run(inv_cmd, timeout=30, cwd=project)
        inv_return_sec = round(time.time() - t_inv0, 3)

        # re-resolve after invoke created invoke/
        if not pdir:
            pdir = resolve_packet_dir(pid, project)
        job = poll_job(pdir, timeout=args.poll_timeout)
        dur = (job.get("result") or {}).get("duration_sec") or job.get("duration_sec")
        verify_raw = (job.get("verify") or {})
        if isinstance(verify_raw, dict):
            verify_result_job = verify_raw.get("result") or ""
        else:
            verify_result_job = str(verify_raw)[:40]

        result_status = ""
        result_verify = ""
        prompt_bytes = 0
        if pdir:
            for rj_path in (pdir / "result.json", pdir / "invoke" / "result.json"):
                if rj_path.exists():
                    try:
                        res = json.loads(rj_path.read_text(encoding="utf-8"))
                        result_status = res.get("status") or ""
                        result_verify = (res.get("verify") or {}).get("result") or ""
                    except Exception:
                        pass
                    break
            inv_dir = pdir / "invoke"
            if inv_dir.is_dir():
                prompt_files = list(inv_dir.glob("*.prompt.md"))
                if prompt_files:
                    prompt_bytes = max(
                        prompt_files, key=lambda p: p.stat().st_mtime
                    ).stat().st_size

        file_ok = False
        if path.is_file():
            try:
                file_ok = tok in path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                file_ok = False

        row = {
            "scenario": sc["name"],
            "to": sc["to"],
            "pack_sec": pack_sec,
            "pack_exit": code,
            "packet_id": pid,
            "handoff_bytes": handoff_bytes,
            "prompt_bytes": prompt_bytes,
            "invoke_return_sec": inv_return_sec,
            "invoke_exit": code2,
            "worker_duration_sec": round(dur, 3) if isinstance(dur, (int, float)) else dur,
            "poll_sec": job.get("_poll_sec"),
            "job_status": job.get("status") or job.get("_job_status_line"),
            "verify_cmd_set": bool(verify_cmd),
            "verify_result": result_verify or verify_result_job or str(job.get("verify") or "")[:40],
            "result_status": result_status,
            "file_ok": file_ok,
            "delta_mode": delta_mode,
            "session_id": (job.get("result") or {}).get("session_id")
            or job.get("session_id")
            or "",
            "proof_path": str(path),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "meta": meta,
        "results": results,
        "pass_criteria": {
            "async_return_under_1s": all((r["invoke_return_sec"] or 99) < 1 for r in results),
            "all_file_ok": all(r["file_ok"] for r in results) if results else False,
            "all_verify_pass": all(r["verify_result"] == "pass" for r in results)
            if results
            else False,
            "prompt_under_2kb": all((r["prompt_bytes"] or 0) < 2048 for r in results)
            if results
            else False,
            "pack_under_2s": all((r["pack_sec"] or 99) < 2 for r in results) if results else False,
        },
    }
    outp = out_dir / "WAVE2_EVAL_REPORT.json"
    outp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("REPORT", outp)
    print(json.dumps(report["pass_criteria"], ensure_ascii=False, indent=2))
    ok = all(report["pass_criteria"].values()) if results else False
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
