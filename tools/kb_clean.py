#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清洗 KB1 历史语料：去掉图片标记、图注与孤立符号行，只留排版好的纯文本。

背景
    微信原文带大量配图。早期入库把图片写成 `[[图片]]` 占位、图注也一并留下，
    学风格时这些是噪音（图注常是"GB/T xxxxx：某某指标见表"这类无图片就无意义
    的碎片）。本脚本把已有语料回洗成"纯文本 + 加粗小标题"的形态。

用法
    python kb_clean.py <KB1目录>              预演（不写盘，打印将删什么）
    python kb_clean.py <KB1目录> --apply      真正清洗（先自动备份）
    python kb_clean.py <KB1目录> --apply --no-backup

清洗内容
    1. `[[图片]]` 整行删除
    2. 图注行删除：紧邻图片位置、≤50 字、不以句号结尾、且不像正文句子
    3. 孤立符号行删除（去图后剩下的孤零零 emoji / 标点）
    4. `****x****` 这类嵌套加粗折叠成 `**x**`
    5. frontmatter 记录清理数量，并同步 word_count_net / cjk_count

安全
    --apply 前先把原文件整目录备份到 `_备份_含图_<时间戳>/`
"""

import argparse
import datetime
import os
import re
import shutil
import sys

IMG_LINE = re.compile(r"^\[\[图片\]\]$")
IMG_INLINE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
SRC_HEAD = re.compile(r"^(内容来源|内容参考|资料来源|参考资料)[:：]?$")
CJK = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
# 明确的图片版权/图注署名。
# 必须同时覆盖带冒号（图片来源：xx）与不带冒号的套话（图片来源于网络）——
# 后者曾在 24 篇里残留，混进正文还会被当成"末段"。
CREDIT_STRONG = re.compile(
    r"(图片来源|图源|图片来自|图片来源于|来源图|图注|图片拍摄|摄影)[:：]?")
CREDIT_WEAK = re.compile(r"(来自网络|来源于网络|来源网络)[:：]?")
# 来源标注（内容来源：xxx）不是图注，必须保住 —— KB2 的"必有来源标注"规则要靠它
SRC_PREFIX = re.compile(r"^(内容来源|内容参考|资料来源|参考资料|来源)[:：]")
# 图注典型形态：以标准号/编号开头，后接冒号与指标名（如 "DBS 44/005-2024：感官指标"）
STDNO_CAPTION = re.compile(
    r"^(?:GB/?T?|ISO|IEC|DBS?|SB/T|QB/T|NY/T|T/[A-Z]{2,8}|JJF)"
    r"[\s\d./\u2010-\u2015\-]*[:：]")
# 以"见图/下图/下表/右图/左图"收尾，或整行就是"（图N）"
REF_FIG_PAT = re.compile(r"(见图|见下图|见右图|见左图|如下图所示|如下表)$|^\(?图\s?\d+\)?$")


def is_caption(line, near_img):
    """判定图注。**保守优先**：只删能确定的，宁可留下疑似图注的短句。

    曾经用"短 + 不以句号结尾 + 紧邻图片"判定，实测会把大量真实小标题
    （如「选购注意事项」「认清标准」）误删——微信排版里小标题后面紧跟图片
    很常见。因此这里只保留三条强特征。
    """
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if SRC_HEAD.match(s) or SRC_PREFIX.match(s) or s.startswith("**"):
        return False
    # 显式带"图片/图/摄影"的署名：还要求署名前面基本没别的字，
    # 否则可能是"正文…… | 图片来自电商平台"这种被粘在一起的行，删了会丢正文
    m = CREDIT_STRONG.search(s)
    if m and len(s[:m.start()].strip()) <= 6:
        return True
    # 裸"来源于网络"这类：必须是短行（≤22 字），否则可能是正文里的句子
    if len(s) <= 22 and CREDIT_WEAK.search(s):
        return True
    if STDNO_CAPTION.match(s):
        return True
    if REF_FIG_PAT.search(s):
        return True
    return False


def clean(body_lines):
    """返回 (新行列表, 统计)"""
    # 1) 标记图片位置
    marks = [bool(IMG_LINE.match(l.strip())) for l in body_lines]
    stats = {"img": 0, "caption": 0, "orphan": 0}
    stats["img"] = sum(marks)

    out = []
    for i, line in enumerate(body_lines):
        s = line.strip()
        if IMG_LINE.match(s):
            continue
        # 去掉行内残留的 Markdown 图片语法
        s = IMG_INLINE.sub("", s).strip()
        near_img = (i > 0 and marks[i - 1]) or (i + 1 < len(marks) and marks[i + 1])
        if is_caption(s, near_img):
            stats["caption"] += 1
            continue
        s = re.sub(r"\*{2,}", "**", s)
        m = re.fullmatch(r"\*\*(.+?)\*\*", s, re.S)
        if m:
            s = "**" + re.sub(r"\*+", "", m.group(1)).strip() + "**"
        else:
            s = re.sub(r"\*+", "", s).strip()
        if s and not (len(s) <= 4 and not CJK.search(s)):
            out.append(s)
        elif s:
            stats["orphan"] += 1

    # 去掉连续重复行
    dedup = []
    for line in out:
        if not dedup or dedup[-1] != line:
            dedup.append(line)
    return dedup, stats


def split_front(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[1], parts[2]
    return "", text


def set_fm(fm, key, val):
    if re.search(r"^%s:" % re.escape(key), fm, flags=re.M):
        return re.sub(r"^%s:.*$" % re.escape(key), "%s: %s" % (key, val), fm, flags=re.M)
    return fm.rstrip("\n") + "\n%s: %s\n" % (key, val)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kbd")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.kbd)
                   if f.startswith("KB1-") and f.endswith(".md"))
    if not files:
        print("没找到 KB1-*.md")
        return 1

    total = {"img": 0, "caption": 0, "orphan": 0}
    changes = []
    for name in files:
        path = os.path.join(args.kbd, name)
        text = open(path, encoding="utf-8").read()
        fm, body = split_front(text)
        lines = body.splitlines()
        new, st = clean(lines)
        for k in total:
            total[k] += st[k]
        if new != [l for l in lines if l.strip()]:
            changes.append((name, st, len(lines), len(new)))

    print("=" * 62)
    print("预演" if not args.apply else "清洗")
    print("=" * 62)
    print(f"  文件数           : {len(files)}（{len(changes)} 个有改动）")
    print(f"  删除 [[图片]]    : {total['img']} 处")
    print(f"  删除图注行       : {total['caption']} 处")
    print(f"  删除孤立符号行   : {total['orphan']} 处")
    print()
    for name, st, a, b in changes[:8]:
        print(f"  · {name[:44]:<46} 图{st['img']:>3} 注{st['caption']:>2}  行 {a}→{b}")
    if len(changes) > 8:
        print(f"  … 另有 {len(changes) - 8} 个文件")

    if not args.apply:
        print("\n这是预演，未写盘。确认无误后加 --apply 执行。")
        return 0

    if not args.no_backup:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(args.kbd, "_备份_含图_%s" % stamp)
        os.makedirs(bak, exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(args.kbd, name), os.path.join(bak, name))
        print(f"\n已备份原始文件到：{os.path.basename(bak)}/")

    n = 0
    for name in files:
        path = os.path.join(args.kbd, name)
        text = open(path, encoding="utf-8").read()
        fm, body = split_front(text)
        new, st = clean(body.splitlines())
        if not st["img"] and not st["caption"] and not st["orphan"]:
            continue
        fm = set_fm(fm, "images_cleared", st["img"])
        fm = set_fm(fm, "captions_cleared", st["caption"])
        fm = set_fm(fm, "images", 0)
        out = "---" + fm + "---\n" + "\n".join(new) + "\n"
        open(path, "w", encoding="utf-8").write(out)
        n += 1
    print(f"已清洗并写回 {n} 个文件。")
    print("下一步：python tools/kb_index.py <KB1> --fix --csv   （重算字数与台账）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
