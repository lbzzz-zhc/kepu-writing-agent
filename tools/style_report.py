#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""风格体检报告：从 KB1 语料算出"个人风格"的量化证据。

为什么单独做成工具
    KB2 规则表里的每条等级（R/T/O）都必须有实测频率支撑，不能凭印象。
    语料一变（新增文章）就要重算一遍，所以这件事必须可重复执行。

输出内容
    1. 总览：篇数、时间跨度、文体分布、篇幅分布
    2. 逐文体对照：各文体在每条特征上的命中率（用于判断规则是否要按文体限定）
    3. 标题型态：问号 / 竖线 / 长度 / 数字
    4. 开篇方式：时令、场景动作、人群情境、热点
    5. 结尾方式：来源模块名分布、末段类型
    6. 连接词与口头禅：高频转折词、过渡词

用法
    python style_report.py <KB1目录>
    python style_report.py <KB1目录> --md 报告.md
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_digest as C  # noqa: E402


# ---------------------------------------------------------------- 特征定义
SEASON = ["春", "夏", "秋", "冬", "换季", "节气", "佳节", "踏青", "开学",
          "新春", "寒冬", "炎炎", "金秋", "初冬", "盛夏", "秋风"]
HOTSPOT = ["新国标", "近日", "最近", "日前", "标准发布", "正式实施", "热搜", "曝光"]
SCENE_ACT = ["买", "选", "吃", "喝", "洗", "戴", "穿", "用", "逛", "做", "看", "闻"]
PEOPLE = ["家长", "老人", "孩子", "上班族", "学生", "消费者", "宝妈", "患者"]

CONNECTIVES = [
    "其实", "那么", "因此", "所以", "但是", "不过", "然而", "但是", "同时",
    "另外", "此外", "值得注意", "需要注意", "事实上", "实际上", "也就是说",
    "换句话说", "简单来说", "总的来说", "首先", "其次", "最后", "比如", "例如",
    "举个例子", "一般来说", "通常", "往往", "可能", "建议", "不妨", "记住",
    "那么问题来了", "看到这里", "说到底", "关键是", "重点是", "划重点",
]
WORDS_HABIT = [
    "消费者", "我们", "大家", "你", "咱", "小伙伴", "朋友",
    "那点事", "避坑", "踩坑", "套路", "猫腻", "坑",
]
# 值得注意的"减法"（越少越像本人）
NEGATIVE = ["绝对", "100%", "一定", "必然", "最", "第一", "国家级", "顶级", "万能", "彻底"]


def load(kb1):
    files = sorted(f for f in os.listdir(kb1)
                   if f.startswith("KB1-") and f.endswith(".md"))
    items, text = [], {}
    for f in files:
        p = os.path.join(kb1, f)
        items.append(C.parse(p))
        text[f] = open(p, encoding="utf-8").read()
    return items, text


def body_lines(raw):
    body = raw.split("---", 2)[-1]
    out = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("# ") or s == "[[图片]]":
            continue
        if re.match(r"^(内容来源|内容参考|资料来源|参考资料|来源)[:：]?$", s):
            break
        out.append(re.sub(r"\*+", "", s))
    return out


def norm_line(s):
    """归一化：去掉加粗标记与行首序号/项目符，否则 **内容参考** 会被漏判。"""
    s = re.sub(r"\*+", "", s).strip()
    return re.sub(r"^[·•\-\d.、)\s]+", "", s).strip()


def pct(k, n):
    return "%d/%d (%d%%)" % (k, n, round(100 * k / max(n, 1)))


def level(k, n, min_n=5):
    p = 100 * k / max(n, 1)
    if k >= min_n and p >= 80:
        return "R"
    return "T" if p >= 40 else "O"


