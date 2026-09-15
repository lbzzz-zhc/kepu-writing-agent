#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""改稿体检（命令行版）—— 本地计算，不调用任何模型

口径与网页版**完全同源**：直接复用 `build_writer.py` 里那套 JS（analyze + checkRules），
用 Node 执行。不另写一份 Python 实现 —— 否则网页与命令行迟早给出不同结论。
阈值从 KB2 规则表解析（篇幅/段数/段长/标题长度），不在本文件里写死。

用法：
    python tools/check_draft.py 稿件.md
    python tools/check_draft.py 稿件.md --genre HEALTH      # 手动指定文体
    python tools/check_draft.py 稿件.md --json
    python tools/check_draft.py - < 稿件.md

退出码：0 = 无 R 级违规；1 = 存在 R 级违规；2 = 参数或环境错误。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import build_writer as W  # noqa: E402  复用页面同一套体检逻辑

NODE_CANDIDATES = [
    r"C:\Users\27077\.workbuddy\binaries\node\versions\22.22.2\node.exe",
    r"C:\Program Files\nodejs\node.exe",
]

HARNESS = """const fs = require('fs');
const [draftPath, targetsPath, outPath, genreArg] = process.argv.slice(2);
const D = { targets: JSON.parse(fs.readFileSync(targetsPath, 'utf8')) };
__SEGMENT__
const a = analyze(fs.readFileSync(draftPath, 'utf8'));
const genre = (genreArg && genreArg !== 'auto') ? genreArg : a.genre;
const rows = checkRules(a, genre);
fs.writeFileSync(outPath, JSON.stringify({
  genre, gConf: a.gConf, gScore: a.gScore, rows,
  m: { title: a.title, chars: a.chars, paras: a.paras, avg: a.avg,
       heads: a.heads, headQ: a.headQ, exc: a.exc, stds: a.stds, emoji: a.emoji,
       src: a.src, titleQ: a.titleQ, titleLen: a.titleLen, first: a.first,
       consumer: a.consumer, we: a.we, you: a.you, conn: a.conn }
}, null, 1), 'utf8');
"""


def find_node(explicit=None):
    if explicit and os.path.exists(explicit):
        return explicit
    for p in NODE_CANDIDATES:
        if os.path.exists(p):
            return p
    found = shutil.which("node")
    if found:
        return found
    return None


def extract_segment():
    """从页面的 JS 里截出「体检」那一段（纯计算，无 DOM 依赖）。"""
    js = W.JS
    start = js.index("const T = D.targets")
    end = js.index("function renderCheck(")
    return js[start:end]


def run_check(text, node, genre="auto"):
    seg = extract_segment()
    targets = W.kb2_targets()
    with tempfile.TemporaryDirectory(prefix="kepu_chk_") as tmp:
        draft = os.path.join(tmp, "draft.txt")
        tgt = os.path.join(tmp, "targets.json")
        out = os.path.join(tmp, "result.json")
        script = os.path.join(tmp, "harness.js")
        open(draft, "w", encoding="utf-8").write(text)
        open(tgt, "w", encoding="utf-8").write(json.dumps(targets, ensure_ascii=False))
        open(script, "w", encoding="utf-8").write(
            HARNESS.replace("__SEGMENT__", seg))
        proc = subprocess.run([node, script, draft, tgt, out, genre],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError("体检脚本执行失败：\n" + (proc.stderr or proc.stdout or ""))
        with open(out, encoding="utf-8") as fh:
            return json.load(fh), targets


def render(result, targets, src_name):
    m = result["m"]
    rows = result["rows"]
    R = [r for r in rows if r["lvl"] == "R"]
    TO = [r for r in rows if r["lvl"] != "R"]
    r_ok = sum(1 for r in R if r["ok"])
    to_ok = sum(1 for r in TO if r["ok"])
    must = [r["code"] for r in R if not r["ok"]]
    lo, hi = (targets.get("chars") or [1000, 1400])[:2]

    print(f"体检：{src_name}")
    conf = result["gConf"]
    print(f"  文体初判：{result['genre']}"
          + (f"（关键词置信度 {conf:.1f}，低于 1.4 请人工确认）" if conf < 1.4 else f"（置信度 {conf:.1f}）"))
    print(f"  篇幅 {m['chars']} 字（目标 {lo}–{hi}，中位 {targets.get('p50')}）"
          f"｜{m['paras']} 段 / 段长 {m['avg']} 字")
    print(f"  标题 {m['titleLen']} 字（{'带问号' if m['titleQ'] else '无问号'}）"
          f"｜小标题 {m['heads']} 个（问句 {m['headQ']} 个）｜标准 {len(m['stds'])} 个｜感叹号 {m['exc']} 个")
    print(f"  来源形态：{ {'module':'独立来源模块','inline':'行内标注','none':'无'}[m['src']] }"
          f"｜称谓：消费者 {m['consumer']} / 我们 {m['we']} / 你 {m['you']}")
    print()
    print(f"  R 级合规 {r_ok}/{len(R)}　·　T/O 级符合 {to_ok}/{len(TO)}"
          + (f"　·　必须先改：{'、'.join(must)}" if must else ""))
    print()
    print("  判定  编号    项目                                          实测 → 目标")
    print("  " + "-" * 92)
    for r in rows:
        mark = "✓ 合规" if r["ok"] else ("✗ 违规" if r["lvl"] == "R" else "△ 偏离")
        print(f"  {mark}  {r['code']:<6} {r['name'][:26]:<26}  {r['got'][:30]} → {r['want'][:26]}")
    return must


def main():
    ap = argparse.ArgumentParser(description="改稿体检（本地计算，口径与网页同源）")
    ap.add_argument("file", help="稿件路径，或 - 表示标准输入")
    ap.add_argument("--genre", default="auto",
                    choices=["auto", "STD", "AVOID", "HEALTH", "SAFETY", "MISC"],
                    help="手动指定文体（默认自动判定，只作建议）")
    ap.add_argument("--node", default=None, help="node 可执行文件路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    node = find_node(args.node)
    if not node:
        print("[错误] 找不到 node，请用 --node 指定路径", file=sys.stderr)
        return 2

    try:
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    except OSError as exc:
        print(f"[错误] 读不到 {args.file}：{exc}", file=sys.stderr)
        return 2

    try:
        result, targets = run_check(text, node, args.genre)
    except RuntimeError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"targets": targets, **result}, ensure_ascii=False, indent=1))
        must = [r["code"] for r in result["rows"] if r["lvl"] == "R" and not r["ok"]]
        return 1 if must else 0

    name = "(标准输入)" if args.file == "-" else args.file
    must = render(result, targets, name)
    return 1 if must else 0


if __name__ == "__main__":
    sys.exit(main())
