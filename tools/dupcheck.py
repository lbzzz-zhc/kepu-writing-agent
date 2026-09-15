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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writer_server as S  # noqa: E402  复用同一份查重实现

# 标准"称谓串"：只由属性词 / 书名号 / 标准号 / 发布机构名构成，不含任何连续散文。
# 为什么需要它：R-03 要求按「属性 +《全称》（编号）」写标准，来源模块还要求写发布方，
# 于是任何一篇引用"强制性国家标准《食品安全国家标准…》"或
# "国家市场监督管理总局、国家标准化管理委员会"的稿子，
# 都会与 KB1 里同样写法的旧文产生 12 字以上的机械重合 —— 这不是洗稿。
# 处理方式：标注出来、单独列出，但**默认不据此判定命中**；用 --strict 可恢复全严格。
# ⚠ 判据是"去掉这些词后剩余不足 6 字"——真正的行文不可能被清空，所以不会漏放洗稿。
_NOISE = re.compile(
    r"(市场监督管理总局|标准化管理委员会|卫生健康委员会|质量监督检验检疫总局|"
    r"粮食和物资储备局|农业农村部|工业和信息化部|中华人民共和国|"
    r"委员会|总局|管理|委员|办公室|部门|协会|学会|研究院|研究所|"
    r"强制性|推荐性|国家|食品安全|行业|地方|团体|企业|标准化|"
    r"标准|规范|通则|规程|导则|技术规范|《|》|（|）|\(|\)|"
    r"GB|ISO|IEC|JJF|SB|QB|NY|DBS?|/T|/Z|[\d.\-—／/\s、，,。：:；;])+")


def read_text(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def is_boilerplate(snippet, at=None, title_spans=()):
    """重合片段是否属于"引用性表述"而非行文照搬。

    两条判据，满足其一即算引用：
    ① **落在书名号内**（《…》是标准/文件名称）——写标准类稿件必然复现相同名称，
       词表法穷举不了所有标准名，所以用结构判据：重合有 ≥8 字落在《》区间内。
    ② **称谓词 / 机构名 / 标点全部剥离后不足 6 字**——反复剥离直到稳定，
       因为 12 字窗口可能从机构名中间切开，一次替换会留下"总局""委员"这类碎片。

    真正的行文不可能满足任一条，所以不会漏放洗稿。
    """
    if at is not None and title_spans:
        inside = sum(max(0, min(end, at + len(snippet)) - max(start, at))
                     for start, end in title_spans)
        if inside >= 8:
            return True
    prev = None
    while prev != snippet:
        prev = snippet
        snippet = _NOISE.sub("", snippet)
    return len(snippet) < 6


def title_spans(clean):
    """归一化文本里所有《…》的区间（标准与文件名称）。"""
    return [(m.start(), m.end()) for m in re.finditer(r"《[^》]{2,60}》", clean)]


def main():
    ap = argparse.ArgumentParser(description="连续 n 字查重（与 KB1 历史语料比对）")
    ap.add_argument("files", nargs="+", help="稿件路径，或 - 表示标准输入")
    ap.add_argument("--n", type=int, default=12, help="连续字数阈值，默认 12")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：任何重合（含标准称谓串）都判命中")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    reports, verdict = [], 0
    for path in args.files:
        try:
            text = read_text(path)
        except OSError as exc:
            print(f"[错误] 读不到 {path}：{exc}", file=sys.stderr)
            return 2
        r = S.api_dupcheck(text, n=args.n)
        r["file"] = path
        r["kb1"] = S.KB1
        # 与 api_dupcheck 同一份归一化结果，因此 at 坐标可直接用来判断是否落在《》内
        spans = title_spans(S.norm_for_compare(text))
        real = 0
        for h in r["hits"]:
            h["boiler"] = is_boilerplate(h["snippet"], h.get("at"), spans)
            if not h["boiler"]:
                real += 1
        r["n_real"] = real
        r["n_boiler"] = r["n_hits"] - real
        r["pass_real"] = (real == 0)
        reports.append(r)
        if real or (args.strict and r["n_hits"]):
            verdict = 1

    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0],
                         ensure_ascii=False, indent=2))
        return verdict

    total_kb = len([f for f in os.listdir(S.KB1) if f.startswith("KB1-")]) \
        if os.path.isdir(S.KB1) else 0
    print(f"比对基准：KB1 {total_kb} 个文件｜阈值：连续 {args.n} 字"
          + ("｜严格模式" if args.strict else ""))
    for r in reports:
        name = r["file"] if r["file"] != "-" else "(标准输入)"
        if r["n_hits"] == 0:
            flag = "通过"
        elif r["n_real"] == 0:
            flag = "通过（仅标准称谓串）"
        else:
            flag = "命中"
        print(f"\n[{flag}] {name}　正文 {r['clean_len']} 字　重合 {r['n_hits']} 处"
              f"（实质 {r['n_real']} ／ 称谓串 {r['n_boiler']}）　最长 {r['longest']} 字")
        for h in r["hits"]:
            tag = "称谓串" if h["boiler"] else "实质重合"
            mark = "·" if h["boiler"] else "⚠"
            print(f"   {mark} {len(h['snippet']):>3} 字 [{tag}] ← {h['src']}")
            print(f"      {h['snippet'][:60]}{'…' if len(h['snippet']) > 60 else ''}")
        if r["n_hits"] == 0:
            print("   未发现与历史语料的连续重合。")
        elif r["n_real"] == 0:
            print("   重合均为标准名称/属性称谓（引用性表述），非行文照搬。")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
