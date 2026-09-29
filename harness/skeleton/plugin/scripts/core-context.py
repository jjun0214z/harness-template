#!/usr/bin/env python3
"""핵심 규칙 요약 넣기 (SessionStart: startup · resume · clear · compact).

플러그인의 core.md 를 읽어 stdout 으로 그대로 낸다. SessionStart 훅의 stdout 은 세션 컨텍스트에 들어간다.
core.md 를 못 읽으면 세션을 막지 않고 stderr 에만 적는다.
"""
import os
import sys


def main():
    root = (os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "core.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"harness core-context: {path} 를 못 읽음 ({e})", file=sys.stderr)
        return
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
    sys.exit(0)
