#!/usr/bin/env python3
"""agent-relay unified CLI — product-agnostic multi-agent handoff.

Usage:
  relay_cli.py peers
  relay_cli.py envs                          # machine-wide agent env scan
  relay_cli.py workspace [--project DIR]     # map any folder → each env's data dirs
  relay_cli.py pack [--from PEER] [--to PEER] [--session PATH] [--goal TEXT] [--budget short|medium]
  relay_cli.py resume [packet-id|latest]
  relay_cli.py bridge KEYWORD [--limit N] [--to PEER]
  relay_cli.py suggest [--task TEXT]
  relay_cli.py invoke --to PEER [--packet ID|latest] [--mode continue|review|implement|fix]
  relay_cli.py handoff --to PEER [--goal TEXT] [--mode …]   # pack then invoke
  relay_cli.py doctor [--cwd DIR]
  relay_cli.py init [--project DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# allow `python scripts/relay_cli.py` from skill root
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from core.evidence import (  # noqa: E402
    collect_evidence,
    git_workspace,
    resolve_session,
    search_sessions,
)
from core.packet import (  # noqa: E402
    is_expired,
    load_packet,
    save_packet,
    validate_packet,
    write_current_pointer,
)
from core.paths import (  # noqa: E402
    RELAY_HOME,
    SKILL_ROOT,
    discover_sd_root,
    latest_packet_dir,
    packet_dir,
    project_relay_dir,
    project_slug,
)
from core.peers import (  # noqa: E402
    PRIMARY_PEERS,
    digger_env_ids,
    detect_host_peer,
    probe_all,
    suggest_to_peer,
)
from core.synthesize import merge_bridge_sources, synthesize  # noqa: E402
from core.invoke import invoke_peer, resolve_binary  # noqa: E402
from core.packet import load_packet  # noqa: E402
from core.goal_lint import format_contract, lint_goal  # noqa: E402
from core.plan import format_plan, plan_action  # noqa: E402
from core.patterns import format_patterns, suggest_patterns  # noqa: E402
from core.job_protocol import format_job_digest, inspect_packet_job  # noqa: E402


def _resolve_packet_dir(cwd: Path, packet_id: str | None) -> Path:
    slug = project_slug(cwd)
    target = packet_id or "latest"
    if target == "latest":
        d = latest_packet_dir(slug)
        if not d:
            cur = project_relay_dir(cwd) / "CURRENT.md"
            if cur.exists():
                text = cur.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "path:" in line and "`" in line:
                        return Path(line.split("`")[1])
            raise FileNotFoundError(f"no packet for slug={slug}")
        return d
    d = packet_dir(slug, target)
    if (d / "packet.json").exists():
        return d
    if RELAY_HOME.is_dir():
        for slug_dir in RELAY_HOME.iterdir():
            cand = slug_dir / target
            if (cand / "packet.json").exists():
                return cand
    raise FileNotFoundError(f"packet not found: {target}")


def cmd_peers(_args: argparse.Namespace) -> int:
    rows = probe_all(include_optional=True)
    digger = digger_env_ids()
    print(f"session-digger: {discover_sd_root() or 'NOT FOUND'}")
    if digger:
        print(f"digger ENV_REGISTRY: {', '.join(digger)}")
    print(f"primary: {', '.join(PRIMARY_PEERS)}")
    print()
    print(f"{'PEER':<14} {'ON':<4} {'EVID':<5} {'PACK':<5} {'DELG':<5} {'PRIMARY':<8} NOTES")
    for r in rows:
        print(
            f"{r.id:<14} "
            f"{'Y' if r.present else '.':<4} "
            f"{'Y' if r.evidence else '.':<5} "
            f"{'Y' if r.pack else '.':<5} "
            f"{'Y' if r.delegate else '.':<5} "
            f"{'Y' if r.primary else '.':<8} "
            f"{r.notes or (r.markers_found[0] if r.markers_found else '')}"
        )
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    from_peer = args.from_peer or "auto"
    try:
        if from_peer == "auto":
            host = detect_host_peer(cwd)
            ref = resolve_session(session=args.session, peer="cross" if not args.session else host, cwd=cwd)
            if ref.get("peer") in (None, "unknown", "cross", "auto"):
                ref["peer"] = host if host != "unknown" else ref.get("peer", "unknown")
        else:
            ref = resolve_session(session=args.session, peer=from_peer, cwd=cwd)
    except Exception as e:
        print(f"ERROR resolve session: {e}", file=sys.stderr)
        return 1

    path = ref["path"]
    peer = ref.get("peer") or from_peer
    if not path:
        print("ERROR: empty session path", file=sys.stderr)
        return 1

    deep = bool(getattr(args, "deep", False))
    budget = args.budget or "short"
    print(f"packing from peer={peer} session={path} deep={deep} budget={budget}")
    t0 = __import__("time").time()
    try:
        evidence = collect_evidence(path, peer, cwd, deep=deep)
    except Exception as e:
        print(f"ERROR evidence: {e}", file=sys.stderr)
        return 1

    # incremental: load previous CURRENT packet if any
    prev_pkt = None
    try:
        cur = project_relay_dir(cwd) / "CURRENT.md"
        if cur.exists():
            for line in cur.read_text(encoding="utf-8").splitlines():
                if "path:" in line and "`" in line:
                    prev_dir = Path(line.split("`")[1])
                    if (prev_dir / "packet.json").exists():
                        prev_pkt = load_packet(prev_dir / "packet.json")
                    break
    except Exception:
        prev_pkt = None

    goal_text = args.goal or ""
    verify_cmd = getattr(args, "verify_cmd", "") or ""
    if getattr(args, "lint_goal", False) and goal_text:
        contract = lint_goal(goal_text, verify_cmd=verify_cmd, peer=args.to_peer or "")
        if contract.get("hardened_goal"):
            goal_text = contract["hardened_goal"]
        if contract.get("verify_cmd") and not verify_cmd:
            verify_cmd = contract["verify_cmd"]
        print(format_contract(contract))
        print("---")

    pkt = synthesize(
        evidence,
        goal=goal_text,
        from_peer=peer,
        to_peer=args.to_peer or "",
        budget=budget,
        previous_packet=prev_pkt,
        verify_cmd=verify_cmd,
    )
    slug = project_slug(cwd)
    out = packet_dir(slug, pkt["id"])
    sources = {
        "session": path,
        "peer": peer,
        "budget": budget,
        "deep": deep,
        "cwd": str(cwd),
        "files_count": len(evidence.get("files") or []),
        "knowledge_count": len(evidence.get("knowledge") or []),
        "pack_sec": round(__import__("time").time() - t0, 3),
        "delta": pkt.get("delta"),
        "verify_cmd": pkt.get("verify_cmd"),
        "fingerprints": len(pkt.get("fingerprints") or []),
    }
    try:
        save_packet(out, pkt, sources=sources)
        write_current_pointer(project_relay_dir(cwd), pkt, out)
        # also under relay home slug CURRENT
        write_current_pointer(RELAY_HOME / slug, pkt, out)
    except Exception as e:
        print(f"ERROR save: {e}", file=sys.stderr)
        return 1

    print(f"packet_id: {pkt['id']}")
    print(f"dir: {out}")
    print(f"handoff: {out / 'HANDOFF.md'}")
    print(f"goal: {pkt['goal'][:200]}")
    print(f"route: {pkt['routing']['from_peer']} → {pkt['routing']['to_peer']}")
    print(f"primary: {len(pkt['files'].get('primary') or [])} touched: {len(pkt['files'].get('touched') or [])}")
    print(f"next_actions: {len(pkt.get('next_actions') or [])} decisions: {len(pkt.get('decisions') or [])}")
    print(f"pack_sec: {sources['pack_sec']} deep={deep}")
    print(f"handoff_bytes: {(out / 'HANDOFF.md').stat().st_size}")
    print(f"verify_cmd: {pkt.get('verify_cmd') or '(none)'}")
    print(f"delta: {pkt.get('delta')}")
    print(f"fingerprints: {len(pkt.get('fingerprints') or [])}")
    print()
    print("Resume in any product:")
    print(f'  python3 "{SKILL_ROOT / "scripts" / "relay_cli.py"}" resume {pkt["id"]}')
    print(f"  or Read {out / 'HANDOFF.md'}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    slug = args.project_slug or project_slug(cwd)
    target = args.packet_id or "latest"
    if target == "latest":
        d = latest_packet_dir(slug)
        if not d:
            # try CURRENT.md in project
            cur = project_relay_dir(cwd) / "CURRENT.md"
            if cur.exists():
                text = cur.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "path:" in line and "`" in line:
                        p = line.split("`")[1]
                        d = Path(p)
                        break
        if not d or not (d / "packet.json").exists():
            print(f"ERROR: no packet for slug={slug}", file=sys.stderr)
            return 1
    else:
        d = packet_dir(slug, target)
        if not (d / "packet.json").exists():
            # search all slugs
            found = None
            if RELAY_HOME.is_dir():
                for slug_dir in RELAY_HOME.iterdir():
                    cand = slug_dir / target
                    if (cand / "packet.json").exists():
                        found = cand
                        break
            if not found:
                print(f"ERROR: packet not found: {target}", file=sys.stderr)
                return 1
            d = found

    pkt = load_packet(d / "packet.json")
    stale = []
    if is_expired(pkt):
        stale.append("packet past expires_hint_hours")
    ws = pkt.get("workspace") or {}
    if ws.get("git_head"):
        import subprocess

        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if head.returncode == 0 and head.stdout.strip() != ws["git_head"]:
                stale.append(f"git HEAD moved (packet={ws['git_head'][:8]}… now={head.stdout.strip()[:8]}…)")
        except Exception:
            pass

    routing = pkt.get("routing") or {}
    to_peer = (routing.get("to_peer") or "").lower().strip()
    from_peer = routing.get("from_peer", "?")

    print("=== agent-relay RESUME INJECT ===")
    print(f"packet_id: {pkt['id']}")
    print(f"dir: {d}")
    print(f"routing: {from_peer} → {to_peer}")
    if stale:
        print("STALE:", "; ".join(stale))
    print()

    # ZCode-specific prefix when routing target is zcode
    if to_peer == "zcode":
        print("## ZCode 续跑（目标为 zcode）")
        print()
        print(f"本接力包目标为 ZCode。因 ZCode 无 CLI 无法自动 invoke，")
        print("请手动操作：")
        print()
        print(f"1. Read 本 HANDOFF.md 与 primary 文件")
        print(f"2. 按 next_actions 顺序推进；背景已在 HANDOFF 中列出，不再问")
        print(f"3. 完成后回包：")
        print(f"   `python3 {SKILL_ROOT / 'scripts' / 'relay_cli.py'} pack --from zcode --to {from_peer}`")
        print(f"4. 如有 VERIFY 命令，完成后手动运行验收")
        print()

    handoff = d / "HANDOFF.md"
    if handoff.exists():
        print(handoff.read_text(encoding="utf-8"))
    else:
        print(json.dumps(pkt, ensure_ascii=False, indent=2))
    print("=== END INJECT ===")
    print()
    print("Instructions for receiving agent:")
    print("1. Treat above as ground navigation; open primary files from disk before editing.")
    print("2. Execute next_actions in order; do not re-ask background already listed.")
    print("3. When done or switching product again: agent-relay pack")
    if stale:
        print()
        print("STALE WARNINGS:")
        for s in stale:
            print(f"  - {s}")
        print("建议: 先 git status/log 确认当前状态，再继续。")
    return 0


def cmd_bridge(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    keyword = args.keyword
    print(f"bridge search: {keyword!r} (agent=cross)")
    text = search_sessions(keyword, limit=args.limit or 8, agent="cross")
    # also pack current/latest session as base
    deep = bool(getattr(args, "deep", False))
    try:
        ref = resolve_session(session=None, peer="cross", cwd=cwd)
        evidence = collect_evidence(
            ref["path"], ref.get("peer") or "unknown", cwd, deep=deep
        )
        pkt = synthesize(
            evidence,
            goal=args.goal or f"继续与「{keyword}」相关的跨环境任务",
            from_peer=ref.get("peer") or "unknown",
            to_peer=args.to_peer or "",
            budget=args.budget or "short",
        )
        pkt = merge_bridge_sources(pkt, text, keyword)
    except Exception as e:
        print(f"WARN base pack from latest session failed: {e}", file=sys.stderr)
        from core.packet import empty_packet

        pkt = empty_packet(
            goal=args.goal or f"跨环境对齐：{keyword}",
            from_peer="mixed",
            to_peer=args.to_peer or "any",
        )
        pkt["next_actions"] = [
            f"阅读 bridge 命中会话中与「{keyword}」相关的结论",
            "打开相关文件，确认未决项",
            "选择 to_peer 继续执行",
        ]
        pkt = merge_bridge_sources(pkt, text, keyword)
        # validate needs files etc — empty_packet already valid

    errs = validate_packet(pkt)
    if errs:
        print("ERROR packet:", errs, file=sys.stderr)
        return 1

    slug = project_slug(cwd)
    out = packet_dir(slug, pkt["id"])
    sources = {"bridge_keyword": keyword, "search_excerpt": (text or "")[:4000]}
    save_packet(out, pkt, sources=sources)
    write_current_pointer(project_relay_dir(cwd), pkt, out)
    print(f"packet_id: {pkt['id']}")
    print(f"dir: {out}")
    print(f"sources: {len((pkt.get('provenance') or {}).get('sources') or [])}")
    print("--- search excerpt ---")
    print((text or "")[:2500])
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    host = detect_host_peer()
    task = args.task or ""
    r = suggest_to_peer(task, from_peer=host)
    print(json.dumps({"from_peer": host, **r, "present": [p.id for p in probe_all() if p.present]}, ensure_ascii=False, indent=2))
    cards = suggest_patterns(task, limit=3)
    if cards:
        print()
        print(format_patterns(cards))
    return 0


def cmd_goal_lint(args: argparse.Namespace) -> int:
    """Harden a vague goal into a falsifiable contract (no model call)."""
    goal = getattr(args, "goal", "") or getattr(args, "task", "") or ""
    c = lint_goal(
        goal,
        verify_cmd=getattr(args, "verify_cmd", "") or "",
        mode=getattr(args, "mode", "") or "",
        peer=getattr(args, "to_peer", "") or "",
    )
    print(format_contract(c, as_json=bool(getattr(args, "json", False))))
    if c.get("grade") == "blocked":
        return 2
    if c.get("grade") == "needs_work":
        return 1
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Dry-run: route + goal contract + risks, zero peer tokens."""
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    p = plan_action(
        task=getattr(args, "task", "") or "",
        goal=getattr(args, "goal", "") or "",
        to_peer=getattr(args, "to_peer", "") or "",
        from_peer=getattr(args, "from_peer", "") or "auto",
        mode=getattr(args, "mode", "") or "",
        verify_cmd=getattr(args, "verify_cmd", "") or "",
        action=getattr(args, "action", "") or "delegate",
        cwd=cwd,
    )
    print(format_plan(p, as_json=bool(getattr(args, "json", False))))
    risks = p.get("risks") or []
    grade = (p.get("contract") or {}).get("grade")
    if grade == "blocked" or any("不可 delegate" in x for x in risks):
        return 2
    if grade == "needs_work" or risks:
        return 1
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from core.env_map import format_workspace_map, map_workspace, scan_environments

    sd = discover_sd_root()
    cwd = Path(getattr(args, "cwd", None) or Path.cwd()).resolve()
    print("=== agent-relay doctor ===")
    print(f"skill_root: {SKILL_ROOT}")
    print(f"relay_home: {RELAY_HOME} exists={RELAY_HOME.exists()}")
    print(f"workspace: {cwd}")
    print(f"session-digger: {sd or 'optional (not installed)'}")
    digger = digger_env_ids()
    if digger:
        print(f"digger envs: {digger}")
    print()
    # environment ↔ this workspace (standalone, digger-inspired)
    print("--- workspace → agent env map (native) ---")
    print(format_workspace_map(map_workspace(cwd)))
    print()
    cmd_peers(args)
    print()
    # recent packets
    if RELAY_HOME.is_dir():
        n = 0
        for slug_dir in sorted(RELAY_HOME.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            if not slug_dir.is_dir():
                continue
            latest = latest_packet_dir(slug_dir.name)
            if latest:
                print(f"packet: {slug_dir.name} -> {latest.name}")
                n += 1
        if n == 0:
            print("packets: none yet")
    print()
    from core.invoke import zcode_invoke_enabled  # local import ok

    print("L2 invoke: claude/grok/kimi when CLI on PATH; zcode 默认关闭（手动 resume）")
    print("  handoff --to zcode: 自动降级为打印手动续跑指令块（exit 0）")
    print("L3 bus: deferred")
    print(f"  visible terminal: {__import__('core.invoke', fromlist=['_preferred_terminal_app'])._preferred_terminal_app()}")
    for peer in ("claude", "grok", "kimi_code", "zcode"):
        b = resolve_binary(peer, force_zcode=(peer == "zcode" and zcode_invoke_enabled()))
        if peer == "zcode" and not zcode_invoke_enabled():
            print(
                "  invoke zcode: DISABLED (pack/resume/manual 接力; "
                "set AGENT_RELAY_ENABLE_ZCODE_INVOKE=1 to experiment)"
            )
        else:
            print(f"  invoke {peer}: {b or 'NOT ON PATH'}")
    # standalone health: skill + at least one env present on machine
    envs = scan_environments()
    any_env = any(e.present for e in envs)
    return 0 if any_env else 1


def cmd_envs(args: argparse.Namespace) -> int:
    """List agent environments on this machine (no digger required)."""
    from core.env_map import format_env_scan, scan_environments

    rows = scan_environments()
    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
    else:
        print(format_env_scan(rows))
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    """Map a project/workspace folder onto each agent env's storage layout."""
    from core.env_map import format_workspace_map, map_workspace

    project = Path(args.project).expanduser().resolve() if getattr(args, "project", None) else Path.cwd()
    report = map_workspace(project)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_workspace_map(report))
    return 0 if report.get("bound_count", 0) >= 0 else 1


