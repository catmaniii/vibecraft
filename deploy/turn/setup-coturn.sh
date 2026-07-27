#!/usr/bin/env bash
# VibeCraft TURN/STUN 中继一键部署（阿里云轻量 · 香港 · Ubuntu 22.04）
#
# 在目标机以 root 运行：
#   bash setup-coturn.sh
#
# 干什么：
#   1. 装 coturn + certbot
#   2. 用 certbot standalone 给 sslip.io 域名签 Let's Encrypt 证书（需要 80 口空闲）
#   3. 生成 static-auth-secret（TURN REST 短期凭证用，打印出来，回填到 vibecraft server）
#   4. 写 /etc/turnserver.conf（3478 + 5349 + 443 + NAT external-ip 映射 + 私网防 SSRF）
#   5. 开机自启 + 启动 + 配置证书续期 reload 钩子
#
# 幂等：可重复跑（证书已存在会跳过签发）。
set -euo pipefail

# ── 真实参数从 gitignored 配置文件读取（模板见 deploy/turn/vibecraft-turn.env.example）──
# 机密/环境值（IP、secret、域名）全部放 .secrets/vibecraft-turn.env，该文件被 .gitignore，绝不入库；
# 仓库里只有去敏的 .example 模板。用法：cp deploy/turn/vibecraft-turn.env.example .secrets/vibecraft-turn.env 后填值。
ENV_FILE="${VIBECRAFT_TURN_ENV:-.secrets/vibecraft-turn.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi

PUBLIC_IP="${VPS_PUBLIC_IP:-<VPS_IP>}"
PRIVATE_IP="${VPS_PRIVATE_IP:-<VPS_PRIVATE_IP>}"
# 没有真实域名 → 用 <IP>.sslip.io 自动解析到该 IP，给 turns:443 的 TLS 证书用
DOMAIN="${TURN_DOMAIN:-${PUBLIC_IP}.sslip.io}"
# 中继媒体端口段（与防火墙放行一致）
MIN_PORT=49160
MAX_PORT=49260
# Let's Encrypt 联系邮箱（可改）
LE_EMAIL="${LE_EMAIL:-admin@${DOMAIN}}"

CONF=/etc/turnserver.conf
SECRET_FILE=/etc/vibecraft-turn-secret

echo "==> [1/5] 安装 coturn + certbot"
export DEBIAN_FRONTEND=noninteractive
if command -v turnserver >/dev/null && command -v certbot >/dev/null; then
  echo "    已安装，跳过 apt（避免和 unattended-upgrades 抢 dpkg 锁）"
else
  # 等 unattended-upgrades 等占用的 dpkg 锁释放，最多 180s
  for _ in $(seq 1 36); do
    fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
    echo "    等待 dpkg 锁释放(unattended-upgrades)..."; sleep 5
  done
  apt-get update -y
  apt-get install -y coturn certbot
fi

echo "==> [2/5] 签发 TLS 证书（domain=${DOMAIN}，需 80 口空闲）"
LE_DIR="/etc/letsencrypt/live/${DOMAIN}"
if [[ -f "${LE_DIR}/fullchain.pem" ]]; then
  echo "    证书已存在，跳过签发"
else
  # standalone 自己起临时 :80 完成 HTTP-01 校验
  certbot certonly --standalone --non-interactive --agree-tos \
    -m "${LE_EMAIL}" -d "${DOMAIN}"
fi

# 关键：coturn 以 turnserver 用户运行，读不了 LE 的 privkey(root 600) → TLS 监听起不来。
# 把证书复制到 turnserver 拥有的目录(续期钩子也同步)。CERT_DIR 给下面写配置用。
CERT_DIR=/etc/coturn/certs
install -d -o turnserver -g turnserver -m 750 "${CERT_DIR}"
install -o turnserver -g turnserver -m 644 "${LE_DIR}/fullchain.pem" "${CERT_DIR}/fullchain.pem"
install -o turnserver -g turnserver -m 600 "${LE_DIR}/privkey.pem"   "${CERT_DIR}/privkey.pem"

