#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信推文 → KB1 历史语料库 入库工具

用途：把微信公众号文章链接抓成符合 KB1 规范的 Markdown 语料文件，
      免去每次手工导出 docx 上传。

用法：
  python wechat_ingest.py <url> [--out DIR] [--genre STD] [--date 2026-08-15]
  python wechat_ingest.py --file <本地.html|.md|.txt> [--out DIR]
  python wechat_ingest.py <url> --dry-run          # 只诊断，不写文件

设计要点：
  - 所有入库文件头部恒带 fact_usable: false —— 历史语料只能提供风格，不能提供事实。
  - 抓取失败时输出明确的失败原因，绝不产出残缺语料充数。
  - 只依赖标准库，无第三方包。
"""

import argparse
import datetime
import html as htmllib
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN"
)

GENRE_KEYWORDS = {
    "STD": ["国家标准", "GB/T", "GB ", "标准发布", "正式实施", "强制性国家标准",
            "行业标准", "团体标准", "ISO ", "标准规定", "执行标准"],
    "AVOID": ["怎么选", "选购", "避坑", "误区", "别买", "挑选", "看懂", "陷阱",
              "值不值", "划不划算", "擦边", "宣传"],
    "HEALTH": ["营养", "摄入", "膳食", "热量", "食用", "成分表", "膳食纤维",
               "维生素", "健康影响", "人体"],
    "SAFETY": ["安全", "风险", "防护", "使用提醒", "注意事项", "危险", "事故",
               "正确使用", "洗护", "更换"],
}

FAIL_MARKERS = [
    ("该内容已被发布者删除", "文章已被删除"),
    ("此内容因违规无法查看", "内容违规不可见"),
    ("环境异常", "触发微信环境校验（需换路径）"),
    ("去验证", "需要人工验证（需换路径）"),
    ("参数错误", "链接参数错误"),
    ("系统繁忙", "微信系统繁忙，请稍后重试"),
]

# 抓取重试参数：微信反爬为间歇性，单次失败不代表链接不可用
FETCH_ATTEMPTS = 3      # 最多请求次数
FETCH_BACKOFF = 2.0     # 每次重试间隔（秒）
MIN_CJK = 300           # 正文汉字下限，低于此判为未取到正文


# ---------------------------------------------------------------- 抓取
def fetch(url, timeout=25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": WECHAT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mp.weixin.qq.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- HTML → 文本
IMG_TOKEN = "\x00IMG\x00"
BOLD_PATS = (
    r"(?is)<strong[^>]*>(.*?)</strong>",
    r"(?is)<b[^>]*>(.*?)</b>",
    r"(?is)<span[^>]*font-weight\s*:\s*(?:bold|700|800|900)[^>]*>(.*?)</span>",
)
ATTR_CAPTION = re.compile(r"^(图片来源|图源|图片来自|来源图)[:：]")
CJK_OR_WORD = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def _cjk(s):
    return len(re.findall(r"[\u4e00-\u9fff]", s))


def html_to_lines(fragment, keep_images=False, keep_captions=False):
    """把微信正文 HTML 转成"排版好的纯文本"行列表。

    规则：
      · 去图片：默认不保留图片占位，也不保留图注（可用开关保留）
      · 还原小标题：整行加粗且短 → 写成 **小标题**；行内零星加粗 → 抹平（不污染正文）
      · 不做 NFKC 归一化，保护全角标点这一层风格特征
    返回 (lines, stats)
    """
    stats = {"images": 0, "captions_dropped": 0, "captions_kept": [], "headings": 0}
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", fragment)

    # Markdown 图片语法（docx→md 转换会带来 ![](data:image...) 这类噪音）
    fragment = re.sub(r"(?s)!\[[^\]]*\]\([^)]*\)", "\n" + IMG_TOKEN + "\n", fragment)
    # 图片 → 令牌（保留位置信息，便于判断图注）
    fragment = re.sub(r"(?is)<img[^>]*>", "\n" + IMG_TOKEN + "\n", fragment)
    stats["images"] = fragment.count(IMG_TOKEN)

    # 加粗 → **...**（先还原再剥标签，才能保住"哪些字是加粗的"这一信息）
    for pat in BOLD_PATS:
        fragment = re.sub(pat, r"**\1**", fragment)

    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|section|div|li|tr|h[1-6]|blockquote)>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", "", fragment)
    fragment = htmllib.unescape(fragment)

    # 切成条目，区分"图片位置"与"文本"
    entries = []
    for chunk in fragment.split("\n"):
        piece = chunk.replace(IMG_TOKEN, "")
        piece = piece.replace("\u200b", "").replace("\ufeff", "")
        piece = re.sub(r"[ \t\u3000]+", " ", piece).strip()
        if chunk.count(IMG_TOKEN):
            entries.append(("img", ""))
        if piece:
            entries.append(("text", piece))

    # 图注判定：紧邻图片位置、长度 ≤45、不以句号结尾、不含标准号/书名号
    def is_caption(idx):
        s = entries[idx][1]
        if not s or len(s) > 45 or s.endswith("。"):
            return False
        if "《" in s or re.search(r"(GB|ISO|T/[A-Z])", s):
            return False
        near = [entries[j][0] for j in (idx - 1, idx + 1) if 0 <= j < len(entries)]
        return "img" in near

    out = []
    for i, (kind, s) in enumerate(entries):
        if kind == "img":
            continue
        if ATTR_CAPTION.match(s) or (not keep_captions and is_caption(i)):
            stats["captions_dropped"] += 1
            if len(stats["captions_kept"]) < 8:
                stats["captions_kept"].append(s[:40])
            continue
        # 折叠嵌套加粗产生的重复标记（<b><strong>…</strong></b> → ****x****）
        s = re.sub(r"\*{2,}", "**", s)
        # 整行加粗且短 → 小标题；否则抹平加粗，避免行内强调污染正文
        m = re.fullmatch(r"\*\*(.+?)\*\*", s, re.S)
        if m and len(m.group(1)) <= 24 and _cjk(m.group(1)) >= 2:
            s = "**" + re.sub(r"\*+", "", m.group(1)).strip() + "**"
            stats["headings"] += 1
        else:
            s = re.sub(r"\*+", "", s).strip()
        # 去图后残留的孤立符号行（如孤零零一个 📖）不入库
        if s and not (len(s) <= 4 and not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", s)):
            out.append(s)

    # 去掉连续重复行（微信排版常产生重复层）
    lines = []
    for line in out:
        if not lines or lines[-1] != line:
            lines.append(line)
    return lines, stats


def _strip_tags(fragment):
    """兼容旧调用：只取纯文本，不保留加粗、不保留图片。"""
    lines, _ = html_to_lines(fragment, keep_images=False, keep_captions=True)
    return lines


def extract_wechat(html):
    """返回 dict：title / account / date / lines / images / fail"""
    info = {"title": "", "account": "", "date": "", "lines": [], "images": 0, "fail": ""}

    for marker, reason in FAIL_MARKERS:
        if marker in html:
            info["fail"] = reason
            return info

    m = re.search(r'(?is)<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html)
    if m:
        info["title"] = htmllib.unescape(m.group(1)).strip()
    if not info["title"]:
        m = re.search(r'(?is)<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', html)
        if m:
            info["title"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()

    m = re.search(r'(?is)<meta[^>]+property="og:article:author"[^>]+content="([^"]*)"', html)
    if m:
        info["account"] = htmllib.unescape(m.group(1)).strip()
    if not info["account"]:
        m = re.search(r'(?is)id="js_name"[^>]*>(.*?)<', html)
        if m:
            info["account"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    m = re.search(r'(?is)<em[^>]*id="publish_time"[^>]*>(.*?)</em>', html)
    if m:
        info["date"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not info["date"]:
        m = re.search(r'var\s+ct\s*=\s*"(\d{9,11})"', html)
        if m:
            ts = int(m.group(1))
            info["date"] = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone(datetime.timedelta(hours=8))
            ).strftime("%Y-%m-%d")
    if not info["date"]:
        m = re.search(r'(?is)<meta[^>]+property="article:published_time"[^>]+content="([^"]*)"', html)
        if m:
            info["date"] = m.group(1)[:10]

    body = _extract_js_content(html)
    info["images"] = body[1]
    info["lines"] = body[0]

    if not info["lines"]:
        info["lines"] = _generic_body(html)
        info["fail"] = "" if info["lines"] else ("未找到正文节点（可能需要换路径）")
    return info


def _extract_js_content(html, keep_images=False, keep_captions=False):
    m = re.search(r'(?is)<div[^>]*id="js_content"[^>]*>', html)
    if not m:
        return ([], 0, {})
    start = m.end()
    depth, end = 1, len(html)
    for tag in re.finditer(r"(?i)<(/?)div\b[^>]*>", html[start:]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            end = start + tag.start()
            break
    region = html[start:end]
    lines, stats = html_to_lines(region, keep_images=keep_images,
                                 keep_captions=keep_captions)
    return (lines, stats.get("images", 0), stats)


def _generic_body(html, keep_images=False, keep_captions=False):
    """非微信页的兜底提取。始终返回 lines（不返回 stats）。"""
    container = html
    m = re.search(r"(?is)<article[^>]*>(.*?)</article>", html)
    if m:
        container = m.group(1)
    else:
        ps = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html)
        if len(ps) >= 5:
            return html_to_lines("\n".join(ps), keep_images, keep_captions)[0]
    return html_to_lines(container, keep_images, keep_captions)[0]


# ---------------------------------------------------------------- 元数据判定
def _flat(lines):
    """去掉加粗标记后再统计，保证与历史字数口径一致（**不占位**）。"""
    return re.sub(r"\*{2,}", "", "".join(lines))


def net_chars(lines):
    return len(re.sub(r"\s", "", _flat(lines)))


def cjk_count(lines):
    return len(re.findall(r"[\u4e00-\u9fff]", _flat(lines)))


def guess_genre(title, lines):
    """粗判文体：全文关键词计分。仅为初判，入库后须人工复核。"""
    blob = (title or "") + "".join(lines)
    scores = {g: sum(blob.count(k) for k in ks) for g, ks in GENRE_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "待判定"


def parse_manual_date(name):
    m = re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", name)
    if m:
        return "-".join(m.groups())
    return ""


# ---------------------------------------------------------------- 输出
def to_markdown(info, url, genre, date, source_note, authority="微信发布版"):
    lines = info["lines"]
    n_chars, n_cjk = net_chars(lines), cjk_count(lines)
    quality = "完整" if n_cjk >= 600 else ("疑似残缺" if n_cjk >= 200 else "残缺")
    acc = info.get("account") or "待确认"
    fm = [
        "---",
        f"id: {source_note}",
        f"title: {info['title'] or '待确认'}",
        f"published: {date or info.get('date') or '待确认'}",
        f"authority: {authority}",
        f"word_count_net: {n_chars}",
        f"cjk_count: {n_cjk}",
        f"genre: {genre or guess_genre(info['title'], lines)}",
        f"byline: 待确认",
        f"account: {acc}",
        f"original_mark: 待确认",
        "diluted: false",
        f"parse_quality: {quality}",
        f"images: {info.get('images', 0)}",
        f"source_url: {url or '本地文件'}",
        "fact_usable: false",
        "---",
        "",
        f"# {info['title'] or '（无标题）'}",
        "",
    ]
    return "\n".join(fm) + "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="")
    ap.add_argument("--file", dest="file", default="")
    ap.add_argument("--out", dest="out", default=".")
    ap.add_argument("--genre", dest="genre", default="")
    ap.add_argument("--date", dest="date", default="")
    ap.add_argument("--index", dest="index", default="01")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-raw", dest="keep_raw", action="store_true",
                    help="保存抓取到的原始 HTML，便于排查反爬问题")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        html = content if "<" in content[:2000] else ""
        if html:
            info = extract_wechat(html)
        else:
            m = re.match(r"\s*\*{0,2}标题[:：]\*{0,2}\s*(.+)", content)
            title = m.group(1).strip().strip("*") if m else ""
            if not title:
                title = os.path.splitext(os.path.basename(args.file))[0]
            lines = _strip_tags(content)
            lines = [l for l in lines if not re.match(r"^\*{0,2}标题[:：]", l)]
            info = {"title": title, "account": "", "date": "",
                    "lines": lines, "images": 0, "fail": ""}
        url = ""
    elif args.url:
        # 微信反爬是间歇性的：同一链接可能第 1 次给薄页面、第 2 次给完整页。
        # 因此必须重试，而不是一次失败就放弃。
        info, last_err, html = None, "", ""
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                html = fetch(args.url)
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                time.sleep(FETCH_BACKOFF)
                continue
            except Exception as e:  # noqa: BLE001
                last_err = f"网络异常：{e}"
                time.sleep(FETCH_BACKOFF)
                continue
            if not html.strip():
                last_err = "页面为空（微信未返回内容）"
                time.sleep(FETCH_BACKOFF)
                continue
            cand = extract_wechat(html)
            if cand.get("fail"):
                hard = cand["fail"] in ("文章已被删除", "内容违规不可见", "链接参数错误")
                info = cand
                last_err = cand["fail"]
                if hard:            # 硬失败重试无意义
                    break
                time.sleep(FETCH_BACKOFF)
                continue
            if cjk_count(cand["lines"]) >= MIN_CJK:
                info = cand
                break
            info = cand            # 保留最后一次，用于最终报错
            last_err = f"正文过少（疑似反爬薄页面，第 {attempt}/{FETCH_ATTEMPTS} 次）"
            if attempt < FETCH_ATTEMPTS:
                print(f"[重试] 第 {attempt} 次返回正文过少，{FETCH_BACKOFF}s 后重试…",
                      file=sys.stderr)
                time.sleep(FETCH_BACKOFF)
        if info is None:
            print(f"[失败] {last_err}｜{args.url}", file=sys.stderr)
            return 2
        if args.keep_raw and html:
            os.makedirs(args.out, exist_ok=True)
            raw_path = os.path.join(args.out, "_raw_html.html")
            with open(raw_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            print(f"[已存原始页] {raw_path}")
        url = args.url
    else:
        print("[失败] 需要提供链接或 --file", file=sys.stderr)
        return 1

    if info.get("fail"):
        print(f"[失败] {info['fail']}｜{url or args.file}", file=sys.stderr)
        print("       处理建议：改用路径 B（浏览器打开后复制正文）或路径 C（导出文件）。",
              file=sys.stderr)
        return 3

    # 低内容量护栏：宁可报失败，也不产出残缺语料污染风格库
    cjk = cjk_count(info["lines"])
    if cjk < MIN_CJK:
        print(f"[失败] 正文提取量过少（仅 {cjk} 个汉字），已重试 {FETCH_ATTEMPTS} 次仍不足",
              file=sys.stderr)
        print("       判断为：需要人工验证 / 已被删除 / 正文以图片承载。", file=sys.stderr)
        print("       处理建议：改用路径 B（浏览器打开后全选复制正文粘贴给我）"
              "或路径 C（导出文件后入库）。", file=sys.stderr)
        return 3

    date = args.date or parse_manual_date(os.path.basename(args.file or ""))
    genre = args.genre or guess_genre(info["title"], info["lines"])
    stamp = (date or info.get("date") or datetime.date.today().isoformat()).replace("-", "")[:6]
    sid = f"KB1-{genre}-{stamp}-{args.index}"

    print(f"标题      : {info['title']}")
    print(f"公众号    : {info.get('account') or '(未获取)'}  发布日期: {date or info.get('date') or '(未获取)'}")
    print(f"文体(推测): {genre}")
    print(f"净字数    : {net_chars(info['lines'])}  (汉字 {cjk_count(info['lines'])})")
    print(f"段落数    : {len(info['lines'])}   图片 {info.get('images', 0)} 张")
    print(f"语料ID    : {sid}")

    if args.dry_run:
        print("\n--- 前 5 段预览 ---")
        for line in info["lines"][:5]:
            print("  " + line[:80])
        return 0

    os.makedirs(args.out, exist_ok=True)
    # 文件名清洗：取主标题（竖线/冒号/问号前）+ 去掉全角标点，避免超长文件名
    head = re.split(r"[｜|：:，,。？！?!\-—]", info["title"] or "untitled")[0]
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", head)[:24].strip("_") or "untitled"
    path = os.path.join(args.out, f"{sid}_{safe}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(to_markdown(info, url, genre, date, sid))
    print(f"\n[已入库] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
