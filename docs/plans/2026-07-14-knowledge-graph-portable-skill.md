# 知识图谱可移植 Skill 化 · 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把知识图谱(方法论 + 检索/校验/渲染脚本 + 前端可视化模板)从 vibecraft 内嵌形态,重构成一个装在全局 `~/.claude/skills/knowledge-graph/` 的可移植 skill;数据 yaml 留项目内;废弃 `knowledge-graph.md` 第二源,changelog 迁回 yaml;前端一份模板两种喂法(drag-drop / 服务端注入)。

**Architecture:** skill 全局为家,只含通用件(SKILL.md / SCHEMA.md / 三脚本 / 一模板),零项目数据。项目侧 `docs/knowledge-graph.yaml` 是唯一数据源。脚本去硬编码路径改 CLI 参数。前端模板从 `kg-template.html`(仅注入)升级成 `kg-viewer.html`(注入占位符 + 客户端 drag-drop 解析共存,统一走可重入 `boot(nodes)`)。vibecraft `/kg` 沿用服务端实时注入,只把模板源指向 skill 的 viewer。KG/skill **不进开源交付物**,故无 cloner CI 自足要求。

**Tech Stack:** Python 3(argparse/pathlib/PyYAML)、纯 JS(内联 js-yaml + SVG DOM)、pytest、vibecraft server(`src/vibecraft/server/http.py`)。

**设计真理源:** `docs/plans/2026-07-14-knowledge-graph-portable-skill-design.md`(读它拿全部决策上下文)。

**全局 skill 绝对路径(本机):** `C:\Users\catmaniii\.claude\skills\knowledge-graph\`(下称 `<SKILL>`)。

---

## 阶段划分与顺序理由

顺序按"最小化破坏"排:先把脚本参数化(vibecraft 仍能跑)→ 迁 changelog(md 还在,旧一致门仍绿)→ 建全局 skill 包 → 重构前端 → 切 `/kg` → 换测试薄壳 → 删 md + 扫引用 → 改 CLAUDE.md → 开源排除。每个 Task 独立可提交。

---

## Task 1: kg_query.py yaml 路径参数化(去硬编码)

**Files:**
- Modify: `scripts/kg_query.py:26`(及 `_load`/`main`)
- Test: `tests/unit/test_kg_scripts.py`(Create)

**Step 1: Write the failing test**

```python
# tests/unit/test_kg_scripts.py
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUERY = REPO / "scripts" / "kg_query.py"
YAML = REPO / "docs" / "knowledge-graph.yaml"


def _run(args):
    return subprocess.run(
        [sys.executable, str(QUERY), *args],
        capture_output=True, text=True, encoding="utf-8",
    )


def test_kg_query_accepts_explicit_yaml_path():
    # 显式 --yaml 指向真实 yaml，--stats 应打印总节点数
    r = _run(["--yaml", str(YAML), "--stats"])
    assert r.returncode == 0, r.stderr
    assert "总节点=" in r.stdout


def test_kg_query_default_yaml_still_works():
    # 不传 --yaml 时回退到 repo 默认路径（vibecraft 内部调用不受影响）
    r = _run(["--stats"])
    assert r.returncode == 0, r.stderr
    assert "总节点=" in r.stdout
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_kg_scripts.py::test_kg_query_accepts_explicit_yaml_path -v`
Expected: FAIL(`--yaml` 未知参数,argparse 报 error / returncode != 0)

**Step 3: Write minimal implementation**

改 `scripts/kg_query.py`:
- 删掉模块级 `YAML_PATH = ...`(第 26 行),改成默认常量 `_DEFAULT_YAML = Path(__file__).resolve().parents[1] / "docs" / "knowledge-graph.yaml"`。
- `_load` 改签名 `def _load(yaml_path: Path) -> list[dict[str, Any]]:`,用传入路径。
- `main` 里加 `ap.add_argument("--yaml", type=Path, default=_DEFAULT_YAML, help="知识图谱 yaml 路径")`,并 `nodes = _load(args.yaml)`。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_kg_scripts.py -v`
Expected: 两条 PASS

**Step 5: Commit**

```bash
git add scripts/kg_query.py tests/unit/test_kg_scripts.py
git commit -m "refactor(kg): kg_query.py yaml 路径改 --yaml 参数(去硬编码,为 skill 化)"
```

---

## Task 2: build_kg_viz.py 路径参数化(yaml/模板/out 全走参数)

