"""L2: invoke another product CLI with a handoff packet (real call)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .packet import load_packet, render_handoff_md, save_packet
from .result_protocol import (
    extract_verify_cmd,
    mode_instruction,
    parse_one_screen_result,
    run_verify,
    write_result_json,
)


@dataclass
class InvokeResult:
    peer: str
    ok: bool
    exit_code: int
    cmd: list[str]
    stdout_path: str
    stderr_path: str
    duration_sec: float
    summary: str
    error: str = ""
    session_id: str = ""
    resume_cmd: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_zcode_session_after(
    *,
    started_after_unix: float,
    marker: str = "agent-relay",
) -> dict[str, str] | None:
    """Locate the ZCode session created by a headless -p run (for user-visible resume)."""
    try:
        from .paths import discover_sd_root
        import sys

        sd = discover_sd_root()
        if not sd:
            return None
        scripts = str(sd / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import echolib  # type: ignore

        rows = echolib.zcode_db_list_sessions(limit=20, keyword="") or []
        if isinstance(rows, dict):
            rows = rows.get("sessions") or []
        best = None
        best_ts = 0.0
        for r in rows:
            if not isinstance(r, dict):
                continue
            sid = str(r.get("id") or "")
            if not sid.startswith("sess_"):
                continue
            title = str(r.get("title") or r.get("summary") or "")
            created = str(r.get("created") or r.get("modified") or "")
            # parse ISO loosely
            ts = 0.0
            try:
                from datetime import datetime

                c = created.replace("Z", "+00:00")
                ts = datetime.fromisoformat(c).timestamp()
            except Exception:
                # fallback: agent dir mtime
                ad = Path.home() / ".zcode" / "cli" / "agents" / sid
                if ad.exists():
                    ts = ad.stat().st_mtime
            if ts + 2 < started_after_unix:  # small clock skew allowance
                continue
            score = ts
            if marker and marker.lower() in title.lower():
                score += 1e9
            if "agent-relay" in title.lower() or "接力" in title:
                score += 1e8
            if score >= best_ts:
                best_ts = score
                best = {
                    "id": sid,
                    "title": title[:120],
                    "created": created,
                    "modified": str(r.get("modified") or ""),
                }
        return best
    except Exception:
        return None


def parse_kimi_session_from_stdout(text: str) -> str:
    """Parse: To resume this session: kimi -r session_xxx"""
    import re

    m = re.search(r"kimi\s+(-r|-S|--session)\s+(session_[A-Za-z0-9-]+)", text or "")
    if m:
        return m.group(2)
    m = re.search(r"(session_[0-9a-fA-F-]{8,})", text or "")
    return m.group(1) if m else ""


def parse_mimo_session_from_text(text: str) -> str:
    """Parse ses_* session ids from mimo stdout/stderr."""
    m = re.search(r"\b(ses_[A-Za-z0-9]+)\b", text or "")
    return m.group(1) if m else ""


def find_mimo_session_after(
    *,
    started_after_unix: float,
    marker: str = "agent-relay",
) -> dict[str, str] | None:
    """Newest mimocode session dir under ~/.local/share/mimocode/memory/sessions."""
    roots = [
        Path.home() / ".local" / "share" / "mimocode" / "memory" / "sessions",
        Path.home() / ".local" / "state" / "mimocode",
    ]
    best = None
    best_score = -1.0
    for root in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir() or not d.name.startswith("ses_"):
                continue
            try:
                mtime = d.stat().st_mtime
            except OSError:
                continue
            if mtime + 2 < started_after_unix:
                continue
            score = float(mtime)
            title = d.name
            for meta_name in ("meta.json", "session.json", "info.json"):
                mp = d / meta_name
                if mp.is_file():
                    try:
                        data = json.loads(mp.read_text(encoding="utf-8"))
                        title = str(data.get("title") or data.get("name") or title)[:160]
                    except Exception:
                        pass
            if marker and marker.lower() in title.lower():
                score += 1e12
            if "agent-relay" in title.lower():
                score += 1e11
            if score > best_score:
                best_score = score
                best = {
                    "id": d.name,
                    "title": title,
                    "path": str(d),
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                }
    return best


def mimo_default_model() -> str:
    """Model for mimo run: env > config.json > known-good default."""
    env = (os.environ.get("AGENT_RELAY_MIMO_MODEL") or "").strip()
    if env:
        return env
    for cfg in (
        Path.home() / ".config" / "mimocode" / "mimocode.json",
        Path.home() / ".config" / "mimocode" / "mimocode.jsonc",
    ):
        if not cfg.is_file():
            continue
        try:
            raw = cfg.read_text(encoding="utf-8")
            raw = re.sub(r"//.*?$", "", raw, flags=re.M)
            data = json.loads(raw)
            m = (data.get("model") or "").strip()
            if m:
                return m
        except Exception:
            continue
    return "deepseek/deepseek-v4-flash"


def find_kimi_session_after(*, started_after_unix: float, marker: str = "agent-relay") -> dict[str, str] | None:
    """Newest kimi-code session dir after timestamp, optionally matching marker in wire.jsonl."""
    root = Path.home() / ".kimi-code" / "sessions"
    if not root.is_dir():
        return None
    best = None
    best_score = -1.0
    for d in root.rglob("session_*"):
        if not d.is_dir():
            continue
        mtime = d.stat().st_mtime
        if mtime + 2 < started_after_unix:
            continue
        sid = d.name  # session_uuid
        score = mtime
        title = ""
        wire = d / "agents" / "main" / "wire.jsonl"
        if wire.exists():
            try:
                with wire.open(encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh):
                        if i > 40:
                            break
                        if marker and marker.lower() in line.lower():
                            score += 1e12
                        if "agent-relay" in line.lower() or "接手任务" in line:
                            score += 1e11
                        if not title and ("prompt" in line or "user" in line.lower()):
                            title = line[:160]
            except Exception:
                pass
        if score > best_score:
            best_score = score
            best = {
                "id": sid,
                "title": title[:120] or sid,
                "path": str(wire if wire.exists() else d),
                "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
            }
    return best


def find_claude_session_after(
    *,
    cwd: Path,
    started_after_unix: float,
    marker: str = "agent-relay",
) -> dict[str, str] | None:
    """Find Claude Code jsonl session created by a recent -p invoke."""
    # project dir encoding matches Claude: replace / with -
    enc = str(cwd.resolve()).replace("/", "-")
    if enc.startswith("-"):
        enc = enc[1:]
    # Claude uses path like -Users-...
    proj = Path.home() / ".claude" / "projects" / f"-{enc}" if not enc.startswith("-") else Path.home() / ".claude" / "projects" / enc
    # also try exact Claude style
    candidates = [
        Path.home() / ".claude" / "projects" / ("-" + str(cwd.resolve()).lstrip("/").replace("/", "-")),
        Path.home() / ".claude" / "projects" / str(cwd.resolve()).replace("/", "-"),
    ]
    # de-dup
    seen = set()
    dirs = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            dirs.append(c)
    best = None
    best_score = -1.0
    for proj_dir in dirs:
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            mtime = f.stat().st_mtime
            if mtime + 2 < started_after_unix:
                continue
            # scan first ~40 lines for marker in user message
            title = ""
            hit_marker = False
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh):
                        if i > 60:
                            break
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
                        role = msg.get("role") or o.get("type") or o.get("role")
                        content = msg.get("content") or o.get("content") or ""
                        if isinstance(content, list):
                            parts = []
                            for b in content:
                                if isinstance(b, dict):
                                    parts.append(str(b.get("text") or ""))
                                else:
                                    parts.append(str(b))
                            content = " ".join(parts)
                        text = str(content)
                        if role in ("user", "human") or o.get("type") == "user":
                            if not title:
                                title = text[:160].replace("\n", " ")
                            if marker and marker.lower() in text.lower():
                                hit_marker = True
                            if "agent-relay" in text.lower() or "接手任务" in text:
                                hit_marker = True
            except Exception:
                continue
            score = mtime
            if hit_marker:
                score += 1e12
            if score > best_score:
                best_score = score
                best = {
                    "id": f.stem,
                    "title": title or f.stem,
                    "path": str(f),
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                }
    return best


def peer_resume_cmd(peer: str, session_id: str) -> str:
    peer = (peer or "").lower().strip()
    if peer == "kimi":
        peer = "kimi_code"
    if peer == "zcode":
        binary = resolve_binary("zcode") or "zcode"
        if binary.endswith(".cjs"):
            node = shutil.which("node") or "node"
            return f'{node} "{binary}" --resume {session_id}'
        return f"{binary} --resume {session_id}"
    if peer == "claude":
        binary = resolve_binary("claude") or "claude"
        return f"{binary} --resume {session_id}"
    if peer == "grok":
        binary = resolve_binary("grok") or "grok"
        return f"{binary} --resume {session_id}"
    if peer == "kimi_code":
        binary = resolve_binary("kimi_code") or "kimi"
        # official: kimi -r session_...  or kimi -S session_...
        return f"{binary} -r {session_id}"
    if peer in ("mimo", "mimocode", "mimo_code"):
        binary = resolve_binary("mimo") or "mimo"
        return f"{binary} -s {session_id}"
    return f"{peer} --resume {session_id}"


def _preferred_terminal_app() -> str:
    """Prefer Ghostty when installed; else Terminal."""
    env = (os.environ.get("AGENT_RELAY_TERMINAL") or "").strip()
    if env:
        return env
    if Path("/Applications/Ghostty.app").exists():
        return "Ghostty"
    return "Terminal"


def open_peer_visible(peer: str, session_id: str, cwd: Path) -> tuple[bool, str]:
    """Open Ghostty/Terminal resumed on the peer session so the user can see chat."""
    if not session_id:
        return False, "no session_id"
    shell_line = peer_resume_cmd(peer, session_id)
    term = _preferred_terminal_app()
    try:
        log = Path.home() / ".agents" / "relay" / f"visible-{peer}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        script = Path.home() / ".agents" / "relay" / f"open-{peer}-{session_id[-12:]}.sh"
        script.write_text(
            "#!/bin/zsh\n"
            f"export PATH=\"{Path.home()}/.kimi-code/bin:{Path.home()}/.local/bin:/usr/local/bin:$PATH\"\n"
            f"cd {shlex_quote(str(cwd))}\n"
            f"echo 'agent-relay visible {peer} session: {session_id}'\n"
            f"echo 'terminal: {term}'\n"
            f"echo 'Run resume now...'\n"
            f"exec {shell_line}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        with log.open("a", encoding="utf-8") as lf:
            lf.write(f"\n# {time.strftime('%Y-%m-%dT%H:%M:%S')} {peer} resume {session_id} via {term}\n")
            lf.write(f"script: {script}\n")
            if term.lower() == "ghostty":
                # macOS: open -na Ghostty.app --args -e <command>
                r = subprocess.run(
                    [
                        "open",
                        "-na",
                        "Ghostty.app",
                        "--args",
                        "-e",
                        str(script),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                r = subprocess.run(
                    ["open", "-a", term if term.endswith(".app") else term, str(script)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            lf.write(f"open {term} exit={r.returncode} err={r.stderr!r}\n")
            if r.returncode == 0:
                return True, f"opened {term} via {script}"
            # fallback Terminal
            r2 = subprocess.run(
                ["open", "-a", "Terminal", str(script)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r2.returncode == 0:
                return True, f"opened Terminal fallback via {script} (ghostty failed: {r.stderr})"
            return False, f"open failed: {r.stderr or r.stdout}"
    except Exception as e:
        return False, str(e)


# back-compat alias
def zcode_resume_cmd(session_id: str) -> str:
    return peer_resume_cmd("zcode", session_id)


def open_zcode_visible(session_id: str, cwd: Path) -> tuple[bool, str]:
    return open_peer_visible("zcode", session_id, cwd)


def shlex_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)


def zcode_invoke_enabled() -> bool:
    """ZCode headless invoke is OFF by default (no reliable public CLI).

    Opt-in only:
      AGENT_RELAY_ENABLE_ZCODE_INVOKE=1
    or pass force_zcode=True from CLI --force-zcode.
    """
    v = (os.environ.get("AGENT_RELAY_ENABLE_ZCODE_INVOKE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def resolve_binary(peer: str, *, force_zcode: bool = False) -> str | None:
    """Return executable path.

    ZCode: by default only accepts a real `zcode`/`z-code` on PATH.
    App-bundle zcode.cjs is experimental and requires opt-in
    (env AGENT_RELAY_ENABLE_ZCODE_INVOKE or force_zcode).
    """
    peer = (peer or "").lower().strip()
    if peer == "kimi":
        peer = "kimi_code"
    if peer in ("mimocode", "mimo_code", "mimo-code"):
        peer = "mimo"
    mapping = {
        "claude": ["claude"],
        "grok": ["grok"],
        "zcode": ["zcode", "z-code"],
        "codex": ["codex"],
        "kimi_code": ["kimi", "kimi-cli"],
        "mimo": ["mimo", "mimocode"],
    }
    # prefer explicit install locations first for kimi
    if peer == "kimi_code":
        for c in (
            Path.home() / ".kimi-code" / "bin" / "kimi",
            Path.home() / ".local" / "bin" / "kimi-cli",
        ):
            if c.is_file() and os.access(c, os.X_OK):
                return str(c)
    if peer == "mimo":
        for c in (
            Path.home() / ".mimocode" / "bin" / "mimo",
            Path.home() / ".local" / "bin" / "mimo",
        ):
            if c.is_file() and os.access(c, os.X_OK):
                return str(c)
    for name in mapping.get(peer, [peer]):
        p = shutil.which(name)
        if p:
            return p
    # Experimental: app-bundled headless CLI — disabled by default
    if peer == "zcode" and (force_zcode or zcode_invoke_enabled()):
        candidates = [
            Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"),
            Path.home() / "Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def _zcode_cmd_prefix(binary: str) -> list[str]:
    """zcode.cjs needs node; a real zcode bin does not."""
    if binary.endswith(".cjs") or binary.endswith("zcode.cjs"):
        node = shutil.which("node") or "node"
        return [node, binary]
    return [binary]


def _cmd_for_log(cmd: list[str]) -> list[str]:
    """Redact long prompt blobs from logged cmd."""
    out: list[str] = []
    skip_next_prompt = False
    for i, part in enumerate(cmd):
        if skip_next_prompt:
            h = hashlib.sha256(part.encode("utf-8", errors="replace")).hexdigest()[:12]
            out.append(f"<prompt len={len(part)} sha={h}>")
            skip_next_prompt = False
            continue
        if part in ("-p", "--single", "--prompt-file"):
            out.append(part)
            # -p / --single take next arg as prompt; --prompt-file is a path (keep)
            if part in ("-p", "--single"):
                skip_next_prompt = True
            continue
        if len(part) > 200:
            h = hashlib.sha256(part.encode("utf-8", errors="replace")).hexdigest()[:12]
            out.append(f"<blob len={len(part)} sha={h}>")
        else:
            out.append(part)
    return out


def build_handoff_prompt(
    pkt: dict[str, Any],
    packet_dir: Path,
    mode: str,
    extra: str = "",
    *,
    local_handoff: Path | None = None,
    lean: bool = True,
) -> str:
    """Build callee prompt. lean=True (default): short, pointer-heavy, low token tax."""
    handoff_path = packet_dir / "HANDOFF.md"
    if not handoff_path.exists():
        handoff_path.write_text(render_handoff_md(pkt), encoding="utf-8")
    read_handoff = local_handoff if local_handoff and local_handoff.exists() else handoff_path
    primary = (pkt.get("files") or {}).get("primary") or []
    if lean:
        primary = primary[:3]
    primary_block = "\n".join(f"- {p}" for p in primary) or "- (none)"
    mode = (mode or "continue").lower()
    mode_instr = mode_instruction(mode, lean=lean)
    nexts = list(pkt.get("next_actions") or [])[: 3 if lean else 8]
    extra_s = (extra or "").strip()
    if lean and len(extra_s) > 240:
        extra_s = extra_s[:240] + "…"
    vcmd = pkt.get("verify_cmd") or extract_verify_cmd(str(pkt.get("goal") or ""))
    fps = pkt.get("fingerprints") or []
    fp_line = ""
    if fps and lean:
        # only show stale risk for primary fingerprints
        bits = []
        for fp in fps[:3]:
            if isinstance(fp, dict) and fp.get("path"):
                bits.append(f"{Path(fp['path']).name}:{fp.get('sha256','')[:8]}")
        if bits:
            fp_line = "fingerprints: " + ", ".join(bits) + "\n"

    if lean:
        return (
            f"agent-relay 接手 mode={mode}。{mode_instr}\n"
            f"goal: {pkt.get('goal')}\n"
            f"verify_cmd: {vcmd or '(none — still report JSON)'}\n"
            f"HANDOFF: {read_handoff}\n"
            f"packet: {packet_dir / 'packet.json'}\n"
            f"primary:\n{primary_block}\n"
            f"{fp_line}"
            f"next:\n" + "\n".join(f"- {a}" for a in nexts) + "\n"
            f"extra: {extra_s or '(无)'}\n"
            f"结束必须输出一屏 JSON："
            f'{{"done":[],"files":[],"open":[],"verify":"pass|fail|unknown"}}'
        )

    return f"""你正在通过 agent-relay 从另一个 Agent 接手任务（mode={mode}）。

