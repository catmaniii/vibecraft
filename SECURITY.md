# 安全策略 / Security Policy

## 报告漏洞 / Reporting a Vulnerability

**请不要用公开 Issue 报告安全问题。**

用 GitHub 的私密渠道：仓库 **Security → Report a vulnerability**
（[Private vulnerability reporting](https://github.com/catmaniii/vibecraft/security/advisories/new)）。

Please **do not** open a public issue for security problems — use GitHub's private
vulnerability reporting instead.

修复前请不要公开细节。这是个人业余项目，尽力在合理时间内回复，但不承诺 SLA。
Please don't disclose details before a fix is out. This is a hobby project — best effort,
no SLA.

## 这个项目的攻击面 / Threat model

VibeCraft 在你自己的机器上跑一个 **HTTP/WebSocket 服务器**，手机通过它连过来操作游戏。
需要特别注意的点：

- **房间 token 就是访问凭据**。任何拿到 URL（含 `?room=<token>`）的人都能给你的 bot 下指令、
  看你的游戏画面。别把带 token 的链接发到公开场合；换 token 就是换一个 `-Token` 参数重启。
- **把服务暴露到公网**（隧道 / 反代 / 端口转发）意味着任何人都能尝试连接。仓库里的
  `deploy/` 脚本只是把它接到你自己的服务器，不含任何鉴权强化。自己评估风险。
- **admin 面板**有独立 token（`-AdminToken`，≥8 位），它能改服务端设置，泄漏后果比房间 token 更大。
- **`/rg` 路由（推理图谱查看器）是刻意无鉴权的**，公网可见 —— 里面是研发过程的认知记录，
  按"非敏感"处理。见 `src/vibecraft/server/http.py` 的 SECURITY 注释。
- **LLM API key** 走环境变量或 `config/llm.yaml`（后者已 gitignore）。玩家说的话会发给你配置的
  LLM 服务商，用之前确认你能接受这一点。

## 别提交这些 / Never commit

`.secrets/`、`*.pem`、`config/llm.yaml`、任何真实 token / key / 私钥。
仓库的 `.gitignore` 已经覆盖它们，pre-commit 也挂了 secret 扫描 —— 但**最终防线是你自己**：
提交前扫一眼 `git diff --staged`。
