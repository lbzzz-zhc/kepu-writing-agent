#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成"能写稿"的在线写作台 site/writer.html。

与 build_site.py 的区别：
  build_site.py   → 展示型工作台（读）
  build_writer.py → 写作型工作台（写），内置七步流水线，复用同一套 KB2 规则

关键设计：七步各发一次**独立 API 请求**，不共享对话历史。
  写作方只拿到规则卡与事实底稿；审校方只拿到成稿。
  → 这样"三审独立"是真的（上下文隔离），而不是同一对话里自己查自己。

用法：
  python build_writer.py --project <工程根目录> --out <输出目录>
"""

import argparse
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_site as B  # noqa: E402  复用规则数据，保证与展示页同源
import pwa_assets  # noqa: E402  让网页可以"安装成应用"

PROJECT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def plain(s):
    """去掉强调标记：提示词里不该出现 <em> 与 **（KB2 用 ** 标注实测数据）。"""
    s = re.sub(r"</?em>", "", s or "")
    s = s.replace("**", "")
    return re.sub(r"`", "", s)


def kb2_targets(project=None):
    """从 KB2 规则表抽体检阈值 —— 页面里不许硬编码这些数字。

    抽不到就退回保守默认值，并在构建日志里说明，避免"悄悄用错阈值"。
    """
    R, T, O, ver, base = B.load_rule_table(project or PROJECT)
    blob = " ".join((r[2] or "") + " " + (r[3] or "") for r in (R + T))
    t = {}
    m = re.search(r"(\d{3,4})\s*[–\-—~至]\s*(\d{3,4})\s*字", blob)
    t["chars"] = [int(m.group(1)), int(m.group(2))] if m else [1000, 1400]
    m = re.search(r"中位\s*\**(\d{3,4})\**\s*字", blob)
    t["p50"] = int(m.group(1)) if m else 1180
    m = re.search(r"段数中位\s*\**(\d+)\**", blob)
    t["paras"] = int(m.group(1)) if m else 20
    m = re.search(r"段长中位\s*\**(\d+)\**\s*字", blob)
    t["avg_para"] = int(m.group(1)) if m else 57
    m = re.search(r"标题\s*平均\s*\**([\d.]+)\**\s*字", blob) or \
        re.search(r"平均\s*\**([\d.]+)\**\s*字（中位", blob)
    t["title_len"] = float(m.group(1)) if m else 16.9
    t["kb2_ver"], t["kb2_base"] = ver, base
    return t


def genre_manual(project=None):
    """把《文体变体手册》按文体拆成 dict，便于只把相关那节塞进提示词。"""
    p = os.path.join(project or PROJECT, "10_知识库", "02_风格规则库",
                     "文体变体手册_v0.1.md")
    if not os.path.exists(p):
        return {}
    text = open(p, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"##\s*[一二三四五六]、\s*(STD|HEALTH|SAFETY|AVOID|MISC)\b(.*?)(?=\n##\s|\Z)",
                         text, re.S):
        out[m.group(1)] = (m.group(1) + " " + m.group(2)).strip()
    # 判定顺序与跨文体对照表也带上
    m = re.search(r"##\s*判定顺序.*?(?=\n##\s|\Z)", text, re.S)
    if m:
        out["_howto"] = m.group(0).strip()
    m = re.search(r"##\s*五、跨文体对照表.*?(?=\n##\s|\Z)", text, re.S)
    if m:
        out["_table"] = m.group(0).strip()
    return out


def rules_text():
    # 规则表的解析结果在 build_site 的模块级变量里；build_writer 单独运行时
    # 必须自己先解析一次，否则这里会拿到空列表（曾因此产出"没有规则的规则卡"）。
    if not B.RULES_R and not B.RULES_T:
        B.load_rules_into_module()
    if not B.RULES_R:
        print("[警告] KB2 规则为空 —— 写作台的规则卡可能不完整，请检查规则表格式")
    lines = ["【R 级 · 默认执行】"]
    for r in B.RULES_R:
        lines.append(f"{r[0]} {r[1]}｜{plain(r[2])}｜频率 {plain(r[3])}｜适用 {plain(r[5])}")
    lines.append("")
    lines.append("【T 级 · 倾向，允许偏离】")
    for r in B.RULES_T:
        lines.append(f"{r[0]} {r[1]}｜{plain(r[2])}｜频率 {plain(r[3])}"
                     f"｜适用 {plain(r[5])}")
    lines.append("")
    lines.append("【不可照搬（防洗稿硬约束）】")
    for b in B.BANLIST:
        lines.append("· " + plain(b))
    return "\n".join(lines)


def variants_text():
    out = []
    for v in B.VARIANTS:
        head = f"{v['code']} {v['name']}（{v['count']} 篇）"
        rows = "；".join(f"{k}:{val}" for k, val in v["rows"])
        warn = f"⚠ {v['warn']}" if v.get("warn") else ""
        out.append(f"{head}\n  {rows}\n  {warn}".rstrip())
    return "\n".join(out)


JS = r"""
const D = JSON.parse(document.getElementById('DATA').textContent);
const $ = s => document.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

/* ---------------- 配置 ---------------- */
const CFG_KEY = 'kepu_writer_cfg';
function cfg(){ try{ return JSON.parse(localStorage.getItem(CFG_KEY))||{}; }catch(e){ return {}; } }
function apiBase(){
  const set = (cfg().local||'').replace(/\/+$/,'');
  if(set) return set;
  const h = location.hostname;
  if(h==='127.0.0.1'||h==='localhost') return location.origin;
  return 'http://127.0.0.1:8787';
}
function saveCfg(){
  localStorage.setItem(CFG_KEY, JSON.stringify({
    base: $('#f-base').value.trim(), key: $('#f-key').value.trim(),
    model: $('#f-model').value.trim(), temp: parseFloat($('#f-temp').value)||0.7,
    useProxy: $('#f-proxy').checked, local: $('#f-local').value.trim()
  }));
  flash('设置已保存在本机浏览器');
}
function loadCfg(){
  const c = cfg();
  $('#f-base').value = c.base || 'https://api.deepseek.com/v1';
  $('#f-key').value = c.key || '';
  $('#f-model').value = c.model || 'deepseek-chat';
  $('#f-temp').value = (c.temp==null?0.7:c.temp);
  $('#f-proxy').checked = !!c.useProxy;
  $('#f-local').value = c.local || '';
}
function flash(msg, bad){
  const el = $('#flash'); el.textContent = msg;
  el.style.color = bad ? '#A32D2D' : '#0F6E56';
  clearTimeout(flash.t); flash.t = setTimeout(()=>{el.textContent='';}, 4000);
}

/* ---------------- 模型调用 ---------------- */
async function callModel(messages, temperature){
  const c = cfg();
  if(!c.key){ throw new Error('未填写 API Key'); }
  if(!c.base || !c.model){ throw new Error('未填写接口地址或模型名'); }
  const url = (c.useProxy ? apiBase() + '/api/chat' : c.base.replace(/\/+$/,'') + '/chat/completions');
  const body = { model: c.model, messages, temperature: (temperature==null?c.temp:temperature), stream: false };
  if(c.useProxy){ body._base = c.base; body._key = c.key; }
  const res = await fetch(url, {
    method:'POST',
    headers: { 'Content-Type':'application/json', 'Authorization':'Bearer ' + c.key },
    body: JSON.stringify(body)
  });
  if(!res.ok){
    const t = await res.text();
    throw new Error('接口返回 ' + res.status + '：' + t.slice(0,300));
  }
  const json = await res.json();
  const txt = json && json.choices && json.choices[0] && json.choices[0].message
    ? json.choices[0].message.content : '';
  if(!txt) throw new Error('接口未返回内容：' + JSON.stringify(json).slice(0,300));
  return txt;
}

