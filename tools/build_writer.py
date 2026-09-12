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


def plain(s):
    return re.sub(r"</?em>", "", s)


def rules_text():
    lines = ["【R 级 · 默认执行】"]
    for r in B.RULES_R:
        lines.append(f"{r[0]} {r[1]}｜{plain(r[2])}｜频率 {r[3]}｜适用 {r[5]}")
    lines.append("")
    lines.append("【T 级 · 倾向，允许偏离】")
    for r in B.RULES_T:
        lines.append(f"{r[0]} {r[1]}｜{plain(r[2])}｜频率 {r[3]}")
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
    $('#finalText').textContent = final;
    const chars = final.replace(/\s/g,'').length;
    $('#finalMeta').textContent = `约 ${chars} 字　·　变体 ${v.variant}　·　${needFix?'经一轮修订':'三审直通'}`;
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

function copyFinal(){
  const t = $('#finalText').textContent;
  const done = ()=>flash('成稿已复制到剪贴板');
  if(navigator.clipboard) navigator.clipboard.writeText(t).then(done).catch(()=>fb(t,done));
  else fb(t,done);
}
function fb(t,done){
  const ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta);
  ta.select(); try{document.execCommand('copy'); done();}catch(e){alert('请手动选中复制');}
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
  catch(e){ throw new Error('连不上本地服务（'+apiBase()+'）。请先运行：python tools/writer_server.py'); }
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
  try{ const r = await svc('/api/restat', {});
    svcMsg('#svcMsg', `本地服务正常 · 当前语料 ${r.counted} 篇`);
    renderRestat(r, '#restatOut');
  }catch(e){ svcMsg('#svcMsg', e.message, true); }
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

loadCfg();
renderSteps();
$('#go').addEventListener('click', run);
$('#save').addEventListener('click', saveCfg);
$('#test').addEventListener('click', testConn);
$('#copy').addEventListener('click', copyFinal);
$('#tog').addEventListener('click', toggleSet);
$('#setbox').style.display='none';
$('#pickFile').addEventListener('click', ()=>$('#fileInput').click());
$('#fileInput').addEventListener('change', e=>handleFiles(e.target.files));
$('#ingGo').addEventListener('click', ingest);
$('#svcCheck').addEventListener('click', checkSvc);
$('#rebuild').addEventListener('click', rebuild);
$('#material').addEventListener('dragover', e=>{ e.preventDefault(); e.currentTarget.style.borderColor='#0F6E56'; });
$('#material').addEventListener('dragleave', e=>{ e.currentTarget.style.borderColor=''; });
$('#material').addEventListener('drop', e=>{ e.preventDefault(); e.currentTarget.style.borderColor=''; handleFiles(e.dataTransfer.files); });
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
  <h1>科普写作台</h1>
  <div class="sub">按你 23 篇语料提炼的风格规则生成公众号科普推文　·　<a href="index.html">← 返回知识库工作台</a></div>

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
    <div class="tools"><button class="btn" id="copy">复制成稿</button></div>
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
    data = {
        "updated": datetime.date.today().isoformat(),
        "variants_text": variants_text(),
        "rules_text": rules_text(),
        "ban_text": "\n".join("· " + plain(b) for b in B.BANLIST),
        "prompt": prompts,
    }
    out_html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__JS__", JS)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "writer.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out_html)
    print(f"[已生成] {path}  ({os.path.getsize(path)/1024:.0f} KB)")
    print(f"  规则文本 {len(rules_text())} 字 · 变体文本 {len(variants_text())} 字 · 七步流水线")
    return 0


if __name__ == "__main__":
    sys.exit(main())