**Files:**
- Modify: `scripts/build_kg_viz.py`
- Test: `tests/unit/test_kg_scripts.py`(追加)

**Step 1: Write the failing test**

追加到 `tests/unit/test_kg_scripts.py`:

```python
BUILD = REPO / "scripts" / "build_kg_viz.py"
TEMPLATE = REPO / "scripts" / "_templates" / "kg-template.html"


def test_build_kg_viz_accepts_explicit_paths(tmp_path):
    out = tmp_path / "kg.html"
    r = _run_script(BUILD, ["--yaml", str(YAML), "--template", str(TEMPLATE), "--out", str(out)])
    assert r.returncode == 0, r.stderr
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # 注入后占位符被真实节点 JSON 取代
    assert '"id":"F1"' in html or '"id": "F1"' in html


def _run_script(script, args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
```

(把文件顶部已有的 `_run` 保留给 kg_query;新增通用 `_run_script`。)

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_kg_scripts.py::test_build_kg_viz_accepts_explicit_paths -v`
Expected: FAIL(`--yaml`/`--template`/`--out` 未知参数)

**Step 3: Write minimal implementation**

改 `scripts/build_kg_viz.py`:
- `build(...)` 改签名 `def build(yaml_path: Path, template_path: Path, output_path: Path) -> Path:`,用参数替代模块级 `_YAML_PATH` / `_TEMPLATE_PATH`。保留 `_DEFAULT_*` 常量做默认。
- `main()` 用 argparse:`--yaml`(default `_DEFAULT_YAML`)、`--template`(default `_DEFAULT_TEMPLATE`)、`--out`(default `_DEFAULT_OUT`);调 `build(args.yaml, args.template, args.out)`。
- 保留位置参数兼容旧用法可选(非必需,YAGNI,不加)。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_kg_scripts.py -v`
Expected: 全 PASS

**Step 5: Commit**

```bash
git add scripts/build_kg_viz.py tests/unit/test_kg_scripts.py
git commit -m "refactor(kg): build_kg_viz.py yaml/模板/out 改参数(为 skill 化,vibecraft 默认路径不变)"
```

---

## Task 3: changelog 迁进 yaml + 删 source_md

**Files:**
- Modify: `docs/knowledge-graph.yaml`(顶部加 `changelog:`、删 `source_md`)
- Test: `tests/unit/test_knowledge_graph.py`(追加 changelog 校验)

**Step 1: Write the failing test**

在 `tests/unit/test_knowledge_graph.py` 追加(先不删旧 md 门):

```python
class TestChangelog:
    def test_changelog_present_and_wellformed(self) -> None:
        data = _load_graph()
        assert "changelog" in data, "yaml 顶层必须有 changelog 段(md 废弃后承接变更日志)"
        assert isinstance(data["changelog"], list) and data["changelog"]
        for entry in data["changelog"]:
            assert "date" in entry and "change" in entry, f"changelog 条目缺 date/change: {entry}"

    def test_source_md_field_removed(self) -> None:
        data = _load_graph()
        assert "source_md" not in data, "md 已废弃，不应再有 source_md 字段"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_knowledge_graph.py::TestChangelog -v`
Expected: 两条 FAIL(还没加 changelog / source_md 还在)

**Step 3: 实现 — 编辑 yaml**

在 `docs/knowledge-graph.yaml`:
- 删第 21 行 `source_md: docs/knowledge-graph.md`。
- 在 `nodes:` 之后(文件末尾亦可,顶层同级)加 `changelog:` 段,把 `docs/knowledge-graph.md` 文末「变更日志」4 条**逐字**迁入。`change` 用 YAML 多行块标量 `|` 承接长文本 + 子项,别压一行。骨架:

