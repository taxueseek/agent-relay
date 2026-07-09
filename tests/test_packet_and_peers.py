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


if __name__ == "__main__":
    unittest.main()
