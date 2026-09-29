"""Windows Git Bash 에서 불렸을 때: install.sh 는 PowerShell 설치로 넘기고, bootstrap.sh 는 py -3 로 그대로 돈다.
uname · powershell.exe · cygpath · py 는 가짜로 흉내 낸다(이 맥에서 실제 Windows 는 못 돌린다)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from helpers import TEMPLATE, Sandbox

BASIC = ("bash", "sh", "sed", "grep", "head", "cat", "mkdir", "rm", "mv", "dirname", "ls", "id", "tr", "env", "sort")
RECORDER = """#!/bin/sh
printf '%s\\n' "$0" > "$REC"
for a in "$@"; do printf 'ARG %s\\n' "$a" >> "$REC"; done
env | grep '^HARNESS_' | sort | sed 's/^/ENV /' >> "$REC"
"""


class GitBash(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.path = self.sb.tmp / "path"
        self.path.mkdir()
        for tool in BASIC:
            if shutil.which(tool):
                os.symlink(shutil.which(tool), self.path / tool)
        self.rec = self.sb.tmp / "rec.txt"

    def fake(self, name, body):
        p = self.path / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)

    def env(self, **extra):
        e = {"HOME": str(self.sb.home), "PATH": str(self.path), "HARNESS_NO_TTY": "1", "LANG": "C.UTF-8",
             "REC": str(self.rec), "HARNESS_OS": "MINGW64_NT-10.0-19045"}
        e.update(extra)
        return e

    def recorded(self):
        lines = self.rec.read_text(encoding="utf-8").splitlines()
        args = [l[4:] for l in lines if l.startswith("ARG ")]
        env = dict(l[4:].split("=", 1) for l in lines if l.startswith("ENV "))
        return lines[0], args, env

    def test_install_hands_off_to_powershell(self):
        self.fake("powershell.exe", RECORDER)
        self.fake("cygpath", '#!/bin/sh\n[ "$1" = -w ] && shift\necho "C:\\\\Users\\\\u\\\\dev"\n')
        dev = self.sb.tmp / "dev"
        dev.mkdir()
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh"), "--yes", "--", "run", "--root", "D:/work/acme"],
                           capture_output=True, text=True, env=self.env(), cwd=str(dev), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PowerShell 설치로 넘긴다", r.stdout)
        exe, args, env = self.recorded()
        self.assertTrue(exe.endswith("powershell.exe"))
        self.assertEqual(args, ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                "irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex"])
        self.assertEqual(env["HARNESS_CALLER_CWD"], "C:\\Users\\u\\dev", "실행한 폴더를 Windows 경로로 넘기지 않았다")
        self.assertEqual(json.loads(env["HARNESS_ARGS_JSON"]), ["run", "--root", "D:/work/acme"])
        self.assertNotIn("HARNESS_ARGS", env)
        self.assertEqual(env["HARNESS_YES"], "1")
        self.assertNotIn("하네스 템플릿 설치", r.stdout, "넘기기 전에 mac · Linux 설치를 시작했다")

    def test_pwsh_preferred_and_msys_cygwin(self):
        self.fake("powershell.exe", RECORDER)
        self.fake("pwsh.exe", RECORDER)
        for osname in ("MSYS_NT-10.0", "CYGWIN_NT-10.0"):
            r = subprocess.run(["bash", str(TEMPLATE / "install.sh"), "--dry-run"], capture_output=True, text=True,
                               env=self.env(HARNESS_OS=osname), stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            exe, _, env = self.recorded()
            self.assertTrue(exe.endswith("pwsh.exe"), exe)
            self.assertEqual(env["HARNESS_DRY_RUN"], "1")
            self.assertEqual(json.loads(env["HARNESS_ARGS_JSON"]), ["run"])

    def fake_cygpath(self):
        # /c/rest -> C:\rest (슬래시는 역슬래시로). 그 밖은 그대로
        self.fake("cygpath", '#!/bin/bash\n[ "$1" = -w ] && shift\np="$1"\n'
                  'if [[ "$p" == /?/* ]]; then d="${p:1:1}"; p="$(printf %s "$d" | tr a-z A-Z):${p:2}"; fi\n'
                  'printf "%s\\n" "${p//\\//\\\\}"\n')

    def test_space_root_and_msys_paths_stay_one_arg(self):
        """공백 든 --root 가 쪼개지지 않고, /c/... 경로는 Windows 경로로, 따옴표 · 역슬래시도 JSON 으로 안전하게 간다."""
        self.fake("powershell.exe", RECORDER)
        self.fake_cygpath()
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh"), "--", "run", "--root", "/c/work/u/My Projects/kids",
                            "--config=/d/cfg dir/h.json", "--note", 'a "b" C:\\x'],
                           capture_output=True, text=True, env=self.env(), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        _, _, env = self.recorded()
        self.assertEqual(json.loads(env["HARNESS_ARGS_JSON"]),
                         ["run", "--root", "C:\\work\\u\\My Projects\\kids", "--config=D:\\cfg dir\\h.json", "--note", 'a "b" C:\\x'])

    def test_handoff_modes(self):
        """mintty 에서 바로 띄운 powershell.exe 는 입력을 못 받을 수 있다: winpty 로 감싸거나 새 PowerShell 창으로 넘긴다."""
        self.fake("powershell.exe", RECORDER)
        self.fake("winpty", RECORDER)
        url = "https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1"
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh")], capture_output=True, text=True,
                           env=self.env(HARNESS_WIN_HANDOFF="winpty"), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        exe, args, _ = self.recorded()
        self.assertTrue(exe.endswith("winpty"), exe)
        self.assertEqual(args, ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", f"irm {url} | iex"])
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh")], capture_output=True, text=True,
                           env=self.env(HARNESS_WIN_HANDOFF="window"), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("새 PowerShell 창에서 이어진다", r.stdout)
        exe, args, _ = self.recorded()
        self.assertTrue(exe.endswith("powershell.exe"), exe)
        self.assertEqual(args[:2], ["-NoProfile", "-Command"])
        self.assertEqual(args[2], "Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoExit','-NoProfile',"
                                  f"'-ExecutionPolicy','Bypass','-Command','irm {url} | iex'")
        # 터미널이 없으면(시험 · 무인) winpty 가 있어도 바로 넘긴다
        subprocess.run(["bash", str(TEMPLATE / "install.sh")], capture_output=True, text=True,
                       env=self.env(), stdin=subprocess.DEVNULL, timeout=60)
        exe, _, _ = self.recorded()
        self.assertTrue(exe.endswith("powershell.exe"), exe)

    def test_no_powershell_explains(self):
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh")], capture_output=True, text=True,
                           env=self.env(), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("PowerShell 을 찾지 못했다", r.stdout)
        self.assertIn("install.ps1 | iex", r.stdout)

    def test_bootstrap_sh_runs_with_py_launcher(self):
        self.fake("uname", "#!/bin/sh\necho MINGW64_NT-10.0-19045\n")
        self.fake("python3", "#!/bin/sh\nexit 9009\n")  # 스토어 가짜 실행 파일 흉내
        self.fake("py", '#!/bin/sh\ncase "$*" in *"-c "*) exit 0 ;; esac\necho "PY $*"\n')
        r = subprocess.run(["bash", str(TEMPLATE / "bootstrap.sh"), "check"], capture_output=True, text=True,
                           env=self.env(), timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), f"PY -3 {TEMPLATE / 'harness' / 'scripts' / 'bootstrap.py'} check")


class Ps1Encoding(unittest.TestCase):
    def test_bootstrap_ps1_is_ascii(self):
        # Windows PowerShell 5.1 은 BOM 없는 파일을 ANSI 로 읽는다. 파일로 실행되는 bootstrap.ps1 은 ASCII 로만 둔다
        (TEMPLATE / "bootstrap.ps1").read_bytes().decode("ascii")

    def test_install_ps1_has_no_bom(self):
        # install.ps1 은 irm | iex(UTF-8 로 받음)로만 실행한다. BOM 이 있으면 iex 에 첫 글자로 남을 수 있어 넣지 않는다
        self.assertFalse((TEMPLATE / "install.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
