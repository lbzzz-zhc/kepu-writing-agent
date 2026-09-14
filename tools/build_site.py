#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本地知识库打包成一个单文件在线工作台（site/index.html）。

用法：
  python build_site.py --project <工程根目录> --out <输出目录>

产出：
  index.html —— 零依赖单文件（CSS/JS/数据全部内联），可直接双击打开，也可整目录部署到静态托管。

为什么不直接手写 HTML：
  语料会持续增长、规则会升级版本。重新跑一次本脚本即可刷新网页，避免网页与知识库脱节。
"""

import argparse
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pwa_assets  # noqa: E402  让网页可以"安装成应用"

STATE = {
    "title": "科普写作智能体 · 在线工作台",
    "subtitle": "个人微信公众号科普写作风格库与规则库",
}

# ---------------------------------------------------------------- 规则（KB2 v0.2）
RULES_R = [
    ("R-01", "开篇", "第 1 段 30–110 字，只写<em>时令 / 生活动作 / 人群情境</em>；主题在第 2–3 句出现；段内不出现标准号，不出现“标准”“规定”字样。", "23/23（100%）", "无", "全部变体"),
    ("R-02", "标准引用", "引用标准必用 <em>《标准全称》（编号）</em>，不写简称、不省编号。", "21/23（91%）", "2 篇未用", "STD 类（HEALTH 可放宽）"),
    ("R-03", "资料密度", "每篇正文至少引 1 个带编号标准，常见 2 个、最多 6 个；每个都要在正文里被解释，不堆砌。", "23/23", "无", "全部变体"),
    ("R-04", "结尾", "结尾有“来源清单”模块，逐条列标准全称（中位 3 条）。<em>模块名不固定</em>：在“内容参考”“内容来源”中任选，禁止写死。", "STD 14/16（88%）", "HEALTH 仅 3/6 → 本规则只适用 STD", "仅 STD"),
    ("R-05", "篇幅", "正文 <em>1100–1800 字</em>，目标 1300–1550。", "P50 1294", "—", "全部变体"),
    ("R-06", "段落节奏", "全文 20–30 段（中位 24）；单段平均 40–70 字（中位 57）；不写超过 120 字的长段。", "23 篇实测", "—", "全部变体"),
    ("R-07", "语气", "正文默认<em>不用感叹号</em>；情绪靠动词、场景、短句承载。", "21/23 篇零使用", "存在 2 处", "全部变体"),
    ("R-08", "标点", "标点全角统一（全角逗号 1159 : 半角 1）。", "23 篇实测", "—", "全部变体"),
    ("R-09", "符号", "用 1 个语义 emoji 做要点/小标题锚点（🌞 分类、📖 来源、👀、📦）；每处只 1 个，不堆叠。", "18/23（78%）", "5 篇未用", "全部变体"),
]

RULES_T = [
    ("T-01", "标题", "属固定系列/栏目的稿件，标题写成 <em>系列名｜副标题</em>，仅此一处用竖线。", "全库 9/23，系列内 9/9", "非系列稿不用竖线", "系列/栏目稿"),
    ("T-02", "小标题", "至少一个小标题用问句。", "17/23（74%）", "—", "全部变体"),
    ("T-03", "引导词", "引标准用“依据/根据/按照”，不用“有研究表明”“据悉”。", "14/23（61%）", "—", "全部变体"),
    ("T-04", "术语", "专业名词用 <em>×× 是指……</em> 下定义，再接白话解释。", "10/23（43%）", "—", "全部变体"),
    ("T-05", "转述", "用“标准规定/明确”转述条款，而非照录原文。", "8/13（v0.1 口径）", "待以 23 篇复核", "STD"),
    ("T-06", "标准属性", "交代标准的强制性/推荐性属性。", "5/13（v0.1 口径）", "待以 23 篇复核", "STD"),
    ("T-07", "图注", "图片下方标注来源或说明，格式“标准号：说明”或“（图源：××）”。", "11/23（48%）", "—", "全部变体"),
]

RULES_O = [
    ("O-01", "数字口诀归纳（“三不原则”“两要两不要”）", "3/23", "效果好，未达门槛，不入库"),
    ("O-02", "标题用箭头 → / ⟶ 收尾", "7/23（30%）", "偏攻略式稿件的习惯，未达门槛"),
    ("O-03", "「专家建议」「专家提醒」作小标题", "2/23", "<b>不是禁用词</b>——作者确实会写，但内容须可溯源"),
    ("O-04", "「那点事」副标题用诗句/文言", "3/7（系列内）", "系列内恰半，不作规则"),
    ("O-05", "抒情型开篇（“金叶随风翻涌，如浪如瀑”）", "2/23", "归入变体 E，样本不足"),
]

BANLIST = [
    "独特比喻与自创说法：「剥洋葱」快递、“衣橱大换血”、“小山丘”、“涂了个寂寞”、食品界的“变形金刚”、马海毛的“柔光滤镜”",
    "标志性句式与诗句：系列稿固定副标题（“白雪凝琼貌，明珠点绛唇”“肌肤炯凝脂，容色眩春雪”）",
    "具体案例与品牌：olive young、GAMA、汕头市文化馆、具体抽检批次",
    "全部标准号、限值、日期、检测数据 —— 每次写作必须重新核验，不得从历史语料继承",
]

# ---------------------------------------------------------------- 变体（KB2 变体手册）
VARIANTS = [
    {
        "code": "A", "name": "「那点事」护肤美妆系列", "count": 7,
        "reps": "卸妆 / 防晒 / 祛痘 / 眼霜 / 春季护肤 / 唇膏 / 护肤精华",
        "rows": [
            ("标题", "固定：X那点事｜副标题，系列内 7/7。副标题诗句型 3 篇 / 白话型 4 篇"),
            ("开篇锚点", "时令 + 肌肤困扰"),
            ("标志性小节", "常见质量问题（监督抽检、限量指标）"),
            ("语气", "养护关怀，唯一会出现情绪化形容词（容貌焦虑、如天鹅绒般柔嫩）"),
            ("资料要求", "最高：每篇 2–6 个标准，会引《化妆品安全技术规范》与学术论文"),
            ("来源模块", "有"),
            ("风险提醒位置", "结尾（“使用前务必皮肤测试”“敏感人群怎么办？”）"),
        ],
    },
    {
        "code": "B", "name": "「热点追踪」栏目", "count": 1,
        "reps": "快递包装新国标（与“剥洋葱”快递说再见）",
        "rows": [
            ("标题", "热点追踪｜新闻式标题"),
            ("开篇锚点", "公众争议现象（“拆了一层又一层”）"),
            ("标志性小节", "新旧规对比 + 往年监督抽查情况"),
            ("语气", "比其他变体直接，允许轻微吐槽"),
            ("资料要求", "新旧规对比 + 抽查历史数据"),
            ("来源模块", "有"),
            ("风险提醒位置", "正文中"),
        ],
        "warn": "仅 1 篇，样本不足，本页只记录栏目定位，不产出规则",
    },
    {
        "code": "C", "name": "生活消费品标准解读", "count": 6,
        "reps": "餐具 / 洗衣 / 车内空气 / 电磁炉 / 恒温龙头 / 马海毛",
        "rows": [
            ("标题", "无固定格式，三种混用：反差设问型 / 疑问句尾型 / 定位陈述型"),
            ("开篇锚点", "生活动作场景（书包里的饭碗、衣橱大换血、摇上车窗）"),
            ("标志性小节", "数字口诀清单（“三不原则”“两要两不要”）"),
            ("语气", "实用导向，动作动词最多；第二人称最多"),
            ("资料要求", "1–4 个标准，偏产品性能指标；图注常带标准号"),
            ("来源模块", "易缺（1/6 无）"),
            ("风险提醒位置", "洗护注意段"),
        ],
    },
    {
        "code": "D", "name": "食材与饮食科普", "count": 7,
        "reps": "固体饮料 / 面筋 / 大米 / 吃啥米 / 牛肉丸 / 韭菜 / 低嘌呤",
        "rows": [
            ("标题", "口语化 / 攻略式（“这份‘水+粉’的快乐”“想吃一碗好饭？”）"),
            ("开篇锚点", "进食场景或时令"),
            ("标志性小节", "辨析类小标题（A VS B、全麦vs非全麦）"),
            ("语气", "生活化程度最高（快乐、暖心大法、基因里的温暖仪式）"),
            ("资料要求", "食品标准 + 行业/地方标准（含 DBS 44/005）＋机构数据"),
            ("来源模块", "4/7 有"),
            ("风险提醒位置", "适用边界句"),
        ],
    },
    {
        "code": "E", "name": "慢生活·自然型", "count": 2,
        "reps": "银杏 / 公园草坪",
        "rows": [
            ("标题", "景物 + 感叹式（“风起金浪，又到银杏醉美时”）"),
            ("开篇锚点", "景物描写（“金叶随风翻涌，如浪如瀑”）"),
            ("标志性小节", "无；小标题仅 2–3 个"),
            ("语气", "文艺、松弛，是最“不科普”的一类"),
            ("资料要求", "最薄：1–2 个标准"),
            ("来源模块", "两篇均无"),
            ("风险提醒位置", "几乎无"),
        ],
        "warn": "仅 2 篇，样本不足，只作记录不出规则；遇到此类主题须提示“风格样本不足”",
    },
]

CHECKLIST = {
    "事实审校（对 KB3）": [
        "每个数字、标准号、条款、日期都能在底稿找到已核验记录",
        "没有出现历史语料里的任何数字或结论",
        "没有“专家表示 / 研究显示”式无源归因",
        "适用范围与不适用情形都写了",
        "结论强度与证据强度匹配（相关≠因果，动物实验≠人体结论）",
    ],
    "风格审校（对 KB2）": [
        "逐条核对规则卡，符合率达标",
        "开头方式、段落节奏、结尾方式符合适用变体",
        "G3 查重通过（与历史语料无连续 12 字重合）",
        "没有命中禁用清单里的词与句式",
    ],
    "合规自查（对 KB4）": [
        "无绝对化用语（最好 / 最安全 / 100% / 彻底 / 根治）",
        "无恐慌式表达（不转就晚了 / 致命 / 致癌）",
        "无医疗诊断、用药建议",
        "无标题党",
        "来源标注完整且位置符合本人习惯",
    ],
}

ACCEPT = [
    ("每条风格规则都有真实语料依据", "ok", "规则表每条带频率与代表篇目，由 corpus_digest.py 实测"),
    ("稳定风格、文体差异、主题事实严格分开", "ok", "KB1 风格 / KB2 规则 / KB3 事实 三分区隔离"),
    ("新文章不自动继承旧标准与旧数据", "mech", "fact_usable:false + 事实底稿单一入口（机制化，未实测）"),
    ("不整句、不整段复制历史文章", "mech", "写作节点不读原文（强隔离）+ 连续 12 字查重"),
    ("不编造标准、数据、来源、编辑意见", "mech", "硬约束 + 暂停话术；无源归因属一票否决"),
    ("资料不足时主动停下来提示", "mech", "固定话术 + 三道硬闸 G1/G2/G3"),
    ("输出有明显个人节奏，不是通用 AI 味", "todo", "规则齐备，但只能靠实测与盲测判断"),
    ("能落地到平台的知识库与工作流", "half", "本地工程就绪；平台侧未建库、提示词未粘贴"),
    ("三审独立性（不是自己查自己）", "todo", "平台自跑时会降级，需先选定方案 A / B"),
]


def esc(s):
    return html.escape(str(s))


def load_corpus(project):
    """读语料特征。缓存缺失或损坏时**直接扫描 KB1 兜底**。

    曾经这里在缓存缺失时 `return []`——结果是"静默失败"：
    页面照常生成，但语料库一栏是空的，看不出哪里错了。
    这里改为兜底扫描，宁可慢一点，也不产出空页面。
    """
    path = os.path.join(project, "_corpus_features.json")
    items = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                items = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 统计缓存损坏，改用直接扫描：{exc}")
    if items is None:
        kb1 = os.path.join(project, "10_知识库", "01_历史语料库")
        if os.path.isdir(kb1):
            try:
                import corpus_digest as C  # 同目录
                items = [C.parse(os.path.join(kb1, f))
                         for f in sorted(os.listdir(kb1))
                         if f.startswith("KB1-") and f.endswith(".md")]
                print(f"[提示] 未找到统计缓存，已直接扫描 KB1 得到 {len(items)} 篇"
                      f"（建议先跑 corpus_digest.py 生成缓存）")
            except Exception as exc:  # noqa: BLE001
                print(f"[警告] 兜底扫描失败：{exc}")
    if not items:
        print("[警告] 语料为空 —— 页面将不显示任何语料，请检查 KB1 与统计缓存")
        return []
    out = []
    for i in items:
        out.append({
            "id": i["id"], "title": i["title"], "date": i["published"],
            "genre": i["genre"], "chars": i["chars"], "paras": i["paras"],
            "first": i["first_text"], "heads": i["heads"][:8],
            "src": i["src_name"] or "无", "stds": i["n_std"],
            "counted": i.get("counted", True),
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def load_prompts(project):
    base = os.path.join(project, "20_工作流")
    res = {}
    for key, fn in (("full", "系统提示词_完整版.md"), ("slim", "系统提示词_压缩版.md")):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            res[key] = open(p, encoding="utf-8").read()
    return res


def stats(corpus):
    counted = [c for c in corpus if c["counted"]]
    cs = sorted(c["chars"] for c in counted) or [0]
    n = len(cs)
    return {
        "total": len(corpus),
        "counted": n,
        "p50": cs[n // 2],
        "p25": cs[n // 4],
        "p75": cs[3 * n // 4],
        "wechat": sum(1 for c in corpus if c.get("authority", "微信发布版") and "微信" in str(c.get("authority", "微信发布版"))),
        "latest": counted[0]["date"] if counted else "",
        "earliest": counted[-1]["date"] if counted else "",
    }


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
:root{
  --bg:#F7F7F5; --card:#FFFFFF; --ink:#1F2328; --muted:#5F5E5A; --line:#E3E1DA;
  --teal:#0F6E56; --teal-bg:#E1F5EE; --blue:#185FA5; --blue-bg:#E6F1FB;
  --amber:#854F0B; --amber-bg:#FAEEDA; --red:#A32D2D; --red-bg:#FCEBEB;
  --gray-bg:#F1EFE8;
}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB",system-ui,-apple-system,sans-serif;
  font-size:14px;line-height:1.7;-webkit-font-smoothing:antialiased}
a{color:var(--blue)}
.wrap{max-width:1120px;margin:0 auto;padding:28px 20px 80px}
header.top{margin-bottom:22px}
h1{font-size:22px;font-weight:600;margin:0 0 6px}
.sub{color:var(--muted);font-size:13px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;min-width:118px}
.kpi b{display:block;font-size:19px;font-weight:600;line-height:1.3}
.kpi span{color:var(--muted);font-size:12px}
nav.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:24px 0 18px;border-bottom:1px solid var(--line);padding-bottom:10px}
nav.tabs button{background:transparent;border:1px solid transparent;border-radius:999px;
  padding:7px 15px;font-size:14px;color:var(--muted);cursor:pointer;font-family:inherit}
nav.tabs button:hover{background:var(--gray-bg)}
nav.tabs button.on{background:var(--teal-bg);border-color:var(--teal);color:var(--teal);font-weight:500}
section.panel{display:none}
section.panel.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.card h2{font-size:16px;font-weight:600;margin:0 0 12px}
.card h3{font-size:14px;font-weight:600;margin:18px 0 8px}
.card p{margin:8px 0}
.muted{color:var(--muted)}
.small{font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12.5px;background:var(--gray-bg)}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;white-space:nowrap}
.t-r{background:var(--teal-bg);color:var(--teal)}
.t-t{background:var(--blue-bg);color:var(--blue)}
.t-o{background:var(--gray-bg);color:var(--muted)}
.t-warn{background:var(--amber-bg);color:var(--amber)}
.t-bad{background:var(--red-bg);color:var(--red)}
.freq{font-variant-numeric:tabular-nums;color:var(--teal);font-weight:500}
em{font-style:normal;background:#FFF3C4;padding:0 3px;border-radius:3px}
.btn{background:var(--teal);color:#fff;border:none;border-radius:8px;padding:8px 16px;
  font-size:13px;cursor:pointer;font-family:inherit}
.btn:hover{opacity:.9}
.btn.ghost{background:transparent;color:var(--teal);border:1px solid var(--teal)}
textarea,pre{width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
  line-height:1.6;background:#FBFBF9;border:1px solid var(--line);border-radius:8px;padding:12px}
pre{overflow:auto;max-height:520px;white-space:pre-wrap;word-break:break-word}
input.search{width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;
  font-size:14px;font-family:inherit;margin-bottom:14px;background:var(--card)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.vhead{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.vcode{width:30px;height:30px;border-radius:9px;background:var(--teal-bg);color:var(--teal);
  display:flex;align-items:center;justify-content:center;font-weight:600}
ul.tick{list-style:none;padding-left:0;margin:6px 0}
ul.tick li{padding-left:24px;position:relative;margin:7px 0}
ul.tick li:before{content:"";position:absolute;left:4px;top:9px;width:9px;height:9px;
  border:1.5px solid var(--muted);border-radius:2px}
.ban li{margin:7px 0;color:var(--muted)}
details{border-top:1px solid var(--line);padding:10px 0}
details summary{cursor:pointer;font-weight:500;font-size:13.5px}
details summary::-webkit-details-marker{display:none}
details summary:before{content:"▸ ";color:var(--muted)}
details[open] summary:before{content:"▾ "}
.art{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--card)}
.art .t{font-weight:500;margin-bottom:4px}
.art .m{color:var(--muted);font-size:12.5px}
.art .f{margin-top:8px;color:#333;font-size:13px}
footer{color:var(--muted);font-size:12.5px;margin-top:30px;text-align:center}
@media (max-width:640px){.wrap{padding:18px 14px 60px}h1{font-size:19px}th:nth-child(4),td:nth-child(4){display:none}}
</style>
</head>
<body>
<div class="wrap">
"""

