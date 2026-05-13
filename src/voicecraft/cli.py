"""voicecraft CLI 入口。M0 仅打印版本占位。"""

from __future__ import annotations

import sys

from voicecraft import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"-V", "--version", "version"}:
        print(f"voicecraft {__version__}")
        return 0
    print(f"voicecraft {__version__} (M0 stub, see CLAUDE.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
