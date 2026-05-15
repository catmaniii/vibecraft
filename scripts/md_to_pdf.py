"""Markdown → PDF（中文友好），不依赖 pandoc / wkhtmltopdf。

流程：md → html（微软雅黑 CSS）→ Edge headless --print-to-pdf。
依赖：`markdown` 库（用 `uv run --with markdown` 临时装，不进 pyproject）+
系统装了 Microsoft Edge（Windows 自带）。

用法：
    uv run --no-sync --with markdown python scripts/md_to_pdf.py <input.md> <output.pdf>
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

_EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @page {{ margin: 1.8cm 1.6cm; }}
  body {{
    font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
    font-size: 11pt; line-height: 1.6; color: #222;
  }}
  h1 {{ font-size: 20pt; border-bottom: 2px solid #444; padding-bottom: 4px; }}
  h2 {{ font-size: 15pt; margin-top: 1.4em;
        border-bottom: 1px solid #ccc; padding-bottom: 3px; }}
  h3 {{ font-size: 12.5pt; margin-top: 1.1em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.8em 0; }}
  th, td {{ border: 1px solid #bbb; padding: 5px 8px;
            text-align: left; font-size: 10pt; }}
  th {{ background: #f0f0f0; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px;
          font-family: Consolas, monospace; font-size: 9.5pt; }}
  pre {{ background: #f6f6f6; padding: 10px; border-radius: 4px;
         overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ border-left: 3px solid #ccc; margin-left: 0;
                padding-left: 12px; color: #555; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.5em 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: python scripts/md_to_pdf.py <input.md> <output.pdf>")
        return 1
    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve()
    if not src.exists():
        print(f"输入文件不存在: {src}")
        return 1
    if not Path(_EDGE).exists():
        print(f"找不到 Edge: {_EDGE}")
        return 1

    md_text = src.read_text(encoding="utf-8")
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    html = _HTML_TEMPLATE.format(body=body)

    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as fh:
        fh.write(html)
        tmp_html = Path(fh.name)

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                _EDGE,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={dst}",
                tmp_html.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
    finally:
        tmp_html.unlink(missing_ok=True)

    if dst.exists():
        print(f"PDF 生成成功: {dst} ({dst.stat().st_size // 1024} KB)")
        return 0
    print("PDF 未生成")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