HTML_BODY = """
<header class="top">
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__　·　数据更新：__UPDATED__</div>
  <p style="margin:12px 0 0"><a href="writer.html" style="display:inline-block;background:var(--teal);
    color:#fff;text-decoration:none;border-radius:8px;padding:8px 16px;font-size:13px">→ 打开写作台（按我的风格生成新推文）</a></p>
  <div class="kpis">
    <div class="kpi"><b>__COUNTED__</b><span>篇语料计入统计</span></div>
    <div class="kpi"><b>9 + 7</b><span>R 级 / T 级规则</span></div>
    <div class="kpi"><b>5</b><span>栏目与系列变体</span></div>
    <div class="kpi"><b>__P50__</b><span>篇幅中位数（字）</span></div>
    <div class="kpi"><b>__SPAN__</b><span>语料时间跨度</span></div>
  </div>
</header>

<nav class="tabs" id="tabs"></nav>

<section class="panel" id="p-overview"></section>
<section class="panel" id="p-rules"></section>
<section class="panel" id="p-variants"></section>
<section class="panel" id="p-corpus"></section>
<section class="panel" id="p-check"></section>
<section class="panel" id="p-prompt"></section>

<footer>
  本页为离线单文件工作台 · 数据由本地知识库生成 · 语料仅供风格参考，<b>其中的标准号、限值、日期一律不得作为新文章的事实来源</b>
</footer>
</div>
<script id="DATA" type="application/json">__DATA__</script>
<script>
__JS__
</script>
</body>
</html>
"""

