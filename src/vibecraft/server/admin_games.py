"""admin_games.py：扫描 logs/ 下真人对局元数据（纯函数，方便单测）。

白名单规则（评审 M1）：
  - 只接受 match_* 前缀目录（真人局 game_id 恒为 match_<ts>_p<slot>）
  - 排除 game_*（build_acceptance 沙盒）、eff_*、e2e_*、*selftest*、*proof* 等
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

_DEFAULT_LOGS_DIR = pathlib.Path("logs")

# 对局记录显示玩家**用过的所有 build（active_recipe）序列** —— 按切换顺序 + 切换时间。
# 玩家会在一局里切多次 build（snapshot 的 active_recipe 随之变）；只读 game_start 首行只能显示开局
# 默认值。把整局 snapshot 切成"连续同名段"，每段 = (build, 起始时间, 持续时长)。
# 过滤：持续 < _MIN_SEGMENT_S 的段不算（开局默认值常几秒就被玩家切掉 = 短段，正好被滤掉）。
#
# ★关键不变量（2026-06-20 用户："自动切要玩家确认，确认了才算"）：本序列只反映**玩家确认过的**
#   build 切换，因为它读的是 snapshot.active_recipe，而 **active_recipe 只在玩家确认/下令时变**：
#     - 玩家 voice/面板下 build 指令 / 确认开局完成的 doctrine 推荐 → director 发 `strategy.set`
#       （events.jsonl，directive_id 非空）→ **真正改 active_recipe** → 进本序列。
#     - bot 的开局完成 doctrine **推荐**走 `strategy.auto_switch`（directive_id=None）→ **只发 toast、
#       不改 active_recipe** → 玩家不确认就**永不进**本序列。
#   实测佐证：740s 的 bc_rush 局有 auto_switch 推荐但玩家没确认 → 序列只有 bc_rush，doctrine 不出现。
#   **维护警告**：若将来给生产加"自动切 build 且直接改 active_recipe（不经玩家确认）"的路径，本序列会
#   错误地把它当成玩家选择显示。届时必须改成只认 events.jsonl 的 `strategy.set`（confirmed 动作），
#   而不是裸读 snapshot.active_recipe。
_MIN_SEGMENT_S = 10.0
# 单文件正向扫描行数上限（安全阀；telemetry.jsonl 仅 game_start+snapshot+结局，长局也就几百行）。
_MAX_SCAN_LINES = 8000

# 去掉 _p<slot> 后缀 → match key（两人局 match_X_p0 / _p1 归同一局）。
_P_SUFFIX = re.compile(r"_p\d+$")


def _match_key(dir_name: str) -> str:
    return _P_SUFFIX.sub("", dir_name)


# 末行反向 seek 最大读取块大小（覆盖绝大多数 JSONL 末行）
_TAIL_CHUNK = 8192

# 排除子串关键词（小写匹配，防止 match_*selftest* 等意外混入）
_EXCLUDE_SUBSTRINGS = ("selftest", "proof")


def _is_match_dir(name: str) -> bool:
    """判断目录名是否为真人对局（match_* 白名单）。

    白名单：match_* 前缀。
    额外排除：名称中含 selftest / proof 的边缘情况。
    """
    if not name.startswith("match_"):
        return False
    lower = name.lower()
    return all(sub not in lower for sub in _EXCLUDE_SUBSTRINGS)


def _read_last_line(path: pathlib.Path) -> str | None:
    """反向 seek 读文件末行，不整文件读入内存。

    策略：从末尾读 _TAIL_CHUNK 字节，去掉尾部换行后找最后一个换行符，
    截取末行内容。若文件小于 _TAIL_CHUNK 则读全文。JSONL 单行通常远小于 8KB。
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return None
        with path.open("rb") as f:
            read_from = max(0, size - _TAIL_CHUNK)
            f.seek(read_from)
            data = f.read()
        # 去掉尾部换行/回车
        data = data.rstrip(b"\r\n")
        if not data:
            return None
        # 找最后一个换行符（末行的开始）
        last_nl = max(data.rfind(b"\n"), data.rfind(b"\r"))
        if last_nl == -1:
            # 只有一行（或文件没有换行）
            return data.decode("utf-8", errors="replace")
        return data[last_nl + 1 :].decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_match_meta(d: pathlib.Path, mtime: float) -> dict[str, Any]:
    """提取单局对局元数据（防御性读取，不抛）。

    读取：
    - telemetry.jsonl 首行（game_start 记录）→ my_race / nickname / roster / 开局默认 build
    - 整局 snapshot 的 active_recipe 切换序列 → builds（玩家用过的 build 序列，已滤 <10s 短段）
    - telemetry.jsonl 末行（snapshot 或结局记录）→ duration_s / result

    builds = [{build, at_s（切换到该 build 的游戏时间秒）, dur_s（该段持续秒）}]，按时间顺序。
    active_recipe（兼容旧字段）= builds 第一段；无段时回退 game_start 的开局默认值。
    """
    telemetry = d / "telemetry.jsonl"

    meta: dict[str, Any] = {
        "match_id": _match_key(d.name),  # 去 _pN → 一局一条（两人局 p0/p1 归一）
        "mtime": mtime,
        "my_race": "—",
        "active_recipe": "—",  # 兼容旧字段：= builds 第一段（无则 game_start 默认）
        "builds": [],  # 玩家用过的 build 序列 [{build, at_s, dur_s}]，按时间，已滤 <10s 段
        "nickname": "—",
        "roster": [],  # 全部参战方 [{name,race,kind}/{race,difficulty,kind:computer}]
        "duration_s": None,
        "result": None,
    }

    if not telemetry.exists():
        return meta

    # --- 单次正向扫描：game_start（首行元数据）+ 整局 active_recipe 切换序列 ---
    # 把连续同名 active_recipe 的 snapshot 并成一段，记 (build, 起始时间)；末段时长用最后 snapshot 时间。
    game_start_recipe = "—"
    transitions: list[tuple[str, float]] = []  # (build, start_t) 切换点，按时间
    last_t = 0.0
    prev_build: str | None = None
    try:
        with telemetry.open("r", encoding="utf-8") as f:
            for idx, raw in enumerate(f):
                if idx > _MAX_SCAN_LINES:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                kind = rec.get("kind")
                if kind == "game_start":
                    meta["my_race"] = rec.get("my_race") or "—"
                    game_start_recipe = rec.get("active_recipe") or "—"
                    # 防御性读取昵称字段（玩家名 plumbing 由另一个 agent 加）
                    meta["nickname"] = rec.get("nickname") or rec.get("player_name") or "—"
                    # 整局 roster（全部参战方）→ admin 显示两人/玩家+电脑种族
                    roster = rec.get("roster")
                    if isinstance(roster, list):
                        meta["roster"] = roster
                elif kind == "snapshot":
                    t_val = rec.get("t")
                    ar = rec.get("active_recipe")
                    if isinstance(t_val, (int, float)):
                        last_t = float(t_val)
                        if ar and ar != "—" and ar != prev_build:
                            transitions.append((ar, float(t_val)))
                            prev_build = ar
    except Exception:
        pass

    # 切换点 → 段（含时长）：段 i = [start_i, start_{i+1})；末段 = [start_last, last_t]。
    # 过滤持续 < _MIN_SEGMENT_S 的段（开局默认值常几秒被切掉 = 短段，正好滤掉）。
    builds: list[dict[str, Any]] = []
    for i, (b, st) in enumerate(transitions):
        end = transitions[i + 1][1] if i + 1 < len(transitions) else last_t
        dur = end - st
        if dur >= _MIN_SEGMENT_S:
            builds.append({"build": b, "at_s": round(st, 1), "dur_s": round(dur, 1)})
    meta["builds"] = builds
    # 兼容旧前端字段：active_recipe = 第一段 build（玩家真正打的开局），无段则 game_start 默认
    meta["active_recipe"] = builds[0]["build"] if builds else game_start_recipe

    # --- 读末行（snapshot 时长 + 胜负）---
    last_raw = _read_last_line(telemetry)
    if last_raw:
        try:
            last_rec = json.loads(last_raw)
            kind = last_rec.get("kind", "")
            t_val = last_rec.get("t")
            if kind == "snapshot":
                meta["duration_s"] = t_val
            elif kind in ("victory", "defeat", "tie", "game_end"):
                meta["result"] = kind
                meta["duration_s"] = t_val
        except Exception:
            pass

    return meta


