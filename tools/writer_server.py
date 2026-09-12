#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地写作台小服务：既托管页面，又做 API 转接（解决浏览器跨域限制）。

用法：
  python writer_server.py                # 默认 http://127.0.0.1:8787
  python writer_server.py --port 9000
  python writer_server.py --site ../site

它做两件事：
  1. 把 site/ 目录当静态站点提供，浏览器访问 http://127.0.0.1:8787/writer.html
  2. 把 POST /api/chat 的请求转发到你填写的 OpenAI 兼容接口
     —— 请求由本机发出，所以不存在跨域问题；你的 API Key 不经过任何第三方服务器。

安全说明：服务只监听 127.0.0.1（本机），不对外网开放。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SITE = "."
TIMEOUT = 180


class Handler(BaseHTTPRequestHandler):
    server_version = "KepuWriter/1.0"

    def log_message(self, fmt, *args):  # 安静一点，只报错误
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---------------------------------------------------------- 静态文件
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            path = "/index.html"
        target = os.path.normpath(os.path.join(SITE, path.lstrip("/")))
        if not target.startswith(os.path.abspath(SITE)):
            self.send_error(403, "forbidden")
            return
        if not os.path.isfile(target):
            self.send_error(404, "not found")
            return
        ctype = "text/html; charset=utf-8"
        if target.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        elif target.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif target.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(target, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------- API 转接
    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/chat":
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": {"message": f"请求体不是合法 JSON：{exc}"}})
            return

        base = (payload.pop("_base", "") or "").rstrip("/")
        key = payload.pop("_key", "") or ""
        if not base:
            self._json(400, {"error": {"message": "缺少 _base（接口地址）"}})
            return

        url = base + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
            self._raw(200, raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            self._json(exc.code, {"error": {"message": f"上游接口返回 {exc.code}：{detail}"}})
        except Exception as exc:  # noqa: BLE001
            self._json(502, {"error": {"message": f"转发失败：{exc}"}})

    # ---------------------------------------------------------- 工具
    def _raw(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._raw(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--site", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs"))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    global SITE
    SITE = os.path.abspath(args.site)
    if not os.path.isdir(SITE):
        print(f"[失败] 找不到站点目录：{SITE}", file=sys.stderr)
        print("       先运行：python tools/build_site.py --project <工程根目录> --out <工程根目录>/docs",
              file=sys.stderr)
        return 1

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("科普写作台已启动")
    print(f"  页面：http://{args.host}:{args.port}/writer.html")
    print(f"  知识库：http://{args.host}:{args.port}/index.html")
    print(f"  站点目录：{SITE}")
    print("  按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