/* ---------------- 七步提示词 ---------------- */
const P = {
  step1: (topic)=>[
    {role:'system', content:
`你是科普稿件的选题分析员。请判断这个选题应使用哪个写作变体，并说明理由。只输出 JSON，不要任何多余文字。

可选变体：
${D.variants_text}

判定顺序（按序匹配，命中即止）：
1. 护肤美妆个护 → A
2. 由新闻 / 新规 / 公众争议触发 → B
3. 家居 / 衣物 / 电器等耐用品怎么用、怎么挑 → C
4. 能吃能喝的东西 → D
5. 自然现象、季节物候 → E
6. 都不明确 → 选最接近的一个，并在 reason 里说明不确定

输出格式（严格 JSON）：
{"variant":"A","name":"变体名称","genre":"STD/HEALTH/SAFETY/AVOID/MISC","confidence":"高/中/低","reason":"一句话理由","note":"若变体样本不足或判定不确定，在此说明"}`},
    {role:'user', content:'选题：' + topic}
  ],

  step2: (topic, material)=>[
    {role:'system', content:
`你是事实核查员。请从用户提供的资料中逐条抽取可用于写作的事实点，做成事实底稿。

规则：
1. 只从用户给的资料里抽取，**禁止**凭记忆补充、禁止编造标准号 / 限值 / 日期 / 数据 / 研究结论。
2. 每条标注可信状态：已核验渠道（官方发布、监管部门、标准原文、权威机构）记 🟩；来源不明或用户转述记 🟨。
3. 若某项是文章支点（标题、核心结论、关键限值）却仍是 🟨，在末尾单独列出「支点缺证」。
4. 输出 Markdown 表格，列为：序号 | 事实点 | 数值/条款/日期 | 来源机构 | 链接 | 核验日期 | 状态

若资料为空或与选题无关，只输出：⏸ 资料不足，无法建立事实底稿。`},
    {role:'user', content:'选题：' + topic + '\n\n===== 用户提供的资料 =====\n' + material}
  ],

  step3: (topic, variant, facts, words)=>[
    {role:'system', content:
`你是面向大众的微信公众号科普写作智能体。请依据下列规则写一篇完整成稿。

===== 风格规则（唯一风格依据，禁止参考任何历史文章原文）=====
${D.rules_text}

===== 本次使用的变体配方 =====
${D.variants_text}

===== 硬约束 =====
1. 事实只能来自下方「事实底稿」。底稿里没有的数字、标准号、日期、结论一律不得出现。
2. 标注 🟨 的条目不得作为标题或核心结论，只能作定性表述且不写具体数字。
3. 开头第 1 段 30–110 字，只写时令 / 生活动作 / 人群情境；段内不出现标准号、不出现"标准""规定"字样；主题在第 2–3 句出现。
4. 正文 1100–1800 字（目标 1300–1550），20–30 段，单段平均 40–70 字，不写超过 120 字的长段。
5. 全文不用感叹号；标点全角。
6. 引用标准用《标准全称》（编号）；每个都要在正文里被解释。
7. 不写"专家表示""研究显示"这类无源归因。
8. 结论强度不得高于证据强度；必须写清适用范围与不适用情形。

===== 输出要求 =====
直接输出成稿正文。不要输出分析、提纲、写作说明、自评。
第一行是标题（只给 1 个，不要给候选列表），随后空一行开始正文。
正文用小标题分段（Markdown 的 ## 或加粗行）。`},
    {role:'user', content:
`选题：${topic}
使用变体：${variant}
目标字数：${words||'1300–1550'}

===== 事实底稿 =====
${facts}`}
  ],

  step4: (draft, facts)=>[
    {role:'system', content:
`你是独立的事实审校员。你只看到成稿与事实底稿，看不到任何写作过程，也不需要体谅作者。

逐句核对以下项目，输出问题清单：
1. 成稿中每个数字、标准号、条款、日期、结论，能否在事实底稿中找到 🟩 记录？逐条列出无法溯源的。
2. 有无把 🟨 条目当作标题或核心结论？
3. 有无"专家表示""研究显示""据悉"式无源归因？
4. 适用范围与不适用情形是否写明？
5. 结论强度是否高于证据强度（相关当因果、动物实验推人体）？

输出格式：
【阻断问题】逐条列出（没有则写"无"）
【提醒问题】逐条列出（没有则写"无"）
【结论】通过 / 不通过
不要改写文稿，不要给修改建议之外的内容。`},
    {role:'user', content:'===== 事实底稿 =====\n'+facts+'\n\n===== 成稿 =====\n'+draft}
  ],

  step5: (draft)=>[
    {role:'system', content:
`你是独立的风格审校员。你只看到成稿与风格规则，看不到写作过程。

逐条核对规则符合情况，输出：
1. 【规则符合】逐条列出 R 级规则的符合与否（不符合的指出具体位置）
2. 【查重风险】有无与常见科普文雷同、或出现规则中"不可照搬"清单里的独特比喻/句式？列出可疑片段
3. 【节奏评估】段落数、平均段长、开篇首段长度是否达标（给出估算值）
4. 【分数】风格符合率 __%，及格线 80%
5. 【结论】通过 / 不通过

===== 风格规则 =====
${D.rules_text}

===== 不可照搬清单 =====
${D.ban_text}`},
    {role:'user', content:'===== 成稿 =====\n'+draft}
  ],

  step6: (draft)=>[
    {role:'system', content:
`你是发布前合规自查员。只依据下列清单检查成稿，输出命中项（没有则写"无"）：
1. 绝对化用语：最好 / 最安全 / 100% / 彻底 / 根治 / 绝对 / 唯一 / 国家级（非确指）
2. 恐慌式表达：不转就晚了 / 致命 / 致癌 / 抓紧 / 千万别
3. 医疗诊断、用药建议、治疗方案
4. 标题党：悬念化、夸大、与正文不符
5. 来源标注完整性（标准全称与编号是否齐全）

输出：
【命中项】逐条列出原文片段 + 类型（无则写"无"）
【结论】通过 / 不通过`},
    {role:'user', content:'===== 成稿 =====\n'+draft}
  ],

  step7: (draft, issues)=>[
    {role:'system', content:
`你是改稿人，只做针对性修订，不重写。

规则：
1. 只修审校问题清单指出的地方，其余文字尽量不动。
2. 不得新增事实底稿之外的数字、标准号、日期、结论。若某处必须删减，就删减，不要用新数据替代。
3. 保持原有结构、段落节奏与标题。
4. 直接输出修订后的完整成稿，不要输出修改说明。

===== 风格规则（保持遵循）=====
${D.rules_text}`},
    {role:'user', content:'===== 三审问题清单 =====\n'+issues+'\n\n===== 待修订成稿 =====\n'+draft}
  ]
};

/* ---------------- 流水线 ---------------- */
const STEPS = [
  ['step1','① 变体判定'],
  ['step2','② 建立事实底稿'],
  ['step3','③ 生成正文'],
  ['step4','④ 事实审校（独立）'],
  ['step5','⑤ 风格审校（独立）'],
  ['step6','⑥ 合规自查'],
  ['step7','⑦ 按审校意见修订']
];
let running = false;

function renderSteps(){
  $('#steps').innerHTML = STEPS.map(([k,n])=>`
    <div class="step" id="s-${k}">
      <div class="shead"><span class="dot"></span>${n}<span class="stat"></span></div>
      <div class="sbody"><pre></pre></div>
    </div>`).join('');
}
function setStep(id, state, text){
  const el = document.getElementById('s-'+id);
  if(!el) return;
  el.className = 'step ' + state;
  const t = {run:'进行中…', ok:'完成', bad:'失败', warn:'需注意'}[state]||'';
  el.querySelector('.stat').textContent = t;
  if(text!=null) el.querySelector('pre').textContent = text;
}

async function run(){
  if(running) return;
  const topic = $('#topic').value.trim();
  const material = $('#material').value.trim();
  if(!topic){ flash('请先填写选题', true); return; }
  if(!cfg().key){ flash('请先填写 API Key 并保存设置', true); return; }

  running = true; $('#go').disabled = true; $('#go').textContent = '执行中…';
  $('#final').style.display='none'; renderSteps(); $('#log').textContent='';
  const log = m => { $('#log').textContent += m + '\n'; };

  try{
    /* 资料不足 → 直接暂停，不写占位稿 */
    if(!material){
      setStep('step2','bad','⏸ 已暂停写作。缺少：本次事实资料。\n请补充：标准原文 / 官方发布 / 权威报道 / 检测数据。\n我不在资料不足的情况下先写占位稿。');
      flash('已暂停：未提供本次事实资料', true);
      return;
    }

    setStep('step1','run');
    const s1raw = await callModel(P.step1(topic), 0.2);
    let v = {variant:'?', name:'', confidence:'中', reason:'', note:''};
    try{ v = JSON.parse(s1raw.replace(/```json|```/g,'').trim()); }
    catch(e){ v = {variant:'?', name:'（未能解析）', confidence:'低', reason:s1raw.slice(0,200), note:''}; }
    setStep('step1','ok', `变体：${v.variant} ${v.name}\n文体：${v.genre||''}\n置信：${v.confidence||''}\n理由：${v.reason||''}\n${v.note?('注意：'+v.note):''}`);
    if(v.note || v.confidence==='低') setStep('step1','warn', `变体：${v.variant}\n${v.note||'判定置信度低'}`);

    setStep('step2','run');
    const facts = await callModel(P.step2(topic, material), 0.2);
    setStep('step2', facts.includes('资料不足')?'bad':'ok', facts);
    if(facts.includes('资料不足')){
      flash('事实底稿不成立，已停止后续步骤', true); return;
    }

    setStep('step3','run');
    const draft = await callModel(P.step3(topic, `${v.variant} ${v.name}`, facts, $('#words').value.trim()), 0.75);
    setStep('step3','ok', draft);

    /* 三审：各自独立请求，不共享上下文 */
    setStep('step4','run');
    const rev4 = await callModel(P.step4(draft, facts), 0.2);
    setStep('step4', /结论[：:]\s*通过/.test(rev4) && !/阻断问题[\s\S]{0,20}(?!无)/.test(rev4)?'ok':'warn', rev4);

    setStep('step5','run');
    const rev5 = await callModel(P.step5(draft), 0.2);
    setStep('step5', /结论[：:]\s*通过/.test(rev5)?'ok':'warn', rev5);

    setStep('step6','run');
    const rev6 = await callModel(P.step6(draft), 0.2);
    setStep('step6', /结论[：:]\s*通过/.test(rev6)?'ok':'warn', rev6);

    const issues = `【④ 事实审校】\n${rev4}\n\n【⑤ 风格审校】\n${rev5}\n\n【⑥ 合规自查】\n${rev6}`;
    const needFix = /不通过/.test(rev4+rev5+rev6);

    let final = draft;
    if(needFix){
      setStep('step7','run');
      final = await callModel(P.step7(draft, issues), 0.4);
      setStep('step7','ok', final);
    } else {
      setStep('step7','ok','三审均通过，未触发修订。');
    }

    $('#final').style.display='block';
    $('#finalText').value = final;
    const chars = final.replace(/\s/g,'').length;
    $('#finalMeta').textContent = `约 ${chars} 字　·　变体 ${v.variant}　·　${needFix?'经一轮修订':'三审直通'}`;
    previewWord();
    log('完成。成稿已生成。');
    flash('成稿已生成');
    $('#final').scrollIntoView({behavior:'smooth'});
  }catch(err){
    log('中断：' + err.message);
    flash(err.message, true);
  }finally{
    running = false; $('#go').disabled = false; $('#go').textContent = '开始生成';
  }
}

