#!/usr/bin/env python3
"""⚠️ 已作废(2026-07-27):本仓库直接开源,不再走"私有仓 → 公开仓脱敏投影"这套两仓模型。

原模型:vibecraft(私有,真理源) → sanitize() → openVibeCraft(公开)。
现模型:**vibecraft 本身就是公开仓**——开源前已就地做完资产合规(SC2 图标/地图不入库)、脱敏
(VPS IP、Tailscale 主机名换占位)、并重建了 git 历史(单根提交)。所以不存在"另一个要同步过去的
公开仓"了,本脚本没有目标仓可写。

保留它只为存档参考(里面的脱敏规则清单还有价值)。**不要再跑它。**

────────────────────────────────────────────────────────────────────────────

原 docstring:

把私有 vibecraft 仓库脱敏同步到公开 openVibeCraft 仓库（可复现的"脱敏投影"）。

模型：vibecraft = 唯一真理源；openVibeCraft = sanitize(vibecraft)，每次由本脚本重新生成。
流程：git archive 当前 HEAD（只含已跟踪文件）→ 删内部-only 文档 → 脱敏机密串 → 镜像进目标仓库
工作树（保留目标 .git，历史延续）→ 终态敏感扫描（有残留即非零退出、不写）。**不自动 commit/push**，
留给人 review `git diff` 后手动提交。

脱敏值**不硬编码进本脚本**（否则真实 IP 又进了仓库）：从 `.secrets/vibecraft-turn.env`（gitignore）
+ `git config user.email` + 仓库绝对路径动态推导。

用法：
    python scripts/sync_to_opensource.py [--target <openVibeCraft路径>] [--dry-run]
默认 target = C:/Users/<user>/openVibeCraft
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# 内部-only：不进公开仓
_REMOVE_FILES = [
    "CLAUDE.md",
    "TASKS.md",
    "CLAUDE.md.bak",
    # 含开源运维任务（轮换密钥/转 public 等）的内部规划文档，建好功能后再发干净版
    "docs/plans/2026-06-27-i18n-localization-design.md",
]
_REMOVE_GLOBS = ["docs/plans/*-implementation-plan.md"]
_REMOVE_DIRS = ["docs/plans/research", ".claude", "vibecraft-ops"]
# config/servers/ 下非 .example 的个人 server 配置不进公开仓
_REMOVE_SERVERS_NONEXAMPLE = True

_TEXT_EXT = {
    ".py",
    ".ps1",
    ".sh",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".js",
    ".ts",
    ".vue",
    ".json",
    ".cfg",
    ".ini",
    ".html",
    ".css",
}


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def _build_scrub_map() -> list[tuple[str, str]]:
    """从 .secrets + git config + 仓库路径动态构造 (真实值 -> 占位符) 列表。"""
    env = _read_env(_REPO / ".secrets" / "vibecraft-turn.env")
    repl: list[tuple[str, str]] = []

    ip = env.get("VPS_PUBLIC_IP", "")
    if ip:
        repl.append((f"root@{ip}", "<USER>@<VPS_IP>"))
        repl.append((ip, "<VPS_IP>"))
    if env.get("VPS_PRIVATE_IP"):
        repl.append((env["VPS_PRIVATE_IP"], "<VPS_PRIVATE_IP>"))
    # pem 文件名（从 SSH_KEY 路径取 basename）
    key = env.get("SSH_KEY", "")
    if key:
        pem = Path(key.replace("\\", "/")).name
        if pem and pem != "<your-key>.pem":
            repl.append((pem, "your-vps-key.pem"))

    # 提交者 gmail -> noreply
    try:
        email = subprocess.check_output(
            ["git", "-C", str(_REPO), "config", "user.email"], text=True
        ).strip()
        if email and "noreply" not in email:
            repl.append((email, "catmaniii@users.noreply.github.com"))
    except Exception:
        pass

    # 仓库绝对路径 -> 占位
    repl.append((str(_REPO), "<repo-root>"))
    repl.append((str(_REPO).replace("/", "\\"), "<repo-root>"))
    return repl


# 正则脱敏（targeted，不依赖具体值）：tailscale magic-dns 主机名
_REGEX_SCRUB = [
    (re.compile(r"[a-z0-9-]+\.tail[a-z0-9]+\.ts\.net"), "<your-host>.<tailnet>.ts.net"),
]


# 终态扫描：这些真实值若残留则中止（从 .secrets 动态取，故只放正则兜底 + 由调用处补充）
def _final_scan(root: Path, literals: list[str]) -> list[str]:
    hits: list[str] = []
    pats = [re.compile(re.escape(s)) for s in literals if s and not s.startswith("<")]
    for p in root.rglob("*"):
        if ".git" in p.parts or not p.is_file():
            continue
        if p.suffix.lower() not in _TEXT_EXT:
            continue
        try:
            s = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in pats:
            if pat.search(s):
                hits.append(f"{p.relative_to(root)} :: {pat.pattern}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    default_target = Path.home() / "openVibeCraft"
    ap.add_argument("--target", type=Path, default=default_target)
    ap.add_argument("--dry-run", action="store_true", help="只生成 + 扫描，不写入目标")
    args = ap.parse_args()

    repl = _build_scrub_map()
    # 终态扫描要拦的真实值（取 .secrets 的实值 + gmail），仅用于校验、不打印
    env = _read_env(_REPO / ".secrets" / "vibecraft-turn.env")
    scan_literals = [env.get("VPS_PUBLIC_IP", ""), env.get("VPS_PRIVATE_IP", "")]
    for a, _b in repl:
        if "@" in a and "noreply" not in a:  # gmail
            scan_literals.append(a)

    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "stage"
        stage.mkdir()
        # 1. git archive HEAD（只含已跟踪文件）
        archive = Path(td) / "head.tar"
        with archive.open("wb") as f:
            subprocess.check_call(["git", "-C", str(_REPO), "archive", "HEAD"], stdout=f)
        with tarfile.open(archive) as t:
            t.extractall(stage)

        # 2. 删内部-only
        removed: list[str] = []
        for rel in _REMOVE_FILES:
            p = stage / rel
            if p.exists():
                p.unlink()
                removed.append(rel)
        for g in _REMOVE_GLOBS:
            for p in stage.glob(g):
                p.unlink()
                removed.append(str(p.relative_to(stage)))
        for d in _REMOVE_DIRS:
            p = stage / d
            if p.exists():
                shutil.rmtree(p)
                removed.append(d + "/")
        if _REMOVE_SERVERS_NONEXAMPLE:
            sdir = stage / "config" / "servers"
            if sdir.exists():
                for p in sdir.iterdir():
                    if p.is_file() and not p.name.endswith(".example"):
                        p.unlink()
                        removed.append(str(p.relative_to(stage)))

        # 2.5 硬闸门：私有运营工具 vibecraft-ops 绝不能进开源投影（主仓已 gitignore，
        # git archive 本就看不到它；这里再兜一道——万一有人强制 git add 了，命中即中止，绝不泄漏）。
        ops_leak = [
            str(p.relative_to(stage))
            for p in stage.rglob("*")
            if "vibecraft-ops" in p.relative_to(stage).parts
        ]
        if ops_leak:
            print("[sync] [X] 检测到 vibecraft-ops 私有运营工具进入投影，已中止（绝不泄漏）：")
            for h in ops_leak[:10]:
                print("   ", h)
            return 3

        # 3. 脱敏
        scrub_count = 0
        for p in stage.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in _TEXT_EXT:
                continue
            try:
                s = p.read_text(encoding="utf-8")
            except Exception:
                continue
            orig = s
            for a, b in repl:
                if a and a in s:
                    scrub_count += s.count(a)
                    s = s.replace(a, b)
            for rx, b in _REGEX_SCRUB:
                s, n = rx.subn(b, s)
                scrub_count += n
            if s != orig:
                p.write_text(s, encoding="utf-8", newline="")

        # 4. 终态扫描
        hits = _final_scan(stage, scan_literals)
        print(f"[sync] 删除内部文档 {len(removed)} 项；脱敏替换 {scrub_count} 处。")
        if hits:
            print("[sync] [X] 终态扫描发现敏感残留，已中止（不写入目标）：")
            for h in hits[:20]:
                print("   ", h)
            return 2
        print("[sync] [OK] 终态敏感扫描 0 命中。")

        if args.dry_run:
            print(
                f"[sync] --dry-run：未写入。stage 内容文件数 = "
                f"{sum(1 for _ in stage.rglob('*') if _.is_file())}"
            )
            return 0

        # 5. 镜像进目标（保留 .git；清空其余后拷入）
        target = args.target
        if not (target / ".git").exists():
            print(f"[sync] [X] 目标不是 git 仓库：{target}（请先 clone openVibeCraft）")
            return 3
        for item in target.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in stage.iterdir():
            dst = target / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        print(f"[sync] [OK] 已镜像到 {target}（.git 保留）。")
        print("[sync] 下一步：cd 到目标仓库，git add -A && git diff --cached 审阅 → commit/push。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
