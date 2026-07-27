"""GET /api/strategies 接口单测。

验证：
- strategy_library=None 时返回空列表
- 注入真实 StrategyLibrary（从 strategies/ 目录加载）时：
  - 返回 HTTP 200、Content-Type: application/json
  - 策略数 = strategies/ 下 yaml 文件数
  - 每条 item 包含必要字段（id / display / race / stage）
  - stage 只有 'opening' / 'persistent'
  - race 只有 None / protoss / zerg / terran
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from vibecraft.server.http import make_process_request
from vibecraft.strategy.library import StrategyLibrary

_run = asyncio.run

# ---------------------------------------------------------------------------
# 测试桩（同 test_server_http.py 风格）
# ---------------------------------------------------------------------------


def _make_request(path: str, upgrade: str = "") -> Any:
    req = MagicMock()
    req.path = path
    headers_mock = MagicMock()
    headers_mock.get = lambda k, d="": upgrade if k == "Upgrade" else d
    req.headers = headers_mock
    return req


def _make_ws(remote: tuple[str, int] = ("127.0.0.1", 9999)) -> Any:
    ws = MagicMock()
    ws.remote_address = remote
    return ws


# ---------------------------------------------------------------------------
# 定位项目 strategies/ 目录
# ---------------------------------------------------------------------------


def _strategies_dir() -> pathlib.Path:
    """从本文件上溯找项目根，返回 strategies/ 路径。"""
    # tests/unit/test_server_http_api.py → 上溯 3 层 = 项目根
    here = pathlib.Path(__file__).parent  # tests/unit/
    root = here.parent.parent  # 项目根
    return root / "strategies"


def _aliases_path() -> pathlib.Path:
    here = pathlib.Path(__file__).parent
    root = here.parent.parent
    return root / "docs" / "aliases" / "protoss.yaml"


# ---------------------------------------------------------------------------
# 无 library 时返回空列表
# ---------------------------------------------------------------------------


class TestStrategiesApiNoLibrary:
    def test_no_library_returns_200_empty(self, tmp_path: pathlib.Path) -> None:
        """strategy_library=None 时接口返回 200 + 空 strategies 列表。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=None)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        assert resp.status_code == 200
        payload = json.loads(resp.body.decode("utf-8"))
        assert payload == {"strategies": []}

    def test_api_path_not_served_as_static(self, tmp_path: pathlib.Path) -> None:
        """/api/strategies 不会被当作静态文件路径（即使 tmp_path 有同名文件）。"""
        # 创建一个假 'api' 目录和 strategies 文件，确认不被 serve 成文件
        (tmp_path / "api").mkdir()
        (tmp_path / "api" / "strategies").write_bytes(b"should_not_be_served")
        hook = make_process_request(static_dir=tmp_path, strategy_library=None)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        assert "strategies" in payload

    def test_json_content_type(self, tmp_path: pathlib.Path) -> None:
        """响应 Content-Type 包含 application/json。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=None)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        ct = resp.headers.get("Content-Type", "")
        assert "application/json" in ct


# ---------------------------------------------------------------------------
# 注入真实 StrategyLibrary 时的验证
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_library() -> StrategyLibrary:
    """从项目 strategies/ 目录加载真实 StrategyLibrary。"""
    sd = _strategies_dir()
    ap = _aliases_path()
    if not sd.exists():
        pytest.skip("strategies/ 目录不存在，跳过集成级测试")
    return StrategyLibrary.from_directories(sd, ap)


@pytest.fixture(scope="module")
def yaml_count() -> int:
    """策略目录下 yaml 文件总数。"""
    sd = _strategies_dir()
    if not sd.exists():
        pytest.skip("strategies/ 目录不存在")
    return len(list(sd.rglob("*.yaml")))


class TestStrategiesApiWithLibrary:
    def test_count_equals_yaml_files(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
        yaml_count: int,
    ) -> None:
        """返回的策略数 = strategies/ 目录下 yaml 文件总数。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        assert len(payload["strategies"]) == yaml_count

    def test_required_fields_present(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
    ) -> None:
        """每条策略都有 id / display / stage / summary_zh / aliases 字段。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        for item in payload["strategies"]:
            assert "id" in item, f"缺 id: {item}"
            assert "display" in item, f"缺 display: {item}"
            assert "stage" in item, f"缺 stage: {item}"
            assert "summary_zh" in item, f"缺 summary_zh: {item}"
            assert "aliases" in item, f"缺 aliases: {item}"

    def test_stage_values_valid(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
    ) -> None:
        """stage 只能是 'opening' 或 'persistent'。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        valid_stages = {"opening", "persistent"}
        for item in payload["strategies"]:
            assert item["stage"] in valid_stages, f"非法 stage {item['stage']!r} in {item['id']}"

    def test_race_values_valid(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
    ) -> None:
        """race 只能是 protoss / zerg / terran / None。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        valid_races = {"protoss", "zerg", "terran", None}
        for item in payload["strategies"]:
            assert item["race"] in valid_races, f"非法 race {item['race']!r} in {item['id']}"

    def test_known_strategy_present(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
    ) -> None:
        """至少包含 4bg（基线 sanity check）。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        ids = {item["id"] for item in payload["strategies"]}
        assert "4bg" in ids

    def test_ws_upgrade_still_passthrough(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
    ) -> None:
        """注入 library 后，WS 升级请求仍放行（不影响握手）。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        req = _make_request("/ws?room=abc", upgrade="websocket")
        assert _run(hook(_make_ws(), req)) is None

    def test_aliases_is_list(
        self,
        tmp_path: pathlib.Path,
        real_library: StrategyLibrary,
    ) -> None:
        """aliases 字段是列表类型。"""
        hook = make_process_request(static_dir=tmp_path, strategy_library=real_library)
        resp = _run(hook(_make_ws(), _make_request("/api/strategies")))
        assert resp is not None
        payload = json.loads(resp.body.decode("utf-8"))
        for item in payload["strategies"]:
            assert isinstance(item["aliases"], list), f"aliases 应是 list，id={item['id']}"
