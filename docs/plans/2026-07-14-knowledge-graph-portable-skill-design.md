# 知识图谱可移植 Skill 化 · 设计文档

> 2026-07-14。把知识图谱(含前端可视化)从 vibecraft 内嵌形态,重构成一个**可移植的全局 skill**,
> 供 vibecraft 及其它项目共用。经 `/superpowers:brainstorming` 收敛。**本文是设计真理源;实施计划由
> writing-plans 后续产出。**

---

## 1. 背景与目标

知识图谱当前是 vibecraft **内嵌**形态,散落四处:

| 现状文件 | 角色 |
|---|---|
| `docs/knowledge-graph.yaml` | 机读单一源(DAG 节点) |
| `docs/knowledge-graph.md` | 人读版(由 yaml 生成,**含 md 独有的变更日志**) |
| `scripts/kg_query.py` | AI 检索(把大图裁成小上下文) |
| `scripts/build_kg_viz.py` | yaml → HTML 注入渲染 |
| `scripts/_templates/kg-template.html` | 可视化模板 |
| `tests/unit/test_knowledge_graph.py` | 一致性门 |
| `.claude/skills/knowledge-graph/SKILL.md` | 方法论(仅 SKILL.md,脚本/模板/数据都在外面) |

**问题**:方法论(通用)、引擎(通用)、模板(通用)和**数据**(项目私有)混在一起,别的项目没法直接复用;
`knowledge-graph.md` 是 yaml 的人读复制品,是**第二份需要同步维护的静态源**(漂移风险)。

**目标**:
1. 沉淀"单一数据源"纪律,消除 md 这个重复源。
2. 把**通用部分**(prompt + 脚本 + 前端模板 + 数据格式)抽成**全局 skill**,可给任意项目用。
3. **数据留项目内**,前端由项目按需决定呈现;skill 保证"纯 yaml 也能开箱可视化"的下限。

**非目标**:不引入数据库;不改动图谱内容/节点;不动 vibecraft 的 bot 逻辑。

---

## 2. 单一数据源纪律(写进 CLAUDE.md)

新增一条实现纪律(归入「实现纪律」章):

> **设计架构时维护单一数据源**(2026-07-14):任何"同一份信息存在多处、改动需要手工同步"的静态数据源
> 设计都要**严谨对待、能免则免**。若确有必要存多份(如人读版/机读版/索引缓存),必须:①明确指定**唯一
> 真理源**,其余全部**由它生成或严格对齐**,绝不手改派生副本导致漂移;②加一致性门(单测/CI)校验副本与
> 源一致。**能用"生成"消灭的重复源,就别用"手工同步"维持**。踩过的坑:`knowledge-graph.md` 曾是 yaml 的
> 人读复制品 + 独占变更日志 → 双源漂移,本次废弃 md、changelog 迁回 yaml 单源(见
> `docs/plans/2026-07-14-knowledge-graph-portable-skill-design.md`)。

并在「知识图谱驱动决策」章补一句:图谱**唯一源 = `docs/knowledge-graph.yaml`**;可视化 = 引用全局 skill 的
统一前端工具(拖 yaml 即渲染),不再有 `knowledge-graph.md` 人读副本。

---

## 3. Skill 组织(全局为家,纯通用件)

skill 装**全局** `~/.claude/skills/knowledge-graph/`(像 pptx skill 那样自带 scripts/assets),
**只含通用件,零项目数据**:

```
~/.claude/skills/knowledge-graph/
├── SKILL.md                    # 方法论 + prompt(何时用/三件事/维护纪律)
├── SCHEMA.md                   # 数据格式权威定义(见 §5)
├── scripts/
│   ├── kg_query.py             # AI 检索(裁子图);yaml 路径走 CLI 参数,零 vibecraft/sc2 依赖
│   ├── kg_validate.py          # 一致性门(DAG/deps/provenance/不确定性传播)
│   └── kg_render.py            # yaml + 模板 → 注入 HTML(vibecraft /kg 用它;yaml/模板/out 走参数)
└── assets/
    └── kg-viewer.html          # 统一可视化工具(自包含;拖 yaml 或注入两种喂法,见 §4)
```

**全局为家、无 CI 自足要求(用户 2026-07-14 拍板)**:KG 图谱 + 本 skill **不进 vibecraft 开源交付物**
(是内部研发工具,不随开源分发)→ 评审担心的"开源 cloner 没全局 skill → CI 挂"**不成立**,无需在 repo 里
保留 CI 自足副本。全局 `~/.claude/skills/knowledge-graph/` 即**唯一权威源**;vibecraft 本机已装全局 skill,
其 KG 一致性测试薄壳直接调用 skill 的 `kg_validate.py`。开源时把 `docs/knowledge-graph.*` / KG 相关脚本/测试
一并排除(见 §9 收尾)。

