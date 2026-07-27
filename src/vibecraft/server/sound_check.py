"""SC2 全局音频配置检查（#522）。

多人局要让两个玩家**同时**听到各自 SC2 实例的声音，前提是 SC2 开了"后台播放"——
即全局 `Variables.txt` 里 ``soundglobal=true``（否则失焦的 SC2 窗口被引擎内部静音，
只有当前聚焦窗口出声）。这个前提开源后新用户不会知道，所以 server 启动时检查一下，
缺失就给**清晰提示**（仅警告，不阻塞启动——单人局 / 不关心音频时无害）。

设计：纯函数 `check_sound_global()` 返回结构化状态，路径可注入（单测用 tmp_path），
默认从 Windows Documents（含 OneDrive 重定向）推断候选路径。
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass


@dataclass(frozen=True)
class SoundGlobalStatus:
    """soundglobal 检查结果。"""

    enabled: bool
    """soundglobal 是否为 true。"""

    found: bool
    """Variables.txt 是否找到（找到但无该键 → enabled=False, found=True）。"""

    path: pathlib.Path | None
    """命中的 Variables.txt 路径（未找到为 None）。"""

    raw_value: str | None
    """找到的 soundglobal 原始值（调试用；键不存在为 None）。"""


def _candidate_variables_paths() -> list[pathlib.Path]:
    """SC2 ``Variables.txt`` 候选路径列表（Windows Documents，含 OneDrive 重定向）。

    SC2 把全局配置写在 ``<Documents>/StarCraft II/Variables.txt``。Documents 可能被
    OneDrive 重定向，所以同时探 USERPROFILE/home 下的 Documents 与 OneDrive/Documents。
    """
    bases: list[pathlib.Path] = []
    up = os.environ.get("USERPROFILE")
    if up:
        bases.append(pathlib.Path(up))
    bases.append(pathlib.Path.home())
    # Windows 的 os.environ 键被转大写 → 用大写查 OneDrive 重定向根
    one_drive = os.environ.get("ONEDRIVE") or os.environ.get("ONEDRIVECONSUMER")
    if one_drive:
        bases.append(pathlib.Path(one_drive))

    out: list[pathlib.Path] = []
    seen: set[str] = set()
    # Documents 直挂 base，或经 OneDrive 重定向（base/OneDrive/Documents/...）
    doc_roots: tuple[tuple[str, ...], ...] = (("Documents",), ("OneDrive", "Documents"))
    for base in bases:
        for parts in doc_roots:
            p = base.joinpath(*parts, "StarCraft II", "Variables.txt")
            key = str(p).lower()
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def _parse_sound_global(text: str) -> str | None:
    """从 Variables.txt 文本里取 soundglobal 的值（key=value 行格式，键大小写不敏感）。

    找不到该键返回 None。
    """
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        if key.strip().lower() == "soundglobal":
            return value.strip()
    return None


def check_sound_global(paths: list[pathlib.Path] | None = None) -> SoundGlobalStatus:
    """检查 SC2 全局 soundglobal 配置。

    paths：候选 Variables.txt 路径（None = 用 `_candidate_variables_paths()` 推断）。
    按顺序取**第一个存在且可读**的文件判定；都不存在 → found=False。
    """
    candidates = paths if paths is not None else _candidate_variables_paths()
    for p in candidates:
        try:
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        value = _parse_sound_global(text)
        if value is not None:
            return SoundGlobalStatus(
                enabled=value.lower() == "true",
                found=True,
                path=p,
                raw_value=value,
            )
        # 文件在但没写 soundglobal 键 → SC2 默认未开后台播放 → 当未启用
        return SoundGlobalStatus(enabled=False, found=True, path=p, raw_value=None)
    return SoundGlobalStatus(enabled=False, found=False, path=None, raw_value=None)


# 启动日志用的人类可读提示（缺失 / 未启用时打给用户）。
HINT_NOT_ENABLED = (
    "多人局两玩家不能同时听到声音：SC2 后台播放未开启。"
    "进 SC2 → 选项 → 声音 → 勾选“在后台播放音频”(写入 Variables.txt 的 "
    "soundglobal=true)，重开 SC2 后生效。"
)
HINT_NOT_FOUND = (
    "未找到 SC2 Variables.txt（至少进过一次游戏才会生成）。"
    "多人音频需 SC2 → 选项 → 声音 → 开启后台播放(soundglobal=true)。"
)
