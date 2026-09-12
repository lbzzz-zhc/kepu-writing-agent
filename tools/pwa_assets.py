#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给生成好的网页补上 PWA 能力：manifest、图标、Service Worker。

为什么做这个
    网页装在桌面/手机主屏后，点图标就能全屏打开，跟 App 一样。
    **它只是个入口**——内容仍在 GitHub Pages 上，改内容照旧：
    改语料/规则 → 重跑 build 脚本 → 推送 → 刷新就是新版。

缓存策略（关键）
    · 页面导航：**网络优先**，拿不到才用缓存 —— 保证推送后刷新就能看到新版
    · 静态资源：缓存优先，后台更新
    · 跨域请求（模型 API）与本地服务接口：一律放行，绝不缓存

图标用纯标准库生成（zlib + struct 手写 PNG），不依赖 Pillow。
"""

import datetime
import os
import struct
import zlib

BG = (15, 110, 86)        # #0F6E56 深绿
PAPER = (255, 255, 255)
INK = (15, 110, 86)


# ---------------------------------------------------------------- PNG 生成
def _rounded_rect(px, w, h, x0, y0, x1, y1, r, color):
    for y in range(max(0, int(y0)), min(h, int(y1))):
        for x in range(max(0, int(x0)), min(w, int(x1))):
            cx = min(max(x, x0 + r), x1 - r)
            cy = min(max(y, y0 + r), y1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                px[y][x] = color


def _rect(px, w, h, x0, y0, x1, y1, color):
    for y in range(max(0, int(y0)), min(h, int(y1))):
        for x in range(max(0, int(x0)), min(w, int(x1))):
            px[y][x] = color


def make_icon(n):
    """画一个"文档 + 文字行"图标：深绿底、白色纸面、绿色文字条。"""
    px = [[BG for _ in range(n)] for _ in range(n)]
    u = n / 16.0
    _rounded_rect(px, n, n, 3.5 * u, 2.2 * u, 12.5 * u, 13.8 * u, 1.1 * u, PAPER)
    # 标题条（粗）
    _rounded_rect(px, n, n, 5 * u, 4.2 * u, 11 * u, 5.4 * u, 0.35 * u, INK)
    # 正文三条（最后一条短一些，像段末）
    for i, x1 in enumerate((11.0, 11.0, 9.4)):
        y = 7.0 + i * 1.9
        _rounded_rect(px, n, n, 5 * u, y * u, x1 * u, (y + 0.95) * u, 0.3 * u, INK)
    return px


def write_png(path, px):
    h = len(px)
    w = len(px[0])
    raw = b"".join(b"\x00" + bytes(v for pixel in row for v in pixel) for row in px)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    open(path, "wb").write(png)
    return len(png)


# ---------------------------------------------------------------- 文本资源
def version():
    return datetime.datetime.now().strftime("%Y%m%d%H%M")


MANIFEST = """{
  "name": "__NAME__",
  "short_name": "__SHORT__",
  "description": "按本人风格写公众号科普推文：风格规则库 + 事实底稿 + 七步流水线",
  "start_url": "./__PAGE__",
  "scope": "./",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#F7F8F7",
  "theme_color": "#0F6E56",
  "lang": "zh-CN",
  "icons": [
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },
    { "src": "icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
"""

SW = """// 科普写作台 Service Worker —— 自动生成，勿手工编辑
const CACHE = 'kepu-__V__';
const PAGES = ['./', './index.html', './writer.html'];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PAGES).catch(() => {})));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(
      ks.filter(k => k !== CACHE).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // 跨域（模型接口等）与本地服务接口：一律放行，绝不缓存
  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf('/api/') === 0) return;

  if (req.mode === 'navigate') {
    // 页面：网络优先 —— 保证推送后刷新就能看到新版
    e.respondWith(
      fetch(req).then(r => {
        const copy = r.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
        return r;
      }).catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
    );
    return;
  }

  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(r => {
      const copy = r.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return r;
    }).catch(() => hit))
  );
});
"""


def manifest_name(page):
    """两个页面各用自己的清单，否则从写作台"安装"会打开展示台。"""
    return "manifest-writer.webmanifest" if page == "writer.html" else "manifest.webmanifest"


def head_tags(page="index.html"):
    """插到 </head> 之前的 PWA 标签 + 更新提示。"""
    return (
        '<link rel="manifest" href="%s">\n' % manifest_name(page) +
        '<meta name="theme-color" content="#0F6E56">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="apple-mobile-web-app-title" content="科普写作台">\n'
        '<link rel="apple-touch-icon" href="icon-192.png">\n'
        '<link rel="icon" href="icon-192.png">\n'
        '<script>\n'
        'if ("serviceWorker" in navigator && location.protocol.indexOf("http") === 0) {\n'
        '  addEventListener("load", function () {\n'
        '    navigator.serviceWorker.register("sw.js").then(function (reg) {\n'
        '      reg.addEventListener("updatefound", function () {\n'
        '        var nw = reg.installing; if (!nw) return;\n'
        '        nw.addEventListener("statechange", function () {\n'
        '          if (nw.state === "installed" && navigator.serviceWorker.controller) showUpdate();\n'
        '        });\n'
        '      });\n'
        '    }).catch(function () {});\n'
        '  });\n'
        '}\n'
        'function showUpdate(){\n'
        '  var b = document.createElement("div");\n'
        '  b.textContent = "有新版本，点此刷新";\n'
        '  b.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9999;'
        'background:#0F6E56;color:#fff;padding:10px 16px;border-radius:22px;'
        'font-size:13px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.18)";\n'
        '  b.onclick = function(){ location.reload(); };\n'
        '  document.body.appendChild(b);\n'
        '}\n'
        '</script>\n'
    )


def write(out_dir, page="index.html"):
    """把 PWA 资源写到输出目录。返回生成的文件清单。"""
    os.makedirs(out_dir, exist_ok=True)
    made = []

    for size, name in ((192, "icon-192.png"), (512, "icon-512.png")):
        p = os.path.join(out_dir, name)
        write_png(p, make_icon(size))
        made.append(name)

    # maskable 版本：图形往中间收，四周留白，避免被系统裁成圆形时切掉
    n = 512
    u = n / 16.0
    px = [[BG for _ in range(n)] for _ in range(n)]
    _rounded_rect(px, n, n, 4.6 * u, 3.4 * u, 11.4 * u, 12.6 * u, 1.0 * u, PAPER)
    _rounded_rect(px, n, n, 6 * u, 5.2 * u, 10 * u, 6.2 * u, 0.3 * u, INK)
    for i, x1 in enumerate((10.0, 10.0, 8.8)):
        y = 7.6 + i * 1.6
        _rounded_rect(px, n, n, 6 * u, y * u, x1 * u, (y + 0.8) * u, 0.3 * u, INK)
    p = os.path.join(out_dir, "icon-maskable.png")
    write_png(p, px)
    made.append("icon-maskable.png")

    titles = {"index.html": ("科普写作智能体 · 知识库展示台", "知识库"),
              "writer.html": ("科普写作台 · 科普推文写作", "写作台")}
    for pg in ("index.html", "writer.html"):
        fname = manifest_name(pg)
        full, short = titles[pg]
        text = (MANIFEST.replace("__PAGE__", pg)
                        .replace("__NAME__", full)
                        .replace("__SHORT__", short))
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
        if fname not in made:
            made.append(fname)

    with open(os.path.join(out_dir, "sw.js"), "w", encoding="utf-8") as fh:
        fh.write(SW.replace("__V__", version()))
    made.append("sw.js")

    return made
