"""OS 판정 · 파이썬 이름 · 설치 명령 · node 탐색 · 설정 검증. Windows · Linux 는 platform 을 흉내 내서 잰다(실기기 아님)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import FORBIDDEN, SCRIPTS, TEMPLATE

sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402


class OsKind(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(hl.os_kind("Darwin"), "mac")
        self.assertEqual(hl.os_kind("Windows"), "windows")
        self.assertEqual(hl.os_kind("CYGWIN_NT-10.0"), "windows")
        self.assertEqual(hl.os_kind("MSYS_NT-10.0"), "windows")
        self.assertEqual(hl.os_kind("Linux"), "linux")


class PythonName(unittest.TestCase):
    def test_posix_is_python3(self):
        self.assertEqual(hl.python_command_name("mac"), "python3")
        self.assertEqual(hl.python_command_name("linux"), "python3")

    def test_windows_prefers_real_interpreter(self):
        table = {"py": r"C:\Windows\py.exe", "python": r"C:\Python312\python.exe"}
        self.assertEqual(hl.python_command_name("windows", which=table.get), "py -3")

    def test_windows_skips_store_stub(self):
        table = {"python3": r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\python3.exe",
                 "python": r"C:\Python312\python.exe"}
        self.assertEqual(hl.python_command_name("windows", which=table.get), "python")

    def test_windows_nothing_found(self):
        self.assertEqual(hl.python_command_name("windows", which=lambda n: None), "python")


class InstallCommands(unittest.TestCase):
    def test_mac_uses_brew(self):
        cmd, hint = hl.install_command("node", "mac", lambda n: n == "brew")
        self.assertEqual(cmd, ["brew", "install", "node@22"])
        cmd, hint = hl.install_command("gh", "mac", lambda n: False)
        self.assertIsNone(cmd)
        self.assertIn("brew.sh", hint)

    def test_windows_uses_winget(self):
        cmd, _ = hl.install_command("gh", "windows", lambda n: n == "winget")
        self.assertEqual(cmd, ["winget", "install", "-e", "--id", "GitHub.cli"])
        cmd, hint = hl.install_command("git", "windows", lambda n: False)
        self.assertIsNone(cmd)
        self.assertIn("winget", hint)

    def test_linux_is_guidance_only(self):
        for tool in ("git", "gh", "node", "python"):
            cmd, hint = hl.install_command(tool, "linux", lambda n: True)
            self.assertIsNone(cmd, tool)
            self.assertTrue(hint)

    def test_agent_cli_via_npm_on_every_os(self):
        for kind in ("mac", "windows", "linux"):
            cmd, _ = hl.install_command("claude", kind, lambda n: n == "npm")
            self.assertEqual(cmd, ["npm", "install", "-g", "@anthropic-ai/claude-code"])
            cmd, _ = hl.install_command("codex", kind, lambda n: n == "npm")
            self.assertEqual(cmd, ["npm", "install", "-g", "@openai/codex"])
            cmd, hint = hl.install_command("codex", kind, lambda n: False)
            self.assertIsNone(cmd)
            self.assertIn("node 22", hint)


class NodeSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ.pop("HARNESS_PATH_ONLY", None)

    def touch(self, *parts):
        p = self.tmp.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        return p

    def test_posix_nvm_and_fnm(self):
        nvm = self.touch("home", ".nvm", "versions", "node", "v22.1.0", "bin", "node")
        fnm = self.touch("home", ".local", "share", "fnm", "node-versions", "v20.0.0", "installation", "bin", "node")
        found = hl.node_candidates("linux", env={"PATH": ""}, home_dir=self.tmp / "home")
        self.assertIn(nvm, found)
        self.assertIn(fnm, found)

    def test_windows_nvm_windows_and_fnm(self):
        nvmw = self.touch("appdata", "nvm", "v22.3.0", "node.exe")
        fnm = self.touch("appdata", "fnm", "node-versions", "v22.0.0", "installation", "node.exe")
        found = hl.node_candidates("windows", env={"PATH": "", "APPDATA": str(self.tmp / "appdata")}, home_dir=self.tmp / "home")
        self.assertIn(nvmw, found)
        self.assertIn(fnm, found)

    def test_path_only_switch(self):
        self.touch("home", ".nvm", "versions", "node", "v22.1.0", "bin", "node")
        os.environ["HARNESS_PATH_ONLY"] = "1"
        try:
            self.assertEqual(hl.node_candidates("linux", env={"PATH": ""}, home_dir=self.tmp / "home"), [])
        finally:
            os.environ.pop("HARNESS_PATH_ONLY", None)

    def test_major(self):
        self.assertEqual(hl.node_major("v22.23.2"), 22)
        self.assertEqual(hl.node_major("v18.12.1"), 18)
        self.assertEqual(hl.node_major(None), 0)


class ConfigValidation(unittest.TestCase):
    def base(self):
        return {"project": {"name": "X", "slug": "x", "github_org": "o", "owner_title": "대표님"},
                "repos": [{"key": "web"}], "engines": ["claude"]}

    def test_defaults_fill_remote(self):
        cfg = hl.normalize(self.base())
        self.assertEqual(hl.validate(cfg), [])
        self.assertEqual(cfg["repos"][0]["remote"], "o/web")
        self.assertEqual(cfg["harness_repo"]["remote"], "o/orchestrator")

    def test_errors(self):
        raw = self.base()
        raw["project"]["slug"] = "한글"
        raw["repos"] = [{"key": "Web"}, {"key": "a", "dir": "orchestrator"}]
        raw["engines"] = ["claude", "claude"]
        errs = hl.validate(hl.normalize(raw))
        joined = "\n".join(errs)
        self.assertIn("project.slug", joined)
        self.assertIn("repos[0].key", joined)
        self.assertIn("repos[1].dir", joined)
        self.assertIn("engines", joined)

    def test_windows_hook_command_uses_configured_python(self):
        raw = self.base()
        raw["platform"] = {"python": "py -3"}
        cfg = hl.normalize(raw)
        hooks = gen.plugin_hooks(cfg)
        self.assertTrue(hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"].startswith("py -3 "))
        settings = gen.claude_settings(cfg)
        self.assertTrue(settings["hooks"]["Stop"][0]["hooks"][0]["command"].startswith("py -3 "))

    def test_toml_escapes_windows_paths(self):
        self.assertEqual(gen.toml_str("C:\\a\\b"), '"C:\\\\a\\\\b"')


class Blocks(unittest.TestCase):
    def test_replace_only_blocks(self):
        old = "앞\n" + hl.block("a", "옛") + "사람\n" + hl.block("b", "옛 b")
        new = hl.block("a", "새") + hl.block("b", "새 b")
        out = hl.replace_blocks(old, new)
        self.assertIn("앞\n", out)
        self.assertIn("사람\n", out)
        self.assertIn("새\n", out)
        self.assertIn("새 b\n", out)
        self.assertNotIn("옛", out)


class Shells(unittest.TestCase):
    def test_bash_syntax(self):
        scripts = [TEMPLATE / "bootstrap.sh"] + sorted((TEMPLATE / "harness" / "skeleton" / "orca").glob("*.sh"))
        for s in scripts:
            r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{s}: {r.stderr}")

    def test_powershell_syntax(self):
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            self.skipTest("pwsh 없음: bootstrap.ps1 문법 미검증")
        ps1 = TEMPLATE / "bootstrap.ps1"
        code = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e) | Out-Null;"
                " if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }") % ps1
        r = subprocess.run([pwsh, "-NoProfile", "-Command", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_ps1_splatted_variables_are_always_arrays(self):
        """PowerShell 은 원소 하나짜리 결과를 문자열로 풀어 버린다. @이름 으로 넘기는 변수는 모두 `$이름 = @(` 로 만들어야
        인자가 글자 단위로 쪼개지지 않는다(윈도우 실측: invalid choice: 'r'). pwsh 가 없어도 이 규칙은 잰다."""
        import re
        for ps1 in ("install.ps1", "bootstrap.ps1"):
            text = (TEMPLATE / ps1).read_text(encoding="utf-8")
            code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
            splats = set(re.findall(r"(?<![\w$'\"])@([A-Za-z_]\w*)", code)) - {"args"}
            self.assertTrue(splats, ps1)
            for name in sorted(splats):
                assigns = re.findall(r"\$" + name + r"\s*=\s*(.+)", code)
                self.assertTrue(assigns, f"{ps1}: @{name} 을 넘기는데 대입이 없다")
                for a in assigns:
                    self.assertTrue(a.startswith("@("), f"{ps1}: ${name} = {a[:40]} 는 @( 로 감싸야 한다")
        install = (TEMPLATE / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("$BootArgs = @(if ($env:HARNESS_ARGS)", install)
        self.assertIn("$py = @(Find-Python)", install)

    def test_setup_skill_copies_identical(self):
        src = (TEMPLATE / "harness/skeleton/plugin/skills/setup/SKILL.md").read_bytes()
        self.assertEqual((TEMPLATE / ".claude/skills/setup/SKILL.md").read_bytes(), src)
        self.assertEqual((TEMPLATE / ".agents/skills/setup/SKILL.md").read_bytes(), src)

    def test_no_emdash_and_no_origin_names_in_engine(self):
        for p in sorted((TEMPLATE / "harness").rglob("*")) + [TEMPLATE / "README.md", TEMPLATE / "CLAUDE.md", TEMPLATE / "AGENTS.md"]:
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, f"em대시: {p}")
            for word in FORBIDDEN:
                self.assertNotIn(word, text, f"{p} 에 {word}")


if __name__ == "__main__":
    unittest.main()
