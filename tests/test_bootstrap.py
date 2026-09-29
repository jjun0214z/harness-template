"""bootstrap 끝까지(비대화형 · --offline · 가짜 도구) × 엔진 3 × Orca 2, 두 번 실행 멱등, 금지어 검사."""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from helpers import FORBIDDEN, Sandbox, tree_snapshot

COMBOS = [(["claude"], False), (["claude"], True), (["codex"], False), (["codex"], True),
          (["claude", "codex"], False), (["claude", "codex"], True)]


class EndToEnd(unittest.TestCase):
    def run_combo(self, engines, orca):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        sb.fake_tools()
        cfg_path = sb.config(engines, orca)
        target = sb.projects / "orchestrator"
        args = ["run", "--config", str(cfg_path), "--target", str(target), "--offline", "--non-interactive"]

        first = sb.bootstrap(*args, FAKE_SLUG="demo")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("| 훅 push 가드 | 통과 |", first.stdout)
        self.assertIn("| 훅 핵심 요약 | 통과 |", first.stdout)
        self.assertNotIn("| 실패 |", first.stdout)
        snap1 = tree_snapshot(target)
        codex_cfg = sb.home / ".codex" / "config.toml"
        codex1 = codex_cfg.read_text(encoding="utf-8") if codex_cfg.exists() else None

        second = sb.bootstrap(*args, FAKE_SLUG="demo")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("바뀐 파일 0", second.stdout, second.stdout)
        self.assertEqual(snap1, tree_snapshot(target), "두 번째 실행이 파일을 바꿨다")
        if codex1 is not None:
            self.assertEqual(codex1, codex_cfg.read_text(encoding="utf-8"), "두 번째 실행이 Codex 설정을 또 덧붙였다")
            self.assertEqual(len(list(codex_cfg.parent.glob("config.toml.harness-bak-*"))), 0)

        # 저장소 두 개가 로컬 원격에서 clone 됐다(폴더 이름에 공백이 있어도)
        self.assertTrue((sb.projects / "web" / ".git").exists())
        self.assertTrue((sb.projects / "api 서버" / ".git").exists())

        files = set(snap1)
        must = {"CLAUDE.md", "AGENTS.md", "harness.json", "상황판.md", "docs/기록/README.md", ".gitignore",
                "plugins/demo/config.json", "plugins/demo/core.md", "plugins/demo/hooks/hooks.json",
                "plugins/demo/scripts/guard-push.py", "plugins/demo/scripts/core-context.py",
                "plugins/demo/agents/web-worker.md", "plugins/demo/agents/api-worker.md",
                "plugins/demo/agents/researcher.md", "plugins/demo/agents/reviewer.md",
                "plugins/demo/skills/setup/SKILL.md", "plugins/demo/skills/absolute-rules/SKILL.md",
                "plugins/demo/skills/code-convention/SKILL.md", "harness/scripts/bootstrap.py",
                "harness/hooks/guard-rules.py", "bootstrap.sh", "bootstrap.ps1"}
        self.assertFalse(must - files, f"빠진 파일: {must - files}")
        claude_files = {".claude/settings.json", ".claude-plugin/marketplace.json", "plugins/demo/.claude-plugin/plugin.json"}
        codex_files = {".codex/hooks.json", ".agents/plugins/marketplace.json", "plugins/demo/.codex-plugin/plugin.json",
                       ".codex/agents/web-worker.toml", "plugins/demo/scripts/codex-bind-session.py",
                       "plugins/demo/scripts/codex_project_binding.py"}
        orca_files = {"scripts/orca-worker.sh", "scripts/orca-send-worker.sh", "scripts/orca-finish-worker.sh", "scripts/orca-common.sh"}
        for group, on in ((claude_files, "claude" in engines), (codex_files, "codex" in engines), (orca_files, orca)):
            if on:
                self.assertFalse(group - files, f"{engines} orca={orca} 에 빠짐: {group - files}")
            else:
                self.assertFalse(group & files, f"{engines} orca={orca} 에 있으면 안 됨: {group & files}")
        archive = "plugins/demo/scripts/archive-codex-session.py"
        self.assertEqual(archive in files, "codex" in engines and orca)

        # 금지어: 템플릿 원본 프로젝트 이름 · 호칭 · 이 기기 경로가 생성물에 없다
        for rel, data in snap1.items():
            text = data.decode("utf-8", errors="ignore")
            for word in FORBIDDEN:
                self.assertNotIn(word, text, f"{rel} 에 금지어 {word}")

        # 설정 파일 모양
        hooks = json.loads(snap1["plugins/demo/hooks/hooks.json"])
        start_cmds = [h["command"] for h in hooks["hooks"]["SessionStart"][0]["hooks"]]
        self.assertEqual(any("codex-bind-session" in c for c in start_cmds), "codex" in engines)
        self.assertEqual("SessionEnd" in hooks["hooks"], "codex" in engines and orca)
        pconf = json.loads(snap1["plugins/demo/config.json"])
        self.assertEqual(pconf["repos"]["demo-org/web"]["ask"], {"main": "상용 배포"})
        self.assertTrue(pconf["repos"]["demo-org/orchestrator"]["harness"])

        # 고른 엔진만 설치 명령이 나갔다
        calls = sb.tool_calls()
        tools = {c["tool"] for c in calls if c["args"][:1] != ["--version"]}
        self.assertEqual("claude" in tools, "claude" in engines)
        self.assertEqual("codex" in tools, "codex" in engines)
        self.assertEqual("orca" in tools, orca)
        self.assertNotIn("gh", tools, "--offline 인데 gh 를 불렀다")
        if "claude" in engines:
            installs = [c for c in calls if c["tool"] == "claude" and c["args"][:2] == ["plugin", "install"]]
            scopes = sorted(c["args"][-1] for c in installs[:3])
            self.assertEqual(scopes, ["local", "local", "project"])
        if "codex" in engines:
            text = codex_cfg.read_text(encoding="utf-8")
            self.assertIn("[marketplaces.demo-local]", text)
            self.assertEqual(text.count("trust_level = \"trusted\""), 3)
            self.assertTrue(any(c["tool"] == "codex" and c["args"][:3] == ["plugin", "add", "demo@demo-local"] for c in calls))
        else:
            self.assertFalse(codex_cfg.exists(), "Codex 를 안 골랐는데 Codex 설정을 만들었다")
        if orca:
            bases = [c["args"] for c in calls if c["tool"] == "orca" and c["args"][:2] == ["repo", "set-base-ref"]]
            self.assertIn("origin/develop", [a[a.index("--ref") + 1] for a in bases])

    def test_claude_no_orca(self):
        self.run_combo(*COMBOS[0])

    def test_claude_orca(self):
        self.run_combo(*COMBOS[1])

    def test_codex_no_orca(self):
        self.run_combo(*COMBOS[2])

    def test_codex_orca(self):
        self.run_combo(*COMBOS[3])

    def test_both_no_orca(self):
        self.run_combo(*COMBOS[4])

    def test_both_orca(self):
        self.run_combo(*COMBOS[5])