JS = r"""
const D = JSON.parse(document.getElementById('DATA').textContent);
const esc = s => String(s==null?'':s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

const TABS = [
  ['overview','概览'],['rules','风格规则'],['variants','变体手册'],
  ['corpus','语料库'],['check','审校清单'],['prompt','系统提示词']
];
document.getElementById('tabs').innerHTML = TABS.map(([k,n],i)=>
  `<button data-k="${k}" class="${i===0?'on':''}">${n}</button>`).join('');

function show(k){
  document.querySelectorAll('nav.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.k===k));
  TABS.forEach(([t])=>document.getElementById('p-'+t).classList.toggle('on',t===k));
  window.scrollTo({top:0,behavior:'smooth'});
}
document.getElementById('tabs').addEventListener('click',e=>{
  if(e.target.dataset.k) show(e.target.dataset.k);
});

/* ---- 概览 ---- */
const badge = {ok:'t-r', mech:'t-t', half:'t-warn', todo:'t-bad'};
const label = {ok:'已通过', mech:'机制齐备·未实测', half:'完成一半', todo:'未完成'};
document.getElementById('p-overview').innerHTML = `
<div class="card">
  <h2>验收标准逐条核验</h2>
  <p class="muted small">按最初约定的 9 条验收标准核对，不凭印象。</p>
  <table><thead><tr><th style="width:44%">验收标准</th><th style="width:16%">状态</th><th>说明</th></tr></thead>
  <tbody>${D.accept.map(a=>`<tr><td>${esc(a[0])}</td>
    <td><span class="tag ${badge[a[1]]}">${label[a[1]]}</span></td>
    <td class="muted">${esc(a[2])}</td></tr>`).join('')}</tbody></table>
</div>
<div class="card">
  <h2>四条不可违反的纪律</h2>
  <ul class="tick">
    <li><b>风格与事实物理隔离</b>：风格只从 KB2 取，事实只从 KB3 取。</li>
    <li><b>历史文章永不作为本次事实</b>：其中的标准号、限值、日期、结论一律不可用。</li>
    <li><b>写作的人不审自己的稿</b>：三审独立执行，只有否决权、没有放行权。</li>
    <li><b>写作节点不读历史原文</b>：只读规则卡 —— 机制化防洗稿。</li>
  </ul>
</div>
<div class="card">
  <h2>已知能力落差（须知情）</h2>
  <p>三审的“独立”依赖在对话中调用独立子代理。若把提示词粘进平台智能体、由平台自行运行，
  三审会退化为同一模型连续自查三次，<b>独立性失效</b>。</p>
  <p class="small muted">方案 A（推荐）：在对话里通过技能触发，由 Agent 编排独立子代理。
  方案 B：平台自跑时改为“三次独立重跑 + 强制交错核对”，并注明已降级。</p>
</div>`;

/* ---- 规则 ---- */
function ruleTable(rows, lv){
  return `<table><thead><tr><th style="width:64px">编号</th><th style="width:76px">维度</th>
    <th>规则（可执行）</th><th style="width:132px">实测频率</th><th style="width:150px">例外</th>
    <th style="width:110px">适用</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td><span class="tag ${lv}">${r[0]}</span></td><td>${esc(r[1])}</td>
      <td>${r[2]}</td><td class="freq">${esc(r[3])}</td>
      <td class="muted small">${esc(r[4])}</td><td class="muted small">${esc(r[5])}</td></tr>`).join('')}
    </tbody></table>`;
}
document.getElementById('p-rules').innerHTML = `
<div class="card"><h2>R 级 · 稳定规则（默认执行）</h2>
  <p class="muted small">同变体 ≥5 篇且无显著例外，写作时无条件执行。</p>${ruleTable(D.rules_r,'t-r')}</div>
<div class="card"><h2>T 级 · 倾向性规则（允许偏离）</h2>
  <p class="muted small">存在例外或样本 3–4 篇，可执行但须标注“倾向”。</p>${ruleTable(D.rules_t,'t-t')}</div>
<div class="card"><h2>观察项 · 不入库</h2>
  <p class="muted small">样本不足，仅记录，不写入规则、不进入写作。</p>
  <table><thead><tr><th style="width:44%">观察</th><th style="width:14%">出现</th><th>结论</th></tr></thead>
  <tbody>${D.rules_o.map(r=>`<tr><td>${esc(r[1])}</td><td class="freq">${esc(r[2])}</td>
    <td class="muted">${r[3]}</td></tr>`).join('')}</tbody></table></div>
<div class="card"><h2>不可照搬项白名单（防洗稿硬约束）</h2>
  <ul class="ban small">${D.banlist.map(b=>`<li>${esc(b)}</li>`).join('')}</ul>
  <p class="small muted"><b>允许沿用</b>：段落长度区间、开篇锚点类型、小标题型态、来源模块结构、行文节奏、变体配方。</p></div>`;

/* ---- 变体 ---- */
document.getElementById('p-variants').innerHTML = D.variants.map(v=>`
<div class="card">
  <div class="vhead"><div class="vcode">${v.code}</div>
    <div><div style="font-weight:600">${esc(v.name)}</div>
    <div class="muted small">${v.count} 篇　·　${esc(v.reps)}</div></div></div>
  ${v.warn?`<p><span class="tag t-warn">样本不足</span> ${esc(v.warn)}</p>`:''}
  <table><tbody>${v.rows.map(r=>`<tr><th style="width:120px">${esc(r[0])}</th>
    <td>${esc(r[1])}</td></tr>`).join('')}</tbody></table>
</div>`).join('') + `
<div class="card"><h2>变体判定顺序（写作时先定变体，再定写法）</h2>
<ol class="small">
  <li>护肤美妆个护 → <b>A</b>，标题必须用系列格式</li>
  <li>由新闻 / 新规 / 公众争议触发 → <b>B</b></li>
  <li>家居 / 衣物 / 电器等耐用品怎么用、怎么挑 → <b>C</b>，结尾给清单</li>
  <li>能吃能喝的东西 → <b>D</b>，必须写辨析段 + 适用边界</li>
  <li>自然现象、季节物候 → <b>E</b>（样本不足，须人工确认）</li>
  <li>判定不唯一 → 停下来问作者</li>
</ol></div>`;

/* ---- 语料 ---- */
function renderCorpus(q){
  const ql = (q||'').trim().toLowerCase();
  const list = D.corpus.filter(c=>!ql ||
    (c.title+c.id+c.genre+c.first+(c.heads||[]).join('')).toLowerCase().includes(ql));
  document.getElementById('corpusList').innerHTML = list.length ? list.map(c=>`
  <div class="art">
    <div class="t">${esc(c.title)}</div>
    <div class="m">
      ${esc(c.id)}　·　${esc(c.date)}　·　${esc(c.genre)}　·　`+
      `<b>${c.chars}</b> 字　·　标准 ${c.stds} 个　·　来源模块：${esc(c.src)}`+
      `${c.counted?'':'　·　<span class="tag t-o">不计入统计</span>'}
    </div>
    <div class="f"><span class="muted">开篇：</span>${esc(c.first)}</div>
    ${(c.heads&&c.heads.length)?`<div class="f small"><span class="muted">小标题：</span>${c.heads.map(esc).join('　/　')}</div>`:''}
  </div>`).join('') : '<p class="muted">没有匹配的语料。</p>';
}
document.getElementById('p-corpus').innerHTML = `
<div class="card">
  <h2>历史语料库 · ${D.corpus.length} 个文件</h2>
  <p class="muted small">仅供风格参考。其中的标准号、限值、日期<b>不得</b>作为新文章的事实来源。</p>
  <input class="search" id="corpusSearch" placeholder="搜索标题、编号、小标题或开篇…">
  <div id="corpusList"></div>
</div>`;
document.getElementById('corpusSearch').addEventListener('input',e=>renderCorpus(e.target.value));
renderCorpus('');

/* ---- 审校清单 ---- */
document.getElementById('p-check').innerHTML = Object.entries(D.checklist).map(([k,v])=>`
<div class="card"><h2>${esc(k)}</h2><ul class="tick">
${v.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></div>`).join('') + `
<div class="card"><h2>一票否决（出现任一项即判不合格）</h2>
<ul class="ban small">
<li>成稿出现无法追溯到事实底稿的标准号、限值、日期、数据、研究结论</li>
<li>与历史语料任意文章出现连续 ≥12 字重合，或复用其独特比喻 / 标志性句式</li>
<li>沿用旧文中的标准与数据</li>
<li>出现医疗诊断、用药建议或治疗方案</li>
<li>应暂停却自行“先写一版”</li>
</ul></div>`;

/* ---- 提示词 ---- */
document.getElementById('p-prompt').innerHTML = `
<div class="card">
  <h2>系统提示词（完整版）</h2>
  <p class="muted small">粘贴到智能体的「系统提示词 / 人格设定」框。<b>第 10 条已注入 KB2 v0.2 规则</b>。</p>
  <button class="btn" onclick="cp('full')">复制完整版</button>
  <button class="btn ghost" onclick="cp('slim')" style="margin-left:8px">复制压缩版</button>
  <div style="margin-top:14px"><pre id="pre-full">${esc(D.prompt.full||'（未找到文件）')}</pre></div>
</div>
<details><summary>压缩版（上下文较短时使用）</summary>
  <pre id="pre-slim" style="margin-top:10px">${esc(D.prompt.slim||'（未找到文件）')}</pre>
</details>`;

function cp(which){
  const txt = (which==='full'?D.prompt.full:D.prompt.slim)||'';
  const done = ()=>{ 
    const b = event.target; const o=b.textContent; b.textContent='已复制 ✓';
    setTimeout(()=>b.textContent=o,1400);
  };
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(done).catch(()=>fallback(txt,done));
  } else { fallback(txt,done); }
}
function fallback(txt,done){
  const ta=document.createElement('textarea'); ta.value=txt; document.body.appendChild(ta);
  ta.select(); try{document.execCommand('copy');done();}catch(e){alert('请手动选中复制');}
  document.body.removeChild(ta);
}
show('overview');
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    corpus = load_corpus(args.project)
    prompts = load_prompts(args.project)
    st = stats(corpus)

    data = {
        "updated": datetime.date.today().isoformat(),
        "counted": st["counted"], "total": st["total"],
        "rules_r": RULES_R, "rules_t": RULES_T, "rules_o": RULES_O,
        "banlist": BANLIST, "variants": VARIANTS,
        "checklist": CHECKLIST, "accept": ACCEPT,
        "corpus": corpus, "prompt": prompts,
    }

    os.makedirs(args.out, exist_ok=True)
    body = HTML_BODY
    body = body.replace("__TITLE__", esc(STATE["title"]))
    body = body.replace("__SUBTITLE__", esc(STATE["subtitle"]))
    body = body.replace("__UPDATED__", datetime.date.today().isoformat())
    body = body.replace("__COUNTED__", str(st["counted"]))
    body = body.replace("__TOTAL__", str(st["total"]))
    body = body.replace("__P50__", str(st["p50"]))
    body = body.replace("__SPAN__", f"{st['earliest'][:7]} → {st['latest'][:7]}".strip(" →"))
    head = HTML_HEAD.replace("__TITLE__", esc(STATE["title"]))
    # 只替换第一处（同 build_writer：页面 JS 里可能还有 </head> 字样）
    head = head.replace("</head>", pwa_assets.head_tags("index.html") + "</head>", 1)
    body = body.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    body = body.replace("__JS__", JS)

    out = os.path.join(args.out, "index.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(head + body)
    size = os.path.getsize(out) / 1024
    print(f"[已生成] {out}  ({size:.0f} KB)")
    made = pwa_assets.write(args.out, page="index.html")
    print(f"  PWA 资源：{' · '.join(made)}")
    print(f"  语料 {st['counted']} 篇计入 / 共 {st['total']} 个文件")
    print(f"  R 级 {len(RULES_R)} 条 · T 级 {len(RULES_T)} 条 · 观察项 {len(RULES_O)} 条")
    print(f"  变体 {len(VARIANTS)} 类 · 提示词 {'完整版+压缩版' if len(prompts)==2 else '缺失'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