```yaml
changelog:
  - date: 2026-07-12
    change: |
      建图。domain nydus-landing 录入 F1-F11 / J1-J7 / D1-D4 / U1-U2。
      复审动作:D1「OL 站位」由「离基地中心 25 格」修正为「离高地边缘 ~10 格、顺悬崖下坡」——
      因 J6(用户纠正:中心距离是废参照)冲击了旧表述。旧「25 格」作废,以 D1 现表述为准。
  - date: 2026-07-12
    change: |
      去分层 + 通用化。①原按 L0/L1/L2/U 分层改成纯 DAG;layer 字段删除,改软标签 kind
      (fact/inference/decision/open,仅描述性质)。②通用化:文件 nydus-* → knowledge-graph.*,
      每节点加 domain。节点集合/依赖边/证据/状态不变。
  - date: 2026-07-12
    change: |
      U1 定解。加 F12/J8/J9(⚠️unverified 待真机)/D5(主力赖家不硬下 canal、改速狗骚扰分矿)。
      U1 由 D5 回答。反向复审 D4 保持「主力不在才落」不改。D5 依赖 J9(unverified)→ D5 标 unverified。
  - date: 2026-07-13
    change: |
      死神 harass 微操整体回退(反向审查)。D12-D19 一串死神微操迭代反复 revert 进无法干净还原
      的回归态(VeryEasy harass 25→0),harass_act.py 整体 checkout 回基线。这些 D 节点保留为
      「尝试记录」,对应代码已回退。教训:违反「反复修=停下质疑架构」,无干净 checkpoint 下反复改。
```

> 迁完**逐条对照 `docs/knowledge-graph.md:505-525` 人工核对长文本未截断**。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_knowledge_graph.py -v`
Expected: 全 PASS(旧 md 一致门此刻仍在、仍绿,因 md 未删)

**Step 5: Commit**

```bash
git add docs/knowledge-graph.yaml tests/unit/test_knowledge_graph.py
git commit -m "refactor(kg): 变更日志迁入 yaml changelog 段 + 删 source_md(消灭 md 第二源前置)"
```

---

## Task 4: 建全局 skill 骨架 + 放 kg_query.py / kg_render.py

**Files:**
- Create: `<SKILL>/scripts/kg_query.py`(从 repo 复制,已参数化)
- Create: `<SKILL>/scripts/kg_render.py`(= repo `build_kg_viz.py` 内容,更名)
- Create: `<SKILL>/assets/`、`<SKILL>/scripts/` 目录

**Step 1: 建目录 + 拷脚本(无单测,验证=能跑)**

用 Bash:

```bash
SKILL="$HOME/.claude/skills/knowledge-graph"
mkdir -p "$SKILL/scripts" "$SKILL/assets"
cp scripts/kg_query.py "$SKILL/scripts/kg_query.py"
cp scripts/build_kg_viz.py "$SKILL/scripts/kg_render.py"
```

**Step 2: 验证 skill 版脚本对本地 yaml 可跑**

Run:
```bash
python "$HOME/.claude/skills/knowledge-graph/scripts/kg_query.py" --yaml docs/knowledge-graph.yaml --stats
```
Expected: 打印各 domain/kind 计数 + `总节点=...`(证明脱离 repo 布局、靠 `--yaml` 参数工作)

**Step 3: 无提交(全局 skill 不在 repo 版本控制内)**

> 说明:`<SKILL>` 在 `~/.claude` 下,不属 vibecraft git。本 Task 只在本机安装,无 commit。skill 自身若要版本化是独立事项(设计定:KG/skill 不开源、暂不单独建仓)。

---

## Task 5: 写 SKILL.md(移入全局) + 新建 SCHEMA.md

**Files:**
- Create/Move: `<SKILL>/SKILL.md`(基于现 `.claude/skills/knowledge-graph/SKILL.md` + 补两种喂法/SCHEMA 强指引)
- Create: `<SKILL>/SCHEMA.md`

**Step 1: 拷 SKILL.md 到全局并补充**

```bash
cp .claude/skills/knowledge-graph/SKILL.md "$HOME/.claude/skills/knowledge-graph/SKILL.md"
```
然后编辑 `<SKILL>/SKILL.md`:
- 「在哪 / 什么形态」小节改:数据 = 各项目自己的 `knowledge-graph.yaml`(路径由项目定,传给脚本 `--yaml`);人读版**已废弃**(不再有 md);可视化 = 本 skill 的 `assets/kg-viewer.html`。
- 加一节「可视化两种喂法」:①drag-drop——浏览器开 `assets/kg-viewer.html`,拖 yaml 即渲染(纯客户端,离线);②服务端注入——项目 server 读 live yaml 注入 viewer 占位符(vibecraft `/kg` 用),`kg_render.py` 是参考实现。`?yaml=` 仅 file/同源。
- 加一句 SCHEMA 强指引:**"数据格式(字段/kind/颜色/status 图例/顶层结构)权威见 `SCHEMA.md`,动任何字段或新增 kind 前必读它。"**
- 「一致性门」小节指向 `scripts/kg_validate.py`(可独立跑),删掉"yaml↔md 一致"表述。

**Step 2: 新建 SCHEMA.md**

Create `<SKILL>/SCHEMA.md`,内容收纳(从设计 §5 + 现 md/模板提炼):

```markdown
# 知识图谱数据格式(SCHEMA)

