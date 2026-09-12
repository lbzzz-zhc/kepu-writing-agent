#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语料特征提取器：对 KB1 历史语料库做量化统计，为风格规则提供"出现频率"证据。

用法：
  python corpus_digest.py <KB1目录>                 # 汇总统计
  python corpus_digest.py <KB1目录> --detail        # 附每篇明细
  python corpus_digest.py <KB1目录> --json out.json # 另存 JSON

为什么需要它：
  风格规则必须带"出现频率"，靠人读容易凭印象。本工具把标题型态、开篇、小标题、
  结尾模块、标点、标准号密度等可数指标一次性算出来，规则升级/降级都有据可依。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

SRC_PAT = re.compile(r"^(内容来源|内容参考|资料来源|参考资料|来源)[:：]?$")
STD_FIND = re.compile(r"(?:GB/T|GB|ISO|T/[A-Z]{2,6}|JJF|QB/T|SB/T|NY/T)\s?\d[\d.\-—]*")
IMG_PAT = re.compile(r"^\[\[图片\]\]$")
CAPTION_PAT = re.compile(r"^(图片来源|图注|注)[:：]")
EMOJI_PAT = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
TITLE_SEPS = ("｜", "丨", "|", " l ", " I ", "丨")


def parse(path):
    text = open(path, encoding="utf-8").read()
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.split("#")[0].strip()
            body = parts[2]

    raw = [l.strip() for l in body.splitlines() if l.strip()]
    raw = [re.sub(r"\*\*", "", l) for l in raw]        # 去掉 docx 转换残留的加粗标记
    raw = [l for l in raw if not IMG_PAT.match(l) and not CAPTION_PAT.match(l)]
    raw = [l for l in raw if not l.startswith("# ")]

    src_name, src_items, body_lines = "", [], []
    for line in raw:
        m = SRC_PAT.match(line)
        if m:
            src_name = m.group(1)
            continue
        (src_items if src_name else body_lines).append(line)

    heads = [l for l in body_lines
             if len(l) <= 22 and not l.endswith("。")
             and not re.match(r"^\d+[.、]", l)]
    joined = "".join(body_lines)
    first = body_lines[0] if body_lines else ""
    lens = [len(l) for l in body_lines]
    stds = sorted(set(STD_FIND.findall(joined)))
    title = meta.get("title", "")

    return {
        "file": os.path.basename(path),
        "id": meta.get("id", ""),
        "title": title,
        "published": meta.get("published", ""),
        "genre": meta.get("genre", ""),
        "chars": len(re.sub(r"\s", "", joined)),
        "paras": len(body_lines),
        "avg_para": round(sum(lens) / max(len(lens), 1), 1),
        "median_para": sorted(lens)[len(lens) // 2] if lens else 0,
        "first_len": len(first),
        "first_has_std_no": bool(STD_FIND.search(first)),
        "first_has_word": ("标准" in first or "规定" in first),
        "first_text": first[:70],
        "heads": heads,
        "n_heads": len(heads),
        "head_q": sum(1 for h in heads if h.endswith(("？", "?"))),
        "src_name": src_name,
        "n_src": len(src_items),
        "stds": stds,
        "n_std": len(stds),
        "excl": joined.count("！") + joined.count("!"),
        "excl_fw": joined.count("！"),
        "ques": joined.count("？") + joined.count("?"),
        "comma_fw": joined.count("，"),
        "comma_hw": joined.count(","),
        "title_sep": next((s for s in TITLE_SEPS if s in title), ""),
        "emoji": len(EMOJI_PAT.findall(joined)),
        "counted": meta.get("counted", "true") != "false",
        "variant_of": meta.get("variant_of", ""),
    }


def pct(n, d):
    return f"{n}/{d} ({round(100 * n / max(d, 1))}%)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kbd")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.kbd)
                   if f.startswith("KB1-") and f.endswith(".md"))
    if not files:
        print("[失败] 目录下没有 KB1-*.md", file=sys.stderr)
        return 1

    items = [parse(os.path.join(args.kbd, f)) for f in files]
    dup = [i for i in items if not i["counted"]]
    if dup:
        print(f"\n[提示] 排除 {len(dup)} 篇变体/同题版本，不计入频率统计："
              f"{[i['id'] for i in dup]}")
    items = [i for i in items if i["counted"]]
    n = len(items)

    print(f"\n{'='*60}\n语料量化特征（{n} 篇）\n{'='*60}")

    print("\n【标题】")
    print(f"  含竖线分隔: {pct(sum(1 for i in items if i['title_sep']), n)}")
    seps = Counter(i["title_sep"] for i in items if i["title_sep"])
    print(f"  分隔符分布: {dict(seps)}")
    print(f"  标题带问号: {pct(sum(1 for i in items if '？' in i['title'] or '?' in i['title']), n)}")
    print(f"  平均标题长度: {round(sum(len(i['title']) for i in items)/n, 1)} 字")

    print("\n【开篇】")
    print(f"  首段不含标准号: {pct(sum(1 for i in items if not i['first_has_std_no']), n)}")
    print(f"  首段不含'标准/规定'字样: {pct(sum(1 for i in items if not i['first_has_word']), n)}")
    print(f"  首段平均长度: {round(sum(i['first_len'] for i in items)/n, 1)} 字")

    print("\n【分节】")
    print(f"  小标题数 中位数: {sorted(i['n_heads'] for i in items)[n//2]}"
          f"  区间: {min(i['n_heads'] for i in items)}–{max(i['n_heads'] for i in items)}")
    print(f"  含问句式小标题: {pct(sum(1 for i in items if i['head_q']), n)}")

    print("\n【结尾来源模块】")
    print(f"  含来源模块: {pct(sum(1 for i in items if i['src_name']), n)}")
    print(f"  模块名分布: {dict(Counter(i['src_name'] for i in items if i['src_name']))}")
    print(f"  来源条目数 中位数: {sorted(i['n_src'] for i in items)[n//2]}")

    print("\n【篇幅】")
    print(f"  净字数区间: {min(i['chars'] for i in items)}–{max(i['chars'] for i in items)}"
          f"  中位数: {sorted(i['chars'] for i in items)[n//2]}")
    print(f"  段落数 中位数: {sorted(i['paras'] for i in items)[n//2]}"
          f"  平均段长 中位数: {sorted(i['avg_para'] for i in items)[n//2]} 字")

    print("\n【标点与符号】")
    print(f"  感叹号合计: {sum(i['excl'] for i in items)} 处"
          f"（无感叹号的篇数: {pct(sum(1 for i in items if i['excl']==0), n)}）"
          f"  其中全角「！」{sum(i['excl_fw'] for i in items)} 处")
    print(f"  问号合计: {sum(i['ques'] for i in items)} 处")
    print(f"  逗号全角/半角: {sum(i['comma_fw'] for i in items)} / "
          f"{sum(i['comma_hw'] for i in items)}")
    print(f"  使用 emoji: {pct(sum(1 for i in items if i['emoji']), n)}")

    print("\n【标准引用】")
    print(f"  每篇引用标准数 中位数: {sorted(i['n_std'] for i in items)[n//2]}"
          f"  区间: {min(i['n_std'] for i in items)}–{max(i['n_std'] for i in items)}")
    allstd = Counter()
    for i in items:
        allstd.update(i["stds"])
    print(f"  全库标准号总数: {len(allstd)}")

    print("\n【文体分布】")
    print(f"  {dict(Counter(i['genre'] for i in items))}")

    print("\n【时间跨度】")
    pubs = sorted(i["published"] for i in items if re.match(r"\d{4}-", i["published"]))
    if pubs:
        print(f"  {pubs[0]} → {pubs[-1]}")

    if args.detail:
        print(f"\n{'='*60}\n逐篇明细\n{'='*60}")
        for i in items:
            print(f"\n▸ {i['id']}  {i['genre']}  {i['chars']}字  {i['published']}")
            print(f"  标题: {i['title']}")
            print(f"  开篇: {i['first_text']}")
            print(f"  小标题({i['n_heads']}): {' / '.join(i['heads'][:6])}")
            print(f"  来源: {i['src_name'] or '(无)'} × {i['n_src']}   标准 {i['n_std']} 个"
                  f"   叹号 {i['excl']}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=2)
        print(f"\n[已存 JSON] {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
