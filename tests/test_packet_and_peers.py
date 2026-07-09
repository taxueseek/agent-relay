#!/usr/bin/env python3
"""Unit tests for agent-relay core (no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.packet import empty_packet, render_handoff_md, save_packet, validate_packet  # noqa: E402
from core.peers import PRIMARY_PEERS, probe_all, suggest_to_peer  # noqa: E402
from core.synthesize import synthesize  # noqa: E402
from core.goal_lint import lint_goal  # noqa: E402
from core.plan import plan_action  # noqa: E402
from core.patterns import suggest_patterns  # noqa: E402
from core.job_protocol import inspect_packet_job  # noqa: E402


class TestPacket(unittest.TestCase):
    def test_empty_valid(self):
        pkt = empty_packet(goal="完成 agent-relay pack", from_peer="grok", to_peer="zcode")
        self.assertEqual(validate_packet(pkt), [])

    def test_missing_schema(self):
        pkt = empty_packet(goal="x", from_peer="a", to_peer="b")
        del pkt["schema"]
        self.assertTrue(any("schema" in e for e in validate_packet(pkt)))

    def test_save_and_render(self):
        pkt = empty_packet(goal="测试保存", from_peer="claude", to_peer="grok")
        pkt["next_actions"] = ["读文件", "改代码"]
        pkt["files"]["primary"] = ["/tmp/example.py"]
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "p1"
            save_packet(d, pkt, sources={"t": 1})
            self.assertTrue((d / "packet.json").exists())
            self.assertTrue((d / "HANDOFF.md").exists())
            data = json.loads((d / "packet.json").read_text())
            self.assertEqual(data["goal"], "测试保存")
            md = render_handoff_md(pkt)
            self.assertIn("下一步", md)
            self.assertIn("example.py", md)


class TestPeers(unittest.TestCase):
    def test_primary_defined(self):
        self.assertIn("claude", PRIMARY_PEERS)
        self.assertIn("grok", PRIMARY_PEERS)
        # zcode 降级为非 primary（无 CLI invoke），但仍应出现在 KNOWN_PEERS
        self.assertNotIn("zcode", PRIMARY_PEERS)
        self.assertIn("kimi_code", PRIMARY_PEERS)

    def test_probe_runs(self):
        rows = probe_all()
        self.assertTrue(len(rows) >= 3)
        ids = {r.id for r in rows}
        for p in PRIMARY_PEERS:
            self.assertIn(p, ids)

    def test_suggest(self):
        r = suggest_to_peer("实现这个 bug 修复", from_peer="claude")
        self.assertIn("recommended_peer", r)


class TestGoalLintAndPlan(unittest.TestCase):
    def test_lint_ready_with_verify(self):
        c = lint_goal(
            "写文件 /tmp/agent-relay-proof.txt 内容 TOKEN=abc "
            "VERIFY: test -f /tmp/agent-relay-proof.txt && rg -q TOKEN=abc /tmp/agent-relay-proof.txt"
        )
        self.assertEqual(c["grade"], "ready")
        self.assertTrue(c["verify_cmd"])
        self.assertIn(c["sandbox"], ("workspace-write", "read-only"))

    def test_lint_vague(self):
        c = lint_goal("帮我优化一下")
        self.assertIn(c["grade"], ("needs_work", "blocked"))
        self.assertTrue(c["warnings"])

    def test_plan_returns_route(self):
        p = plan_action(task="审查 peers.py 安全问题", action="delegate", to_peer="claude")
        self.assertEqual(p["schema"], "agent-relay/plan/v1")
        self.assertEqual(p["route"]["to_peer"], "claude")
        self.assertIn("contract", p)
        self.assertTrue(p.get("next_commands"))

    def test_patterns_quota(self):
        cards = suggest_patterns("额度快没了要交接", limit=2)
        ids = {c["id"] for c in cards}
        self.assertIn("quota_handoff", ids)

    def test_job_digest_idle(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pkt = empty_packet(goal="x", from_peer="a", to_peer="b")
            save_packet(d, pkt)
            dig = inspect_packet_job(d)
            self.assertEqual(dig["state"], "idle")
            self.assertEqual(dig["schema"], "agent-relay/job/v1")


class TestSynthesize(unittest.TestCase):
    def test_from_evidence(self):
        ev = {
            "session_path": "/tmp/fake.jsonl",
            "peer": "grok",
            "files": ["/Users/x/a.py", "/Users/x/b.py"],
            "messages": "[USER] 2026-01-01\n  请实现 agent-relay pack 功能\n",
            "knowledge": [{"category": "decision", "content": "用 packet 而非全文 transcript"}],
            "workspace": {"cwd": "/tmp", "git_head": "abc", "dirty": False, "dirty_files": []},
        }
        pkt = synthesize(ev, from_peer="grok", to_peer="zcode", budget="short")
        self.assertEqual(validate_packet(pkt), [])
        self.assertIn("agent-relay", pkt["goal"] or "agent-relay")
        self.assertTrue(pkt["files"]["primary"])
        self.assertEqual(pkt["routing"]["to_peer"], "zcode")

    def test_noise_user_not_in_next(self):
        ev = {
            "session_path": "/tmp/fake.jsonl",
            "peer": "claude",
            "files": [],
            "messages": "[USER]\n  <command-message>doctor</command-message>\n",
            "knowledge": [],
            "workspace": {"cwd": "/tmp", "git_head": "", "dirty": False, "dirty_files": []},
        }
        pkt = synthesize(ev, goal="明确目标：修 invoke", from_peer="claude", to_peer="grok")
        joined = " ".join(pkt["next_actions"])
        self.assertNotIn("command-message", joined)
        self.assertIn("明确目标", joined)

    def test_short_budget_caps(self):
        files = [f"/tmp/f{i}.py" for i in range(10)]
        ev = {
            "session_path": "/tmp/fake.jsonl",
            "peer": "grok",
            "files": files,
            "messages": "[USER]\n  do stuff\n",
            "knowledge": [{"category": "decision", "content": f"d{i}"} for i in range(10)],
            "workspace": {"cwd": "/tmp", "git_head": "", "dirty": False, "dirty_files": []},
        }
        pkt = synthesize(
            ev,
            goal="short budget goal VERIFY: test -f /tmp/x",
            from_peer="grok",
            to_peer="claude",
            budget="short",
        )
        self.assertLessEqual(len(pkt["files"]["primary"]), 3)
        self.assertLessEqual(len(pkt["files"]["touched"]), 5)
        self.assertLessEqual(len(pkt["next_actions"]), 3)
        self.assertLessEqual(len(pkt["decisions"]), 3)
        self.assertTrue(pkt.get("verify_cmd"))

    def test_result_protocol_verify(self):
        from core.result_protocol import extract_verify_cmd, parse_one_screen_result, complexity_route

        g = "写 /tmp/a.md 两行 TOKEN=ABC VERIFY: test -f /tmp/a.md && rg -q ABC /tmp/a.md"
        self.assertIn("test -f", extract_verify_cmd(g))
        parsed = parse_one_screen_result('```json\n{"done":["x"],"files":["/a"],"open":[],"verify":"pass"}\n```')
        self.assertEqual(parsed["done"], ["x"])
        # trivial should prefer kimi if present
        class P:
            def __init__(self, on=True):
                self.present = on

        r = complexity_route("只写两行 token proof 验收", {"kimi_code": P(True), "claude": P(True)})
        self.assertEqual(r["complexity"], "trivial")
        self.assertEqual(r["recommended_peer"], "kimi_code")


class TestPathsSessionDiscovery(unittest.TestCase):
    def test_grok_project_keys_stable(self):
        from core.paths import grok_project_keys
        from urllib.parse import unquote

        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "MyProject"
            proj.mkdir()
            keys = grok_project_keys(proj)
            self.assertTrue(keys)
            decoded = unquote(keys[0])
            self.assertIn("MyProject", decoded)

    def test_native_scoped_without_digger(self):
        """Full multi-env layout under fake HOME; digger forced absent."""
        from core.session_discover import find_sessions, resolve_latest
        from core.paths import find_latest_session_info, grok_project_keys, claude_project_keys
        from urllib.parse import quote
        from unittest import mock
        import time as _t

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            project = Path(td) / "work" / "app"
            other = Path(td) / "work" / "other"
            project.mkdir(parents=True)
            other.mkdir(parents=True)

            # Grok layout
            gkey = quote(str(project.resolve()), safe="")
            gkey_o = quote(str(other.resolve()), safe="")
            g_target = home / ".grok" / "sessions" / gkey / "sess-a" / "chat_history.jsonl"
            g_noise = home / ".grok" / "sessions" / gkey_o / "sess-b" / "chat_history.jsonl"
            g_target.parent.mkdir(parents=True)
            g_noise.parent.mkdir(parents=True)
            g_target.write_text('{"g":1}\n', encoding="utf-8")
            g_noise.write_text('{"g":2}\n', encoding="utf-8")
            _t.sleep(0.02)
            g_noise.write_text('{"g":3}\n', encoding="utf-8")  # newer but other project

            # Claude layout
            ckey = claude_project_keys(project)[0]
            c_target = home / ".claude" / "projects" / ckey / "abc-uuid.jsonl"
            c_target.parent.mkdir(parents=True)
            c_target.write_text('{"c":1}\n', encoding="utf-8")

            # Kimi layout (project name in dir)
            k_target = (
                home
                / ".kimi-code"
                / "sessions"
                / f"wd_{project.name}_hash"
                / "session_k1"
                / "agents"
                / "main"
                / "wire.jsonl"
            )
            k_target.parent.mkdir(parents=True)
            k_target.write_text('{"k":1}\n', encoding="utf-8")

            with mock.patch("core.session_discover._home", return_value=home), mock.patch(
                "core.paths.discover_sd_root", return_value=None
            ):
                rows_g = find_sessions(
                    agent="grok", scope="current", limit=5, project=project, home=home
                )
                rows_c = find_sessions(
                    agent="claude", scope="current", limit=5, project=project, home=home
                )
                rows_k = find_sessions(
                    agent="kimi_code", scope="current", limit=5, project=project, home=home
                )
                rows_x = find_sessions(
                    agent="cross", scope="current", limit=10, project=project, home=home
                )
                info = resolve_latest("grok", project=project, home=home)
                # find_latest uses native which uses Path.home internally via _home
                info2 = find_latest_session_info(
                    "claude", project=project, allow_global_fallback=False
                )

            self.assertEqual(len(rows_g), 1)
            self.assertTrue(rows_g[0]["path"].endswith("chat_history.jsonl"))
            self.assertEqual(rows_g[0]["source"], "native")
            self.assertEqual(len(rows_c), 1)
            self.assertEqual(rows_c[0]["agent"], "claude")
            self.assertGreaterEqual(len(rows_k), 1)
            agents = {r["agent"] for r in rows_x}
            self.assertIn("grok", agents)
            self.assertIn("claude", agents)
            self.assertIsNotNone(info)
            self.assertEqual(info["source"], "native")
            # find_latest_session_info uses real home for native_resolve which calls _home
            # patch session_discover path through find_latest - may miss if not patched on resolve
            # re-run find_latest with patched _home
            with mock.patch("core.session_discover._home", return_value=home), mock.patch(
                "core.paths.discover_sd_root", return_value=None
            ):
                info3 = find_latest_session_info(
                    "grok", project=project, allow_global_fallback=False
                )
            self.assertIsNotNone(info3)
            self.assertEqual(info3["source"], "native")
            self.assertEqual(info3["path"].stat().st_ino, g_target.resolve().stat().st_ino)

    def test_find_explicit_and_env(self):
        from core.paths import find_latest_session
        import os

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "chat_history.jsonl"
            f.write_text("x\n", encoding="utf-8")
            self.assertEqual(
                find_latest_session("grok", explicit=f).resolve(), f.resolve()
            )
            old = os.environ.get("AGENT_RELAY_EVAL_SESSION")
            try:
                os.environ["AGENT_RELAY_EVAL_SESSION"] = str(f)
                self.assertEqual(
                    find_latest_session("grok", project=Path(td)).resolve(), f.resolve()
                )
            finally:
                if old is None:
                    os.environ.pop("AGENT_RELAY_EVAL_SESSION", None)
                else:
                    os.environ["AGENT_RELAY_EVAL_SESSION"] = old

    def test_portable_verify_cmd(self):
        from core.paths import portable_file_token_verify
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "proof.md"
            p.write_text("hello TOKEN123 wave\n", encoding="utf-8")
            cmd = portable_file_token_verify(p, "TOKEN123")
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            cmd_bad = portable_file_token_verify(p, "MISSING")
            r2 = subprocess.run(cmd_bad, shell=True, capture_output=True, text=True)
            self.assertNotEqual(r2.returncode, 0)


class TestEnvMapWorkspace(unittest.TestCase):
    """Environment ↔ workspace mapping without session-digger."""

    def test_two_projects_map_to_different_env_dirs(self):
        from core.env_map import map_workspace, project_keys_for_env, scan_environments
        from urllib.parse import quote

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            app_a = Path(td) / "ws" / "AppA"
            app_b = Path(td) / "ws" / "AppB"
            app_a.mkdir(parents=True)
            app_b.mkdir(parents=True)

            # plant Claude + Grok layouts for both projects
            for proj in (app_a, app_b):
                gkey = quote(str(proj.resolve()), safe="")
                ckey = project_keys_for_env("claude", proj)[0]
                g = home / ".grok" / "sessions" / gkey / "s1" / "chat_history.jsonl"
                c = home / ".claude" / "projects" / ckey / "sess.jsonl"
                g.parent.mkdir(parents=True)
                c.parent.mkdir(parents=True)
                g.write_text("{}\n", encoding="utf-8")
                c.write_text("{}\n", encoding="utf-8")

            scan = scan_environments(home=home)
            present = {e.env_id for e in scan if e.present}
            self.assertIn("claude", present)
            self.assertIn("grok", present)

            ma = map_workspace(app_a, home=home)
            mb = map_workspace(app_b, home=home)
            self.assertGreaterEqual(ma["bound_count"], 2)
            self.assertGreaterEqual(mb["bound_count"], 2)
            # dirs must differ between projects
            dirs_a = {
                e["env_id"]: e["project_dirs"]
                for e in ma["environments"]
                if e["bound"]
            }
            dirs_b = {
                e["env_id"]: e["project_dirs"]
                for e in mb["environments"]
                if e["bound"]
            }
            self.assertNotEqual(dirs_a.get("grok"), dirs_b.get("grok"))
            self.assertNotEqual(dirs_a.get("claude"), dirs_b.get("claude"))

    def test_live_workspace_and_cli(self):
        from core.env_map import map_workspace
        import subprocess

        proj = Path.home() / "Documents" / "GPT"
        if not proj.is_dir():
            self.skipTest("no GPT workspace")
        rep = map_workspace(proj)
        self.assertEqual(rep["schema"], "agent-relay/workspace-map/v1")
        self.assertIn("environments", rep)
        # at least one bound env on this machine for GPT
        self.assertGreaterEqual(rep["bound_count"], 1)
        cli = Path(__file__).resolve().parents[1] / "scripts" / "relay_cli.py"
        p = subprocess.run(
            [sys.executable, str(cli), "workspace", "--project", str(proj), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertGreaterEqual(data.get("bound_count", 0), 1)

    def test_native_list_and_resolve_no_digger_required(self):
        from core.evidence import list_sessions, resolve_session
        from core.paths import find_latest_session_info
        from unittest import mock

        proj = Path.home() / "Documents" / "GPT"
        if not proj.is_dir():
            proj = Path.cwd()

        with mock.patch("core.evidence.discover_sd_root", return_value=None), mock.patch(
            "core.paths.discover_sd_root", return_value=None
        ):
            rows = list_sessions("cross", limit=5, scope="current", cwd=proj, prefer_native=True)
            self.assertIsInstance(rows, list)
            for r in rows:
                self.assertEqual(r.get("source"), "native")
                self.assertTrue(Path(r["path"]).is_file(), r["path"])
            info = find_latest_session_info(
                "grok", project=proj, allow_global_fallback=True, prefer_digger=False
            )
            if info:
                self.assertEqual(info["source"], "native")
                self.assertTrue(Path(info["path"]).is_file())
            if rows:
                resolved = resolve_session(peer="auto", cwd=proj)
                self.assertTrue(Path(resolved["path"]).is_file())
                self.assertIn(resolved.get("source"), ("native", "explicit"))


if __name__ == "__main__":
    unittest.main()
