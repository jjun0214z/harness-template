"""공개 전 검사: 템플릿 전체(생성물만이 아니라 이 저장소의 모든 파일)에 남으면 안 되는 것.

원본 프로젝트 이름 · 호칭 · 이 기기 경로 · 메일 주소 · 토큰이나 키 모양 문자열 · 원본 이슈 번호.
이 파일도 검사를 받으므로 찾는 말은 조각을 붙여 만든다.
"""
from __future__ import annotations

import re
import unittest

from helpers import FORBIDDEN, TEMPLATE

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
EMAIL_OK = ("example.com", "example.org")  # 문서 · 시험용으로 예약된 주소
TOKENS = [re.compile(p) for p in (
    r"\bsk-[A-Za-z0-9_-]{16,}", r"\bgh[pousr]_[A-Za-z0-9]{20,}", r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]
ISSUE = re.compile(r"(?<![\w&/])#\d{2,}\b")


def files():
    for p in sorted(TEMPLATE.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.relative_to(TEMPLATE).parts:
            yield p


class PublicScan(unittest.TestCase):
    def test_every_file(self):
        problems = []
        for p in files():
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                problems.append(f"{p.relative_to(TEMPLATE)}: 글자 파일이 아니다(공개 전에 확인)")
                continue
            rel = p.relative_to(TEMPLATE)
            for word in FORBIDDEN:
                if word in text:
                    problems.append(f"{rel}: {word}")
            for m in EMAIL.finditer(text):
                if not m.group(0).lower().endswith(EMAIL_OK):
                    problems.append(f"{rel}: 메일 주소 {m.group(0)}")
            for pat in TOKENS:
                if pat.search(text):
                    problems.append(f"{rel}: 토큰 모양 {pat.pattern}")
            for m in ISSUE.finditer(text):
                problems.append(f"{rel}: 이슈 번호 {m.group(0)}")
            if chr(0x2014) in text:
                problems.append(f"{rel}: em대시")
        self.assertEqual(problems, [])

    def test_scanner_catches_samples(self):
        # 검사 장치가 실제로 잡는지 틀린 입력으로 한 번 떨어뜨려 본다
        self.assertTrue(EMAIL.search("a" + "@" + "corp.co.kr"))
        self.assertTrue(any(p.search("s" + "k-" + "A" * 20) for p in TOKENS))
        self.assertTrue(any(p.search("gh" + "p_" + "b" * 30) for p in TOKENS))
        self.assertTrue(any(p.search("ey" + "J" + "x" * 12 + "." + "y" * 12) for p in TOKENS))
        self.assertFalse(EMAIL.search("brew install python" + "@" + "3.12"))
        self.assertTrue(ISSUE.search("결정 #" + "129"))
        self.assertFalse(ISSUE.search("색 #2563EB"))

    def test_license_is_mit(self):
        text = (TEMPLATE / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("MIT License"))


if __name__ == "__main__":
    unittest.main()
