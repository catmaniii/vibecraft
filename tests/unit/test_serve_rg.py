"""`/rg` 路由：从全局 reasoning-graph skill 的 viewer 注入 live yaml。

批 E（2026-07-14）：模板源从 repo `scripts/_templates/kg-template.html` 切到全局 skill
`~/.claude/skills/reasoning-graph/assets/rg-viewer.html`，仍是服务端实时注入同一占位符
`/*__RG_JSON__*/.../*__RG_JSON_END__*/`，零副本、改 yaml 刷新即生效。

2026-07-14 用户决定：`/rg` **刻意无门控、公网可见**（推理图谱是内部研发认知、不含密钥，接受
公网前门任意访问换取裸 URL 便利）。故本路由无 room-token 校验，任何请求都返回渲染结果。
"""

from pathlib import Path

import pytest

from vibecraft.server.http import _serve_rg

_SKILL_VIEWER = Path.home() / ".claude" / "skills" / "reasoning-graph" / "assets" / "rg-viewer.html"


@pytest.mark.skipif(not _SKILL_VIEWER.exists(), reason="全局 reasoning-graph skill viewer 未安装")
def test_serve_rg_injects_from_skill_viewer() -> None:
    resp = _serve_rg()

    assert resp.status_code == 200

    body = resp.body.decode("utf-8") if isinstance(resp.body, (bytes, bytearray)) else resp.body

    # 注入了真实节点（占位符被替换成 docs/reasoning-graph.yaml 里的真实数据）
    assert '"id":"F1"' in body
    # 占位符已消失（真的注入了，不是原样返回）
    assert "/*__RG_JSON__*/" not in body
    # 用的是 skill viewer（带 drag-drop 区，证明模板已切到 skill 而非旧 repo 模板）
    assert 'id="dropZone"' in body