FEATURES = [
    ("首段无标准号（开篇不摆条款）", lambda i: not i["first_has_std_no"]),
    ("首段无“标准/规定”字样", lambda i: not i["first_has_word"]),
    ("零感叹号", lambda i: i["excl"] == 0),
    ("正文引用 ≥1 个标准号", lambda i: i["n_std"] > 0),
    ("有来源清单模块", lambda i: bool(i["src_name"])),
    ("含问句式小标题", lambda i: i["head_q"] > 0),
    ("用 emoji 做锚点", lambda i: i["emoji"] > 0),
    ("标题带竖线（系列/栏目征）", lambda i: bool(i["title_sep"])),
    ("标题带问号", lambda i: ("？" in i["title"] or "?" in i["title"])),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kbd")
    ap.add_argument("--md", dest="md", default="")
    args = ap.parse_args()

    items, texts = load(args.kbd)
    counted = [i for i in items if i.get("counted", True)]
    n = len(counted)
    genres = sorted({i["genre"] for i in counted})
    out = []

    def w(s=""):
        out.append(s)

    w("# 个人风格体检报告")
    w()
    w(f"- 语料：KB1 共 {len(items)} 个文件，**计入 {n} 篇**")
    dates = sorted(i["published"] for i in counted if i["published"])
    if dates:
        w(f"- 时间跨度：{dates[0]} → {dates[-1]}")
    w(f"- 文体分布：" + " ｜ ".join(
        f"{g} {sum(1 for i in counted if i['genre'] == g)}" for g in genres))
    cs = sorted(i["chars"] for i in counted)
    w(f"- 篇幅：最小 {cs[0]} ／ P25 {cs[n//4]} ／ **中位 {cs[n//2]}** ／ "
      f"P75 {cs[3*n//4]} ／ 最大 {cs[-1]}")
    ps = sorted(i["paras"] for i in counted)
    av = sorted(i["avg_para"] for i in counted)
    w(f"- 段落：篇均段数中位 {ps[n//2]} ／ 平均段长中位 {av[n//2]} 字")
    w()
    w("## 一、逐条特征：全库与分文体")
    w()
    w("| 特征 | 全库 | " + " | ".join(genres) + " | 等级 |")
    w("|---|---|" + "---|" * len(genres) + "---|")
    for name, pred in FEATURES:
        k = sum(1 for i in counted if pred(i))
        cells = []
        for g in genres:
            pool = [i for i in counted if i["genre"] == g]
            cells.append(pct(sum(1 for i in pool if pred(i)), len(pool)))
        w(f"| {name} | {pct(k, n)} | " + " | ".join(cells)
          + f" | **{level(k, n)}** |")
    w()
    w("## 二、标题型态")
    w()
    ln = sorted(len(i["title"]) for i in counted)
    w(f"- 平均长度 {round(sum(ln)/max(n,1),1)} 字（中位 {ln[n//2]}）")
    sep = Counter(i["title_sep"] for i in counted if i["title_sep"])
    w(f"- 竖线分隔：{pct(sum(sep.values()), n)}　形态 {dict(sep)}")
    q = sum(1 for i in counted if "？" in i["title"] or "?" in i["title"])
    w(f"- 带问号：{pct(q, n)}")
    num = sum(1 for i in counted if re.search(r"\d", i["title"]))
    w(f"- 含数字：{pct(num, n)}")
    arw = sum(1 for i in counted if "→" in i["title"])
    w(f"- 结尾箭头 →：{pct(arw, n)}")
    w()
    w("### 标题样例（每文体 3 条）")
    for g in genres:
        pool = [i for i in counted if i["genre"] == g][:3]
        for i in pool:
            w(f"- `{g}` {i['title']}")
    w()
    w("## 三、开篇方式")
    w()
    firsts = [(i, (texts[i["file"]].split("---", 2)[-1]))
              for i in counted]
    def first_para(raw):
        for ln2 in raw.splitlines():
            s = ln2.strip()
            if s and not s.startswith("# ") and not s.startswith("**"):
                return re.sub(r"\*+", "", s)
        return ""
    fps = {i["file"]: first_para(raw) for i, raw in firsts}
    for label, keys in (("时令/季节锚点", SEASON), ("热点/新闻锚点", HOTSPOT)):
        k = sum(1 for i in counted
                if any(x in fps.get(i["file"], "")[:40] for x in keys))
        w(f"- {label}：{pct(k, n)}")
    k = sum(1 for i in counted
            if any(x in fps.get(i["file"], "")[:30] for x in PEOPLE))
    w(f"- 人群情境开篇（家长/老人/学生…）：{pct(k, n)}")
    k = sum(1 for i in counted
            if any(x in fps.get(i["file"], "")[:30] for x in SCENE_ACT))
    w(f"- 生活动作开篇：{pct(k, n)}")
    ln2 = sorted(len(fps.get(i["file"], "")) for i in counted)
    w(f"- 首段长度中位 {ln2[n//2]} 字")
    w()
    w("### 首段首句样例")
    for i in counted[:8]:
        w(f"- {i['id'][-2:]} {fps.get(i['file'], '')[:60]}")
    w()
    w("## 四、结尾方式")
    w()
    sn = Counter(i["src_name"] for i in counted if i["src_name"])
    w(f"- 来源模块名分布：{dict(sn)}")
    # 三态拆分：独立模块 / 行内标注 / 完全没有。
    # 必须先把 ** 与行首符号归一化再判，否则 **内容参考** 这类加粗模块会被漏掉
    # （曾因此把"有来源标注"错算成 48%，实际 81%）。
    MOD = re.compile(r"^(内容来源|内容参考|资料来源|参考资料|来源|参考文献)[:：]?\s*$")
    INL = re.compile(r"^(内容来源|内容参考|资料来源|参考资料|来源)[:：]\s*\S")
    cnt = Counter()
    for i in counted:
        lines = [norm_line(x) for x in texts[i["file"]].split("---", 2)[-1].splitlines()]
        lines = [x for x in lines if x]
        if any(MOD.match(x) for x in lines):
            cnt["module"] += 1
        elif any(INL.match(x) for x in lines):
            cnt["inline"] += 1
        else:
            cnt["none"] += 1
    w(f"- **独立来源模块**：{cnt['module']}/{n}（{round(100*cnt['module']/max(n,1))}%）"
      f"　**行内标注**（内容来源：xxx）：{cnt['inline']}/{n}"
      f"（{round(100*cnt['inline']/max(n,1))}%）"
      f"　**完全没有**：{cnt['none']}/{n}（{round(100*cnt['none']/max(n,1))}%）")
    w(f"- **有来源标注合计：{pct(cnt['module']+cnt['inline'], n)}** ← 规则应以此为准")
    w("- 无标注篇目的文体分布：" + " ｜ ".join(
        f"{g} {sum(1 for i in counted if i['genre'] == g and not re.search(r'(内容来源|内容参考|资料来源|参考资料)', texts[i['file']]))}"
        f"/{sum(1 for i in counted if i['genre'] == g)}" for g in genres))
    w()
    w("### 末段（来源模块之前那一段）样例")
    for i in counted[:8]:
        bl = body_lines(texts[i["file"]])
        tail = bl[-1] if bl else ""
        w(f"- {i['id'][-2:]} {tail[:64]}")
    w()
    w("## 五、高频连接词与习惯用语")
    w()
    allbody = "".join("".join(body_lines(texts[i["file"]])) for i in counted)
    w("| 词 | 总次数 | 出现篇数 | 篇覆盖率 |")
    w("|---|---|---|---|")
    for word in CONNECTIVES:
        tot = allbody.count(word)
        if tot == 0:
            continue
        docs = sum(1 for i in counted if word in texts[i["file"]])
        w(f"| {word} | {tot} | {docs} | {round(100*docs/max(n,1))}% |")
    w()
    w("| 称谓与标签词 | 总次数 | 出现篇数 |")
    w("|---|---|---|")
    for word in WORDS_HABIT:
        tot = allbody.count(word)
        if tot == 0:
            continue
        docs = sum(1 for i in counted if word in texts[i["file"]])
        w(f"| {word} | {tot} | {docs} |")
    w()
    w("## 六、绝对化用语自查（越少越好）")
    w()
    for word in NEGATIVE:
        tot = allbody.count(word)
        docs = sum(1 for i in counted if word in texts[i["file"]])
        if tot:
            w(f"- `{word}`：{tot} 次 / {docs} 篇")
    w()
    w("## 七、修辞与表达手法（怎么把术语讲成人话）")
    w()
    RHET = [
        ("比喻/类比", ["就像", "好比", "相当于", "如同", "犹如", "仿佛"]),
        ("对比/辨析", ["VS", "vs", "相比", "区别在于", "区别于", "不同于", "不如"]),
        ("举例", ["比如", "例如", "举个例子", "为例", "譬如"]),
        ("设问/追问", ["呢？", "吗？", "为什么", "怎么办", "如何", "究竟"]),
        ("数字/口诀式归纳", ["三不", "两要两不要", "第一步", "首先", "其次", "一是", "二是",
                            "三步", "四看", "一看", "二看"]),
        ("下定义", ["是指", "指的是", "定义为", "即为"]),
        ("留余地", ["可能", "通常", "往往", "一般", "相对", "较为", "多数"]),
        ("绝对化（越少越好）", ["绝对", "一定能", "必然", "百分之百", "彻底", "万能"]),
    ]
    w("| 手法 | 总次数 | 出现篇数 | 篇覆盖率 | 上下文样例 |")
    w("|---|---|---|---|---|")
    for label, keys in RHET:
        tot = sum(allbody.count(k) for k in keys)
        docs = sum(1 for i in counted if any(k in texts[i["file"]] for k in keys))
        eg = ""
        for k in keys:
            idx = allbody.find(k)
            if idx >= 0:
                eg = allbody[max(0, idx - 12):idx + 18]
                break
        w(f"| {label} | {tot} | {docs} | {round(100*docs/max(n,1))}% | {eg} |")
    w()
    w("**风险与适用边界的表达**")
    w()
    RISK = [("条件式限定（若/一旦/前提是）", ["若", "如果", "一旦", "前提是", "条件下"]),
            ("行动建议（建议/应当/务必）", ["建议", "应当", "需要", "务必", "注意"]),
            ("谨慎表述（可能存在/仍有待）", ["可能存在", "有一定", "仍需", "有待", "不排除"]),
            ("恐慌式表达（越少越好）", ["千万不要", "必死", "致命", "有毒", "千万别"])]
    for label, keys in RISK:
        tot = sum(allbody.count(k) for k in keys)
        docs = sum(1 for i in counted if any(k in texts[i["file"]] for k in keys))
        w(f"- {label}：{tot} 次 / {docs} 篇（{round(100*docs/max(n,1))}% 篇覆盖）")
    w()
    w("**情绪强度**")
    w()
    EMO = ["可怕", "焦虑", "担心", "惊喜", "惊艳", "暖心", "安心", "委屈", "难受", "划算"]
    tot = sum(allbody.count(k) for k in EMO)
    docs = sum(1 for i in counted if any(k in texts[i["file"]] for k in EMO))
    w(f"- 情绪色彩词：{tot} 次 / {docs} 篇（{round(100*docs/max(n,1))}%）")
    w(f"- 感叹号：全库 {allbody.count('！')} 处；"
      f"{sum(1 for i in counted if i['excl'] == 0)}/{n} 篇零使用")
    w(f"- 问号：全库 {allbody.count('？')} 处，篇均 {round(allbody.count('？')/max(n,1),1)} 处")
    w()
    w("## 八、标点与符号")
    w()
    w(f"- 全角逗号 {allbody.count('，')} ／ 半角逗号 {allbody.count(',')}")
    w(f"- 全角句号 {allbody.count('。')} ／ 分号 {allbody.count('；')}")
    w(f"- 破折号 —— {allbody.count('——')} ／ 顿号 {allbody.count('、')}")
    w(f"- 感叹号 全角 {allbody.count('！')} ／ 半角 {allbody.count('!')}")
    w(f"- 问号 全角 {allbody.count('？')} ／ 半角 {allbody.count('?')}")
    w(f"- emoji 总数 {len(C.EMOJI_PAT.findall(allbody))}")

    # ---- 九、时间分层：稳定规则里混着"时代习惯"，必须拆开看 ----
    w()
    w("## 九、时间分层（判断是真风格还是阶段习惯）")
    w()
    w("> 跨越 6 年的语料里，某些“规则”其实是“某段时间的习惯”。"
      "不拆时间层，会把阶段性写法当成风格底线。")

    def _year(i):
        m = re.search(r"(\d{4})-\d{2}-\d{2}", str(i.get("published") or ""))
        return int(m.group(1)) if m else None

    def _src_kind(f):
        lines = [norm_line(l) for l in texts[f].split("---", 2)[-1].splitlines()]
        lines = [x for x in lines if x]
        if any(re.match(r"^(内容来源|内容参考|资料来源|参考资料|来源|参考文献)[:：]?$", x)
               for x in lines):
            return "模块"
        if any(re.match(r"^(内容来源|内容参考|资料来源|参考资料|来源)[:：]\S", x) for x in lines):
            return "行内"
        return "无"

    w()
    w("| 年份 | 篇数 | 篇幅中位 | 有来源标注 | 独立模块 | 行内标注 | 零感叹号 |")
    w("|---|---|---|---|---|---|---|")
    years = sorted({y for y in (_year(i) for i in counted) if y})
    for y in years:
        v = [i for i in counted if _year(i) == y]
        if not v:
            continue
        cs = sorted(i["chars"] for i in v)
        k = Counter(_src_kind(i["file"]) for i in v)
        nv = len(v)
        w("| %d | %d | %d | %s | %s | %s | %s |" % (
            y, nv, cs[nv // 2],
            "%d%%" % round(100 * (k["模块"] + k["行内"]) / nv),
            "%d%%" % round(100 * k["模块"] / nv),
            "%d%%" % round(100 * k["行内"] / nv),
            "%d%%" % round(100 * sum(1 for i in v if i["excl"] == 0) / nv)))
    w()
    early = [i for i in counted if _year(i) and _year(i) <= 2023]
    late = [i for i in counted if _year(i) and _year(i) >= 2024]
    for name, v in (("早期 2020–2023", early), ("近期 2024–2026", late)):
        if not v:
            continue
        nv = len(v)
        cs = sorted(i["chars"] for i in v)
        k = Counter(_src_kind(i["file"]) for i in v)
        w("- **%s**（%d 篇）：篇幅中位 %d｜有来源标注 %d%%（模块 %d%%／行内 %d%%）"
          "｜零感叹号 %d%%｜中位标准数 %d" % (
              name, nv, cs[nv // 2],
              round(100 * (k["模块"] + k["行内"]) / nv),
              round(100 * k["模块"] / nv), round(100 * k["行内"] / nv),
              round(100 * sum(1 for i in v if i["excl"] == 0) / nv),
              sorted(i["n_std"] for i in v)[nv // 2]))

    report = "\n".join(out)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(f"[已生成] {args.md}（{len(report)} 字）")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
