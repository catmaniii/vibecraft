#!/usr/bin/env bash
# VibeCraft 公网前门（让国内手机直连阿里云 VPS，不再依赖 Tailscale）。
#
#   nginx 443 SNI 分流：
#     turn.<ip>.sslip.io  → 透传给 coturn(5349)      ← 媒体中继 TLS
#     其余(app.<ip>...)   → nginx 终止 TLS → 反向隧道(127.0.0.1:18080) → 你 PC:8080
#
# PC 侧用 SSH -R 把 8080 映到 VPS 127.0.0.1:18080（见 pc-tunnel.ps1），零新开端口。
# coturn 从 443 让到 5349（nginx 443 passthrough 回它）；可回退（见末尾注释）。
set -euo pipefail

# 真实 IP 从 gitignored 配置读取（模板 deploy/turn/vibecraft-turn.env.example；文件 .secrets/vibecraft-turn.env 不入库）
ENV_FILE="${VIBECRAFT_TURN_ENV:-.secrets/vibecraft-turn.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
IP="${VPS_PUBLIC_IP:-<VPS_IP>}"
BASE=${IP}.sslip.io
APP=app.${BASE}
TURN=turn.${BASE}
TUNNEL_PORT=18080      # SSH -R: PC:8080 → VPS 127.0.0.1:18080
APP_TLS_PORT=8443      # nginx 内部 app TLS 终止口（只 127.0.0.1）
LE=/etc/letsencrypt/live/${BASE}

echo "==> [1/5] 扩展证书到 3 个 SAN: ${BASE} / ${APP} / ${TURN}"
systemctl stop turn-testpage 2>/dev/null || true   # 让出 80 给 certbot standalone
certbot certonly --standalone --non-interactive --agree-tos --expand \
  -m "admin@${BASE}" -d "${BASE}" -d "${APP}" -d "${TURN}"
systemctl start turn-testpage 2>/dev/null || true

echo "==> [2/5] coturn 让出 443 → TLS 改 5349（nginx 接管 443，passthrough 回 coturn）"
install -o turnserver -g turnserver -m 644 "${LE}/fullchain.pem" /etc/coturn/certs/fullchain.pem
install -o turnserver -g turnserver -m 600 "${LE}/privkey.pem"   /etc/coturn/certs/privkey.pem
sed -i 's/^tls-listening-port=.*/tls-listening-port=5349/' /etc/turnserver.conf
systemctl restart coturn
sleep 1
ss -tulnp 2>/dev/null | grep turnserver | awk '{print "    coturn 监听", $1, $5}' | sort -u

echo "==> [3/5] 装 nginx"
if ! command -v nginx >/dev/null; then
  for _ in $(seq 1 36); do fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break; sleep 5; done
  apt-get update -y && apt-get install -y nginx
fi
# 默认站点占 80（和测试页冲突）+ 不需要 → 移除
rm -f /etc/nginx/sites-enabled/default

echo "==> [4/5] 写 nginx：443 SNI 分流(stream) + app TLS 终止→隧道(http)"
# stream 块必须顶层（不能在 http）→ 直接进 nginx.conf
if ! grep -q "VBC-STREAM" /etc/nginx/nginx.conf; then
cat >> /etc/nginx/nginx.conf <<EOF

# VBC-STREAM: 443 SNI 分流（turn.*→coturn / 其余→app）
stream {
    map \$ssl_preread_server_name \$vbc_upstream {
        ${TURN}   127.0.0.1:5349;
        default   127.0.0.1:${APP_TLS_PORT};
    }
    server {
        listen 443;
        listen [::]:443;
        ssl_preread on;
        proxy_pass \$vbc_upstream;
        proxy_timeout 1h;        # 长连 WS / 媒体不被掐
    }
}
EOF
fi
# app http server（TLS 终止 → 反向隧道到 PC）
cat > /etc/nginx/sites-available/vbc-app.conf <<EOF
map \$http_upgrade \$vbc_conn_upgrade { default upgrade; '' close; }
server {
    listen 127.0.0.1:${APP_TLS_PORT} ssl;
    server_name ${APP} ${BASE};
    ssl_certificate ${LE}/fullchain.pem;
    ssl_certificate_key ${LE}/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:${TUNNEL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$vbc_conn_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/vbc-app.conf /etc/nginx/sites-enabled/vbc-app.conf
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "==> [5/5] 完成"
echo "    coturn TLS → 5349（nginx 443 SNI passthrough）"
echo "    app https://${APP}/ → nginx → 隧道 127.0.0.1:${TUNNEL_PORT} → PC:8080"
echo "    手机连: https://${APP}/?room=<token>"
echo ""
echo "  回退 coturn 到 443（若出问题）:"
echo "    sed -i 's/^tls-listening-port=.*/tls-listening-port=443/' /etc/turnserver.conf"
echo "    systemctl stop nginx; systemctl restart coturn"