**移植性核实(评审纠正)**:`kg_query.py` / `build_kg_viz.py` import 确为纯 argparse/pathlib/yaml,**零
vibecraft/sc2/sharpy** ✓;但两者都**硬编码了 repo 相对路径**(`parents[1]/docs/knowledge-graph.yaml`
等)——搬进 skill 后 `parents[1]` 指向 skill 目录、开箱即断。**移动不是零改动**:必须把 yaml/模板/输出
路径全部改成 **CLI 参数/环境变量**(skill 版不假定任何 repo 布局,vibecraft 侧传本地路径)。
`kg_validate.py` 从现 `test_knowledge_graph.py` 抽核心校验(去 pytest 壳,可独立跑)。

**skill 不含**:任何 `knowledge-graph.yaml`(数据)、任何项目专属 server 路由。

---

## 4. 前端可视化架构(一份 viewer,两种喂数据法)

核心:**skill 提供一份自包含的统一可视化工具 `kg-viewer.html`**,同一份模板支持**两种喂数据法**,
渲染逻辑复用同一套(评审确认:数据契约不变,`nodes` 结构 python `json.dumps` 与客户端 `js-yaml`
解析**完全一致**,渲染代码不用改)。

### 喂法 A · drag-drop 客户端解析(通用下限,零配置)
- `kg-viewer.html` 单文件、纯客户端:内联 JS YAML 解析器(js-yaml)+ 图渲染逻辑 + **drag-drop 处理器**。
- 任何项目——哪怕只有一个 `knowledge-graph.yaml`、别的什么都没有——浏览器打开它、把 yaml 拖上去,
  **立即可视化**。零服务器、零构建、离线可用。
- `?yaml=<URL>` fetch 模式**仅限 `file://` 或同源服务端**(如 vibecraft `/kg` 同源):**已发布 Artifact
  的 CSP 会拦跨 host fetch**,那种场景只能用 drag-drop 或喂法 B。

### 喂法 B · 服务端注入(项目要"实时/公网"时)
- 服务端读 live yaml + 读同一份 `kg-viewer.html` 模板 → 把 `json.dumps(nodes)` 注入模板的占位符
  (`/*__KG_JSON__*/`)→ serve 成品。**零副本、零漂移、改 yaml 刷新即生效**。这就是 `kg_render.py` 的用途
  (也可服务端内联同等逻辑)。
- 模板同时保留**注入占位符**(喂法 B)和 **drop 区**(喂法 A):有注入数据就直接渲染,没有就显示拖拽区,
  两条路都汇入同一个可重入 `init(nodes)`。

**实现增量(评审纠正:是中等重构、非"直接")**:现模板 `GRAPH_DATA` 在 IIFE **顶层被立即消费**(建 index /
computeLayout / 建 SVG / 建 sidebar,~1285 行、20+ 处直接引用)。改造要:①把整段立即执行逻辑**包成可重入
`init(nodes)`**;②drag-drop **再次拖入时清空上一张图**(SVG 层、事件、`revealTimers` 定时器);③内联
js-yaml + drop handler。属中等重构,不是加两个函数就完。

### vibecraft 具体落地(用户 2026-07-14 拍板:喂法 B 服务端注入)
- 保留 `/kg` server 路由(手机/公网实时看),沿用**现有服务端实时注入**(`http.py::_serve_kg` 每请求读
  live yaml + 模板注入),**只把注入用的模板源从 `scripts/_templates/kg-template.html` 换成 skill 的
  `kg-viewer.html`**。
- **零副本、零新端点、零漂移**:不拷 static 副本、不加 `/api/kg-yaml`。vibecraft server 跑在用户 PC(已装
  全局 skill),运行时能读 `~/.claude/.../kg-viewer.html`;VPS 只是隧道中转、不跑 server,不涉及 skill 依赖。
- 与其它项目**共用 skill 同一份前端源**,vibecraft 只多了"服务端注入 live yaml + 手机访问外壳"。
- (原设计的 static 副本 + `/api/kg-yaml` 方案**弃用**——它多引一个受控派生副本 + 一个端点,还要配 regen +
  一致性检查才不违反单一源;服务端注入更省。)

---

## 5. SCHEMA.md(数据格式收纳)

新建 `SCHEMA.md` 收纳所有"格式/呈现约定",让格式定义与方法论(SKILL.md)分离:

