#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KB1 台账维护工具：统一字数口径 + 重算元数据 + 重新生成语料清单。

用法：
  python kb_index.py <KB1目录>              # 只报告
  python kb_index.py <KB1目录> --fix        # 修正文件头统计字段
  python kb_index.py <KB1目录> --csv        # 重新生成 语料清单.csv

口径（与 corpus_digest.py 完全一致）：
  净字数 = 正文全部字符 − 空白 − 图片占位行 − 来源清单及其之后的所有行
  这样"篇幅"只反映真正的正文，不受配图数量与参考文献长度影响。
"""

import argparse
import csv
import os
import re
import sys

IMG_PAT = re.compile(r"^\[\[图片\]\]$")
SRC_PAT = re.compile(r"^(内容来源|内容参考|资料来源|参考资料|来源)[:：]?$")
CAPTION_PAT = re.compile(r"^(图片来源|图注|注)[:：]")
FM_KEYS = ("word_count_net", "cjk_count")


def split_file(path):
    text = open(path, encoding="utf-8").read()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.split("#")[0].strip()
    return meta, parts[2]


def body_lines(body):
    lines, out, in_src = [], [], False
    for raw in body.splitlines():
        l = re.sub(r"\*\*", "", raw.strip())
        if not l or IMG_PAT.match(l) or l.startswith("# ") or CAPTION_PAT.match(l):
            continue
        if SRC_PAT.match(l):
            in_src = True
        if in_src:
            continue
        lines.append(l)
    return lines


def counts(lines):
    joined = "".join(lines)
    return len(re.sub(r"\s", "", joined)), len(re.findall(r"[\u4e00-\u9fff]", joined))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kbd")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--csv", dest="make_csv", action="store_true")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.kbd)
                   if f.startswith("KB1-") and f.endswith(".md"))
    rows, changed = [], 0
    for f in files:
        path = os.path.join(args.kbd, f)
        meta, body = split_file(path)
        lines = body_lines(body)
        n_chars, n_cjk = counts(lines)
        counted = meta.get("counted", "true") != "false"
        old = meta.get("word_count_net", "")
        flag = ""
        if str(old) != str(n_chars) and old:
            flag = f"  (原 {old} → 修正)"
            if args.fix:
                text = open(path, encoding="utf-8").read()
                text = re.sub(r"^word_count_net:.*$", f"word_count_net: {n_chars}",
                              text, count=1, flags=re.M)
                if re.search(r"^cjk_count:", text, re.M):
                    text = re.sub(r"^cjk_count:.*$", f"cjk_count: {n_cjk}",
                                  text, count=1, flags=re.M)
                else:
                    text = text.replace("word_count_net:", f"cjk_count: {n_cjk}\nword_count_net:", 1)
                open(path, "w", encoding="utf-8").write(text)
                changed += 1
        rows.append({
            "序号": 0, "文件名": f, "标题": meta.get("title", ""),
            "发布日期": meta.get("published", ""), "文体码": meta.get("genre", ""),
            "是否原创标": meta.get("original_mark", "待确认"),
            "署名方式": meta.get("byline", "待确认"),
            "正文净字数": n_chars, "可解析性": meta.get("parse_quality", ""),
            "图片承载信息": "偏高" if int(meta.get("images") or 0) > 20 else "中",
            "来源模块": meta.get("src_name") or ("有" if re.search(
                r"^(内容来源|内容参考)", body, re.M) else "无"),
            "判定": "采纳",
            "备注": ("同题异版本，不计入统计" if not counted else "本人亲笔（用户确认）"),
        })
        print(f"{f[:44]:<46} {n_chars:>6} 字  {'计入' if counted else '排除'}{flag}")

    tot = sum(r["正文净字数"] for r in rows if "排除" not in r["备注"])
    print(f"\n共 {len(rows)} 篇（计入 {sum(1 for r in rows if '排除' not in r['备注'])} 篇，"
          f"合计 {tot} 字）")

    if args.make_csv:
        out = os.path.join(args.kbd, "语料清单.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for i, r in enumerate(sorted(rows, key=lambda x: x["发布日期"]), 1):
                r["序号"] = i
                w.writerow(r)
        print(f"[已生成] {out}")

    if args.fix:
        print(f"[已修正] {changed} 个文件的字数口径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