> 权威格式定义。动任何字段/新增 kind 前必读。方法论见 SKILL.md。

## 顶层结构
- version: int
- nodes: [节点]
- changelog: [{date, change(块标量), review?}]
（暂无 sources 字段——多文件是 loader 的容错能力,真拆分再引入。）

## 节点字段
| 字段 | 说明 |
|---|---|
| id | 短稳定 id，正则 `[FJDU]\d+` |
| domain | 领域 slug（`^[a-z0-9]+(-[a-z0-9]+)*$`） |
| kind | fact / inference / decision / open（软标签，不排序） |
| statement | 结论/陈述（非空） |
| title | 一句"回答什么问题"（非空） |
| deps | 依赖节点 id 列表（根 fact 可空，其余非空） |
| evidence | 证据/来源 |
| status | verified / unverified / assumed / pending |
| provenance | 仅 fact：env-game / env-api / math / empirical |
| repro | empirical fact 必填：可重跑的命令/log |
| by | 仅 decision：player / agent |

## kind → 形状/颜色(可视化)
- fact = 圆 / 蓝 `#cfe3ff`
- inference = 三角 / 黄 `#fff3c4`
- decision = 菱形 / 绿 `#c6f6d5`
- open = 方框 / 灰虚线 `#e2e8f0`

## status 图例
verified ✅ / unverified ⚠️ / assumed 🔶 / pending ❓

## level 约定
`level(n)=0` 若无 deps，否则 `1+max(level(d))`。**渲染时算，不存 yaml**（避免漂移）。

