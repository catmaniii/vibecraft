"""Per-server YAML config file loader.

Recognized keys: ``name``, ``token``, ``port``, ``ip``, ``host``.

SECURITY HARD RULE: ``admin_token`` is FORBIDDEN in config files.
Admin tokens must be passed via ``--admin-token`` CLI flag or
``VIBECRAFT_ADMIN_TOKEN`` env var, and kept in ``.secrets/`` (gitignore'd).

Design mirrors the graceful-degradation style of :mod:`vibecraft.server.turn_config`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)

# Keys that are safe to expose in a per-server config file.
# ``admin_token`` is intentionally absent — secrets must NOT live in repo files.
_RECOGNIZED_KEYS: frozenset[str] = frozenset({"name", "token", "port", "ip", "host"})


def load_server_config_file(path: str) -> dict[str, Any]:
    """Load a per-server YAML config file and return recognized fields.

    Recognized keys: ``name``, ``token``, ``port``, ``ip``, ``host``.
    Unknown keys are silently ignored (forward-compatibility).

    Args:
        path: Path to the YAML config file.

    Returns:
        Dict containing only the recognized fields present in the file.

    Raises:
        FileNotFoundError: The file does not exist.
        ValueError: The file contains ``admin_token`` (security hard rule).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"服务器配置文件未找到: {path}\n请确认路径，或在 config/servers/ 下创建配置文件。"
        )

    raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"配置文件格式错误（期望 YAML mapping，实际为 {type(raw).__name__}）: {path}"
        )

    # SECURITY HARD RULE: admin_token MUST NOT appear in a config file.
    # It must be passed via --admin-token CLI flag or VIBECRAFT_ADMIN_TOKEN env var,
    # and stored in .secrets/ (which is .gitignore'd).
    if "admin_token" in raw:
        raise ValueError(
            "安全规则违反：服务器配置文件中不允许包含 admin_token！\n"
            "admin_token 必须通过以下方式之一传入（绝不入库/配置文件）：\n"
            "  • CLI 参数：--admin-token <token>\n"
            "  • 环境变量：VIBECRAFT_ADMIN_TOKEN=<token>\n"
            "  • 保存在 .secrets/ 目录中（已由 .gitignore 排除）\n"
            f"受影响的文件: {path}"
        )

    unknown = set(raw.keys()) - _RECOGNIZED_KEYS
    if unknown:
        logger.warning("server_config_unknown_keys", keys=sorted(unknown), path=path)

    return {k: v for k, v in raw.items() if k in _RECOGNIZED_KEYS}
