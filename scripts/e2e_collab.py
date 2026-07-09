#!/usr/bin/env python3
"""E2E collaboration test: env adapt → pack → silent/visible invoke → VERIFY.

Purpose (agent-relay product goal)
----------------------------------
Cross-product handoff must survive device / workspace / path changes:

  1. Discover which agent envs exist on *this* machine and bind to *this* project
  2. Pack using auto session resolve (no hard-coded user paths)
  3. Invoke silently (async, no window) and with --visible
  4. VERIFY proof files under the project

Usage
-----
  python3 scripts/e2e_collab.py [--project DIR] [--silent-peer grok] [--visible-peer kimi_code]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RELAY = Path(__file__).resolve().parent / "relay_cli.py"
PY = sys.executable


def step(results: dict, name: str, ok: bool, detail: str = "", **extra) -> bool:
    results["steps"].append({"step": name, "ok": bool(ok), "detail": detail, **extra})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"[:220])
    return bool(ok)


def run_relay(args: list[str], cwd: Path, timeout: int = 90) -> tuple[int, str, str]:
    p = subprocess.run(
        [PY, str(RELAY), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def portable_verify(path: Path, token: str) -> str:
    code = (
        "from pathlib import Path; "
        f"p=Path({json.dumps(str(path))}); "
        "t=p.read_text(encoding='utf-8',errors='replace') if p.is_file() else ''; "
        f"raise SystemExit(0 if p.is_file() and {json.dumps(token)} in t else 1)"
    )
    return f"{PY} -c {json.dumps(code)}"


def parse_packet_id(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("packet_id:"):
            return line.split(":", 1)[1].strip()
    return ""


def parse_job_status(out: str) -> str:
    st = "unknown"
    for line in out.splitlines():
        if line.startswith("JOB_STATUS="):
            st = line.split("=", 1)[1].strip()
        if "state=" in line:
            for part in line.split():
                if part.startswith("state="):
                    st = part.split("=", 1)[1]
    return st


def poll_job(cwd: Path, packet: str, timeout: int = 180) -> tuple[str, str]:
    t0 = time.time()
    last = ""
    st = "unknown"
    while time.time() - t0 < timeout:
        _c, out, _e = run_relay(["job-status", "--packet", packet], cwd, timeout=30)
        last = out
        st = parse_job_status(out)
        if st in ("completed", "stopped", "done", "failed", "error", "ok"):
            break
        time.sleep(4)
    return st, last


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="agent-relay E2E collab test")
    ap.add_argument("--project", default="", help="workspace (default: cwd)")
    ap.add_argument("--silent-peer", default="grok")
    ap.add_argument("--visible-peer", default="kimi_code")
    ap.add_argument("--poll-timeout", type=int, default=180)
    args = ap.parse_args(argv)

    project = Path(args.project).expanduser().resolve() if args.project else Path.cwd().resolve()
    out_dir = project / ".relay" / "eval" / "e2e-collab"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": str(project),
        "plan": [
            "workspace map (device-local env binding)",
            "peers with delegate capability",
            "pack auto session via env adapter",
            "silent invoke (no --visible)",
            "visible handoff (--visible)",
            "path encoding portability",
        ],
        "steps": [],
    }

    # 1 workspace
    code, out, _ = run_relay(["workspace", "--project", str(project), "--json"], project)
    try:
        ws = json.loads(out)
    except Exception:
        ws = {}
    bound = ws.get("bound_envs") or []
    step(
        results,
        "workspace_map",
        code == 0 and len(bound) >= 1,
        f"bound={ws.get('bound_count')} envs={bound}",
        bound_envs=bound,
    )

    # 2 peers
    _c, pout, _ = run_relay(["peers"], project)
    delegable: list[str] = []
    for line in pout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] in (
            "claude",
            "grok",
            "kimi_code",
            "mimo",
            "codex",
        ):
            if parts[1] == "Y" and parts[4] == "Y":
                delegable.append(parts[0])
    step(results, "peers_delegate", len(delegable) >= 1, f"delegable={delegable}", delegable=delegable)

    silent_peer = args.silent_peer if args.silent_peer in delegable else (delegable[0] if delegable else "")
    vis_peer = (
        args.visible_peer
        if args.visible_peer in delegable
        else next((p for p in ("kimi_code", "claude", "grok") if p in delegable), silent_peer)
    )

    # 3 pack
    token_s = f"E2E_SILENT_{time.strftime('%H%M%S')}"
    proof_s = out_dir / "silent_proof.md"
    goal_s = f"写文件 {proof_s} 两行：{token_s} 与 E2E_OK。VERIFY: {portable_verify(proof_s, token_s)}"
    code, out, _ = run_relay(
        ["pack", "--from", "auto", "--to", silent_peer or "grok", "--goal", goal_s],
        project,
        timeout=60,
    )
    pid = parse_packet_id(out)
    step(results, "pack_auto_session", code == 0 and bool(pid), f"packet_id={pid} peer={silent_peer}")

    # 4–5 silent invoke
    if pid and silent_peer:
        code, out, _ = run_relay(
            ["invoke", "--to", silent_peer, "--packet", pid, "--mode", "implement"],
            project,
            timeout=45,
        )
        step(results, "invoke_silent_start", code == 0, f"peer={silent_peer} exit={code}")
        st, poll = poll_job(project, pid, timeout=args.poll_timeout)
        file_ok = proof_s.is_file() and token_s in proof_s.read_text(encoding="utf-8", errors="replace")
        step(
            results,
            "invoke_silent_done",
            st in ("completed", "done", "ok") or file_ok,
            f"status={st} file_ok={file_ok}",
            status=st,
            file_ok=file_ok,
        )
    else:
        step(results, "invoke_silent_start", False, "no peer/packet")
        step(results, "invoke_silent_done", False, "skip")

    # 6 visible handoff
    if vis_peer:
        token_v = f"E2E_VIS_{time.strftime('%H%M%S')}"
        proof_v = out_dir / "visible_proof.md"
        goal_v = f"写文件 {proof_v} 两行：{token_v} 与 E2E_VIS_OK。VERIFY: {portable_verify(proof_v, token_v)}"
        code, out, _ = run_relay(
            [
                "handoff",
                "--to",
                vis_peer,
                "--goal",
                goal_v,
                "--visible",
                "--mode",
                "implement",
            ],
            project,
            timeout=45,
        )
        pid2 = parse_packet_id(out)
        step(
            results,
            "handoff_visible_start",
            code == 0,
            f"peer={vis_peer} packet={pid2}",
        )
        st2, _ = poll_job(project, pid2 or "latest", timeout=args.poll_timeout)
        file_ok2 = proof_v.is_file() and token_v in proof_v.read_text(encoding="utf-8", errors="replace")
        step(
            results,
            "handoff_visible_done",
            st2 in ("completed", "done", "ok") or file_ok2,
            f"status={st2} file_ok={file_ok2}",
            status=st2,
            file_ok=file_ok2,
        )
    else:
        step(results, "handoff_visible_start", False, "no visible peer")
        step(results, "handoff_visible_done", False, "skip")

    # 7 portability of encoding
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from core.env_map import project_keys_for_env

    fake = Path("/other/machine/work/DemoProj")
    kg = project_keys_for_env("grok", fake)[0]
    kc = project_keys_for_env("claude", fake)[0]
    step(
        results,
        "path_encoding_portable",
        "DemoProj" in kc and kg.startswith("%2F"),
        f"grok={kg[:48]} claude={kc[:48]}",
    )

    results["pass_count"] = sum(1 for s in results["steps"] if s["ok"])
    results["fail_count"] = sum(1 for s in results["steps"] if not s["ok"])
    results["all_pass"] = results["fail_count"] == 0
    report = out_dir / "E2E_COLLAB_REPORT.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("REPORT", report)
    print(json.dumps({"pass": results["pass_count"], "fail": results["fail_count"], "all_pass": results["all_pass"]}))
    return 0 if results["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
