# dev / prod 环境分离（**未来考虑，暂不实施**）

> 用户 2026-07-05 提出，记录待未来实施。触发痛点：dev 边改边跑同一个文件夹 →
> 改代码时不敢重启 server（半成品会被加载 / 提交），dev 自测和玩家测试抢同一份代码。

## 硬约束
**prod server 必须还在用户的 Windows PC 上跑** —— 它需要 SC2 客户端 + `SC2PATH` + Windows。
香港 VPS 是 Linux，只当**前门（nginx 反代）+ TURN 中继（coturn）**，server 搬不过去。
所以"生产环境" = **同一台 PC 上一个独立文件夹里的稳定版实例**，不是另一台机器。

## 推荐方案：git worktree（方案 A）
```
D:\code\claudecode\vibecraft         ← dev（这个，随便改 + 跑 dev 自测）
D:\code\vibecraft-prod   (git worktree add ../vibecraft-prod <稳定tag>)
                                     ← prod（稳定版，给用户/朋友玩，纹丝不动）
```
- 共享同一个 `.git`，切版本一条 `git checkout <tag>`，不用重新 clone。
- 备选：独立 `git clone`（彻底独立但多占磁盘 + 两份 .git）。
- **淘汰**：单文件夹 + 分支 —— 没隔离工作区，改 main 照样影响在跑的 server，治不了痛点。

## 关键摩擦：venv（**落地前必须解决**）
手动装的 **torch / funasr / asr（语音 ASR）不在 `uv.lock` 里** —— prod 新 venv `uv sync`
会漏掉它们（sync 还会把它们当多余删掉）→ **语音直接废**。两条路：
- **短期**：prod 建自己的 venv，跑一个 setup 脚本（`uv sync` + 手动 `uv pip install` 那几个），一次性 + 依赖变了再跑。
- **长期（更该做）**：把 funasr/torch 那几个 **pin 进 pyproject/lock**，dev/prod 都一条 `uv sync` 复现。
  当初没 pin 大概是 CUDA / 平台特定版本 —— 落地前先确认能不能锁版本。

## 部署 / 开发流
- **端口**：prod 8080（隧道 `pc-tunnel.ps1` 指它，不动），dev 用 8081 或干脆非实时自测不起 server → 互不抢。
- **prod 需要**：独立 venv + 拷一份 `.secrets/`（admin token / TURN 凭证，gitignore 的）+ config。
- **发布**：dev 这边 commit + 打 tag（如 `v0.3`）→ `cd ../vibecraft-prod; git fetch; git checkout v0.3;`
  （依赖变了才 `uv sync`）`; 重启 prod server`。隧道不动。
- **日常**：dev 文件夹随便改，prod 那个稳定跑。

## 落地时机
需要一个**稳定 commit 当锚点** —— 计划在某批改动 commit + 打 tag 后，用那个 tag 建 prod worktree。

## 待用户拍板的 3 个问题（实施时问）
1. 方案 A（worktree）确认？
2. venv：短期（prod 自己 venv + setup 脚本）还是长期（funasr/torch pin 进 lock）先做？
3. 具体端口分配（prod 8080 / dev 8081）确认？
