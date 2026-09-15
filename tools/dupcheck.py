#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""G3 硬闸 · 连续 n 字查重（命令行版）

用途：在对话/脚本里直接跑，不必启动本地服务。
算法与网页版**同源** —— 直接调用 writer_server.api_dupcheck()，
不复制一份实现（避免"两份真相"）。

用法：
    python tools/dupcheck.py 稿件.md
    python tools/dupcheck.py 稿件.md --n 12 --json
    python tools/dupcheck.py - < 稿件.md          # 从标准输入读
    python tools/dupcheck.py A.md B.md            # 多份一起查

退出码：0 = 通过（无重合）；1 = 命中（疑似洗稿）；2 = 参数或文件错误。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writer_server as S  # noqa: E402  复用同一份查重实现


def read_text(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser(description="连续 n 字查重（与 KB1 历史语料比对）")
    ap.add_argument("files", nargs="+", help="稿件路径，或 - 表示标准输入")
    ap.add_argument("--n", type=int, default=12, help="连续字数阈值，默认 12")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    reports, worst = [], 0
    for path in args.files:
        try:
            text = read_text(path)
        except OSError as exc:
            print(f"[错误] 读不到 {path}：{exc}", file=sys.stderr)
            return 2
        r = S.api_dupcheck(text, n=args.n)
        r["file"] = path
        r["kb1"] = S.KB1
        reports.append(r)
        worst = max(worst, r["longest"])

    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0],
                         ensure_ascii=False, indent=2))
        return 0 if worst == 0 else 1

    total_kb = len([f for f in os.listdir(S.KB1) if f.startswith("KB1-")]) \
        if os.path.isdir(S.KB1) else 0
    print(f"比对基准：KB1 {total_kb} 个文件｜阈值：连续 {args.n} 字")
    for r in reports:
        name = r["file"] if r["file"] != "-" else "(标准输入)"
        flag = "通过" if r["pass"] else "命中"
        print(f"\n[{flag}] {name}　正文 {r['clean_len']} 字　命中 {r['n_hits']} 处"
              f"　最长重合 {r['longest']} 字")
        for h in r["hits"]:
            print(f"   ⚠ {len(h['snippet']):>3} 字 ← {h['src']}")
            print(f"      {h['snippet'][:60]}{'…' if len(h['snippet']) > 60 else ''}")
        if r["pass"]:
            print("   未发现与历史语料的连续重合。")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
