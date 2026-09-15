#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地写作台小服务：托管页面 + API 转接 + 知识库读写。

用法：
  python writer_server.py                 # http://127.0.0.1:8787
  python writer_server.py --port 9000

它做四件事：
  1. 把 docs/ 目录当静态站点提供（浏览器访问 /writer.html）
  2. POST /api/chat      转发模型请求到你填的 OpenAI 兼容接口（绕开浏览器跨域）
  3. POST /api/extract   解析上传的 docx / pdf / txt / md → 纯文本
  4. POST /api/ingest    抓取微信公众号链接 → 写入 KB1 语料库 → 重算统计
     POST /api/restat    重算语料特征（"自我总结"）
     POST /api/rebuild   重新生成 docs/ 两个页面

为什么这些必须走本地服务：
  浏览器出于安全策略无法直接读写磁盘，也无法跨域抓取 mp.weixin.qq.com。
  本地服务在你自己机器上运行，只监听 127.0.0.1，不对外网开放。

安全：仅监听回环地址；不接受任何外部写入路径（路径由本文件固定推导）。
"""

import argparse
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# 每次改动本文件请+1。页面用它判断"服务是不是旧版本，需要重启"。
SERVER_VERSION = "1.3"
SERVER_STARTED = time.time()
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(TOOLS, ".."))
KB1 = os.path.join(PROJECT, "10_知识库", "01_历史语料库")
DOCS = os.path.join(PROJECT, "docs")
SITE = DOCS
TIMEOUT = 180
MAX_UPLOAD = 40 * 1024 * 1024

sys.path.insert(0, TOOLS)


# ---------------------------------------------------------------- 文本抽取
def extract_text(filename, raw):
    """按扩展名抽取纯文本。优先 markitdown，回退标准库。返回 (text, method)"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md", ".csv", ".tsv", ".json"):
        for enc in ("utf-8", "gb18030", "utf-16"):
            try:
                return raw.decode(enc), "直接解码 " + enc
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace"), "直接解码(容错)"

    # 写临时文件交给 markitdown
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "kepu_upload" + ext)
    with open(tmp, "wb") as fh:
        fh.write(raw)

    try:
        from markitdown import MarkItDown
        text = MarkItDown().convert(tmp).text_content
        if text and len(re.sub(r"\s", "", text)) >= 50:
            return text, "markitdown"
    except Exception:  # noqa: BLE001
        pass

    if ext in (".docx", ".doc", ".dotx"):
        try:
            return _docx_stdlib(tmp), "标准库(zip+xml)"
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"docx 解析失败：{exc}") from exc
    if ext == ".pdf":
        try:
            return _pdf_stdlib(tmp), "标准库(zlib 流解析)"
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"pdf 解析失败：{exc}。建议安装 markitdown（pip install markitdown）"
                "，或把正文复制粘贴进来") from exc
    raise RuntimeError(f"暂不支持的文件类型：{ext}")