- 节点字段定义(id/domain/kind/statement/deps/evidence/status/provenance/by/repro/title)。
- `kind` → 形状/颜色映射:fact=蓝 `#cfe3ff` / inference=黄 `#fff3c4` / decision=绿 `#c6f6d5` /
  open=灰虚线 `#e2e8f0`(从 md Mermaid classDef 提炼)。
- status 图例:verified ✅ / unverified ⚠️ / assumed 🔶 / pending ❓。
- `level(n)` 渲染时依赖深度算出、**不存 yaml**(避免漂移)的约定。
- 顶层结构:`version` / `nodes` / **`changelog`**(见 §6)。**暂不加** `sources` schema 字段(多文件是 loader
  的容错能力、还没采用 → 未用先造属 YAGNI,等真拆分再引入;见 §7)。
- 一致性门规则清单(供 `kg_validate.py` 实现)。

**SCHEMA.md 与 SKILL.md 的关系**:SKILL.md 里放一句**强指引**——"数据格式权威见 SCHEMA.md,动任何字段/新增
kind 前必读它",避免 AI 只读 SKILL.md 漏掉格式约定。

---

## 6. 废弃 md + changelog 迁回 yaml(消灭第二源)

**删除 `docs/knowledge-graph.md`**。删前**抢救它独占的变更日志**——迁进 yaml 顶层新增 `changelog:` 段:

```yaml
version: 4
# source_md 字段删除(不再有 md)
nodes:
  - id: F1
    ...
changelog:                         # ← 新增顶层段,承接原 md 文末「变更日志」
  - date: 2026-07-12
    change: "建图。domain nydus-landing 录入 F1-F11 / J1-J7 / D1-D4 / U1-U2。"
    review: "D1 由『离基地中心 25 格』修正为『离高地边缘 ~10 格、顺悬崖下坡』(J6 冲击旧表述)。"
  - date: 2026-07-12
    change: "去分层 + 通用化:layer 字段删除改软标签 kind;文件 nydus-* → knowledge-graph.*,加 domain。"
  # ... 迁移 md 现有 4 条变更日志全部 ...
```

迁移映射(md 文末 4 条 → yaml `changelog` 4 条,逐字搬):2026-07-12 建图、2026-07-12 去分层通用化、
2026-07-12 U1 定解、2026-07-13 死神 harass 微操回退。**注意**:md 每条含多级子项(复审动作/①②),`change`
用 **YAML 多行块标量**(`|`)承接、别硬压一行;迁完**人工核对长文本未截断**。

**删 md 的信息抢救已核实充分**(评审):md 独有的 Mermaid 依赖图本身是"第二套手工维护的节点+边",删它
**正好符合**单一源纪律(不是损失);section 0 方法论摘要与 SKILL.md 重复;逐节点散文与 yaml `statement` 重复。
唯一要确保进 SCHEMA.md 的 classDef 颜色 + status emoji 图例,§5 已列。

改 `test_knowledge_graph.py`(→ 迁为 skill `kg_validate.py` + 项目薄壳):删掉"yaml↔md 节点 id 一致"这条门
(md 没了),新增"yaml 顶层含 `changelog` 且每条有 date/change"。其余门(DAG/deps/provenance/不确定性传播)不变。

---

## 7. 扩展与开放担忧(架构复审触发器)

**记录用户 2026-07-14 提出的增长担忧,作为显式复审触发器:**

> **单 yaml 增长复审触发器**:当前"扁平单 yaml + `kg_query` 检索"在**手工策展的推理节点**规模下(增长慢、
> 上限约几千节点)完全够用,可 diff / 可手改 / 单一源 / 可移植,不上数据库是**当前正确取舍**。
> **但若某项目 KG 增长远超预期**(节点进入**数万级** / 单文件加载明显变慢 / 多 agent 并发写冲突频发)
> **→ 必须回来重新审视本架构**,按此顺序升级、能不动就不动:
> 1. **先拆多文件**:按 `domain` 拆成 `docs/kg/<domain>.yaml`,loader glob 合并。**从一开始就把 loader
>    写成"单文件 or `kg/` 目录 glob 都吃"**(纯 loader 容错、零成本预留);**但暂不加 `sources` schema 字段/门**
>    (未用先造属 YAGNI,真拆分时再引入)。
> 2. 仍不够:**从 yaml 派生只读索引缓存**(sqlite/json),**yaml 始终是唯一源**,缓存是派生产物、可重建。
> 3. 数据库是**最后手段**——它会赔掉 diff/手改/单源/可移植这几个当前核心优点,不到量级不上。