echo "==> [3/5] 生成/读取 static-auth-secret"
if [[ -f "${SECRET_FILE}" ]]; then
  SECRET="$(cat "${SECRET_FILE}")"
  echo "    复用已有 secret（${SECRET_FILE}）"
else
  SECRET="$(openssl rand -hex 32)"
  umask 077; echo -n "${SECRET}" > "${SECRET_FILE}"
  echo "    新生成 secret → ${SECRET_FILE}"
fi

echo "==> [4/5] 写 ${CONF}"
cat > "${CONF}" <<EOF
# ── VibeCraft coturn 配置（脚本生成，勿手改，改脚本重跑）──
# 监听：标准 STUN/TURN(3478) + TURN over TLS 直接放 443(穿中国防火墙，看着像 HTTPS)。
# 单 IP 下 alt-tls-listening-port 不可靠 → 443 直接当主 TLS 端口，客户端用 turns:443。
listening-port=3478
tls-listening-port=443
listening-ip=0.0.0.0

# NAT：阿里云轻量是私网网卡 + 公网 NAT。relay 绑私网、对外通告公网，
# 否则把私网地址发给手机 → 中继连不上（关键）。
relay-ip=${PRIVATE_IP}
external-ip=${PUBLIC_IP}/${PRIVATE_IP}

# 中继媒体端口段（与云防火墙放行一致）
min-port=${MIN_PORT}
max-port=${MAX_PORT}

# 鉴权：TURN REST 短期凭证（vibecraft server 用同一 secret 现签 username/password）
use-auth-secret
static-auth-secret=${SECRET}
realm=${DOMAIN}
server-name=${DOMAIN}

# TLS 证书（Let's Encrypt）
cert=${CERT_DIR}/fullchain.pem
pkey=${CERT_DIR}/privkey.pem

# 安全加固：禁旧 TLS、禁 CLI、加指纹；禁止把私网/环回当中继 peer（防 SSRF 探内网）
no-tlsv1
no-tlsv1_1
no-cli
fingerprint
no-multicast-peers
denied-peer-ip=0.0.0.0-0.255.255.255
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
EOF

# 让 Ubuntu 的 coturn 守护进程真正启用（默认 disabled）
sed -i 's/^#\?TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn || true
grep -q '^TURNSERVER_ENABLED=1' /etc/default/coturn || echo 'TURNSERVER_ENABLED=1' >> /etc/default/coturn

echo "==> [5/5] 证书续期 reload 钩子 + 启动"
# 续期后自动 reload coturn 重载新证书
HOOK=/etc/letsencrypt/renewal-hooks/deploy/reload-coturn.sh
mkdir -p "$(dirname "${HOOK}")"
cat > "${HOOK}" <<HK
#!/usr/bin/env bash
# 续期后：把新证书复制到 coturn 可读目录 + reload
install -o turnserver -g turnserver -m 644 "${LE_DIR}/fullchain.pem" "${CERT_DIR}/fullchain.pem"
install -o turnserver -g turnserver -m 600 "${LE_DIR}/privkey.pem"   "${CERT_DIR}/privkey.pem"
systemctl reload coturn 2>/dev/null || systemctl restart coturn
HK
chmod +x "${HOOK}"

# coturn 以非 root 的 turnserver 用户运行 → 默认绑不了特权端口 443。
# 给它 CAP_NET_BIND_SERVICE（systemd drop-in），允许绑 443。
DROPIN=/etc/systemd/system/coturn.service.d/override.conf
mkdir -p "$(dirname "${DROPIN}")"
cat > "${DROPIN}" <<'EOF'
[Service]
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
EOF
systemctl daemon-reload

systemctl enable coturn
systemctl restart coturn
sleep 1
systemctl --no-pager --full status coturn | head -n 12 || true

echo ""
echo "================ 部署完成 ================"
echo "TURN domain   : ${DOMAIN}"
echo "STUN/TURN     : stun:${DOMAIN}:3478 / turn:${DOMAIN}:3478"
echo "TURN over TLS : turns:${DOMAIN}:443"
echo "static-secret : ${SECRET}"
echo "  ↑ 回填到 vibecraft server（用于现签短期 TURN 凭证），也存在 ${SECRET_FILE}"
echo "=========================================="
