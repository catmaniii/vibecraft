#!/usr/bin/env python3
"""VibeCraft TURN 手机测试页服务（部署在 VPS:80）。

手机浏览器打开 http://<域名>/ → 自动用现签的短期凭证测 turns:443 + turn:3478，
显示 "✓ 通 / ✗ 不通" + relay 候选。零输入。

凭证服务端现签（读 /etc/vibecraft-turn-secret），每次刷新都是新鲜的，不会过期。
顺带放行 /.well-known/acme-challenge/（certbot webroot 续期用，配合 renewal pre/post
钩子停/起本服务，见部署脚本）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import os
import socketserver
import time
from pathlib import Path

# 域名从环境变量读（真实 IP 不写死进仓库）：部署时 export TURN_DOMAIN=turn.<你的IP>.sslip.io
# 或在 .secrets/vibecraft-turn.env 设 TURN_DOMAIN 后 `set -a; . .secrets/vibecraft-turn.env` 再跑本脚本。
DOMAIN = os.environ.get("TURN_DOMAIN", "turn.<VPS_IP>.sslip.io")  # 经 nginx 443 SNI 路由到 coturn
SECRET = Path("/etc/vibecraft-turn-secret").read_text().strip()
ACME_ROOT = Path("/var/www/acme")

_HTML = """<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VibeCraft TURN 测试</title>
<style>
body{font-family:-apple-system,sans-serif;padding:18px;font-size:17px;color:#222;max-width:640px;margin:auto}
#r{font-size:30px;font-weight:bold;margin:14px 0}
.ok{color:#0a0}.bad{color:#c00}.run{color:#555}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
td,th{border:1px solid #ddd;padding:6px;text-align:left}
button{font-size:17px;padding:10px 18px;margin-top:14px;border-radius:8px;border:1px solid #888;background:#f4f4f4}
small{color:#888}
</style></head><body>
<h2>VibeCraft TURN 中继测试</h2>
<div id="r" class="run">测试中…（最多 15 秒）</div>
<table id="tbl"><tr><th>路径</th><th>结果</th><th>候选地址</th></tr></table>
<button onclick="location.reload()">重新测试</button>
<p><small>域名 __DOMAIN__ · 现签凭证 · 在中国请用 4G/5G 流量测（别用 wifi）</small></p>
<script>
const DOMAIN="__DOMAIN__", USER="__USER__", PASS="__PASS__";
const tests=[
 {name:"turns TLS:443（穿墙关键）", urls:["turns:"+DOMAIN+":443?transport=tcp"]},
 {name:"turn UDP:3478", urls:["turn:"+DOMAIN+":3478"]},
];
function gather(urls){
 return new Promise(res=>{
  let pc;
  try{ pc=new RTCPeerConnection({iceServers:[{urls,username:USER,credential:PASS}]}); }
  catch(e){ return res({err:String(e),relay:null}); }
  const cands=[];
  pc.onicecandidate=e=>{
   if(e.candidate){ cands.push(e.candidate); }
   else { done(); }
  };
  pc.onicegatheringstatechange=()=>{ if(pc.iceGatheringState==="complete") done(); };
  let finished=false;
  function done(){
   if(finished) return; finished=true;
   const relay=cands.find(c=>c.type==="relay"||(c.candidate||"").includes("typ relay"));
   res({relay});
  }
  pc.createDataChannel("probe");
  pc.createOffer().then(o=>pc.setLocalDescription(o));
  setTimeout(done,15000);
 });
}
(async()=>{
 let anyRelay=false;
 for(const t of tests){
  const {relay,err}=await gather(t.urls);
  if(relay) anyRelay=true;
  const tr=document.createElement("tr");
  const cell=err?("错误:"+err):(relay?(relay.address||relay.candidate):"仅 host/srflx（中继没通）");
  tr.innerHTML="<td>"+t.name+"</td><td class="+(relay?"ok":"bad")+">"+(relay?"✓ 通":"✗ 不通")+"</td><td style='font-size:11px'>"+cell+"</td>";
  document.getElementById("tbl").appendChild(tr);
 }
 const r=document.getElementById("r");
 if(anyRelay){ r.textContent="✓ TURN 中继可用"; r.className="ok"; }
 else{ r.textContent="✗ 中继不通（看下表哪条失败）"; r.className="bad"; }
})();
</script></body></html>"""


def make_cred(ttl: int = 3600) -> tuple[str, str]:
    expiry = int(time.time()) + ttl
    username = f"{expiry}:phonetest"
    digest = hmac.new(SECRET.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # ACME 续期挑战（certbot webroot）
        if self.path.startswith("/.well-known/acme-challenge/"):
            token = self.path.rsplit("/", 1)[-1]
            f = ACME_ROOT / ".well-known" / "acme-challenge" / token
            if f.is_file():
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)
            return
        user, pw = make_cred()
        page = _HTML.replace("__DOMAIN__", DOMAIN).replace("__USER__", user).replace("__PASS__", pw)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # 静音访问日志
        pass


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", 80), Handler) as httpd:
        httpd.serve_forever()