另记一条**未决**(不阻塞本次实施,记录待定):统一 viewer 后续可加"多项目 yaml 切换/对比"能力(一个 viewer
拖入多个项目的 yaml 分标签看),目前 YAGNI,有需求再说。

---

## 8. 文件迁移映射(总表)

| 现位置 | 去向 | 动作 |
|---|---|---|
| `.claude/skills/knowledge-graph/SKILL.md` | 全局 `~/.claude/skills/knowledge-graph/SKILL.md` | 移到全局 + 补"何时用/两种喂法/SCHEMA 强指引" |
| (新建) | skill `SCHEMA.md` | 新建,收纳格式/呈现约定(§5) |
| `scripts/kg_query.py` | skill `scripts/kg_query.py` | 移动 + **yaml 路径改 CLI 参数**(去硬编码 `parents[1]`) |
| `scripts/build_kg_viz.py` | skill `scripts/kg_render.py` | 移动更名 + **yaml/模板/out 改参数**;是 /kg 注入引擎(非可选) |
| `scripts/_templates/kg-template.html` | skill `assets/kg-viewer.html` | **中等重构**:立即执行→可重入 `init(nodes)` + 内联 js-yaml + drop handler + 再拖清场;保留注入占位符 |
| `tests/unit/test_knowledge_graph.py` | skill `scripts/kg_validate.py` + 项目薄壳测试 | 抽核心校验为独立门(全局已装,薄壳直接调) |
| `docs/knowledge-graph.yaml` | **留原地**(项目数据) | 加 `changelog:` 段、删 `source_md` |
| `docs/knowledge-graph.md` | **删除** | changelog 迁入 yaml 后删 |
| vibecraft `/kg`(`http.py::_serve_kg`) | 保留 | **仅把注入模板源指向 skill `kg-viewer.html`**;零副本、无新端点 |
| CLAUDE.md | 改 | 加单一源纪律 + 更新图谱章指针 + skill 何时用 |

---

## 9. 实施步骤概览(细化交 writing-plans)

1. yaml 加 `changelog:` 段(迁 md 4 条、`change` 用块标量、核对不截断)、删 `source_md`;跑校验。
2. 建全局 skill 目录骨架:移 SKILL.md、新建 SCHEMA.md、移 3 脚本、移模板。
3. **脚本去硬编码路径**:`kg_query.py`/`kg_render.py` yaml/模板/out 全走 CLI 参数,skill 版不假定 repo 布局。
4. 改造 `kg-viewer.html`(中等重构):立即执行逻辑→可重入 `init(nodes)`;内联 js-yaml + drag-drop + 再拖清场;
   保留注入占位符走喂法 B;`?yaml=` 标注仅 file/同源。本地拖 vibecraft yaml 自验渲染 + 注入路径自验。
5. 抽 `kg_validate.py`(去 pytest 壳),项目侧薄壳测试调用它;删 md↔yaml 一致门、加 changelog 门。
6. vibecraft `/kg`:把 `_serve_kg` 的模板源换成 skill `kg-viewer.html`(仍服务端注入 live yaml);起 server
   看图自验(零副本、改 yaml 刷新即生效)。
7. 删 `docs/knowledge-graph.md`;全局搜引用(SKILL.md/CLAUDE.md/yaml 头注释/`source_md`/脚本注释)改指向。
8. CLAUDE.md 加单一源纪律 + 更新图谱章 + skill 何时用。
9. **开源排除**:把 `docs/knowledge-graph.*` / KG 脚本 / KG 测试标记为**不进开源交付物**(内部研发工具);
   落地方式(gitignore 分支 / 发布时 strip / 单独目录)由 writing-plans 定。
10. 存 memory:单一源纪律 + 单 yaml 增长复审触发器(已存,见 `arch_single_data_source_discipline` /
    `arch_kg_single_yaml_growth_trigger`)。

**验收**:①纯 yaml 项目拖 `kg-viewer.html` 能渲染(喂法 A);②vibecraft `/kg` 服务端注入能看图、改 yaml 刷新
即生效(喂法 B);③`kg_validate.py` 全绿;④仓库无 `knowledge-graph.md` 残留引用;⑤`kg_query.py` 传本地
yaml 路径检索正常。

**验收**:①纯 yaml 项目拖 `kg-viewer.html` 能渲染;②vibecraft `/kg` 能看图;③`kg_validate.py` 全绿;
④仓库无 `knowledge-graph.md` 残留引用;⑤`kg_query.py` 在 vibecraft yaml 上检索正常。