def _docx_stdlib(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n in ("word/document.xml",)]
        if not names:
            raise RuntimeError("不是标准 docx（缺少 word/document.xml）")
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    import html as H
    text = H.unescape(xml)
    lines = [re.sub(r"[ \t\u3000]+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def _pdf_stdlib(path):
    """基础 PDF 文本抽取：解压 FlateDecode 流后取 Tj/TJ 字符串。对扫描件无效。"""
    import zlib
    data = open(path, "rb").read()
    chunks = []
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        blob = data[start:end]
        try:
            blob = zlib.decompress(blob)
        except Exception:  # noqa: BLE001
            continue
        for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)", blob):
            s = tm.group(0)[1:-1]
            s = re.sub(rb"\\([()\\])", rb"\1", s)
            chunks.append(s)
        if len(b"".join(chunks)) > 200000:
            break
    if not chunks:
        # 未压缩流（无 FlateDecode）的 PDF：直接在原始数据里扫 Tj/TJ
        for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|TJ|')", data):
            s = tm.group(0)
            s = s[: s.rfind(b")") + 1]
            chunks.append(re.sub(rb"\\([()\\])", rb"\1", s[1:-1]))
    if not chunks:
        raise RuntimeError("未取到文本层（可能是扫描件或加密 PDF）")
    raw = b" ".join(chunks)
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        txt = raw.decode("utf-8", "replace")
    return re.sub(r"[ \t]+", " ", txt).strip()


# ---------------------------------------------------------------- 语料读写
def api_ingest(urls, dry_run=False, genre_override=""):
    import wechat_ingest as W
    os.makedirs(KB1, exist_ok=True)
    existing = [f for f in os.listdir(KB1) if f.startswith("KB1-") and f.endswith(".md")]
    results = []
    nxt = 1
    for u in urls:
        u = u.strip()
        if not u:
            continue
        item = {"url": u, "ok": False, "msg": ""}
        try:
            info, last_err, html = None, "", ""
            for attempt in range(1, W.FETCH_ATTEMPTS + 1):
                try:
                    html = W.fetch(u)
                except Exception as exc:  # noqa: BLE001
                    last_err = f"网络异常：{exc}"
                    continue
                cand = W.extract_wechat(html)
                if cand.get("fail"):
                    last_err = cand["fail"]
                    if cand["fail"] in ("文章已被删除", "内容违规不可见", "链接参数错误"):
                        break
                    continue
                if W.cjk_count(cand["lines"]) >= W.MIN_CJK:
                    info = cand
                    break
                last_err = "正文过少（疑似反爬薄页面）"
            if info is None:
                item["msg"] = f"抓取失败：{last_err or '未知原因'}"
                results.append(item)
                continue

            genre = (genre_override or "").strip().upper() or W.guess_genre(info["title"], info["lines"])
            date = info.get("date") or ""
            stamp = date.replace("-", "")[:6] or "000000"
            idx = f"{len(existing) + nxt:02d}"
            sid = f"KB1-{genre}-{stamp}-{idx}"
            head = re.split(r"[｜|：:，,。？！?!\-—]", info["title"] or "untitled")[0]
            safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", head)[:24].strip("_") or "untitled"
            fname = f"{sid}_{safe}.md"
            if not dry_run:
                with open(os.path.join(KB1, fname), "w", encoding="utf-8") as fh:
                    fh.write(W.to_markdown(info, u, genre, date, sid, authority="微信发布版"))
            nxt += 1
            item.update({
                "ok": True, "title": info["title"], "genre": genre, "date": date,
                "chars": W.net_chars(info["lines"]), "cjk": W.cjk_count(info["lines"]),
                "file": fname, "id": sid, "dry_run": dry_run,
                "msg": "已入库" if not dry_run else "试跑（未写文件）",
            })
        except Exception as exc:  # noqa: BLE001
            item["msg"] = f"异常：{exc}"
        results.append(item)
    return results


def api_restat():
    import corpus_digest as C
    files = sorted(f for f in os.listdir(KB1) if f.startswith("KB1-") and f.endswith(".md"))
    items = [C.parse(os.path.join(KB1, f)) for f in files]
    counted = [i for i in items if i.get("counted", True)]
    n = len(counted)
    if not n:
        return {"total": len(items), "counted": 0}

    def hit(pred, pool=None):
        pool = pool or counted
        return sum(1 for i in pool if pred(i))

    cs = sorted(i["chars"] for i in counted)
    std = [i for i in counted if i["genre"] == "STD"]
    metrics = [
        {"name": "首段不含标准号 / 不含“标准·规定”", "hit": hit(lambda i: not i["first_has_std_no"] and not i["first_has_word"]), "total": n},
        {"name": "零感叹号", "hit": hit(lambda i: i["excl"] == 0), "total": n},
        {"name": "使用 emoji 锚点", "hit": hit(lambda i: i["emoji"] > 0), "total": n},
        {"name": "标题带竖线（系列/栏目稿）", "hit": hit(lambda i: i["title_sep"]), "total": n},
        {"name": "含问句式小标题", "hit": hit(lambda i: i["head_q"] > 0), "total": n},
        {"name": "有来源清单模块（全库）", "hit": hit(lambda i: i["src_name"]), "total": n},
        {"name": "有来源清单模块（仅 STD）", "hit": sum(1 for i in std if i["src_name"]), "total": len(std)},
    ]
    for m in metrics:
        m["pct"] = round(100 * m["hit"] / max(m["total"], 1))
        m["level"] = "R" if (m["hit"] >= 5 and m["pct"] >= 80) else ("T" if m["pct"] >= 40 else "O")
    return {
        "total": len(items), "counted": n,
        "chars": {"p25": cs[n // 4], "p50": cs[n // 2], "p75": cs[3 * n // 4],
                  "min": cs[0], "max": cs[-1]},
        "genres": {g: sum(1 for i in counted if i["genre"] == g)
                   for g in sorted({i["genre"] for i in counted})},
        "metrics": metrics,
        "latest": sorted(i["published"] for i in counted)[-3:],
    }


_NGRAM_CACHE = {"sig": None, "grams": {}, "n": 0}


def corpus_ngrams(n=12):
    """把 KB1 全部语料切成 n 字片段 → {片段: 语料ID}。按语料 mtime 做缓存。

    G3 硬闸：改稿/成稿与历史语料连续 ≥12 字重合即判"疑似洗稿"。
    这件事必须放在服务端做 —— KB1 只存在于本机。
    """
    sig = 0
    files = []
    if os.path.isdir(KB1):
        for f in sorted(os.listdir(KB1)):
            if f.startswith("KB1-") and f.endswith(".md"):
                p = os.path.join(KB1, f)
                mt = os.path.getmtime(p)
                files.append(f)
                sig = (sig * 31 + int(mt * 1000)) % (2 ** 61 - 1)
    if _NGRAM_CACHE["n"] == n and _NGRAM_CACHE["sig"] == sig:
        return _NGRAM_CACHE["grams"]

    grams = {}
    for f in files:
        raw = open(os.path.join(KB1, f), encoding="utf-8").read()
        body = raw.split("---", 2)[-1]
        # 归一化后再切：去加粗标记、去空白、去图片占位
        text = re.sub(r"\*+", "", body)
        text = re.sub(r"\[\[图片\]\]", "", text)
        text = re.sub(r"[\s\u3000]+", "", text)
        m = re.match(r"(KB1-[A-Z]+-\d{6}-\d+)", f)
        sid = m.group(1) if m else f
        for i in range(0, max(0, len(text) - n + 1)):
            g = text[i:i + n]
            if g not in grams:
                grams[g] = sid
    _NGRAM_CACHE.update({"sig": sig, "grams": grams, "n": n})
    sys.stderr.write(f"  [查重] KB1 n-gram 索引已重建：{len(grams)} 条（{len(files)} 篇）\n")
    return grams


def norm_for_compare(text):
    """查重前的归一化：去加粗标记 + 去空白。

    单独抽成函数，供 /api/dupcheck 与命令行工具 tools/dupcheck.py 共用同一口径
    （命令行那边还要用它定位书名号区间，判断重合是否落在标准/文件名称里）。
    """
    return re.sub(r"[\s\u3000]+", "", re.sub(r"\*+", "", text or ""))


def api_dupcheck(text, n=12):
    """比对文本与 KB1，返回连续重合片段及来源篇目（合并后按长度排序）。"""
    grams = corpus_ngrams(n)
    clean = norm_for_compare(text)
    hits, i = [], 0
    while i <= len(clean) - n:
        g = clean[i:i + n]
        if g in grams:
            j = i + n
            while j < len(clean) and clean[j:j + n] in grams:
                j += 1
            hits.append({"snippet": clean[i:j], "src": grams[g],
                         "at": i, "end": j, "src_all": {grams[g]}})
            i = j
        else:
            i += 1
    merged = []
    for h in hits:
        if merged and h["src"] == merged[-1]["src"] and h["at"] - merged[-1]["end"] < n:
            merged[-1]["snippet"] += h["snippet"]
            merged[-1]["end"] = h["end"]
        else:
            merged.append(dict(h))
    merged.sort(key=lambda x: -len(x["snippet"]))
    for h in merged:
        h.pop("src_all", None)
    return {
        "n": n,
        "clean_len": len(clean),
        "hits": merged[:20],
        "n_hits": len(merged),
        "longest": len(merged[0]["snippet"]) if merged else 0,
        "pass": (len(merged) == 0),
    }


def features_path():
    """统计缓存必须落在工程根目录 —— 与 build_site.py 读取的位置保持一致。

    历史 bug：这里曾写成 dirname 三次，落到工程的**上一层**，
    于是"入库 → 重算统计"写的是一处、"重建页面"读的是另一处，
    结果工作台永远显示旧数据（实测缓存里 80 篇、页面只有 24 篇）。
    KB1 = <工程>/10_知识库/01_历史语料库，所以向上两级才是工程根。
    """
    return os.path.join(os.path.dirname(os.path.dirname(KB1)),
                        "_corpus_features.json")


def api_ping():
    """服务自检：版本、启动时间、语料数，以及"代码是否比进程新"。

    为什么要"代码是否比进程新"
        服务一旦启动，跑的就是启动那一刻的代码。之后我改了脚本，
        你那边不重启就还是旧逻辑（实测踩过：入库不重建页面）。
        这里把磁盘上脚本的修改时间与进程启动时间比一比，页面上直接提示重启。
    """
    try:
        code_mtime = os.path.getmtime(os.path.abspath(__file__))
    except OSError:
        code_mtime = 0
    n_files = len([f for f in os.listdir(KB1)
                   if f.startswith("KB1-") and f.endswith(".md")]) if os.path.isdir(KB1) else 0
    n_counted = 0
    fp = features_path()
    if os.path.exists(fp):
        try:
            with open(fp, encoding="utf-8") as fh:
                n_counted = len(json.load(fh))
        except Exception:
            pass
    return {
        "version": SERVER_VERSION,
        "started_at": datetime.datetime.fromtimestamp(SERVER_STARTED).strftime("%Y-%m-%d %H:%M"),
        "code_mtime": datetime.datetime.fromtimestamp(code_mtime).strftime("%Y-%m-%d %H:%M"),
        "need_restart": code_mtime > SERVER_STARTED + 1,
        "kb1": KB1,
        "files": n_files,
        "counted": n_counted,
    }


def run_script(name, *argv):
    p = os.path.join(TOOLS, name)
    out = subprocess.run([sys.executable, p, *argv], capture_output=True, text=True, cwd=TOOLS)
    return out.returncode, (out.stdout or "")[-3000:], (out.stderr or "")[-1500:]


class Handler(BaseHTTPRequestHandler):
    server_version = "KepuWriter/1.2"

    # 允许来自线上页面（GitHub Pages 等）的跨域调用。
    # 背景：https 页面调 http://127.0.0.1 属于"公网 → 私有网络"请求，
    # 浏览器会先发 OPTIONS 预检；服务端必须回 CORS 头并显式允许私有网络，
    # 否则 fetch 一定失败（这是线上页面调不到本地服务的真正原因）。
    CORS_HEADERS = (
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Filename-B64"),
        ("Access-Control-Max-Age", "86400"),
        ("Access-Control-Allow-Private-Network", "true"),
    )

    def log_message(self, fmt, *args):
        code = str(args[1] if len(args) > 1 else "")
        if code.startswith(("4", "5")):
            sys.stderr.write("  %s\n" % (fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in self.CORS_HEADERS:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------------------------------------------------- 静态
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            path = "/index.html"
        target = os.path.normpath(os.path.join(SITE, path.lstrip("/")))
        if not target.startswith(os.path.abspath(SITE)) or not os.path.isfile(target):
            self.send_error(404, "not found")
            return
        ctype = "text/html; charset=utf-8"
        if target.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        elif target.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif target.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif target.endswith(".webmanifest"):
            # PWA 清单必须用这个类型，否则浏览器不认
            ctype = "application/manifest+json; charset=utf-8"
        elif target.endswith(".png"):
            ctype = "image/png"
        elif target.endswith(".svg"):
            ctype = "image/svg+xml"
        elif target.endswith(".ico"):
            ctype = "image/x-icon"
        with open(target, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in self.CORS_HEADERS:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------- POST 路由
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/chat":
                return self.h_chat()
            if path == "/api/extract":
                return self.h_extract()
            if path == "/api/ingest":
                return self.h_ingest()
            if path == "/api/restat":
                return self.h_json(200, api_restat())
            if path == "/api/ping":
                return self.h_json(200, api_ping())
            if path == "/api/dupcheck":
                payload = self._read_json()
                return self.h_json(200, api_dupcheck(str(payload.get("text") or ""),
                                                     int(payload.get("n") or 12)))
            if path == "/api/rebuild":
                return self.h_rebuild()
            self.send_error(404, "not found")
        except Exception as exc:  # noqa: BLE001
            self.h_json(500, {"error": {"message": f"{type(exc).__name__}: {exc}"}})

    # ---------------------------------------------------------- 各处理器
    def h_chat(self):
        payload = self._read_json()
        base = (payload.pop("_base", "") or "").rstrip("/")
        key = payload.pop("_key", "") or ""
        if not base:
            return self.h_json(400, {"error": {"message": "缺少 _base（接口地址）"}})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + "/chat/completions", data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                self.h_raw(200, resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            self.h_json(exc.code, {"error": {"message": f"上游接口返回 {exc.code}：{detail}"}})
        except Exception as exc:  # noqa: BLE001
            self.h_json(502, {"error": {"message": f"转发失败：{exc}"}})

    def h_extract(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self.h_json(413, {"error": {"message": "文件过大（上限 40MB）"}})
        raw = self.rfile.read(length)
        name_b64 = self.headers.get("X-Filename-B64") or ""
        name = base64.b64decode(name_b64).decode("utf-8", "replace") if name_b64 else "upload.txt"
        text, method = extract_text(name, raw)
        self.h_json(200, {"name": name, "chars": len(re.sub(r"\s", "", text)),
                          "method": method, "text": text})

    def h_ingest(self):
        payload = self._read_json()
        urls = payload.get("urls") or []
        if isinstance(urls, str):
            urls = [u for u in re.split(r"[\s,]+", urls) if u.strip()]
        if not urls:
            return self.h_json(400, {"error": {"message": "没有可用的链接"}})
        results = api_ingest(urls, dry_run=bool(payload.get("dry_run")),
                             genre_override=str(payload.get("genre") or ""))
        ok = sum(1 for r in results if r["ok"])
        # 入库后：重算统计 + 重建台账 + **重建页面**
        # 页面数据是烤进 HTML 的，不重建就等于"知识库更新了但工作台看不到"。
        restat, rebuilt, note = None, False, ""
        if ok and not payload.get("dry_run"):
            run_script("kb_index.py", KB1, "--fix", "--csv")
            run_script("corpus_digest.py", KB1, "--json", features_path())
            restat = api_restat()
            a = run_script("build_site.py", "--project", PROJECT, "--out", DOCS)
            b = run_script("build_writer.py", "--project", PROJECT, "--out", DOCS)
            rebuilt = a[0] == 0 and b[0] == 0
            if not rebuilt:
                note = ("页面重建失败（数据已入库，可点「重建页面」重试）："
                        + (a[2] or b[2])[-160:])
        self.h_json(200, {"results": results, "ok": ok, "fail": len(results) - ok,
                          "restat": restat, "rebuilt": rebuilt, "note": note,
                          "kb1": KB1})

    def h_rebuild(self):
        a = run_script("build_site.py", "--project", PROJECT, "--out", DOCS)
        b = run_script("build_writer.py", "--project", PROJECT, "--out", DOCS)
        ok = a[0] == 0 and b[0] == 0
        self.h_json(200 if ok else 500, {
            "ok": ok, "site": a[1][-200:], "writer": b[1][-200:],
            "hint": "页面已重建；刷新浏览器即可看到最新数据"
                    if ok else f"重建失败：{a[2][-200:]} {b[2][-200:]}",
        })

    # ---------------------------------------------------------- 工具
    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"请求体不是合法 JSON：{exc}") from exc

    def h_raw(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in self.CORS_HEADERS:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def h_json(self, code, obj):
        self.h_raw(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--site", default=DOCS)
    args = ap.parse_args()

    global SITE
    SITE = os.path.abspath(args.site)
    if not os.path.isdir(SITE):
        print(f"[失败] 找不到站点目录：{SITE}", file=sys.stderr)
        return 1
    if not os.path.isdir(KB1):
        print(f"[警告] 找不到语料库目录：{KB1}（入库功能将不可用）", file=sys.stderr)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("科普写作台已启动")
    print(f"  写作台：http://{args.host}:{args.port}/writer.html")
    print(f"  展示台：http://{args.host}:{args.port}/index.html")
    print(f"  站点目录：{SITE}")
    print(f"  语料库：{KB1}")
    print("  能力：模型转接 · 文件解析(docx/pdf) · 链接入库 · 统计重算 · 页面重建")
    print("  按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
