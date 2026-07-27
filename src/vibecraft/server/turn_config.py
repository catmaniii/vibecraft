"""TURN/STUN 配置加载 + 短期凭证现签（阶段1 多人中继接入）。

云 coturn 用 `use-auth-secret` + `static-auth-secret`（REST 短期凭证）。本模块：
  - load_turn_config()  从 env 或 .secrets/vibecraft-turn.env 读配置（缺 → None，graceful）
  - mint_credential()   用 secret 现签 coturn REST 短期 username/password（HMAC-SHA1）
  - build_ice_servers() 组 WebRTC 标准 iceServers（coturn STUN + turn/turns + 现签凭证）

不变量：无 TURN 配置（load 返 None）时上层一切照旧（纯 P2P/Tailnet，iceServers 空）。

凭证方案与 deploy/turn/turn_selftest.py 逐字一致（真机已 PASS）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# 默认凭证 TTL：24h。coturn allocation 的 Refresh 会重校验 username 里的 expiry，
# 短 TTL（如 1h）会让 >1h 的长局中继静默断（评审采纳：1h→24h）。
_DEFAULT_TTL_S = 86400

# .secrets/vibecraft-turn.env：deploy 写入的单一真值源（gitignore）。
# turn_config.py 在 src/vibecraft/server/ → parents[3] = 仓库根。
_SECRETS_ENV = Path(__file__).resolve().parents[3] / ".secrets" / "vibecraft-turn.env"


@dataclass(frozen=True)
class TurnConfig:
    """TURN 服务器配置。"""

    domain: str
    secret: str
    port: int = 3478
    tls_port: int = 443


def _read_secrets_env(path: Path = _SECRETS_ENV) -> dict[str, str]:
    """读 .secrets/vibecraft-turn.env（KEY="VALUE" 行）；不存在/读失败返回空 dict。"""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def load_turn_config(secrets_env: Path = _SECRETS_ENV) -> TurnConfig | None:
    """加载 TURN 配置：env var 优先，回退 .secrets/vibecraft-turn.env。

    env：VIBECRAFT_TURN_DOMAIN / VIBECRAFT_TURN_SECRET / VIBECRAFT_TURN_PORT /
         VIBECRAFT_TURN_TLS_PORT。
    文件：TURN_DOMAIN / TURN_STATIC_SECRET / TURN_PORT / TURN_TLS_PORT。
    domain 或 secret 缺 → None（graceful，无 TURN）。
    """
    domain = os.environ.get("VIBECRAFT_TURN_DOMAIN")
    secret = os.environ.get("VIBECRAFT_TURN_SECRET")
    port = os.environ.get("VIBECRAFT_TURN_PORT")
    tls_port = os.environ.get("VIBECRAFT_TURN_TLS_PORT")

    if not (domain and secret):
        env = _read_secrets_env(secrets_env)
        domain = domain or env.get("TURN_DOMAIN")
        secret = secret or env.get("TURN_STATIC_SECRET")
        port = port or env.get("TURN_PORT")
        tls_port = tls_port or env.get("TURN_TLS_PORT")

    if not (domain and secret):
        return None

    try:
        return TurnConfig(
            domain=domain,
            secret=secret,
            port=int(port) if port else 3478,
            tls_port=int(tls_port) if tls_port else 443,
        )
    except ValueError:
        logger.warning("turn_config_bad_port", port=port, tls_port=tls_port)
        return None


def mint_credential(
    secret: str, ttl_s: int = _DEFAULT_TTL_S, name: str = "vibecraft"
) -> tuple[str, str]:
    """现签 coturn REST 短期凭证。

    username = f"{expiry}:{name}"，password = base64(HMAC-SHA1(secret, username))。
    与 coturn use-auth-secret/static-auth-secret 匹配。
    """
    expiry = int(time.time()) + ttl_s
    username = f"{expiry}:{name}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


def build_ice_servers(
    cfg: TurnConfig, ttl_s: int = _DEFAULT_TTL_S, name: str = "vibecraft"
) -> list[dict[str, Any]]:
    """组 WebRTC 标准 iceServers：coturn STUN（中国可达，做 srflx）+ turn/turns（现签凭证）。

    **每次调用现签新凭证**（不要缓存）。返回结构直接给浏览器；aiortc 侧另转 RTCIceServer。
    """
    username, credential = mint_credential(cfg.secret, ttl_s, name)
    return [
        {"urls": [f"stun:{cfg.domain}:{cfg.port}"]},
        {
            "urls": [
                f"turn:{cfg.domain}:{cfg.port}?transport=udp",
                f"turn:{cfg.domain}:{cfg.port}?transport=tcp",
                f"turns:{cfg.domain}:{cfg.tls_port}?transport=tcp",
            ],
            "username": username,
            "credential": credential,
        },
    ]