class Regenerate(unittest.TestCase):
    def test_human_parts_kept_and_stale_removed(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        sb.fake_tools()
        target = sb.projects / "orchestrator"
        both = sb.config(["claude", "codex"], True)
        r = sb.bootstrap("run", "--config", str(both), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        skill = target / "plugins/demo/skills/code-convention/SKILL.md"
        skill.write_text("사람이 채운 규칙\n", encoding="utf-8")
        claude_md = target / "CLAUDE.md"
        text = claude_md.read_text(encoding="utf-8")
        text = text.replace("<!-- 채울 자리: 이 프로젝트만의 결정 · 주의점. 규칙은 플러그인 스킬에 둔다. -->", "우리 팀 메모")
        text = text.replace("머리를 비워 두는 것이 역할이다.", "블록 안을 손으로 고침")
        claude_md.write_text(text, encoding="utf-8")

        # Claude 만 · Orca 없이로 설정을 바꿔 다시 생성
        data = json.loads(both.read_text(encoding="utf-8"))
        data["engines"], data["orca"]["enabled"] = ["claude"], False
        (target / "harness.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        r = sb.bootstrap("generate", "--target", str(target))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        self.assertEqual(skill.read_text(encoding="utf-8"), "사람이 채운 규칙\n")
        text = claude_md.read_text(encoding="utf-8")
        self.assertIn("우리 팀 메모", text)
        self.assertIn("머리를 비워 두는 것이 역할이다.", text)
        self.assertNotIn("블록 안을 손으로 고침", text)
        self.assertFalse((target / ".codex/hooks.json").exists(), "Codex 를 뺐는데 .codex/hooks.json 이 남았다")
        self.assertFalse((target / "scripts/orca-worker.sh").exists(), "Orca 를 뺐는데 orca-worker.sh 가 남았다")
        self.assertTrue((target / ".claude/settings.json").exists())

    def test_existing_human_claude_md_is_not_lost(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        target = sb.projects / "orchestrator"
        target.mkdir()
        (target / "CLAUDE.md").write_text("# 원래 있던 지침\n지우면 안 된다\n", encoding="utf-8")
        cfg = sb.config(["claude"], False)
        r = sb.bootstrap("run", "--config", str(cfg), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 2, "하네스 표시 없는 폴더에서 멈추지 않았다")
        r = sb.bootstrap("run", "--config", str(cfg), "--target", str(target), "--offline", "--non-interactive", "--skip-install", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("<!-- harness:begin claude-md", text)
        self.assertIn("지우면 안 된다", text)


class Interactive(unittest.TestCase):
    def test_questions_write_config(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        target = sb.projects / "orchestrator"
        answers = "\n".join([
            "Quiz 앱", "quiz", "quiz-org", "팀장님", "orchestrator", "main",   # 1 프로젝트
            "app", "", "", "", "develop", "모바일 앱", "1",                      # 2 키 · 폴더 · 새로 만들기 · 원격 · 브랜치 · 설명 · 스택 node
            "develop=dev 배포,main=상용 배포", "main", "",                       #   배포 · 물을 브랜치 · 검사(스택 기본값)
            "",                                                                   # 저장소 끝
            "y", "n", "n", "y",                                                   # 3 채울 자리 틀
            "3", "n",                                                             # 4 엔진 둘 다 · Orca 아니오
        ]) + "\n"
        r = sb.bootstrap("run", "--target", str(target), "--offline", "--skip-install", input_text=answers)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = json.loads((target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["project"]["owner_title"], "팀장님")
        self.assertEqual(cfg["repos"][0]["remote"], "quiz-org/app")
        self.assertEqual(cfg["repos"][0]["deploy"], {"develop": "dev 배포", "main": "상용 배포"})
        self.assertEqual((cfg["repos"][0]["source"], cfg["repos"][0]["stack"]), ("new", "node"))
        self.assertEqual(cfg["repos"][0]["checks"], ["pnpm lint", "pnpm test"])
        self.assertEqual(cfg["skills"]["fill"], ["code-convention", "design"])
        self.assertEqual(cfg["engines"], ["claude", "codex"])
        self.assertTrue((target / "plugins/quiz/skills/design/SKILL.md").exists())
        self.assertFalse((target / "plugins/quiz/skills/operations/SKILL.md").exists())

    def test_non_interactive_without_config_fails(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        r = sb.bootstrap("run", "--target", str(sb.projects / "x"), "--offline", "--non-interactive")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--config", r.stderr)


if __name__ == "__main__":
    unittest.main()