## 强制纪律
1. 先 Read 接力文件与 primary 文件，再动手。
2. 文件与 git 是真源；HANDOFF 是导航。
3. 不要向用户复述背景；直接推进。
4. {mode_instr}
5. 结束时用简短条目报告：做了什么 / 改了哪些路径 / 未决。

## 接力包
- packet_id: {pkt.get('id')}
- goal: {pkt.get('goal')}
- from → to: {(pkt.get('routing') or {}).get('from_peer')} → {(pkt.get('routing') or {}).get('to_peer')}
- HANDOFF 路径: {read_handoff}
- 全局 HANDOFF: {handoff_path}
- packet.json: {packet_dir / 'packet.json'}

## Primary 文件
{primary_block}

## Next actions
{chr(10).join(f'- {a}' for a in nexts)}

## 额外指令
{extra_s or '(无)'}

请立即开始：先 Read `{read_handoff}`，再执行。
"""


def _mirror_handoff_into_cwd(cwd: Path, packet_dir: Path) -> Path:
    """Copy HANDOFF into project .relay so sandboxed peers can read it."""
    src = packet_dir / "HANDOFF.md"
    dest_dir = cwd / ".relay"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "HANDOFF.md"
    if src.exists():
        shutil.copy2(src, dest)
    return dest


def append_invoke_history(packet_dir: Path, result: InvokeResult) -> None:
    hist = packet_dir / "invoke" / "history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    row = result.to_dict()
    row["cmd"] = _cmd_for_log(result.cmd)
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with hist.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def mark_packet_delegated(packet_dir: Path, pkt: dict[str, Any], result: InvokeResult) -> None:
    """Update packet status after invoke so resume sees progress."""
    try:
        pkt = dict(pkt)
        if result.ok:
            pkt["status"] = "delegated"
            done = list(pkt.get("done") or [])
            done.append(
                f"invoke {result.peer} ok ({result.duration_sec:.1f}s) → {result.stdout_path}"
            )
            pkt["done"] = done[-12:]
        else:
            open_items = list(pkt.get("open") or [])
            open_items.append(
                f"invoke {result.peer} failed exit={result.exit_code}: {(result.error or '')[:200]}"
            )
            pkt["open"] = open_items[-12:]
        save_packet(packet_dir, pkt)
    except Exception:
        pass


def _empty_skills_dir() -> Path:
    d = Path.home() / ".agents" / "relay" / "_empty_skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_async_job(job_path: str | Path) -> int:
    """Background worker: run saved job cmd, finalize history/session/visible."""
    job_path = Path(job_path)
    job = json.loads(job_path.read_text(encoding="utf-8"))
    peer = job["peer"]
    cmd = job["cmd"]
    cwd = Path(job["cwd"])
    packet_dir = Path(job["packet_dir"])
    stdout_path = Path(job["stdout"])
    stderr_path = Path(job["stderr"])
    timeout = int(job.get("timeout") or 600)
    started_unix = float(job.get("started_unix") or time.time())
    marker = job.get("marker") or "agent-relay"
    visible = bool(job.get("visible"))
    t0 = time.time()
    job["status"] = "running"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "AGENT_RELAY_PACKET": str(packet_dir)},
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")
        duration = time.time() - t0
        summary = (proc.stdout or "").strip()
        if len(summary) > 2000:
            summary = summary[:2000] + "\n…(truncated)"
        session_id, resume_cmd = "", ""
        if proc.returncode == 0:
            session_id, resume_cmd, summary = _surface_session(
                peer=peer,
                cwd=cwd,
                packet_dir=packet_dir,
                started_unix=started_unix,
                marker=marker,
                stdout=proc.stdout or "",
                summary=summary,
                visible=visible,
            )
        # one-screen result + local verify_cmd
        try:
            pkt = load_packet(packet_dir / "packet.json")
        except Exception:
            pkt = {}
        parsed = parse_one_screen_result(proc.stdout or "")
        vcmd = (pkt or {}).get("verify_cmd") or extract_verify_cmd(str((pkt or {}).get("goal") or ""))
        verify = run_verify(vcmd, cwd=cwd)
        result_path = write_result_json(
            packet_dir,
            peer=peer,
            parsed=parsed,
            verify=verify,
            duration_sec=duration,
            session_id=session_id,
            exit_code=proc.returncode,
        )
        summary = (summary or "") + f"\n[RESULT] {result_path} verify={verify.get('result')}"
        result = InvokeResult(
            peer=peer,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            cmd=cmd,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            duration_sec=duration,
            summary=summary or "(empty stdout)",
            error="" if proc.returncode == 0 else (proc.stderr or "")[:500],
            session_id=session_id,
            resume_cmd=resume_cmd,
        )
        meta = {
            "peer": peer,
            "cmd": _cmd_for_log(cmd),
            "exit_code": proc.returncode,
            "duration_sec": duration,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "async": True,
            "job": str(job_path),
            "session_id": session_id,
            "result_json": str(result_path),
            "verify": verify,
        }
        meta_path = Path(job.get("meta") or (stdout_path.with_suffix(".meta.json")))
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        append_invoke_history(packet_dir, result)
        try:
            if not pkt:
                pkt = load_packet(packet_dir / "packet.json")
            # attach verify into packet.verification
            pkt = dict(pkt)
            pkt["verification"] = [verify]
            if verify.get("result") == "pass":
                done = list(pkt.get("done") or [])
                done.append(f"verify_cmd pass: {verify.get('cmd')}")
                pkt["done"] = done[-12:]
            mark_packet_delegated(packet_dir, pkt, result)
        except Exception:
            pass
        job["status"] = "done" if result.ok else "failed"
        job["result"] = result.to_dict()
        job["result"]["cmd"] = _cmd_for_log(cmd)
        job["verify"] = verify
        job["result_json"] = str(result_path)
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        done_flag = packet_dir / "invoke" / "LAST_JOB.json"
        done_flag.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # overall success: process ok AND (no verify or verify pass/skipped)
        ok = result.ok and verify.get("result") in ("pass", "skipped", None)
        if verify.get("result") == "fail":
            return 2
        return 0 if ok else (result.exit_code or 1)
    except subprocess.TimeoutExpired:
        job["status"] = "timeout"
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stderr_path.write_text(f"timeout after {timeout}s\n", encoding="utf-8")
        return 124
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1


def _surface_session(
    *,
    peer: str,
    cwd: Path,
    packet_dir: Path,
    started_unix: float,
    marker: str,
    stdout: str,
    summary: str,
    visible: bool,
) -> tuple[str, str, str]:
    """Return (session_id, resume_cmd, summary_with_hints)."""
    if peer == "kimi":
        peer = "kimi_code"
    time.sleep(0.35)
    hit = None
    if peer == "zcode":
        hit = find_zcode_session_after(started_after_unix=started_unix - 5, marker=marker or "agent-relay")
    elif peer == "claude":
        hit = find_claude_session_after(
            cwd=cwd, started_after_unix=started_unix - 5, marker=marker or "agent-relay"
        )
    elif peer == "kimi_code":
        sid = parse_kimi_session_from_stdout(stdout)
        if sid:
            hit = {
                "id": sid,
                "title": "kimi-code headless",
                "path": "",
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        else:
            hit = find_kimi_session_after(
                started_after_unix=started_unix - 5, marker=marker or "agent-relay"
            )
    elif peer == "mimo":
        sid = parse_mimo_session_from_text(stdout)
        if sid:
            hit = {
                "id": sid,
                "title": "mimocode headless",
                "path": "",
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        else:
            hit = find_mimo_session_after(
                started_after_unix=started_unix - 5, marker=marker or "agent-relay"
            )
    if not hit:
        return "", "", summary
    session_id = hit["id"]
    resume_cmd = peer_resume_cmd(peer, session_id)
    term = _preferred_terminal_app()
    vis = packet_dir / "invoke" / "VISIBLE_SESSION.md"
    vis.write_text(
        f"# {peer} 可见会话\n\n"
        f"- peer: `{peer}`\n"
        f"- session_id: `{session_id}`\n"
        f"- title: {hit.get('title', '')}\n"
        f"- path: {hit.get('path', '')}\n"
        f"- created: {hit.get('created', '')}\n"
        f"- open_with: `{term}`\n\n"
        f"## 打开\n\n```bash\ncd {cwd}\n{resume_cmd}\n```\n",
        encoding="utf-8",
    )
    if visible:
        ok_open, msg = open_peer_visible(peer, session_id, cwd)
        summary = (summary or "") + f"\n\n[VISIBLE] session={session_id}\n{msg}\n{resume_cmd}"
    else:
        summary = (summary or "") + f"\n\n[SESSION] {session_id}\n[RESUME] {resume_cmd}"
    return session_id, resume_cmd, summary


def invoke_peer(
    peer: str,
    pkt: dict[str, Any],
    packet_dir: Path,
    *,
    cwd: Path,
    mode: str = "continue",
    extra: str = "",
    timeout: int = 600,
    max_turns: int | None = 8,
    dry_run: bool = False,
    visible: bool = False,
    marker: str = "agent-relay",
    force_zcode: bool = False,
    wait: bool = False,
    lean: bool = True,
) -> InvokeResult:
    t0 = time.time()
    started_unix = time.time()
    cmd: list[str] = []
    stdout_path = Path("")
    stderr_path = Path("")
    session_id = ""
    resume_cmd = ""
    try:
        peer = peer.lower().strip()
        if peer == "zcode" and not (force_zcode or zcode_invoke_enabled()):
            return InvokeResult(
                peer=peer,
                ok=False,
                exit_code=78,  # EX_CONFIG
                cmd=[],
                stdout_path="",
                stderr_path="",
                duration_sec=time.time() - t0,
                summary="",
                error=(
                    "zcode invoke 默认关闭（无稳定 PATH CLI，headless 体验难验证）。"
                    "仍可用 pack/resume 交接。"
                    "若坚持试验：export AGENT_RELAY_ENABLE_ZCODE_INVOKE=1 或 --force-zcode"
                ),
                session_id="",
                resume_cmd="",
            )
        binary = resolve_binary(peer, force_zcode=force_zcode)
        if not binary:
            return InvokeResult(
                peer=peer,
                ok=False,
                exit_code=127,
                cmd=[],
                stdout_path="",
                stderr_path="",
                duration_sec=time.time() - t0,
                summary="",
                error=f"binary not found for peer={peer}; pack/resume still works",
            )

        local_handoff = _mirror_handoff_into_cwd(cwd, packet_dir)
        prompt = build_handoff_prompt(
            pkt, packet_dir, mode, extra, local_handoff=local_handoff, lean=lean
        )
        out_dir = packet_dir / "invoke"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        prompt_file = out_dir / f"{peer}-{ts}.prompt.md"
        stdout_path = out_dir / f"{peer}-{ts}.stdout.txt"
        stderr_path = out_dir / f"{peer}-{ts}.stderr.txt"
        meta_path = out_dir / f"{peer}-{ts}.meta.json"
        prompt_file.write_text(prompt, encoding="utf-8")

        # lean: only cwd + packet + handoff-related dirs (avoid dumping entire ~/.agents skill forest)
        add_dirs: list[str] = []
        lean_dirs = [cwd, packet_dir]
        if not lean:
            lean_dirs.extend(
                [
                    Path.home() / ".agents" / "skills" / "agent-relay",
                    Path.home() / ".agents" / "relay",
                    Path.home() / ".agents",
                ]
            )
        else:
            lean_dirs.append(Path.home() / ".agents" / "skills" / "agent-relay")
        for p in lean_dirs:
            try:
                rp = p.resolve()
                if rp.exists() and str(rp) not in add_dirs:
                    add_dirs.append(str(rp))
            except Exception:
                pass

        empty_skills = _empty_skills_dir()
        if peer == "claude":
            display_name = f"agent-relay-{pkt.get('id', 'session')}"
            turns = int(max_turns or 8)
            if lean:
                turns = min(turns, 8)
            cmd = [
                binary,
                "-p",
                prompt,
                "--permission-mode",
                "bypassPermissions",
                "-n",
                display_name,
                "--max-turns",
                str(turns),
            ]
            if lean:
                # skip hooks/plugins tax; no slash skill catalog
                cmd.append("--bare")
                cmd.append("--disable-slash-commands")
            for d in add_dirs:
                cmd.extend(["--add-dir", d])
        elif peer == "grok":
            turns = int(max_turns or 8)
            if lean:
                turns = min(turns, 8)
            cmd = [
                binary,
                "--cwd",
                str(cwd),
                "--always-approve",
                "--permission-mode",
                "bypassPermissions",
                "--prompt-file",
                str(prompt_file),
                "--max-turns",
                str(turns),
            ]
            if lean:
                cmd.append("--minimal")
        elif peer == "zcode":
            cmd = [
                *_zcode_cmd_prefix(binary),
                "--cwd",
                str(cwd),
                "--mode",
                "yolo",
                "-p",
                prompt,
            ]
            handoff = packet_dir / "HANDOFF.md"
            if handoff.exists():
                cmd.extend(["--attach", str(handoff)])
            if local_handoff.exists():
                cmd.extend(["--attach", str(local_handoff)])
        elif peer in ("kimi_code", "kimi"):
            peer = "kimi_code"
            cmd = [binary, "-p", prompt]
            # lean: only empty skills-dir + essential add-dir (not whole ~/.agents)
            if lean:
                cmd.extend(["--skills-dir", str(empty_skills)])
            for d in add_dirs:
                cmd.extend(["--add-dir", d])
        elif peer in ("mimo", "mimocode", "mimo_code", "mimo-code"):
            peer = "mimo"
            # mimocode: message MUST come before -f/--file (array would swallow it).
            # Prefer prompt-file content as the sole positional; attach HANDOFF via -f after.
            model = mimo_default_model()
            title = f"agent-relay-{pkt.get('id', 'session')}"
            if marker and "agent-relay" not in (marker or ""):
                title = f"{marker}-{pkt.get('id', 'session')}"[:80]
            # short message points at prompt_file so argv stays small; full text still on disk
            short_msg = (
                f"Read and execute the full handoff prompt at {prompt_file}. "
                f"Also read any attached HANDOFF.md. Do the goal with VERIFY. "
                f"End with one-screen JSON done/files/open/verify."
            )
            cmd = [
                binary,
                "run",
                short_msg,
                "--dir",
                str(cwd),
                "--dangerously-skip-permissions",
                "--title",
                title,
                "-m",
                model,
                "-f",
                str(prompt_file),
            ]
            handoff = packet_dir / "HANDOFF.md"
            if handoff.exists():
                cmd.extend(["-f", str(handoff)])
            if local_handoff.exists() and local_handoff.resolve() != handoff.resolve():
                cmd.extend(["-f", str(local_handoff)])
        else:
            cmd = [binary, "-p", prompt]

        if dry_run:
            return InvokeResult(
                peer=peer,
                ok=True,
                exit_code=0,
                cmd=cmd,
                stdout_path="",
                stderr_path="",
                duration_sec=time.time() - t0,
                summary="DRY_RUN: " + " ".join(_cmd_for_log(cmd)),
                error="",
            )

        # ---- async default: spawn job worker, return immediately ----
        if not wait:
            job_id = f"job-{ts}"
            job_path = out_dir / f"{job_id}.json"
            job = {
                "job_id": job_id,
                "status": "queued",
                "peer": peer,
                "cmd": cmd,
                "cwd": str(cwd),
                "packet_dir": str(packet_dir),
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "meta": str(meta_path),
                "prompt_file": str(prompt_file),
                "started_unix": started_unix,
                "marker": marker,
                "visible": visible,
                "timeout": timeout,
                "mode": mode,
                "lean": lean,
            }
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (out_dir / "LAST_JOB.json").write_text(
                json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            # worker
            worker = [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, %r); "
                    "from core.invoke import run_async_job; "
                    "raise SystemExit(run_async_job(%r))"
                )
                % (str(Path(__file__).resolve().parent.parent), str(job_path)),
            ]
            logf = out_dir / f"{job_id}.worker.log"
            lf = open(logf, "w", encoding="utf-8")
            proc = subprocess.Popen(
                worker,
                cwd=str(cwd),
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "AGENT_RELAY_PACKET": str(packet_dir)},
            )
            job["status"] = "running"
            job["pid"] = proc.pid
            job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (out_dir / "LAST_JOB.json").write_text(
                json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            # mark packet as delegated (async start)
            try:
                pkt2 = dict(pkt)
                pkt2["status"] = "delegated"
                done = list(pkt2.get("done") or [])
                done.append(f"async invoke {peer} started job={job_id} pid={proc.pid}")
                pkt2["done"] = done[-12:]
                save_packet(packet_dir, pkt2)
            except Exception:
                pass
            return InvokeResult(
                peer=peer,
                ok=True,
                exit_code=0,
                cmd=cmd,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                duration_sec=time.time() - t0,
                summary=(
                    f"ASYNC_STARTED job={job_id} pid={proc.pid}\n"
                    f"job_file={job_path}\n"
                    f"poll: python3 .../relay_cli.py job-status\n"
                    f"worker_log={logf}"
                ),
                error="",
                session_id="",
                resume_cmd="",
            )

        # ---- sync wait path ----
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "AGENT_RELAY_PACKET": str(packet_dir)},
        )
        duration = time.time() - t0
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")
        summary = (proc.stdout or "").strip()
        if len(summary) > 2000:
            summary = summary[:2000] + "\n…(truncated)"
        meta = {
            "peer": peer,
            "cmd": _cmd_for_log(cmd),
            "exit_code": proc.returncode,
            "duration_sec": duration,
            "prompt_file": str(prompt_file),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "lean": lean,
            "async": False,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if peer in ("zcode", "claude", "grok", "kimi_code", "kimi", "mimo") and proc.returncode == 0:
            if peer == "kimi":
                peer = "kimi_code"
            if peer in ("mimocode", "mimo_code", "mimo-code"):
                peer = "mimo"
            session_id, resume_cmd, summary = _surface_session(
                peer=peer,
                cwd=cwd,
                packet_dir=packet_dir,
                started_unix=started_unix,
                marker=marker or "agent-relay",
                stdout=proc.stdout or "",
                summary=summary,
                visible=visible,
            )

        parsed = parse_one_screen_result(proc.stdout or "")
        vcmd = pkt.get("verify_cmd") or extract_verify_cmd(str(pkt.get("goal") or ""))
        verify = run_verify(vcmd, cwd=cwd)
        result_path = write_result_json(
            packet_dir,
            peer=peer,
            parsed=parsed,
            verify=verify,
            duration_sec=duration,
            session_id=session_id,
            exit_code=proc.returncode,
        )
        summary = (summary or "") + f"\n[RESULT] {result_path} verify={verify.get('result')}"
        meta["verify"] = verify
        meta["result_json"] = str(result_path)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = InvokeResult(
            peer=peer,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            cmd=cmd,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            duration_sec=duration,
            summary=summary or "(empty stdout)",
            error="" if proc.returncode == 0 else (proc.stderr or "")[:500],
            session_id=session_id,
            resume_cmd=resume_cmd,
        )
        append_invoke_history(packet_dir, result)
        try:
            pkt2 = dict(pkt)
            pkt2["verification"] = [verify]
            mark_packet_delegated(packet_dir, pkt2, result)
        except Exception:
            mark_packet_delegated(packet_dir, pkt, result)
        return result
    except subprocess.TimeoutExpired as e:
        duration = time.time() - t0
        out_s = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
        try:
            if stdout_path:
                Path(stdout_path).write_text(out_s or "", encoding="utf-8")
            if stderr_path:
                Path(stderr_path).write_text(f"timeout after {timeout}s\n", encoding="utf-8")
        except Exception:
            pass
        result = InvokeResult(
            peer=peer,
            ok=False,
            exit_code=124,
            cmd=cmd,
            stdout_path=str(stdout_path) if stdout_path else "",
            stderr_path=str(stderr_path) if stderr_path else "",
            duration_sec=duration,
            summary="",
            error=f"timeout after {timeout}s",
        )
        try:
            append_invoke_history(packet_dir, result)
            mark_packet_delegated(packet_dir, pkt, result)
        except Exception:
            pass
        return result
    except Exception as e:
        result = InvokeResult(
            peer=peer if "peer" in dir() else "unknown",
            ok=False,
            exit_code=1,
            cmd=cmd,
            stdout_path=str(stdout_path) if stdout_path else "",
            stderr_path=str(stderr_path) if stderr_path else "",
            duration_sec=time.time() - t0,
            summary="",
            error=str(e),
        )
        try:
            append_invoke_history(packet_dir, result)
        except Exception:
            pass
        return result


def load_packet_dir(path: Path) -> tuple[dict[str, Any], Path]:
    if path.is_file() and path.name == "packet.json":
        return load_packet(path), path.parent
    if (path / "packet.json").exists():
        return load_packet(path / "packet.json"), path
    raise FileNotFoundError(f"no packet.json under {path}")