def cmd_invoke(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    to_peer = (args.to_peer or "").lower().strip()
    if not to_peer:
        print("ERROR: --to peer required (claude|grok|zcode|…)", file=sys.stderr)
        return 2
    try:
        d = _resolve_packet_dir(cwd, args.packet_id)
        pkt = load_packet(d / "packet.json")
    except Exception as e:
        print(f"ERROR load packet: {e}", file=sys.stderr)
        return 1

    # default to routing.to_peer if --to any
    if to_peer == "any":
        to_peer = (pkt.get("routing") or {}).get("to_peer") or (pkt.get("routing") or {}).get(
            "recommended_peer"
        ) or "claude"
        if to_peer in ("any", "unknown"):
            to_peer = "claude"
    if to_peer == "kimi":
        to_peer = "kimi_code"

    force_zcode = bool(getattr(args, "force_zcode", False))
    bin_path = resolve_binary(to_peer, force_zcode=force_zcode)
    print(f"invoke peer={to_peer} binary={bin_path or 'MISSING'} packet={pkt.get('id')}")
    print(f"mode={args.mode} dir={d}")

    # ZCode: no reliable CLI → graceful handoff to manual resume
    if to_peer == "zcode" and not bin_path:
        handoff_path = d / "HANDOFF.md"
        cwd_path = str(cwd)
        from_peer = (pkt.get("routing") or {}).get("from_peer", "?")
        print()
        print("=== ZCode 手动接力 ===")
        print(f"packet_id: {pkt['id']}")
        print(f"dir: {d}")
        print()
        print("ZCode 无稳定 CLI，无法自动 invoke。请手动在 ZCode 中续跑：")
        print()
        print(f"1. 当前目录: `{cwd_path}`")
        print(f"2. 读取接力文件: Read `{handoff_path}`")
        print(f"   或 Read `.relay/CURRENT.md`")
        print(f"3. 按 next_actions 推进，完成后: `pack --from zcode --to {from_peer}`")
        print()
        print("ZCode 续跑指令块（可直接复制到 ZCode 聊天框）:")
        print("```")
        print(f"agent-relay 接力包 `{pkt['id']}`")
        print(f"目标：{pkt.get('goal', '')[:200]}")
        print(f"来源：{from_peer} → zcode")
        print(f"方法：")
        print(f"  1. Read `{handoff_path}`")
        print(f"  2. 打开 primary 文件（见 HANDOFF 中的「先读这些文件」）")
        print(f"  3. 按下一步清单（next_actions）顺序执行")
        print(f"  4. 完成后运行: `python3 {SKILL_ROOT / 'scripts' / 'relay_cli.py'} pack --from zcode --to {from_peer}`")
        print(f"  5. 如有 VERIFY 命令，完成后手动运行验收")
        print("```")
        print()
        print("已验证: ZCode resume 路径可用（print HINT + exit 0）")
        return 0

    if not bin_path:
        print(
            f"ERROR: cannot invoke {to_peer} (CLI not on PATH). Use resume + open that product UI, or install CLI.",
            file=sys.stderr,
        )
        print(f"HINT: python3 {SKILL_ROOT / 'scripts' / 'relay_cli.py'} resume {pkt.get('id')}")
        return 127

    wait = bool(getattr(args, "wait", False))
    lean = not bool(getattr(args, "full_context", False))
    result = invoke_peer(
        to_peer,
        pkt,
        d,
        cwd=cwd,
        mode=args.mode or "continue",
        extra=args.extra or "",
        timeout=int(args.timeout or 600),
        max_turns=int(args.max_turns) if args.max_turns is not None else 8,
        dry_run=bool(getattr(args, "cmd_only", False)),
        visible=bool(getattr(args, "visible", False)),
        marker=getattr(args, "marker", None) or "agent-relay",
        force_zcode=force_zcode,
        wait=wait,
        lean=lean,
    )
    # redact prompt in printed cmd
    printable = result.to_dict()
    from core.invoke import _cmd_for_log

    printable["cmd"] = _cmd_for_log(result.cmd)
    print(json.dumps(printable, ensure_ascii=False, indent=2)[:4000])
    if result.stdout_path:
        print(f"stdout_file: {result.stdout_path}")
    hist = d / "invoke" / "history.jsonl"
    if hist.exists():
        print(f"history: {hist}")
    if result.session_id:
        print()
        print("======== 可见会话（请在本机打开印证） ========")
        print(f"session_id: {result.session_id}")
        print(f"resume_cmd: {result.resume_cmd}")
        vis = d / "invoke" / "VISIBLE_SESSION.md"
        if vis.exists():
            print(f"pointer: {vis}")
        print("==========================================")
    if result.ok:
        if "ASYNC_STARTED" in (result.summary or ""):
            print("INVOKE_ASYNC_OK")
        else:
            print("INVOKE_OK")
        return 0
    print(f"INVOKE_FAIL: {result.error}", file=sys.stderr)
    return result.exit_code or 1


def cmd_job_status(args: argparse.Namespace) -> int:
    """Poll packet job digest (LAST_JOB + result.json → fleet-like state)."""
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    try:
        d = _resolve_packet_dir(cwd, getattr(args, "packet_id", None) or "latest")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    digest = inspect_packet_job(d)
    if getattr(args, "json", False):
        print(json.dumps(digest, ensure_ascii=False, indent=2))
    else:
        print(format_job_digest(digest))
    # raw LAST_JOB for debug when --verbose
    if getattr(args, "verbose", False):
        last = d / "invoke" / "LAST_JOB.json"
        if last.exists():
            print("--- LAST_JOB ---")
            print(last.read_text(encoding="utf-8")[:4000])
    st = digest.get("state")
    print(f"JOB_STATUS={st}")
    print(f"VERIFY={digest.get('verify')}")
    if digest.get("stalled"):
        print("WARN: job appears stalled (>15m no progress)")
    return 0 if st in ("completed", "running", "idle") else 1


def cmd_handoff(args: argparse.Namespace) -> int:
    """pack (if needed) then invoke target peer — real collaboration entry."""
    # 1) pack
    pack_ns = argparse.Namespace(
        from_peer=args.from_peer or "auto",
        to_peer=args.to_peer or "",
        session=args.session,
        goal=args.goal or "",
        budget=args.budget or "short",
        cwd=args.cwd,
        deep=bool(getattr(args, "deep", False)),
        verify_cmd=getattr(args, "verify_cmd", "") or "",
        lint_goal=bool(getattr(args, "lint_goal", False)),
    )
    rc = cmd_pack(pack_ns)
    if rc != 0:
        return rc
    # 2) invoke latest
    inv = argparse.Namespace(
        to_peer=args.to_peer or "any",
        packet_id="latest",
        mode=args.mode or "continue",
        extra=args.extra or "",
        timeout=args.timeout or 600,
        max_turns=args.max_turns if args.max_turns is not None else 8,
        cwd=args.cwd,
        cmd_only=False,
        visible=bool(getattr(args, "visible", False)),
        marker=getattr(args, "marker", None) or "agent-relay-VISIBLE",
        force_zcode=bool(getattr(args, "force_zcode", False)),
        wait=bool(getattr(args, "wait", False)),
        full_context=bool(getattr(args, "full_context", False)),
    )
    return cmd_invoke(inv)


def cmd_delegate(args: argparse.Namespace) -> int:
    """Delegate a task to another agent (subordinate pattern), wait, report result.

    Unlike handoff which transfers ownership, delegate keeps the caller in charge.
    Returns structured result lines for the calling agent to consume.
    """
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else Path.cwd()
    to_peer = (args.to_peer or "").lower().strip()
    if not to_peer:
        print("ERROR: --to peer required (claude|grok|kimi_code|…)", file=sys.stderr)
        return 2
    if to_peer == "kimi":
        to_peer = "kimi_code"

    task = args.task or ""
    if not task.strip():
        print("ERROR: --task TEXT required", file=sys.stderr)
        return 2

    # 1. pack — 优先尝试用 session-digger 收集上下文，没有则构建最小证据
    from_peer = args.from_peer or detect_host_peer(cwd)
    sd_root = discover_sd_root()
    evidence = None
    if sd_root:
        try:
            ref = resolve_session(session=args.session, peer=from_peer, cwd=cwd)
            path = str(ref.get("path") or "")
            peer = ref.get("peer") or from_peer
            if path and path.strip():
                evidence = collect_evidence(path.strip(), peer, cwd, deep=False)
        except Exception:
            evidence = None

    if evidence is None:
        # No session-digger or session resolution — build minimal evidence from workspace
        ws = git_workspace(cwd)
        evidence = {
            "session_path": "",
            "peer": from_peer or "unknown",
            "files": [],
            "messages": "",
            "knowledge": [],
            "workspace": ws,
        }
        peer = from_peer or detect_host_peer(cwd)
        if not sd_root:
            print("delegate: session-digger 未安装 — 跳过会话分析，仅依赖 task + workspace")

    # harden goal (trust loop front half)
    verify_cmd = getattr(args, "verify_cmd", "") or ""
    contract = lint_goal(task, verify_cmd=verify_cmd, mode=getattr(args, "mode", "") or "", peer=to_peer)
    goal = contract.get("hardened_goal") or task
    if contract.get("verify_cmd") and not verify_cmd:
        verify_cmd = contract["verify_cmd"]
    if contract.get("grade") != "ready":
        warn = "; ".join(contract.get("warnings") or [])
        print(f"delegate: goal grade={contract.get('grade')} — {warn}")
        if getattr(args, "strict_goal", False):
            print("ERROR: --strict-goal and goal not ready; run goal-lint first", file=sys.stderr)
            return 2

    pkt = synthesize(
        evidence,
        goal=goal,
        from_peer=peer,
        to_peer=to_peer,
        budget=args.budget or "short",
        previous_packet=None,
        verify_cmd=verify_cmd,
    )
    # override routing: delegate doesn't transfer ownership
    pkt["routing"]["from_peer"] = peer
    pkt["routing"]["to_peer"] = to_peer
    pkt["routing"]["handoff_phrase"] = (
        f"委派任务：{task[:120]}. 完成后输出一屏 JSON 包含 done/files/open/verify."
    )
    pkt["status"] = "delegated"

    slug = project_slug(cwd)
    out = packet_dir(slug, pkt["id"])
    session_path = evidence.get("session_path") or ""
    sources = {
        "session": session_path,
        "peer": peer,
        "delegate_to": to_peer,
        "task": task,
        "cwd": str(cwd),
        "files_count": len(evidence.get("files") or []),
    }
    save_packet(out, pkt, sources=sources)
    # write CURRENT pointer so resume works, but note it's a delegation
    write_current_pointer(project_relay_dir(cwd), pkt, out)
    write_current_pointer(RELAY_HOME / slug, pkt, out)

    # 2. invoke with wait=true (subordinate must finish before we continue)
    bin_path = resolve_binary(to_peer, force_zcode=False)
    if not bin_path:
        print(f"DELEGATE_FAIL peer={to_peer} reason='CLI not on PATH'")
        if to_peer == "zcode":
            print(f"  ZCode 无 CLI，无法委派。请用 handoff + 手动 resume。")
        print(f"  HINT: python3 {SKILL_ROOT / 'scripts' / 'relay_cli.py'} resume {pkt['id']}")
        return 127

    t0 = __import__("time").time()
    result = invoke_peer(
        to_peer, pkt, out,
        cwd=cwd,
        mode=args.mode or "implement",
        extra=f"委派任务：{task}",
        timeout=int(args.timeout or 300),
        max_turns=int(args.max_turns) if args.max_turns is not None else 6,
        dry_run=False,
        visible=bool(getattr(args, "visible", False)),
        marker=f"agent-relay-delegate-{pkt['id']}",
        force_zcode=False,
        wait=True,
        lean=True,
    )
    duration = round(__import__("time").time() - t0, 2)

    # 3. parse result.json for structured report
    result_json_path = out / "result.json"
    rj = out / "invoke" / "result.json"
    if not result_json_path.exists() and rj.exists():
        result_json_path = rj

    result_data = {}
    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    verify_result = (result_data.get("verify") or {}).get("result") or result_data.get("status") or "unknown"
    done_items = result_data.get("done") or []
    files_changed = result_data.get("files") or []
    open_items = result_data.get("open") or []

    # 4. print structured result for the calling agent
    if result.ok and verify_result in ("pass", "verified_pass", "completed_unverified"):
        print(f"DELEGATE_OK peer={to_peer} duration={duration}s verify={verify_result}")
    elif result.ok:
        print(f"DELEGATE_PARTIAL peer={to_peer} duration={duration}s verify={verify_result}")
    else:
        print(f"DELEGATE_FAIL peer={to_peer} duration={duration}s exit={result.exit_code}")

    if done_items:
        print(f"done: {'; '.join(done_items[:5])}")
    if files_changed:
        print(f"files: {' '.join(files_changed[:8])}")
    if open_items:
        print(f"open: {'; '.join(open_items[:3])}")
    if result.session_id:
        print(f"session: {result.session_id}")
    print(f"result: {result_json_path}")
    print(f"packet: {out}/packet.json")

    if getattr(args, "visible", False) and result.session_id:
        from core.invoke import open_peer_visible
        ok_open, msg = open_peer_visible(to_peer, result.session_id, cwd)
        if ok_open:
            print(f"visible: opened")

    # update packet status
    try:
        pkt2 = dict(pkt)
        pkt2["status"] = f"delegate_{'ok' if result.ok else 'fail'}"
        pkt2["done"] = list(dict.fromkeys(list(pkt.get("done") or []) + done_items))[-12:]
        pkt2["open"] = list(open_items)
        save_packet(out, pkt2)
    except Exception:
        pass

    if result.ok and verify_result in ("pass", "verified_pass"):
        return 0
    return result.exit_code or (2 if verify_result == "fail" else 1)


def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve() if args.project else Path.cwd()
    rd = project_relay_dir(project)
    rd.mkdir(parents=True, exist_ok=True)
    readme = rd / "README.md"
    if not readme.exists():
        readme.write_text(
            "# .relay\n\n"
            "agent-relay 项目级指针目录。\n\n"
            "- `CURRENT.md` — 最新接力包指针（pack 自动更新）\n"
            "- 全局包目录：`~/.agents/relay/<project-slug>/<packet-id>/`\n\n"
            "任意产品续跑：Read CURRENT.md → 打开 HANDOFF.md → 执行 next_actions。\n",
            encoding="utf-8",
        )
    gitignore = rd / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# 可选：若不想提交接力状态\n# CURRENT.md\n", encoding="utf-8")
    # entry snippets
    templates = SKILL_ROOT / "templates"
    for name in ("entry-claude.md", "entry-grok.md", "entry-zcode.md"):
        src = templates / name
        if src.exists():
            dest = rd / name
            if not dest.exists():
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"initialized: {rd}")
    print("Add a one-liner to CLAUDE.md / AGENTS.md / ZCode rules: see templates in .relay/")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-relay", description="Multi-agent task relay (product-agnostic)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("peers", help="Probe local agent products")
    p_doc = sub.add_parser("doctor", help="Health check + workspace env map")
    p_doc.add_argument("--cwd", default=None, help="workspace to map (default: cwd)")

    p_envs = sub.add_parser(
        "envs",
        help="Scan agent environments on this machine (standalone, digger-inspired)",
    )
    p_envs.add_argument("--json", action="store_true")

    p_ws = sub.add_parser(
        "workspace",
        help="Map a project folder → each env's data dirs (any workspace path)",
    )
    p_ws.add_argument(
        "--project",
        "-p",
        default=None,
        help="project/workspace path (default: cwd)",
    )
    p_ws.add_argument("--json", action="store_true")

    p_pack = sub.add_parser("pack", help="Pack session into handoff packet")
    p_pack.add_argument("--from", dest="from_peer", default="auto", help="source peer or auto")
    p_pack.add_argument("--to", dest="to_peer", default="", help="target peer")
    p_pack.add_argument("--session", default=None, help="session path or zcode://id")
    p_pack.add_argument("--goal", default="", help="explicit completion criteria")
    p_pack.add_argument("--budget", choices=("short", "medium"), default="short")
    p_pack.add_argument(
        "--deep",
        action="store_true",
        help="expensive extract-knowledge + larger message window (default off)",
    )
    p_pack.add_argument(
        "--verify-cmd",
        dest="verify_cmd",
        default="",
        help='shell验收 e.g. test -f path && rg -q TOKEN path',
    )
    p_pack.add_argument(
        "--lint-goal",
        action="store_true",
        help="harden --goal via goal-lint before pack (trust loop)",
    )
    p_pack.add_argument("--cwd", default=None)

    p_res = sub.add_parser("resume", help="Print resume inject block")
    p_res.add_argument("packet_id", nargs="?", default="latest")
    p_res.add_argument("--project-slug", default=None)
    p_res.add_argument("--cwd", default=None)

    p_br = sub.add_parser("bridge", help="Cross-peer search + pack")
    p_br.add_argument("keyword")
    p_br.add_argument("--limit", type=int, default=8)
    p_br.add_argument("--to", dest="to_peer", default="")
    p_br.add_argument("--goal", default="")
    p_br.add_argument("--budget", choices=("short", "medium"), default="short")
    p_br.add_argument("--deep", action="store_true", help="deep evidence when packing bridge base")
    p_br.add_argument("--cwd", default=None)

    p_sg = sub.add_parser("suggest", help="Recommend to_peer")
    p_sg.add_argument("--task", default="")

    p_inv = sub.add_parser("invoke", help="Actually call another product CLI with packet")
    p_inv.add_argument("--to", dest="to_peer", required=True, help="claude|grok|zcode|any")
    p_inv.add_argument("--packet", dest="packet_id", default="latest")
    p_inv.add_argument(
        "--mode",
        default="continue",
        choices=("continue", "review", "implement", "fix"),
    )
    p_inv.add_argument("--extra", default="", help="extra instruction for the callee")
    p_inv.add_argument("--timeout", type=int, default=600)
    p_inv.add_argument("--max-turns", dest="max_turns", type=int, default=8)
    p_inv.add_argument("--cmd-only", dest="cmd_only", action="store_true", help="dry-run: print cmd only")
    p_inv.add_argument(
        "--wait",
        action="store_true",
        help="block until callee finishes (default: async, return immediately)",
    )
    p_inv.add_argument(
        "--full-context",
        action="store_true",
        help="disable lean prompt/flags (more skills/context, slower)",
    )
    p_inv.add_argument(
        "--visible",
        action="store_true",
        help="when job finishes, open Ghostty/Terminal with --resume",
    )
    p_inv.add_argument("--marker", default="agent-relay", help="session title marker to locate")
    p_inv.add_argument(
        "--force-zcode",
        action="store_true",
        help="opt-in: allow experimental ZCode app-bundle headless invoke (default OFF)",
    )
    p_inv.add_argument("--cwd", default=None)

    p_job = sub.add_parser("job-status", help="Poll async invoke job status")
    p_job.add_argument("--packet", dest="packet_id", default="latest")
    p_job.add_argument("--json", action="store_true", help="machine-readable digest")
    p_job.add_argument("--verbose", action="store_true", help="include raw LAST_JOB")
    p_job.add_argument("--cwd", default=None)

    p_gl = sub.add_parser("goal-lint", help="Harden goal into falsifiable contract")
    p_gl.add_argument("--goal", default="", help="goal text")
    p_gl.add_argument("--task", default="", help="alias of --goal")
    p_gl.add_argument("--verify-cmd", dest="verify_cmd", default="")
    p_gl.add_argument("--mode", default="")
    p_gl.add_argument("--to", dest="to_peer", default="")
    p_gl.add_argument("--json", action="store_true")

    p_pl = sub.add_parser("plan", help="Dry-run route+contract (no peer tokens)")
    p_pl.add_argument("--task", default="", help="rough task")
    p_pl.add_argument("--goal", default="", help="alias of --task")
    p_pl.add_argument("--to", dest="to_peer", default="")
    p_pl.add_argument("--from", dest="from_peer", default="auto")
    p_pl.add_argument("--mode", default="")
    p_pl.add_argument("--verify-cmd", dest="verify_cmd", default="")
    p_pl.add_argument(
        "--action",
        default="delegate",
        choices=("delegate", "pack", "handoff", "bridge"),
    )
    p_pl.add_argument("--json", action="store_true")
    p_pl.add_argument("--cwd", default=None)

    p_ho = sub.add_parser("handoff", help="pack then invoke (real cross-product call)")
    p_ho.add_argument("--from", dest="from_peer", default="auto")
    p_ho.add_argument("--to", dest="to_peer", required=True)
    p_ho.add_argument("--session", default=None)
    p_ho.add_argument("--goal", default="")
    p_ho.add_argument("--budget", choices=("short", "medium"), default="short")
    p_ho.add_argument(
        "--mode",
        default="continue",
        choices=("continue", "review", "implement", "fix"),
    )
    p_ho.add_argument("--extra", default="")
    p_ho.add_argument("--timeout", type=int, default=600)
    p_ho.add_argument("--max-turns", dest="max_turns", type=int, default=12)
    p_ho.add_argument("--visible", action="store_true")
    p_ho.add_argument("--marker", default="agent-relay-VISIBLE")
    p_ho.add_argument("--force-zcode", action="store_true")
    p_ho.add_argument("--wait", action="store_true", help="block until invoke finishes")
    p_ho.add_argument("--full-context", action="store_true")
    p_ho.add_argument("--deep", action="store_true", help="deep pack evidence")
    p_ho.add_argument("--cwd", default=None)

    p_del = sub.add_parser("delegate", help="Delegate task to subordinate agent (wait+result)")
    p_del.add_argument("--to", dest="to_peer", required=True, help="target peer (claude|grok|kimi_code|…)")
    p_del.add_argument("--task", required=True, help="task description (becomes goal + VERIFY)")
    p_del.add_argument("--from", dest="from_peer", default="auto", help="source peer override")
    p_del.add_argument("--session", default=None, help="session path")
    p_del.add_argument("--mode", default="implement", choices=("continue", "review", "implement", "fix"))
    p_del.add_argument("--budget", choices=("short", "medium"), default="short")
    p_del.add_argument("--verify-cmd", dest="verify_cmd", default="", help="acceptance command")
    p_del.add_argument(
        "--strict-goal",
        action="store_true",
        help="abort if goal-lint grade != ready",
    )
    p_del.add_argument("--timeout", type=int, default=300)
    p_del.add_argument("--max-turns", type=int, default=6)
    p_del.add_argument("--visible", action="store_true", help="open visible terminal window")
    p_del.add_argument("--cwd", default=None)

    p_init = sub.add_parser("init", help="Init project .relay/")
    p_init.add_argument("--project", default=None)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "peers": cmd_peers,
        "envs": cmd_envs,
        "workspace": cmd_workspace,
        "pack": cmd_pack,
        "resume": cmd_resume,
        "bridge": cmd_bridge,
        "suggest": cmd_suggest,
        "invoke": cmd_invoke,
        "handoff": cmd_handoff,
        "delegate": cmd_delegate,
        "job-status": cmd_job_status,
        "goal-lint": cmd_goal_lint,
        "plan": cmd_plan,
        "doctor": cmd_doctor,
        "init": cmd_init,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
