"""server_config.py 单测：YAML 加载、安全拦截、优先级逻辑。"""

from __future__ import annotations

import pytest

from vibecraft.server.server_config import load_server_config_file

# ---------------------------------------------------------------------------
# 基础解析
# ---------------------------------------------------------------------------


def test_valid_yaml_returns_recognized_fields(tmp_path):
    """正常 YAML 文件 → 返回 recognized 字段。"""
    f = tmp_path / "test.yaml"
    f.write_text("name: my_server\ntoken: tok123\nport: 9090\n", encoding="utf-8")
    result = load_server_config_file(str(f))
    assert result == {"name": "my_server", "token": "tok123", "port": 9090}


def test_all_recognized_keys(tmp_path):
    """全部 recognized key（name/token/port/ip/host）都能正确读取。"""
    f = tmp_path / "full.yaml"
    f.write_text(
        "name: srv\ntoken: t\nport: 1234\nip: 1.2.3.4\nhost: 0.0.0.0\n",
        encoding="utf-8",
    )
    result = load_server_config_file(str(f))
    assert result == {
        "name": "srv",
        "token": "t",
        "port": 1234,
        "ip": "1.2.3.4",
        "host": "0.0.0.0",
    }


def test_partial_keys_only_present_keys_returned(tmp_path):
    """只写部分 key → 只返回出现的 key，不补 None。"""
    f = tmp_path / "partial.yaml"
    f.write_text("name: foo\n", encoding="utf-8")
    result = load_server_config_file(str(f))
    assert result == {"name": "foo"}
    assert "token" not in result
    assert "port" not in result


def test_empty_file_returns_empty_dict(tmp_path):
    """空文件 → 空 dict（不报错）。"""
    f = tmp_path / "empty.yaml"
    f.write_text("", encoding="utf-8")
    result = load_server_config_file(str(f))
    assert result == {}


def test_yaml_with_comments_and_null_ip(tmp_path):
    """注释 + null ip（等价于留空）不报错。"""
    f = tmp_path / "commented.yaml"
    f.write_text(
        "# server config\nname: close_test\ntoken: vibecraft-dev\nport: 8080\n# ip: null\n",
        encoding="utf-8",
    )
    result = load_server_config_file(str(f))
    assert result["name"] == "close_test"
    assert result["token"] == "vibecraft-dev"
    assert "ip" not in result


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_missing_file_raises_file_not_found(tmp_path):
    """文件不存在 → FileNotFoundError，错误信息含路径。"""
    missing = str(tmp_path / "nonexistent.yaml")
    with pytest.raises(FileNotFoundError, match="服务器配置文件未找到"):
        load_server_config_file(missing)


def test_admin_token_raises_value_error(tmp_path):
    """含 admin_token → ValueError（安全硬规则，硬失败）。"""
    f = tmp_path / "bad.yaml"
    f.write_text("name: x\nadmin_token: secret123\n", encoding="utf-8")
    with pytest.raises(ValueError, match="admin_token"):
        load_server_config_file(str(f))


def test_admin_token_error_message_mentions_alternatives(tmp_path):
    """admin_token 错误信息要告知正确传法（--admin-token / env var / .secrets/）。"""
    f = tmp_path / "bad.yaml"
    f.write_text("admin_token: secret\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc_info:
        load_server_config_file(str(f))
    msg = str(exc_info.value)
    assert "--admin-token" in msg or "VIBECRAFT_ADMIN_TOKEN" in msg


# ---------------------------------------------------------------------------
# 未知 key 处理
# ---------------------------------------------------------------------------


def test_unknown_keys_are_silently_ignored(tmp_path):
    """未知 key 不崩溃，从结果中排除。"""
    f = tmp_path / "unknown.yaml"
    f.write_text("name: x\nfuture_feature: 42\nanother_unknown: hello\n", encoding="utf-8")
    result = load_server_config_file(str(f))
    assert result == {"name": "x"}
    assert "future_feature" not in result
    assert "another_unknown" not in result


# ---------------------------------------------------------------------------
# 优先级逻辑（loader 产出 + 手动合并，模拟 CLI 分层覆盖）
# ---------------------------------------------------------------------------


def test_cli_override_wins_over_file_token(tmp_path):
    """文件 token vs 显式 CLI token → CLI 胜出（模拟 serve() 的 None-check 逻辑）。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("token: file-token\nport: 9000\n", encoding="utf-8")
    file_cfg = load_server_config_file(str(f))

    # 模拟 serve() 中 "显式 CLI 参数 is not None → 覆盖"
    explicit_token = "cli-token"
    final_token = explicit_token if explicit_token is not None else file_cfg.get("token")
    assert final_token == "cli-token"


def test_none_cli_arg_does_not_override_file_value(tmp_path):
    """CLI 参数为 None（未显式传）→ 不覆盖文件值（符合 serve() None-check 逻辑）。"""
    f = tmp_path / "cfg.yaml"
    f.write_text("token: file-token\n", encoding="utf-8")
    file_cfg = load_server_config_file(str(f))

    # CLI token 未传（None）→ 保留文件值
    cli_token: str | None = None
    final_token = cli_token if cli_token is not None else file_cfg.get("token")
    assert final_token == "file-token"


# ---------------------------------------------------------------------------
# close_test.yaml 真实文件解析验证
# ---------------------------------------------------------------------------


def test_close_test_yaml_parses_correctly():
    """config/servers/close_test.yaml 应能被加载，且 name=close_test。"""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    yaml_path = repo_root / "config" / "servers" / "close_test.yaml"
    result = load_server_config_file(str(yaml_path))
    assert result["name"] == "close_test"
    assert result["token"] == "vibecraft-dev"
    assert result["port"] == 8080