async function testConn(){
  const c = cfg();
  if(!c.key){ flash('请先填写 API Key 并保存设置', true); return; }
  flash('正在测试连接…');
  try{
    const r = await callModel([{role:'user',content:'回复两个字：可用'}], 0);
    flash('连接正常，模型回复：' + r.trim().slice(0,20));
  }catch(e){ flash('连接失败：' + e.message, true); }
}

/* ---------------- 成稿输出：Word 友好排版 ---------------- */
function mdToPlain(t){
  return (t||'')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/^\s*[-*+]\s+/gm, '· ')
    .replace(/^\s*\d+\.\s+/gm, m=>m)
    .replace(/^\s*>\s?/gm, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
function plainLines(t){
  return mdToPlain(t).split('\n').map(s=>s.trim()).filter(s=>s.length);
}
/* 把成稿转成 Word 友好的 HTML：第 1 行作标题，短行/原文 ## 行作小标题 */
function buildWordHtml(indent){ return buildWordHtmlText($('#finalText').value, indent); }
function buildWordHtmlText(src, indent){
  const lines = plainLines(src);
  if(!lines.length) return '';
  // 小标题判定：短行、不以句末标点结尾；排除列表项、来源模块、以冒号收尾的行
  const isHead = (s, i) => i>0 && s.length<=22
    && !/[。；，,;：:]$/.test(s)
    && !s.startsWith('·') && !s.startsWith('内容') && !/^\d+[.、]/.test(s);
  const bodyStyle = 'font-family:"微软雅黑",sans-serif;font-size:11pt;line-height:1.75;'
    + 'margin:0 0 8pt;' + (indent ? 'text-indent:2em;' : '');
  const out = [];
  lines.forEach((s, i)=>{
    if(i===0){
      out.push(`<p style="font-family:\"微软雅黑\",sans-serif;font-size:16pt;font-weight:bold;`
        + `text-align:center;line-height:1.5;margin:0 0 16pt">${esc(s)}</p>`);
    } else if(isHead(s, i)){
      out.push(`<p style="font-family:\"微软雅黑\",sans-serif;font-size:13pt;font-weight:bold;`
        + `line-height:1.6;margin:14pt 0 8pt">${esc(s)}</p>`);
    } else {
      out.push(`<p style="${bodyStyle}">${esc(s)}</p>`);
    }
  });
  return '<html><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
    + '<body style="font-family:\"微软雅黑\",sans-serif;font-size:11pt;line-height:1.75">'
    + out.join('') + '</body></html>';
}
function previewWord(){
  const html = buildWordHtml($('#indent').checked);
  $('#wordPreview').innerHTML = html
    ? html.replace(/<\/?html[^>]*>|<\/?head[^>]*>|<\/?body[^>]*>|<meta[^>]*>/g,'')
    : '<p class="muted">还没有成稿。</p>';
}
async function copyRich(){ return copyRichText($('#finalText').value, '还没有成稿'); }
async function copyRichText(src, emptyMsg){
  const html = buildWordHtmlText(src, $('#indent') ? $('#indent').checked : false);
  const text = mdToPlain(src);
  if(!html){ flash(emptyMsg || '没有内容', true); return; }
  try{
    if(window.ClipboardItem && navigator.clipboard && navigator.clipboard.write){
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html], {type:'text/html'}),
        'text/plain': new Blob([text], {type:'text/plain'})
      })]);
      flash('已复制「带格式」版本 —— 粘到 Word 里直接是标题+正文');
      return;
    }
    throw new Error('clipboard-unsupported');
  }catch(e){
    fb(text, ()=>flash('浏览器不支持富文本复制，已改为复制纯文本（无 Markdown 符号）'));
  }
}
function copyPlain(){
  const text = mdToPlain($('#finalText').value);
  fb(text, ()=>flash('已复制纯文本（Markdown 符号已清理）'));
}
function downloadDoc(){ return downloadDocText($('#finalText').value, '成稿'); }
function downloadDocText(src, fallbackName){
  const html = buildWordHtmlText(src, $('#indent') ? $('#indent').checked : false);
  if(!html){ flash('没有可导出的内容', true); return; }
  const name = (plainLines(src)[0] || fallbackName || '成稿').replace(/[\\/:*?"<>|]/g,'').slice(0,40);
  const blob = new Blob(['\ufeff', html], {type:'application/msword'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name + '.doc';
  document.body.appendChild(a); a.click();
  setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); }, 0);
  flash('已下载 Word 文档：' + name + '.doc');
}

function copyFinal(){ copyRich(); }
function fb(t,done){
  const ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta);
  ta.select(); try{document.execCommand('copy'); done&&done();}catch(e){alert('请手动选中复制');}
  document.body.removeChild(ta);
}
function toggleSet(){
  const b = $('#setbox');
  b.style.display = b.style.display==='none' ? 'block' : 'none';
}

/* ---------------- 本地服务：文件解析 / 语料入库 / 重算 ---------------- */
function svcMsg(el, msg, bad){ const e=$(el); e.textContent = msg; e.style.color = bad ? '#A32D2D' : '#0F6E56'; }
async function svc(path, payload, raw, filename){
  const opt = { method:'POST' };
  if(raw){ opt.body = raw; opt.headers = { 'Content-Type':'application/octet-stream',
            'X-Filename-B64': btoa(unescape(encodeURIComponent(filename||'upload.bin'))) }; }
  else { opt.headers = { 'Content-Type':'application/json' }; opt.body = JSON.stringify(payload||{}); }
  let res;
  try{ res = await fetch(apiBase()+path, opt); }
  catch(e){
    const remote = !['127.0.0.1','localhost'].includes(location.hostname);
    throw new Error(remote
      ? '连不上本地服务。① 确认已运行 start-writer.bat 或 python tools/writer_server.py；② 若已在运行，可能是浏览器拦截了"线上页面 → 本机服务"的请求，最稳的做法是直接打开 http://127.0.0.1:8787/writer.html 使用（同源，不受拦截）'
      : '连不上本地服务（'+apiBase()+'）。请先运行：python tools/writer_server.py');
  }
  const txt = await res.text();
  let json = null; try{ json = JSON.parse(txt); }catch(e){}
  if(!res.ok){
    const m = (json && json.error && json.error.message) || txt.slice(0,300);
    throw new Error(m);
  }
  return json || {};
}

async function checkSvc(){
  svcMsg('#svcMsg','检测中…');
  const RESTART = '请关掉那个黑窗口，再双击桌面「科普写作台（本地启动）」重新打开。'
                + '（旧版服务不会在入库后自动重建页面，所以工作台看不到新语料）';
  let ping = null;
  try{ ping = await svc('/api/ping', {}); }
  catch(e){
    // /api/ping 不存在 = 服务是升级前的旧版本
    try{
      const r0 = await svc('/api/restat', {});
      svcMsg('#svcMsg', `⚠ 本地服务是旧版本（不认识 /api/ping）。${RESTART}`, true);
      renderRestat(r0, '#restatOut');
      return;
    }catch(e2){ svcMsg('#svcMsg', e2.message, true); return; }
  }
  if(ping.need_restart){
    svcMsg('#svcMsg',
      `⚠ 本地服务代码已更新，但服务还在跑旧代码（启动于 ${ping.started_at}，代码更新于 ${ping.code_mtime}）。`
      + RESTART, true);
  }else{
    svcMsg('#svcMsg', `本地服务正常 v${ping.version} · 知识库 ${ping.files} 个文件 / 计入 ${ping.counted} 篇`);
  }
  const r = await svc('/api/restat', {});
  renderRestat(r, '#restatOut');
}

/* 页面打开时静默探一次本地服务：让"当前语料 N 篇"始终是实时的 */
async function syncBadge(){
  try{
    const p = await svc('/api/ping', {});
    const el = $('#liveBadge');
    if(el){
      el.style.display = 'inline-block';
      el.className = 'tag ' + (p.need_restart ? 't-t' : 't-r');
      el.textContent = p.need_restart
        ? `本地服务待重启（语料 ${p.counted} 篇）`
        : `已连本地服务 · 语料 ${p.counted} 篇`;
      el.title = `服务 v${p.version}｜启动 ${p.started_at}｜代码 ${p.code_mtime}`;
    }
  }catch(e){ /* 没起服务就静默跳过 */ }
}