def scan_match_games(
    logs_dir: pathlib.Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """扫描 logs/ 下真人对局，返回最近 limit 局元数据列表（按 mtime 倒序）。

    筛选规则：
    - 白名单：match_* 前缀目录
    - 隐式排除：game_*、eff_*、e2e_*、*selftest*、*proof* 等（非 match_* 前缀）

    性能：
    - 仅按目录 mtime 排序，不深读每个目录（排序后才读）
    - 每局只读 telemetry.jsonl 首行 + 末行（_TAIL_CHUNK 字节反向 seek）
    - 调用方可缓存结果，避免 /api/admin/games 频繁触发全扫
    """
    base = logs_dir or _DEFAULT_LOGS_DIR
    if not base.exists():
        return []

    # 收集符合条件的目录 + mtime
    entries: list[tuple[float, pathlib.Path]] = []
    try:
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            if not _is_match_dir(entry.name):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            entries.append((mtime, entry))
    except OSError:
        return []

    # 按 match key 去重（两人局 p0/p1 归一局，留 mtime 最新的代表目录）
    by_key: dict[str, tuple[float, pathlib.Path]] = {}
    for mtime, entry in entries:
        key = _match_key(entry.name)
        if key not in by_key or mtime > by_key[key][0]:
            by_key[key] = (mtime, entry)

    # 按 mtime 倒序，取最近 limit 个
    deduped = sorted(by_key.values(), key=lambda x: x[0], reverse=True)[:limit]
    return [_extract_match_meta(d, mtime) for mtime, d in deduped]