## 一致性门(kg_validate.py 实现)
无环 DAG / deps 指向存在节点 / 非根节点必有 deps / kind·status·domain 合法 /
fact 必有合法 provenance / empirical fact 必有 repro / math fact 不依赖 empirical /
decision 必有合法 by / 不确定性传播(verified 不得依赖 pending·unverified) /
顶层含 changelog。
```

**Step 3: 无提交(全局)**,但**删除 repo 内旧 SKILL.md** 留到 Task 9 统一处理(先确认全局版可用)。

---

## Task 6: 抽 kg_validate.py 到 skill(去 pytest 壳,可独立跑)

**Files:**
- Create: `<SKILL>/scripts/kg_validate.py`

**Step 1: 写 kg_validate.py**

把 `tests/unit/test_knowledge_graph.py` 的校验逻辑抽成**独立可执行**脚本(不依赖 pytest):
- `--yaml <path>` 参数(default 无,必传或回退当前目录 `docs/knowledge-graph.yaml`)。
- 复用全部检查:schema(kind/status/domain/required/provenance/repro/math-not-empirical/decision-by)、deps 完整性、无环、不确定性传播、**changelog 存在**。
- **删掉** md 一致性检查(md 已废弃)。
- 每个检查失败 `print(错误)` 到 stderr + 累计;结束 `return 1 if 有错 else 0`。用 `argparse`。

骨架:

```python
#!/usr/bin/env python
"""知识图谱一致性门(通用,独立可跑,不依赖 pytest)。用法: kg_validate.py --yaml <path>"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
import yaml

VALID_KINDS = {"fact", "inference", "decision", "open"}
VALID_STATUSES = {"verified", "unverified", "assumed", "pending"}
UNCERTAIN = {"pending", "unverified"}
VALID_PROVENANCE = {"env-game", "env-api", "math", "empirical"}
VALID_DECISION_BY = {"player", "agent"}
DOMAIN_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

def validate(data: dict) -> list[str]:
    errs: list[str] = []
    nodes = data.get("nodes", [])
    by_id = {}
    for n in nodes:
        if n["id"] in by_id:
            errs.append(f"重复 id: {n['id']}")
        by_id[n["id"]] = n
    # ... 逐项检查(照搬 test_knowledge_graph.py 各断言，改成 errs.append 累计) ...
    if "changelog" not in data:
        errs.append("顶层缺 changelog 段")
    # 无环(DFS 三色)
    # 不确定性传播
    return errs

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", type=Path, default=Path("docs/knowledge-graph.yaml"))
    args = ap.parse_args()
    data = yaml.safe_load(args.yaml.read_text(encoding="utf-8"))
    errs = validate(data)
    for e in errs:
        print(f"FAIL: {e}", file=sys.stderr)
    if errs:
        print(f"{len(errs)} 个一致性问题", file=sys.stderr)
        return 1
    print("知识图谱一致性 OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

> 逐项检查要**完整照搬** `test_knowledge_graph.py` 的 `TestSchema` / `TestDepsIntegrity` / `TestUncertaintyPropagation` 全部断言,一条不漏(除 `TestMdConsistency`)。

**Step 2: 验证对本地 yaml 通过**

Run:
```bash
python "$HOME/.claude/skills/knowledge-graph/scripts/kg_validate.py" --yaml docs/knowledge-graph.yaml
```
Expected: `知识图谱一致性 OK`,returncode 0

**Step 3: 反向验证能抓错(临时改坏一个 dep)**

手动把 yaml 里某节点 deps 加一个不存在 id → 重跑 → 应 `FAIL: ... 依赖了不存在的节点` + returncode 1;验证后改回。

**Step 4: 无提交(全局)**

---

## Task 7: 前端模板 → kg-viewer.html(可重入 boot + drag-drop + 保留注入)

**这是最大的一步。核心:把一次性 IIFE 改成可被数据驱动重跑的 `boot(nodes)`,并加客户端 yaml 加载。**

**Files:**
- Create: `<SKILL>/assets/kg-viewer.html`(基于 repo `scripts/_templates/kg-template.html`)

**Step 1: 拷模板到 skill**

```bash
cp scripts/_templates/kg-template.html "$HOME/.claude/skills/knowledge-graph/assets/kg-viewer.html"
```

**Step 2: 参数化 IIFE 成 boot(nodes)**

编辑 `<SKILL>/assets/kg-viewer.html` 的 `<script>`(现 432 起):
- 把 `(function () { "use strict"; var GRAPH_DATA = /*__KG_JSON__*/[]/*__KG_JSON_END__*/; ... })();`
  改成 `function boot(GRAPH_DATA) { "use strict"; ... }`(**保留注入占位符那行作为读取点**,见 Step 4)。
- `boot` 内所有对 `GRAPH_DATA` 的引用不变(它现在是形参)。
- **可重入清场**:`boot` 开头先清空上一轮渲染 —— `svg` 内所有子节点、sidebar 列表容器、图例、`clearReveal()` 若定义了定时器数组要先清。把渲染产出集中到已知容器,`boot` 起始 `container.innerHTML = ""`。
- **window 级监听只绑一次**:`window.addEventListener("resize"/"pointermove"/...)` 移出 `boot`、或用 `if (!window.__kgBound)` 守卫,避免每次 re-boot 叠加监听。这些 handler 引用的 state 改成读模块级 `currentState`(boot 每次刷新它)。

**Step 3: 加数据加载引导(注入优先,否则 drop)**

在 `boot` 定义之后、`</script>` 之前加引导:

```javascript
// ---- 数据引导:注入优先，否则 drag-drop ----
var INJECTED = /*__KG_JSON__*/[]/*__KG_JSON_END__*/;

function loadFromYamlText(text) {
  var data = jsyaml.load(text);          // 内联 js-yaml，见 Step 5
  boot(data.nodes || []);
}

if (INJECTED && INJECTED.length) {
  boot(INJECTED);                        // 喂法 B:服务端注入
} else if (location.search.indexOf("yaml=") !== -1) {
  var u = new URLSearchParams(location.search).get("yaml");
  fetch(u).then(function (r) { return r.text(); }).then(loadFromYamlText);  // 仅 file/同源
} else {
  showDropZone();                        // 喂法 A:等拖入
}

function showDropZone() {
  var dz = document.getElementById("dropZone");   // 一个覆盖层，HTML 里加
  dz.style.display = "flex";
  function onDrop(e) {
    e.preventDefault();
    var f = e.dataTransfer.files[0];
    var reader = new FileReader();
    reader.onload = function () { dz.style.display = "none"; loadFromYamlText(reader.result); };
    reader.readAsText(f);
  }
  dz.addEventListener("dragover", function (e) { e.preventDefault(); });
  dz.addEventListener("drop", onDrop);
}
```

> HTML body 里加一个 `<div id="dropZone">拖入 knowledge-graph.yaml</div>` 覆盖层(默认 `display:none`,居中样式)。

**Step 4: 保留注入占位符**

`INJECTED = /*__KG_JSON__*/[]/*__KG_JSON_END__*/;` 这行**必须保留占位符标记**,让 `kg_render.py` / vibecraft `/kg` 能注入。注入后它变成真实数组、`INJECTED.length>0` → 走 `boot(INJECTED)`。未注入时是 `[]` → 走 drop。

**Step 5: 内联 js-yaml**

下载 js-yaml 单文件 min 版(约 40KB)**内联**进 `<script>`(CSP 下第三方库作字面量嵌入允许)。放在 `boot` 之前。全局暴露 `jsyaml`。
- 取得方式:从已装 npm 包或可信 CDN 取 `js-yaml.min.js` 文本,整段粘进一个 `<script>` 块。**不留外链**(Artifact CSP 禁外部 host)。

**Step 6: 自验 —— drag-drop 渲染**

本地浏览器打开 `<SKILL>/assets/kg-viewer.html`(空数据 → 显示 drop 区),把 `docs/knowledge-graph.yaml` 拖进去 → 应渲染出完整图(节点/边/sidebar)。截图判读(参考 CLAUDE.md「截图自验法」):看到圆/三角/菱形/方框 + domain 分组即成功。再拖第二次(同 yaml)验证清场无叠影。

**Step 7: 自验 —— 注入渲染(喂法 B 兼容)**

Run:
```bash
python "$HOME/.claude/skills/knowledge-graph/scripts/kg_render.py" \
  --yaml docs/knowledge-graph.yaml \
  --template "$HOME/.claude/skills/knowledge-graph/assets/kg-viewer.html" \
  --out /tmp/kg_injected.html
```
浏览器开 `/tmp/kg_injected.html` → 应**直接渲染**(不显示 drop 区,因 INJECTED 非空)。证明注入路径仍工作。

**Step 8: 无提交(全局)**

---

## Task 8: vibecraft /kg 指向 skill viewer(服务端注入,零副本)

**Files:**
- Modify: `src/vibecraft/server/http.py:610-640`(`_serve_kg`,模板路径)
- Test: `tests/unit/test_serve_kg.py`(Create)

**Step 1: Write the failing test**

```python
# tests/unit/test_serve_kg.py
from vibecraft.server.http import _serve_kg

def test_serve_kg_injects_nodes_from_live_yaml():
    resp = _serve_kg()
    assert resp.status == 200
    body = resp.body.decode("utf-8") if isinstance(resp.body, bytes) else resp.body
    # 注入了真实节点(占位符被替换)
    assert '"id":"F1"' in body or '"id": "F1"' in body
    # 用的是 skill viewer(含 drop 区标记，证明模板已切换)
    assert 'id="dropZone"' in body
```

> 若 `_serve_kg` 返回类型/取 body 方式不同,按实际 Response 结构调整断言取值。

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_serve_kg.py -v`
Expected: FAIL(`id="dropZone"` 不在——仍用旧 `kg-template.html`)

**Step 3: 实现 — 改模板路径**

`_serve_kg` 里(现 `tmpl_path = repo_root / "scripts" / "_templates" / "kg-template.html"`)改成指向 skill viewer:

```python
from pathlib import Path
_SKILL_VIEWER = Path.home() / ".claude" / "skills" / "knowledge-graph" / "assets" / "kg-viewer.html"
...
tmpl_path = _SKILL_VIEWER
```

- 注入逻辑(找 `/*__KG_JSON__*/` 替换)不变——viewer 保留了同一占位符。
- **容错**:若 `_SKILL_VIEWER` 不存在(skill 没装),回退到 repo 旧模板并 log warning(过渡期兜底;删旧模板后回退分支可去掉,见 Task 9)。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_serve_kg.py -v`
Expected: PASS

**Step 5: 起真 server 自验**

```bash
# 后台起 server(参考 CLAUDE.md 重启流程)后:
curl -s http://127.0.0.1:8080/kg | grep -c 'id="dropZone"'   # 应 >=1
curl -s http://127.0.0.1:8080/kg | grep -c 'F1'              # 应 >=1(注入了节点)
```
Expected: 都 ≥1;浏览器开 `/kg` 直接渲染图(不显示 drop 区,因注入非空)。改 yaml 存盘 → 刷新页面即变(零副本、live)。

**Step 6: Commit**

```bash
git add src/vibecraft/server/http.py tests/unit/test_serve_kg.py
git commit -m "feat(kg): /kg 模板源指向全局 skill kg-viewer.html(服务端注入 live yaml,零副本)"
```

---

## Task 9: 换测试薄壳 + 删 repo 内旧模板/脚本/SKILL.md + 删 md

**Files:**
- Rewrite: `tests/unit/test_knowledge_graph.py`(→ 薄壳,subprocess 调 skill kg_validate.py)
- Delete: `docs/knowledge-graph.md`
- Delete: `scripts/_templates/kg-template.html`、`scripts/build_kg_viz.py`、`scripts/kg_query.py`(已进 skill)
- Modify: `docs/knowledge-graph.yaml` 头注释(去 md 引用)、`src/vibecraft/server/http.py`(去旧模板回退分支)

**Step 1: Write the failing test(薄壳)**

把 `tests/unit/test_knowledge_graph.py` **整体替换**为薄壳:

```python
"""知识图谱一致性 —— 薄壳:调用全局 skill 的 kg_validate.py 校验本项目 yaml。"""
import subprocess, sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
YAML = REPO / "docs" / "knowledge-graph.yaml"
VALIDATE = Path.home() / ".claude" / "skills" / "knowledge-graph" / "scripts" / "kg_validate.py"


@pytest.mark.skipif(not VALIDATE.exists(), reason="全局 knowledge-graph skill 未安装")
def test_knowledge_graph_consistency():
    r = subprocess.run(
        [sys.executable, str(VALIDATE), "--yaml", str(YAML)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, f"一致性门失败:\n{r.stderr}"
```

**Step 2: Run test to verify it passes(skill 已装)**

Run: `uv run pytest tests/unit/test_knowledge_graph.py -v`
Expected: PASS(不再有 md 一致门;走 skill validate)

**Step 3: 删除 repo 内已迁移文件**

```bash
git rm docs/knowledge-graph.md
git rm scripts/_templates/kg-template.html
git rm scripts/build_kg_viz.py scripts/kg_query.py
```
- 若 `scripts/_templates/` 空了,一并删目录。
- `scripts/kg_query.py` 有没有别处 import?先 `grep -rn "build_kg_viz\|kg_query\|kg-template\|_templates/kg" src/ scripts/ tests/ docs/` 确认无残留引用(测试 `test_kg_scripts.py` 引用了 repo 脚本——它也要改成指向 skill 或一并删,见 Step 4)。

**Step 4: 收尾 test_kg_scripts.py**

Task 1/2 建的 `tests/unit/test_kg_scripts.py` 指向 repo 脚本,repo 脚本已删 → **改成指向 skill 脚本**(subprocess `<SKILL>/scripts/kg_query.py` / `kg_render.py`,`skipif` skill 不存在),或若价值不大直接 `git rm`。推荐改成薄壳(验证 skill 脚本 + 本地 yaml 能跑),与 test_knowledge_graph 薄壳一致。

**Step 5: 清引用**

- `docs/knowledge-graph.yaml` 头注释(第 3-5 行)删 "docs/knowledge-graph.md(人读)" 表述,改"人读版已废弃;可视化见全局 skill assets/kg-viewer.html"。
- `src/vibecraft/server/http.py`:删 Task 8 加的旧模板回退分支(旧模板已不存在)。
- `grep -rn "knowledge-graph.md" .`(排除本 plan/design 文档)确保零残留。

**Step 6: Run full suite**

Run: `uv run pytest tests/unit/test_knowledge_graph.py tests/unit/test_serve_kg.py -v && uv run ruff check .`
Expected: 全 PASS + lint 干净

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor(kg): 删 md 第二源 + repo 脚本/模板(已迁全局 skill);测试改薄壳调 skill kg_validate"
```

---

## Task 10: 更新 CLAUDE.md(单一源纪律 + 图谱章 + skill 何时用)

**Files:**
- Modify: `CLAUDE.md`(「实现纪律」章加单一源纪律;「知识图谱驱动决策」章更新指针)
- Delete: `.claude/skills/knowledge-graph/SKILL.md`(项目内旧 SKILL,已迁全局)+ 目录

**Step 1: 加单一源纪律**

在 CLAUDE.md「实现纪律」章加一条(设计 §2 定稿文本):

> **设计架构时维护单一数据源**(2026-07-14):任何"同一份信息存在多处、改动需手工同步"的静态数据源设计都要严谨对待、能免则免。确需多份就:①明确唯一真理源,其余由它生成或严格对齐,绝不手改派生副本;②加一致性门校验。踩坑:`knowledge-graph.md` 曾是 yaml 人读复制品 + 独占变更日志 → 双源漂移,已废弃 md、changelog 迁回 yaml 单源。

**Step 2: 更新「知识图谱驱动决策」章**

- 图谱唯一源 = `docs/knowledge-graph.yaml`;人读 md **已废弃**;可视化 = 全局 skill `~/.claude/skills/knowledge-graph/assets/kg-viewer.html`(拖 yaml 即渲染 / vibecraft `/kg` 注入)。
- skill 现装**全局**(非项目内);「何时用」不变(设计/算法/战术/架构判断前 + 遇反常)。
- 检索用 `python ~/.claude/skills/knowledge-graph/scripts/kg_query.py --yaml docs/knowledge-graph.yaml ...`;校验用 `kg_validate.py`。

**Step 3: 删项目内旧 skill**

```bash
git rm -r .claude/skills/knowledge-graph
```
> 确认全局 `<SKILL>/SKILL.md` 已就位(Task 5)再删。

**Step 4: 验证**

Run: `grep -rn "knowledge-graph.md\|.claude/skills/knowledge-graph" CLAUDE.md`
Expected: 无过时引用(md 引用清零;skill 路径若提及应是全局 `~/.claude`)

**Step 5: Commit**

```bash
git add -A
git commit -m "docs(kg): CLAUDE.md 加单一数据源纪律 + 图谱章指向全局 skill;删项目内旧 SKILL"
```

---

## Task 11: 开源排除标记 + 收尾验证

**Files:**
- Modify: `.gitignore` 或开源打包脚本(标记 KG 数据/测试不进开源交付物)

**Step 1: 决定排除方式**

设计定:KG 图谱 + skill **不进开源交付物**(内部研发工具)。当前无开源打包流程 → **最小落地**:在开源相关文档/脚本(若有 `scripts/prepare_opensource.py` 之类)记一条排除清单:`docs/knowledge-graph.yaml`、`tests/unit/test_knowledge_graph.py`、`tests/unit/test_kg_scripts.py`、`tests/unit/test_serve_kg.py`、`/kg` 路由。若无打包流程,仅在设计文档 §9 记录待开源时处理(已记),本 Task 只加一行 TODO 到 `TASKS.md` 开源准备清单。

> 不引入 gitignore(这些文件当前要正常提交/跑测试),只标记"开源时 strip"。

**Step 2: 全量回归**

Run:
```bash
uv run pytest tests/unit/ -q
uv run ruff check . && uv run ruff format --check .
```
Expected: 全绿

**Step 3: 端到端自验清单(逐条过)**

- [ ] `python ~/.claude/skills/knowledge-graph/scripts/kg_validate.py --yaml docs/knowledge-graph.yaml` → OK
- [ ] 浏览器开 `<SKILL>/assets/kg-viewer.html` 拖 yaml → 渲染;再拖一次 → 无叠影
- [ ] vibecraft `/kg` → 注入渲染、改 yaml 刷新即变
- [ ] `git grep -l "knowledge-graph.md"`(排除 docs/plans/)→ 空
- [ ] `kg_query.py --yaml docs/knowledge-graph.yaml --search "overlord"` → 有命中

**Step 4: Commit**

```bash
git add -A
git commit -m "chore(kg): 开源排除标记 + skill 化收尾全量回归"
```

---

## 附:关键风险与注意

1. **模板重构(Task 7)是本计划最脆的一步** —— IIFE→boot 参数化后,~30 处 `GRAPH_DATA` 引用靠形参传递、window 监听去重、清场是易错点。每改一段都在浏览器拖 yaml 验一次,别攒到最后。
2. **js-yaml 内联**要取可信来源、整段嵌入,CSP 禁外链——别图省事留 `<script src>`。
3. **全局 skill 不在 repo 版本控制** —— Task 4/5/6/7 的产物在 `~/.claude`,无 commit;只有 vibecraft 侧改动进 git。若日后要把 skill 版本化(独立仓/dotfiles),是单独事项。
4. **测试薄壳 skipif** —— skill 没装时 KG 测试 skip(非 fail),因 KG 不开源、不强制他人有 skill;本机务必装好,否则一致性门静默跳过。
5. `_serve_kg` 的 Response 结构(status/body 取法)按实际代码调整 Task 8 断言。