async function handleFiles(files){
  if(!files || !files.length) return;
  svcMsg('#fileMsg','解析中…');
  const parts = [];
  for(const f of files){
    try{
      const r = await svc('/api/extract', null, await f.arrayBuffer(), f.name);
      parts.push(`【来自文件：${f.name}｜${r.method}｜${r.chars} 字】\n${r.text}`);
    }catch(e){
      parts.push(`【${f.name} 解析失败：${e.message}】`);
    }
  }
  const ta = $('#material');
  ta.value = (ta.value ? ta.value.replace(/\s*$/,'') + '\n\n' : '') + parts.join('\n\n');
  svcMsg('#fileMsg', `已并入资料区（${files.length} 个文件）`);
}

async function ingest(){
  const raw = $('#links').value.trim();
  if(!raw){ svcMsg('#ingMsg','请先粘贴链接', true); return; }
  const btn = $('#ingGo'); btn.disabled = true; btn.textContent = '抓取中…';
  svcMsg('#ingMsg','正在抓取并入库…');
  try{
    const r = await svc('/api/ingest', { urls: raw, genre: $('#ingGenre').value });
    const rows = r.results.map(it => it.ok
      ? `<tr><td><span class="tag t-r">已入库</span></td><td>${esc(it.title)}</td>
         <td>${esc(it.genre)}</td><td>${it.chars} 字</td><td class="muted small">${esc(it.file)}</td></tr>`
      : `<tr><td><span class="tag t-bad">失败</span></td><td colspan="4" class="muted">${esc(it.msg)}｜${esc(it.url)}</td></tr>`).join('');
    $('#ingOut').innerHTML = `
      <div class="card" style="margin:14px 0 0">
        <h2>入库结果：成功 ${r.ok} 篇 · 失败 ${r.fail} 篇</h2>
        <table><thead><tr><th style="width:80px">状态</th><th>标题</th><th style="width:70px">文体</th>
        <th style="width:80px">字数</th><th>文件名</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="small muted" style="margin-top:10px">已写入：${esc(r.kb1)}</p>
      </div>`;
    svcMsg('#ingMsg', `完成：成功 ${r.ok} 篇`);
    if(r.restat) renderRestat(r.restat, '#restatOut');
    if(r.rebuilt){
      const bar = $('#ingOut');
      bar.insertAdjacentHTML('afterbegin', `
        <div class="card" style="margin:14px 0 0;border-left:3px solid var(--ok,#0F6E56)">
          <h2>知识库已同步</h2>
          <p class="small">新语料已写入、特征已重算、<b>展示台与写作台的页面已重建</b>。
          当前这页的数据还是旧的，刷新一次即可看到最新统计。</p>
          <div class="tools">
            <button class="btn" id="reloadNow">立即刷新页面</button>
            <a class="btn ghost" href="index.html" target="_blank" rel="noopener">打开展示台看新语料</a>
          </div>
        </div>`);
      const rl = $('#reloadNow');
      if(rl) rl.addEventListener('click', ()=>location.reload());
    }else if(r.note){
      svcMsg('#ingMsg', r.note, true);
    }
  }catch(e){ svcMsg('#ingMsg', e.message, true); }
  finally{ btn.disabled = false; btn.textContent = '抓取并入库'; }
}

function renderRestat(rs, sel){
  if(!rs || !rs.counted){ $(sel).innerHTML = ''; return; }
  const c = rs.chars||{};
  const lv = {R:'t-r', T:'t-t', O:'t-o'};
  const rows = (rs.metrics||[]).map(m=>`<tr>
    <td>${esc(m.name)}</td>
    <td class="freq">${m.hit}/${m.total} = ${m.pct}%</td>
    <td><span class="tag ${lv[m.level]||'t-o'}">${m.level} 级</span></td>
    <td class="muted small">${m.level==='R'?'可作稳定规则':'待样本累积'}</td></tr>`).join('');
  $(sel).innerHTML = `
    <div class="card" style="margin:14px 0 0">
      <h2>自我总结 · 语料特征重算</h2>
      <p class="small">计入 <b>${rs.counted}</b> 篇（共 ${rs.total} 个文件）　·　正文篇幅：最小 ${c.min} / P25 ${c.p25} / <b>中位 ${c.p50}</b> / P75 ${c.p75} / 最大 ${c.max} 字</p>
      <table><thead><tr><th>特征</th><th style="width:140px">实测频率</th><th style="width:90px">等级</th><th style="width:130px">说明</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="small muted" style="margin-top:10px">
        规则升级纪律：单篇新样本只进 KB1、不改规则；同一新特征在 <b>≥3 篇</b>出现且与现规则冲突，才修订 KB2。
        R 级门槛为同文体 ≥5 篇且频率 ≥80%。</p>
      <p class="small muted">点「重建页面」可把新语料与规则同步到展示台。</p>
    </div>`;
}

async function rebuild(){
  svcMsg('#ingMsg','重建页面中…');
  try{ const r = await svc('/api/rebuild', {}); svcMsg('#ingMsg', r.hint || '已重建'); }
  catch(e){ svcMsg('#ingMsg', e.message, true); }
}

/* ==================== 改稿体检（本地计算，不经过模型） ==================== */
const T = D.targets || {};

const RE_STDNO = /(?:GB\/T|GB|ISO|IEC|DBS?|SB\/T|QB\/T|NY\/T|T\/[A-Z]{2,8}|JJF)\s?\d[\d.\-—]*/g;
const RE_EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu;
const RE_SRC_MOD = /^(内容来源|内容参考|资料来源|参考资料|来源)[:：]?\s*$/;
const RE_SRC_INL = /^(内容来源|内容参考|资料来源|参考资料|来源)[:：]\s*\S/;
const CONN = ["其实","因此","通常","可能","建议","同时","但是","所以","那么","不过","例如","另外","值得注意"];
/* 文体判定的关键词（短语比单词区分度高；标准号另加权）。
   参数是在 80 篇真实语料上回归出来的：关键词 + 标准号×3 + 标题×3 ≈ 71% 准确率。
   所以判定结果只作**建议**，页面会显示置信度，最终以用户选的文体为准。 */
const GENRE_WORDS = {
  STD: ["标准规定","根据标准","标准要求","依据《","国标","强制性","推荐性","条款","限值","监督抽查","执行标准","技术要求"],
  AVOID: ["怎么选","选购","避坑","踩坑","误区","值不值","辨别","套路","别买","吃亏","划算","挑选","挑对","性价比"],
  HEALTH: ["营养","摄入","膳食","热量","蛋白","脂肪","维生素","钙","吃","喝","消化","代谢","成分"],
  SAFETY: ["风险","防护","事故","危害","误用","伤害","使用不当","安全隐患","中毒","烫","爆炸","漏电"],
};

function guessGenre(joined, title, nStd){
  const sc = {};
  for(const g in GENRE_WORDS){
    let v = 0;
    for(const k of GENRE_WORDS[g]){
      v += joined.split(k).length - 1;
      v += (title.split(k).length - 1) * 3;   // 标题里的词更能说明意图
    }
    sc[g] = v;
  }
  sc.STD += nStd * 3;                          // 有带编号标准 → 强烈指向 STD
  let best = 'MISC', bv = 0;
  for(const g in sc){ if(sc[g] > bv){ bv = sc[g]; best = g; } }
  const sorted = Object.values(sc).sort((a,b)=>b-a);
  const conf = bv / Math.max(sorted[1] || 0, 1);
  if(bv < 4) return {genre:'MISC', sc, conf:0};
  return {genre:best, sc, conf};
}

function jLines(t){ return (t||'').replace(/\r/g,'').split('\n').map(s=>s.replace(/\*\*/g,'').trim()).filter(Boolean); }

function analyze(text){
  const raw = jLines(text);
  // 首行若是短句且不以句末标点结尾，视为标题
  let title = '';
  if(raw.length && raw[0].length <= 34 && !/[。；，,]$/.test(raw[0])){
    title = raw[0].replace(/^#+\s*/,'');
  }
  const lines = raw.slice(title ? 1 : 0);
  const moduleAt = lines.findIndex(l => RE_SRC_MOD.test(l));
  const srcModule = moduleAt >= 0;
  const srcInline = lines.some(l => RE_SRC_INL.test(l));
  const body = (srcModule ? lines.slice(0, moduleAt) : lines)
    .filter(l => !/^\[\[图片\]\]$/.test(l) && !/^https?:\/\//.test(l));

  const joined = body.join('');
  const chars = joined.replace(/\s/g,'').length;
  const lens = body.map(l => l.length);
  const avg = lens.length ? Math.round(lens.reduce((a,b)=>a+b,0)/lens.length) : 0;
  const first = body[0] || '';
  /* 小标题口径必须与 corpus_digest.py 完全一致：
     短行（≤22 字）且不以「。」结尾，排除 "1." 这类序号行。
     历史 bug：这里曾把「。；！？，,」全部排除，导致以问号结尾的小标题永远数不到 ——
     问句式小标题恒为 0，STD 的 T-01b 检查因此永远判违规。 */
  const heads = body.filter(l => l.length <= 22 && !/。$/.test(l) && !/^\d+[.、]/.test(l));
  const headQ = heads.filter(l => /[？?]$/.test(l)).length;
  const stds = Array.from(new Set(joined.match(RE_STDNO) || []));
  const emoji = (joined.match(RE_EMOJI) || []).length;

  const gj = guessGenre(joined, title, stds.length);
  const genre = gj.genre, gScore = gj.sc;

  return {
    title, chars, paras: body.length, avg, first, firstHasStd: RE_STDNO.test(first),
    firstHasWord: /标准|规定/.test(first), heads: heads.length, headQ,
    exc: (joined.match(/[！!]/g)||[]).length,
    fwComma: (joined.match(/，/g)||[]).length, hwComma: (joined.match(/,/g)||[]).length,
    /* 半角引号 / 撇号计数（KB2 减法清单 S-05：引号一律全角）。
       语料实测：全库 284 对全角引号，仅 1 篇残留 4 个半角引号。 */
    hwQuote: (joined.match(/[\u0022\u0027]/g)||[]).length,
    stds, emoji, srcModule, srcInline,
    src: srcModule ? 'module' : (srcInline ? 'inline' : 'none'),
    genre, gScore, gConf: gj.conf, conn: CONN.filter(w => joined.includes(w)),
    consumer: joined.split('消费者').length-1, we: joined.split('我们').length-1,
    you: joined.split('你').length-1, titleQ: /[？?]/.test(title), titleLen: title.length,
    body,
  };
}

function checkRules(a, genre){
  const lo = (T.chars||[1000,1400])[0], hi = (T.chars||[1000,1400])[1];
  const rows = [];
  const add = (lvl, code, name, ok, got, want) =>
    rows.push({lvl, code, name, ok, got, want});

  add('R','R-01','首段不摆条款（无标准号、无“标准/规定”）',
      !a.firstHasStd && !a.firstHasWord,
      (a.firstHasStd?'首段含标准号':'') + (a.firstHasWord?(a.firstHasStd?'、':'')+'首段含“标准/规定”':'') || '干净',
      '首段只写场景，主题压到第 2–3 句');
  add('R','R-02','几乎不用感叹号', a.exc === 0, a.exc + ' 个', '全篇 0 个');
  add('R','R-03','正文引用 ≥1 个带编号标准', a.stds.length >= 1,
      a.stds.length + ' 个' + (a.stds.length?'（'+a.stds.slice(0,3).join('、')+'）':''),
      '≥1 个，中位 2 个');
  add('R','R-04','有来源标注（模块或行内）', a.src !== 'none',
      a.src==='module'?'独立来源模块':(a.src==='inline'?'行内“内容来源：”':'无'),
      '两种形态任选，必须有');
  /* S-xx = KB2《四、减法清单》条目，与 R 级同属必改项 */
  add('R','S-05','标点全角（引号与逗号都不用半角）',
      a.hwQuote === 0 && a.hwComma === 0,
      '半角引号 ' + a.hwQuote + ' 个 / 半角逗号 ' + a.hwComma + ' 个',
      '引号用“”，逗号用， (KB2 减法清单 S-05)');
  add('T','T-01','加粗短句切节，≥3 个小标题',
      a.heads >= 3, a.heads + ' 个（其中问句 ' + a.headQ + ' 个）', '中位 6 个（区间 2–24）');
  add('T','T-03','篇幅 ' + lo + '–' + hi + ' 字', a.chars >= lo && a.chars <= hi,
      a.chars + ' 字', '中位 ' + (T.p50||1180) + ' 字');
  add('T','T-04','段落节奏（段数与段长）',
      a.paras >= 12 && a.paras <= 34 && a.avg >= 38 && a.avg <= 82,
      a.paras + ' 段 / 平均 ' + a.avg + ' 字', '中位 ' + (T.paras||20) + ' 段 / 段长 ' + (T.avg_para||57) + ' 字');
  add('T','T-02','标题 ' + (T.title_len||16.9) + ' 字上下、宜带问号',
      a.titleLen >= 8 && a.titleLen <= 26,
      (a.title?a.titleLen+' 字':'未识别标题') + (a.titleQ?'，带问号':'，无问号'),
      '平均 ' + (T.title_len||16.9) + ' 字，带问号 59%');
  if(genre === 'STD')
    add('T','T-01b','标准类：至少一个问句式小标题', a.headQ >= 1,
        a.headQ + ' 个', 'STD 实测 85%');
  if(genre === 'HEALTH')
    add('O','O-01','健康类可用 emoji 做锚点', true,
        a.emoji + ' 个', 'HEALTH 实测 59%（可选）');
  // T-06 只作观察：连接词密度因人因题而异，不该判"违规"
  add('O','T-06','日常连接词使用（观察项）', true,
      (a.conn.join('、') || '本篇偏少') + '（' + a.conn.length + ' 种）',
      '全库常见：其实/因此/通常/可能/建议');
  add('T','T-07','称谓以“消费者”为主',
      a.consumer + a.we + a.you > 0,
      '消费者 ' + a.consumer + ' / 我们 ' + a.we + ' / 你 ' + a.you, '消费者 67%、我们 51%');
  return rows;
}

function renderCheck(a, rows){
  const R = rows.filter(r=>r.lvl==='R'), Tr = rows.filter(r=>r.lvl!=='R');
  const rOk = R.filter(r=>r.ok).length, tOk = Tr.filter(r=>r.ok).length;
  const must = R.filter(r=>!r.ok);
  const score = Math.round(100*(R.filter(r=>r.ok).length*3 + Tr.filter(r=>r.ok).length)
                           / (R.length*3 + Tr.length));
  const lv = score>=90?['t-r','很稳']:(score>=75?['t-t','接近']:['t-bad','需要改']);
  $('#revScore').innerHTML =
    `<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
       <div style="font-size:30px;font-weight:600;color:${score>=90?'#0F6E56':(score>=75?'#854F0B':'#A32D2D')}">${score}</div>
       <div><span class="tag ${lv[0]}">${lv[1]}</span>
         <div class="small muted" style="margin-top:4px">
           R 级 ${rOk}/${R.length} 项合规　·　T/O 级 ${tOk}/${Tr.length} 项符合
           ${must.length?`　·　<b class="bad-mark">${must.map(m=>m.code).join('、')} 必须先改</b>`:''}
         </div></div></div>`;

  const trs = rows.map(r=>`<tr>
    <td><span class="${r.ok?'ok-mark':(r.lvl==='R'?'bad-mark':'warn-mark')}">${r.ok?'✓ 合规':(r.lvl==='R'?'✗ 违规':'△ 偏离')}</span></td>
    <td><b>${r.code}</b> ${esc(r.name)}</td>
    <td class="small">${esc(r.got)}</td>
    <td class="small muted">${esc(r.want)}</td></tr>`).join('');
  $('#revTable').querySelector('tbody').innerHTML = trs;

  const g = Object.entries(a.gScore).sort((x,y)=>y[1]-x[1]).slice(0,3)
    .map(([k,v])=>`${k} ${v}`).join('　·　');
  const lowConf = a.gConf < 1.4;
  $('#revMetrics').innerHTML = `
    <div class="small muted" style="line-height:1.9">
      <b>文体初判</b>：<b>${a.genre}</b>
      　（关键词得分：${g || '无明显特征词'}｜置信度 ${a.gConf.toFixed(2)}×）
      ${lowConf?'　<span class="warn-mark">置信度偏低，请在上面手动确认文体</span>':''}
      ${a.genre==='AVOID'?'　<span class="warn-mark">该文体仅 3 篇样本，手册规则未达 R 级</span>':''}
      ${a.genre==='MISC'?'　<span class="warn-mark">特征不明显，按 STD 骨架走但去掉标准段</span>':''}<br>
      <b>标点</b>：全角逗号 ${a.fwComma} ／ 半角 ${a.hwComma}${a.hwComma? '　<span class="warn-mark">半角逗号需统一为全角</span>':''}
      　·　emoji ${a.emoji} 个<br>
      <b>标题</b>：${a.title? esc(a.title) : '（未识别，首行不像标题）'}
    </div>`;
  $('#revReport').style.display = 'block';
}

function genreBlock(genre){
  const gm = D.genre_manual || {};
  let out = '';
  if(gm._howto) out += gm._howto + '\n\n';
  if(gm[genre]) out += gm[genre] + '\n\n';
  if(gm._table) out += '【跨文体对照（供参考，不要套用别的文体）】\n' + gm._table;
  return out;
}

function revisePrompt(text, genre, level, a){
  const lv = {light:'轻度：只改违反 KB2 的项，尽量保留原句与原顺序，不大改结构。',
              normal:'常规：按手册重整段落与小标题，统一语气与标点，保留原有的信息与例子。',
              deep:'重度：按手册重写结构与表达方式（可重新切节、重写过渡句），但事实与例子不得改变。'};
  return `你是《质量与标准化》杂志的资深编辑，现在要按【这位作者本人的风格规则】修改一篇已写好的稿件。

【硬约束 · 违反即失败】
1. 不得改动任何标准号、编号、数值、限值、日期、机构名、检测数据、结论方向。
2. 不得新增原文没有的事实、案例、数据、来源。
3. 不得删除原文的实质信息（例子、风险提醒、适用边界都要保留）。
4. 不得整句照搬任何历史语料（你只看得到规则，看不到历史原文）。
5. 不要加"综上所述""由此可见""值得注意的是"这类书面连接。

【KB2 风格规则（${(D.targets||{}).kb2_ver||''} · 基准 ${(D.targets||{}).kb2_base||''} 篇实测）】
${D.rules_text}

【本篇文体：${genre} · 按该文体手册改】
${genreBlock(genre)}

【本次体检发现的偏离项（必须逐条修正）】
${checkRules(a, genre).filter(r=>!r.ok).map(r=>`- ${r.code} ${r.name}｜现状：${r.got}`).join('\n') || '（无）'}

【改稿力度】${lv[level]}

【原文】
${text}

【输出要求】
直接输出修改后的完整正文，不要任何解释、不要前后缀、不要"以下是修改稿"之类的话。
标题单独一行；小标题单独一行并用 **加粗** 标记；其余为正文段落。
字数目标 ${(T.chars||[1000,1400])[0]}–${(T.chars||[1000,1400])[1]} 字。`;
}

async function runCheck(){
  const text = $('#revText').value.trim();
  if(!text){ svcMsg('#revMsg','请先粘贴或上传待改文章', true); return null; }
  const a = analyze(text);
  const g = $('#revGenre').value || a.genre;
  a.genreFinal = g;
  renderCheck(a, checkRules(a, g));
  svcMsg('#revMsg', `体检完成：${a.chars} 字 / ${a.paras} 段 / 判定文体 ${g}`);
  window.__revA = a;
  const mt = $('#revText');
  return a;
}

async function reviseNow(){
  const a = await runCheck();
  if(!a) return;
  const text = $('#revText').value.trim();
  const genre = a.genreFinal;
  const level = $('#revLevel').value;
  const btn = $('#revGo'); btn.disabled = true; btn.textContent = '改稿中…';
  svcMsg('#revGoMsg','正在按手册改稿（一次独立请求）…');
  try{
    const out = await callModel([{role:'user', content: revisePrompt(text, genre, level, a)}], 0.35);
    $('#revResult').value = out.trim();
    $('#revOut').style.display = 'block';
    $('#revOutMeta').textContent =
      `文体 ${genre}　·　力度 ${level}　·　原文 ${a.chars} 字 → 改稿 ${out.replace(/\s/g,'').length} 字`;
    svcMsg('#revGoMsg','改稿完成。可点③再体检一次做前后对比。');
    $('#revOut').scrollIntoView({behavior:'smooth', block:'start'});
  }catch(e){ svcMsg('#revGoMsg', e.message, true); }
  finally{ btn.disabled = false; btn.textContent = '② 按手册改稿'; }
}

function compareTable(before, after){
  const rows = [
    ['字数', before.chars, after.chars],
    ['段数', before.paras, after.paras],
    ['平均段长', before.avg, after.avg],
    ['感叹号', before.exc, after.exc],
    ['小标题', before.heads, after.heads],
    ['问句式小标题', before.headQ, after.headQ],
    ['标准号', before.stds.length, after.stds.length],
    ['来源标注', {module:'独立模块',inline:'行内',none:'无'}[before.src],
                {module:'独立模块',inline:'行内',none:'无'}[after.src]],
    ['emoji', before.emoji, after.emoji],
  ];
  const trs = rows.map(([k,b,c])=>{
    const changed = String(b) !== String(c);
    return `<tr><td>${k}</td><td class="small">${b}</td><td class="small ${changed?'ok-mark':''}">${c}${changed?' ←改':''}</td></tr>`;
  }).join('');
  return `<div class="card" style="background:#FBFBF9;margin:0">
    <h2>改稿前后对照（本地计算）</h2>
    <table><thead><tr><th style="width:130px">指标</th><th>改前</th><th>改后</th></tr></thead>
    <tbody>${trs}</tbody></table></div>`;
}

async function recheck(){
  const a2 = analyze($('#revResult').value);
  const g = $('#revGenre').value || a2.genre;
  renderCheck(a2, checkRules(a2, g));
  if(window.__revA) $('#revCompare').innerHTML = compareTable(window.__revA, a2);
  svcMsg('#revOutMsg','已重新体检，见上方报告与对照。');
}

async function dupCheck(){
  svcMsg('#revGoMsg','查重中（与 79 篇历史语料比对连续 12 字）…');
  try{
    const r = await svc('/api/dupcheck', { text: $('#revText').value, n: 12 });
    if(r.pass){ svcMsg('#revGoMsg', `查重通过：与历史语料无 ≥${r.n} 字连续重合`); $('#revDiffOut').innerHTML=''; return; }
    const rows = r.hits.map(h=>`<tr><td class="small">${esc(h.src)}</td>
      <td class="small">${h.snippet.length} 字</td>
      <td class="small">${esc(h.snippet.slice(0,60))}…</td></tr>`).join('');
    $('#revDiffOut').innerHTML = `<div class="card" style="border-left:3px solid #A32D2D;margin:14px 0 0">
      <h2>⚠ 查重命中：${r.n_hits} 处</h2>
      <p class="small">与历史语料连续重合最长 <b>${r.longest}</b> 字，超过 ${r.n} 字阈值即判疑似洗稿，必须改掉。</p>
      <table><thead><tr><th style="width:190px">来源篇目</th><th style="width:70px">长度</th><th>重合片段</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
    svcMsg('#revGoMsg', `查重命中 ${r.n_hits} 处，最长 ${r.longest} 字`, true);
  }catch(e){ svcMsg('#revGoMsg', e.message, true); }
}

async function diffList(){
  const before = $('#revText').value.trim(), after = $('#revResult').value.trim();
  if(!after){ svcMsg('#revDiffMsg','先完成改稿', true); return; }
  svcMsg('#revDiffMsg','生成中…');
  try{
    const out = await callModel([{role:'user', content:
`对比下面两稿，用中文列一份改动清单：逐条写「原文问题 → 改成了什么 → 依据哪条规则」。
只列写作层面的改动（结构、语气、标点、来源标注、小标题），不要评价事实对错，不要复述全文。
控制在 12 条以内，用无序列表。

【原稿】
${before}

【改后稿】
${after}`}], 0.2);
    $('#revDiffOut').innerHTML = `<div class="card" style="background:#FBFBF9;margin:14px 0 0">
      <h2>改动清单</h2><div class="small" style="white-space:pre-wrap;line-height:1.85">${esc(out.trim())}</div></div>`;
    svcMsg('#revDiffMsg','已生成');
  }catch(e){ svcMsg('#revDiffMsg', e.message, true); }
}

function revDownload(){
  const txt = $('#revResult').value;
  if(!txt){ svcMsg('#revOutMsg','还没有修订稿', true); return; }
  downloadDocText(txt, '修订稿');
}

function on(sel, ev, fn){ const el = $(sel); if(el) el.addEventListener(ev, fn); }
function showPage(id){
  document.querySelectorAll('#page-write,#page-revise')
    .forEach(el => el.style.display = (el.id === id ? '' : 'none'));
  document.querySelectorAll('#modeTabs .tab')
    .forEach(b => b.classList.toggle('on', b.dataset.page === id));
}
document.querySelectorAll('#modeTabs .tab').forEach(b => b.addEventListener('click', ()=>{
  showPage(b.dataset.page);
  window.scrollTo({top:0, behavior:'smooth'});
}));

on('#revCheck','click', runCheck);
on('#revGo','click', reviseNow);
on('#revRecheck','click', recheck);
on('#revDup','click', dupCheck);
on('#revDiff','click', diffList);
on('#revDownload','click', revDownload);
on('#revPickFile','click', ()=>{ const el=$('#revFileInput'); if(el) el.click(); });
on('#revFileInput','change', e => revFiles(e.target.files));
on('#revCopyRich','click', ()=>{
  const t = $('#revResult').value;
  if(!t){ svcMsg('#revOutMsg','还没有修订稿', true); return; }
  copyRichText(t);
});
const revTa = $('#revText');
if(revTa){
  revTa.addEventListener('dragover', e=>{ e.preventDefault(); revTa.style.borderColor='#0F6E56'; });
  revTa.addEventListener('dragleave', ()=>{ revTa.style.borderColor=''; });
  revTa.addEventListener('drop', e=>{ e.preventDefault(); revTa.style.borderColor=''; revFiles(e.dataTransfer.files); });
}

async function revFiles(files){
  if(!files || !files.length) return;
  svcMsg('#revMsg','解析中…');
  const parts = [];
  for(const f of files){
    try{
      const r = await svc('/api/extract', null, await f.arrayBuffer(), f.name);
      parts.push(r.text);
    }catch(e){ parts.push(`【${f.name} 解析失败：${e.message}】`); }
  }
  const ta = $('#revText');
  ta.value = (ta.value ? ta.value.replace(/\s*$/,'') + '\n\n' : '') + parts.join('\n\n');
  svcMsg('#revMsg', `已载入 ${files.length} 个文件，可点「① 体检」`);
}

loadCfg();
renderSteps();
(function initStat(){
  const el = $('#subStat');
  if(el) el.textContent = `按 ${D.counted} 篇语料提炼的风格规则生成公众号科普推文`
    + `（篇幅中位 ${D.p50} 字，目标 ${D.words||'1300–1550'} 字）`;
})();
syncBadge();
/* 空值保护：某个元素不存在时不要连带废掉后面的绑定 */
function on(sel, ev, fn){ const el = $(sel); if(el) el.addEventListener(ev, fn); }
on('#go','click', run);
on('#save','click', saveCfg);
on('#test','click', testConn);
on('#copyRich','click', copyRich);
on('#copyPlain2','click', copyPlain);
on('#dlDoc','click', downloadDoc);
on('#indent','change', previewWord);
on('#tog','click', toggleSet);
on('#pickFile','click', ()=>$('#fileInput').click());
on('#fileInput','change', e=>handleFiles(e.target.files));
on('#ingGo','click', ingest);
on('#svcCheck','click', checkSvc);
on('#rebuild','click', rebuild);
on('#material','dragover', e=>{ e.preventDefault(); e.currentTarget.style.borderColor='#0F6E56'; });
on('#material','dragleave', e=>{ e.currentTarget.style.borderColor=''; });
on('#material','drop', e=>{ e.preventDefault(); e.currentTarget.style.borderColor=''; handleFiles(e.dataTransfer.files); });
$('#setbox').style.display='none';
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>科普写作台 · 按本人风格生成推文</title>
<style>
*{box-sizing:border-box}
:root{--bg:#F7F7F5;--card:#fff;--ink:#1F2328;--muted:#5F5E5A;--line:#E3E1DA;
  --teal:#0F6E56;--teal-bg:#E1F5EE;--amber:#854F0B;--amber-bg:#FAEEDA;--red:#A32D2D;--red-bg:#FCEBEB;--gray:#F1EFE8}
html,body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei",system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.7}
.wrap{max-width:900px;margin:0 auto;padding:26px 18px 80px}
h1{font-size:21px;font-weight:600;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
.card h2{font-size:15px;font-weight:600;margin:0 0 10px}
label{display:block;font-size:12.5px;color:var(--muted);margin:10px 0 4px}
input,textarea{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;
  font-size:14px;font-family:inherit;background:#FBFBF9;color:var(--ink)}
textarea{min-height:150px;resize:vertical;line-height:1.7}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.row>div{flex:1;min-width:150px}
.btn{background:var(--teal);color:#fff;border:none;border-radius:8px;padding:9px 18px;
  font-size:14px;cursor:pointer;font-family:inherit}
.btn:disabled{opacity:.5;cursor:default}
.btn.ghost{background:transparent;color:var(--teal);border:1px solid var(--teal)}
.btn.sm{padding:6px 12px;font-size:12.5px}
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
.tabs{display:flex;gap:6px;margin:0 0 14px}
.tab{background:transparent;border:1px solid var(--line);border-radius:999px;
  padding:7px 18px;font-size:13.5px;color:var(--muted);cursor:pointer;font-family:inherit}
.tab.on{background:var(--teal);border-color:var(--teal);color:#fff}
.ok-mark{color:#0F6E56;font-weight:600}
.bad-mark{color:#A32D2D;font-weight:600}
.warn-mark{color:#854F0B;font-weight:600}
input[type=text],select{font-family:inherit}
select{padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;
  background:#fff;color:var(--ink);width:100%}
#flash{font-size:12.5px}
.step{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px;background:var(--card)}
.step .shead{display:flex;align-items:center;gap:8px;font-weight:500;font-size:13.5px}
.step .dot{width:9px;height:9px;border-radius:50%;background:#C9C7BF;flex:none}
.step .stat{margin-left:auto;font-size:12px;color:var(--muted);font-weight:400}
.step pre{margin:8px 0 0;padding:10px;background:#FBFBF9;border:1px solid var(--line);border-radius:8px;
  font-size:12.5px;white-space:pre-wrap;word-break:break-word;max-height:340px;overflow:auto;display:none}
.step.run{border-color:#85B7EB;background:#F5F9FE}
.step.run .dot{background:#378ADD;animation:pulse 1s infinite alternate}
.step.run pre{display:block}
.step.ok .dot{background:var(--teal)}
.step.ok pre{display:block}
.step.warn{border-color:#EF9F27;background:#FFFCF5}
.step.warn .dot{background:#BA7517}
.step.warn pre{display:block}
.step.bad{border-color:#E24B4A;background:#FFF8F8}
.step.bad .dot{background:var(--red)}
.step.bad pre{display:block}
@keyframes pulse{from{opacity:.35}to{opacity:1}}
#final textarea{min-height:420px;font-size:14px;line-height:1.85}
.notice{background:var(--amber-bg);color:var(--amber);border-radius:10px;padding:10px 14px;font-size:12.5px;margin-bottom:14px}
.warnbox{background:var(--red-bg);color:var(--red);border-radius:10px;padding:10px 14px;font-size:12.5px;margin-bottom:14px}
a{color:var(--teal)}
</style>
</head>
<body>
<div class="wrap">
  <h1>科普写作台 <span class="tag t-o" id="liveBadge" style="display:none;vertical-align:middle"></span></h1>
  <div class="sub"><span id="subStat"></span>　·　<a href="index.html">← 返回知识库工作台</a></div>

  <div class="tabs" id="modeTabs">
    <button class="tab on" data-page="page-write">写新稿</button>
    <button class="tab" data-page="page-revise">改稿体检</button>
  </div>

  <div class="notice">
    <b>资料不足会直接停工。</b>本页不会在没有事实资料的情况下先写一版占位稿——这是刻意设计的。
  </div>

  <div class="card">
    <h2>模型设置 <button class="btn ghost sm" id="tog" style="float:right">显示 / 隐藏</button></h2>
    <div id="setbox">
      <div class="row">
        <div><label>接口地址（OpenAI 兼容）</label><input id="f-base" placeholder="https://api.deepseek.com/v1"></div>
        <div><label>模型名</label><input id="f-model" placeholder="deepseek-chat"></div>
      </div>
      <label>API Key（只保存在你的浏览器本地，不上传任何服务器）</label>
      <input id="f-key" type="password" placeholder="sk-...">
      <div class="row">
        <div><label>温度（写作用）</label><input id="f-temp" type="number" step="0.1" min="0" max="1.5" value="0.7"></div>
        <div style="flex:none">
          <label>跨域被拦时</label>
          <label style="color:var(--ink);font-size:13px"><input type="checkbox" id="f-proxy" style="width:auto"> 走本地代理端口</label>
        </div>
      </div>
      <label>本地服务地址（留空则自动探测；本机服务为 127.0.0.1:8787）</label>
      <input id="f-local" placeholder="http://127.0.0.1:8787">
      <div class="tools">
        <button class="btn" id="save">保存设置</button>
        <button class="btn ghost" id="test">测试模型连接</button>
        <button class="btn ghost" id="svcCheck">检测本地服务</button>
        <span id="flash"></span>
        <span id="svcMsg" style="font-size:12.5px"></span>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-top:10px">
        兼容 DeepSeek / 通义 / 智谱 / Kimi / OpenAI 等任意 OpenAI 格式接口。若浏览器报跨域错误，
        勾选「走本地代理端口」并运行 <code>python tools/writer_server.py</code>（详见页面底部说明）。
      </div>
    </div>
  </div>

<div id="page-write">
  <div class="card">
    <h2>写作任务</h2>
    <label>选题（一个主题，不要一串）</label>
    <input id="topic" placeholder="例：新国标下怎么挑儿童学习桌椅">
    <label>本次事实资料（标准原文 / 官方发布 / 权威报道 / 检测数据 —— 必须粘贴，缺了就停工）</label>
    <textarea id="material" placeholder="把标准原文、官方通报、权威报道原文粘进来。资料越实，成稿越稳。&#10;也可以点下面的按钮上传 docx / pdf / txt，或直接把文件拖到这里。"></textarea>
    <div class="tools">
      <input type="file" id="fileInput" multiple accept=".docx,.doc,.dotx,.pdf,.txt,.md,.csv" style="display:none">
      <button class="btn ghost sm" id="pickFile">上传资料文件（docx / pdf / txt）</button>
      <span id="fileMsg" style="font-size:12.5px;color:var(--muted)"></span>
    </div>
    <div style="font-size:12px;color:var(--muted);margin-top:8px">
      文件解析由本地服务完成（浏览器无法直接读盘）。若提示连不上，先运行
      <code>python tools/writer_server.py</code>。扫描件 PDF 无文本层时解析会失败，请改为粘贴正文。
    </div>
    <div class="row">
      <div><label>目标字数（可留空，默认 1300–1550）</label><input id="words" placeholder="1300–1550"></div>
    </div>
    <div class="tools">
      <button class="btn" id="go">开始生成</button>
      <span id="log" style="font-size:12.5px;color:var(--muted)"></span>
    </div>
  </div>

  <div class="card">
    <h2>执行过程（七步，每步一次独立请求）</h2>
    <div style="font-size:12.5px;color:var(--muted);margin-bottom:10px">
      写作方只拿到规则卡与事实底稿；三审方只拿到成稿 —— 上下文隔离，不是自己查自己。
    </div>
    <div id="steps"></div>
  </div>

  <div class="card" id="final" style="display:none">
    <h2>最终成稿</h2>
    <div style="font-size:12.5px;color:var(--muted);margin-bottom:8px" id="finalMeta"></div>
    <textarea id="finalText" readonly></textarea>

    <div class="card" style="background:#FBFBF9;margin:14px 0 0">
      <h2 style="margin-bottom:6px">按 Word 排版输出</h2>
      <div style="font-size:12.5px;color:var(--muted);margin-bottom:10px">
        成稿里带 Markdown 符号（<code>##</code>、<code>**</code>），直接粘进 Word 会显示成符号。
        下面三个出口会自动清理并套上排版。
      </div>
      <div class="tools">
        <button class="btn" id="copyRich">复制「带格式」→ 粘进 Word</button>
        <button class="btn ghost" id="copyPlain2">复制纯文本</button>
        <button class="btn ghost" id="dlDoc">下载 Word 文档（.doc）</button>
        <label style="color:var(--ink);font-size:13px;display:flex;align-items:center;gap:6px">
          <input type="checkbox" id="indent" style="width:auto"> 首行缩进 2 字符
        </label>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-top:8px">
        第 1 行作居中标题，短行作小标题（加粗、段前后留白），其余作正文。
      </div>
    </div>

    <details style="margin-top:14px" open>
      <summary>排版预览（粘进 Word 大致就是这个样子）</summary>
      <div id="wordPreview" style="margin-top:10px;background:#fff;border:1px solid var(--line);
        border-radius:8px;padding:18px 22px"></div>
    </details>
  </div>

  <div class="card" id="ingCard">
    <h2>语料入库 · 让智能体学习新文章</h2>
    <div class="notice">
      把新的公众号文章链接粘进来，一键抓取并存入历史语料库，然后自动重算风格特征。
      <b>此功能需要本地服务在运行</b>：<code>python tools/writer_server.py</code>。
      浏览器不能写磁盘、也不能跨域抓微信链接，所以必须由本机服务代劳。
    </div>
    <label>微信公众号链接（一行一个，可批量）</label>
    <textarea id="links" style="min-height:110px" placeholder="https://mp.weixin.qq.com/s/xxxxxxxx"></textarea>
    <div class="row">
      <div><label>文体码（不确定就留自动判断）</label>
        <select id="ingGenre" style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font-size:14px;font-family:inherit;background:#FBFBF9;color:var(--ink)">
          <option value="">自动判断</option>
          <option>STD</option><option>AVOID</option><option>HEALTH</option><option>SAFETY</option><option>MISC</option>
        </select>
      </div>
    </div>
    <div class="tools">
      <button class="btn" id="ingGo">抓取并入库</button>
      <button class="btn ghost" id="rebuild">重建页面</button>
      <span id="ingMsg" style="font-size:12.5px;color:var(--muted)"></span>
    </div>
    <div id="ingOut"></div>
    <div id="restatOut"></div>
    <p class="small muted" style="margin-top:10px">
      入库只进 KB1（仅用于风格）；新文章里的标准与数据<b>永远不作为</b>写作事实来源。
    </p>
  </div>

  <div class="card">
    <h2>为什么需要你自己的 API</h2>
    <div style="font-size:13px;color:var(--muted)">
      静态网页本身不能生成文字，必须调用一个模型接口。为了不把你的 Key 放在任何服务器上，
      本页直接把请求发给你填的接口地址；如果你的接口没开跨域，就跑一下本地小服务
      <code>python tools/writer_server.py</code>（在本机 8787 端口起一个转接），
      然后在上面勾选「走本地代理端口」。
    </div>
  </div>
</div>

<div id="page-revise" style="display:none">
  <div class="card">
    <h2>改稿体检</h2>
    <div style="font-size:12.5px;color:var(--muted);margin-bottom:10px">
      把待改的文章粘进来 → 先<b>体检</b>（本地逐条对照 KB2 规则表与文体手册，不经过模型，结果客观可复算）
      → 再决定要不要让它<b>按手册改稿</b>。改稿只动写法与结构，<b>标准号、限值、日期、结论一律不动</b>。
    </div>
    <label>待改文章正文</label>
    <textarea id="revText" style="min-height:260px" placeholder="粘贴要改的稿件。也可以上传 docx / pdf / txt，或把文件拖到这里。"></textarea>
    <div class="tools">
      <input type="file" id="revFileInput" multiple accept=".docx,.doc,.dotx,.pdf,.txt,.md,.csv" style="display:none">
      <button class="btn ghost sm" id="revPickFile">上传稿件（docx / pdf / txt）</button>
      <button class="btn" id="revCheck">① 体检</button>
      <span id="revMsg" style="font-size:12.5px;color:var(--muted)"></span>
    </div>
    <div class="row" style="margin-top:10px">
      <div><label>文体（默认自动判定，可手动改）</label>
        <select id="revGenre">
          <option value="">自动判定</option>
          <option value="STD">STD · 标准与热点解读</option>
          <option value="HEALTH">HEALTH · 健康与营养科普</option>
          <option value="SAFETY">SAFETY · 产品安全与使用提醒</option>
          <option value="AVOID">AVOID · 消费避坑指南</option>
          <option value="MISC">MISC · 其他</option>
        </select>
      </div>
      <div><label>改稿力度</label>
        <select id="revLevel">
          <option value="light">轻度：只改违规项，保留大部分原句</option>
          <option value="normal" selected>常规：按手册重整段落与语气</option>
          <option value="deep">重度：按手册重写结构与表达（事实不动）</option>
        </select>
      </div>
    </div>
  </div>

  <div class="card" id="revReport" style="display:none">
    <h2>体检报告</h2>
    <div id="revScore"></div>
    <div style="font-size:12.5px;color:var(--muted);margin:10px 0 6px">
      逐条对照 KB2 规则（阈值与频率来自规则表实测）与文体手册要求：
    </div>
    <table id="revTable"><thead><tr>
      <th style="width:70px">判定</th><th style="width:190px">规则</th>
      <th style="width:150px">本文实测</th><th>门槛 / 依据</th></tr></thead><tbody></tbody></table>
    <div id="revMetrics" style="margin-top:12px"></div>
    <div class="tools" style="margin-top:12px">
      <button class="btn" id="revGo">② 按手册改稿</button>
      <button class="btn ghost" id="revDup">查重（与历史语料比对）</button>
      <span id="revGoMsg" style="font-size:12.5px;color:var(--muted)"></span>
    </div>
  </div>

  <div class="card" id="revOut" style="display:none">
    <h2>修订稿</h2>
    <div style="font-size:12.5px;color:var(--muted);margin-bottom:8px" id="revOutMeta"></div>
    <textarea id="revResult" style="min-height:320px"></textarea>
    <div class="tools">
      <button class="btn" id="revRecheck">③ 对修订稿再体检一次</button>
      <button class="btn ghost" id="revCopyRich">复制带格式</button>
      <button class="btn ghost" id="revDownload">下载 Word 文档</button>
      <span id="revOutMsg" style="font-size:12.5px;color:var(--muted)"></span>
    </div>
    <div id="revCompare" style="margin-top:12px"></div>
    <details style="margin-top:12px">
      <summary style="cursor:pointer;font-size:13px">看改动说明（可选，需再发一次请求）</summary>
      <div class="tools" style="margin-top:8px">
        <button class="btn ghost sm" id="revDiff">生成改动清单</button>
        <span id="revDiffMsg" style="font-size:12.5px;color:var(--muted)"></span>
      </div>
      <div id="revDiffOut" style="margin-top:8px"></div>
    </details>
  </div>
</div>
</div>
<script id="DATA" type="application/json">__DATA__</script>
<script>
__JS__
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    prompts = B.load_prompts(args.project)
    corpus = B.load_corpus(args.project)
    st = B.stats(corpus)
    targets = kb2_targets(args.project)
    data = {
        "updated": datetime.date.today().isoformat(),
        "counted": st["counted"], "files": st["total"],
        "p50": st["p50"], "words": "1000–1400",
        "targets": targets, "genre_manual": genre_manual(args.project),
        "variants_text": variants_text(),
        "rules_text": rules_text(),
        "ban_text": "\n".join("· " + plain(b) for b in B.BANLIST),
        "prompt": prompts,
    }
    out_html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__JS__", JS)
    # 写作台的"装成应用"入口指向写作台本身。
    # 只替换第一处 —— 页面里的"导出 Word"模板字符串中也有 </head>，
    # 全量替换会把 PWA 标签注入到那段字符串里。
    out_html = out_html.replace("</head>", pwa_assets.head_tags("writer.html") + "</head>", 1)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "writer.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out_html)
    print(f"[已生成] {path}  ({os.path.getsize(path)/1024:.0f} KB)")
    made = pwa_assets.write(args.out, page="writer.html")
    print(f"  PWA 资源：{' · '.join(made)}")
    print(f"  规则文本 {len(rules_text())} 字 · 变体文本 {len(variants_text())} 字 · 七步流水线")
    return 0


if __name__ == "__main__":
    sys.exit(main())
