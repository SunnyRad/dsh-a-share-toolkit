# -*- coding: utf-8 -*-
"""A 股估值分位体检 · 报告渲染

输出单文件自包含 HTML：内联 CSS + 内联 SVG，零外链、零图片文件。
（平台禁用 .png 等扩展名，所以图只能内联 SVG。）

产品边界（重要）：
- **不输出股价、不出 K 线、不做分时或走势行情图**。
  本产品是分析工具，不是行情工具。报告只呈现估值倍数与历史分位位置。
- **如实披露数据来源**：数据由调用方提供，报告必须原样印出来源说明，
  否则读者会误以为这是技能自带的行情数据。
- 不做「推荐买卖」表述，只陈述「处于历史什么位置」这一客观事实。
- 无数据处显式写「无数据 / 不适用」，不留空白、不填 0。
"""
from __future__ import annotations

import html
import math
from typing import Any, Dict, List, Optional

# 平台禁用图片扩展名，图形一律内联 SVG。这里定义配色，避免散落。
C_HIGH = "#dc2626"      # 分位高（历史偏贵）
C_MID = "#d97706"
C_LOW = "#059669"       # 分位低（历史偏便宜）
C_MUTE = "#94a3b8"


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _n(v, nd: int = 2, suffix: str = "") -> str:
    if v is None:
        return '<span class="na">无数据</span>'
    try:
        return f"{float(v):,.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return '<span class="na">无数据</span>'


def _pct(v, nd: int = 1) -> str:
    if v is None:
        return '<span class="na">不适用</span>'
    return _n(v, nd, "%")


def _pct_color(p) -> str:
    if p is None:
        return C_MUTE
    if p >= 70:
        return C_HIGH
    if p >= 30:
        return C_MID
    return C_LOW


def _gauge(percentile: Optional[float], label: str, size: int = 132) -> str:
    """半圆仪表：分位 0-100。分位低 = 历史便宜（绿），高 = 历史贵（红）。"""
    h = size // 2 + 22
    if percentile is None:
        return (f'<svg width="{size}" height="{h}" viewBox="0 0 {size} {h}">'
                f'<text x="{size//2}" y="{size//2}" text-anchor="middle" '
                f'font-size="12" fill="{C_MUTE}">不适用</text>'
                f'<text x="{size//2}" y="{size//2+16}" text-anchor="middle" '
                f'font-size="11" fill="{C_MUTE}">{_esc(label)}</text></svg>')
    p = max(0.0, min(100.0, float(percentile)))
    r = size / 2 - 12
    cx, cy = size / 2, size / 2
    ang = math.pi * (1 - p / 100.0)
    x = cx + r * math.cos(ang)
    y = cy - r * math.sin(ang)
    color = _pct_color(p)
    return (
        f'<svg width="{size}" height="{h}" viewBox="0 0 {size} {h}">'
        f'<path d="M {cx-r:.1f} {cy:.1f} A {r:.1f} {r:.1f} 0 0 1 {cx+r:.1f} {cy:.1f}" '
        f'fill="none" stroke="#e5e7eb" stroke-width="11" stroke-linecap="round"/>'
        f'<path d="M {cx-r:.1f} {cy:.1f} A {r:.1f} {r:.1f} 0 0 1 {x:.1f} {y:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="11" stroke-linecap="round"/>'
        f'<text x="{cx:.1f}" y="{cy-4:.1f}" text-anchor="middle" font-size="23" '
        f'font-weight="700" fill="{color}">{p:.0f}%</text>'
        f'<text x="{cx:.1f}" y="{cy+13:.1f}" text-anchor="middle" font-size="11" '
        f'fill="#6b7280">{_esc(label)}</text></svg>'
    )


def _strip(percentile: Optional[float], width: int = 260) -> str:
    """分位条：0-100 的横向刻度，标出当前位置。"""
    if percentile is None:
        return '<svg width="0" height="0"></svg>'
    p = max(0.0, min(100.0, float(percentile)))
    x = p / 100.0 * width
    color = _pct_color(p)
    return (f'<svg width="{width}" height="18" viewBox="0 0 {width} 18">'
            f'<rect x="0" y="6" width="{width}" height="6" rx="3" fill="#e5e7eb"/>'
            f'<rect x="0" y="6" width="{x:.1f}" height="6" rx="3" fill="{color}"/>'
            f'<circle cx="{x:.1f}" cy="9" r="5" fill="{color}" stroke="#fff" stroke-width="2"/>'
            f'</svg>')


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
     background:#f8fafc;color:#0f172a;line-height:1.6;font-size:14px}
.wrap{max-width:1040px;margin:0 auto;padding:32px 20px 64px}
header{border-bottom:3px solid #0f172a;padding-bottom:18px;margin-bottom:26px}
h1{margin:0 0 6px;font-size:25px;letter-spacing:-.02em}
.sub{color:#64748b;font-size:13px}
.tag{display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:4px;padding:1px 7px;font-size:12px;margin-left:8px}
section{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:20px 22px;margin-bottom:18px}
h2{margin:0 0 16px;font-size:16px;font-weight:600;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:3px;height:16px;background:#2563eb;border-radius:2px}
.gauges{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;text-align:center}
.gbox{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 6px}
.gbox .cap{font-size:12px;color:#64748b;margin-top:2px}
.gbox .cur{font-size:13px;color:#334155;margin-top:4px;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #f1f5f9}
th{color:#64748b;font-weight:500;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
/* 口径说明表：术语列固定窄宽，避免只有首行写了 width 时其余行被拉宽 */
table.glossary th{width:118px;white-space:nowrap;color:#334155;font-weight:600;vertical-align:top}
table.glossary td{color:#475569}
.na{color:#cbd5e1;font-style:italic;font-size:12px}
.disclaimer{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px 16px;
            color:#78350f;font-size:13px;margin-bottom:18px}
.warn{background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:14px 16px;
      color:#7f1d1d;font-size:13px;margin-bottom:18px}
.warn ul{margin:8px 0 0;padding-left:20px}
.warn li{margin:3px 0}
.note{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:14px 16px;
      color:#0c4a6e;font-size:13px;margin-bottom:18px}
.badge{display:inline-block;padding:1px 8px;border-radius:4px;font-size:11px;font-weight:600}
.b-ok{background:#d1fae5;color:#065f46}
.b-watch{background:#fef3c7;color:#92400e}
.b-bad{background:#fee2e2;color:#991b1b}
.b-na{background:#f1f5f9;color:#64748b}
footer{color:#94a3b8;font-size:12px;text-align:center;margin-top:30px;line-height:1.8;
       border-top:1px solid #e2e8f0;padding-top:18px}
@media(max-width:720px){.gauges{grid-template-columns:1fr}}
"""

# 分位 -> 文字档位。刻意用中性描述，不做任何买卖暗示。
def _bucket(p: Optional[float]) -> tuple:
    if p is None:
        return ("不适用", "b-na")
    if p >= 80:
        return ("历史高位", "b-bad")
    if p >= 60:
        return ("历史偏高", "b-watch")
    if p >= 40:
        return ("历史中位", "b-na")
    if p >= 20:
        return ("历史偏低", "b-ok")
    return ("历史低位", "b-ok")


# 注：这里曾有一个 _status_note()，把指标失效原因挂在「指标明细」表底部。
# 2026-11-10 视觉核查发现它造成两个问题：
#   1. 同一句「PS(TTM)：该指标当期无数据」在页面上出现两遍
#      （「适用性限制」框里本来就有一条）；
#   2. 最初版本还丢了指标名，只剩「该指标当期无数据」，读者无从判断指哪一行。
# 表底那处是纯重复，已整体删除；失效原因只在「适用性限制」框里说明一次。
# 删除后它变成无调用者的死代码，一度留下但变异 M40 存活证明了「测死代码 = 假绿」，
# 故连同对应测试一并删除。


def render(result: Dict[str, Any], source: str = "") -> str:
    r = result
    m = r.get("metrics", {})
    pe, pb, ps = m.get("pe_ttm", {}), m.get("pb_mrq", {}), m.get("ps_ttm", {})
    # 注：这里曾有 `div = r.get("divergence") or {}`，只在背离框里用。
    # 背离框并入 recommendation 后它变成无调用者的死代码，
    # 按 M40 的教训（测死代码=假绿）一并删除，不留下。
    ind = r.get("industry_comparison") or {}
    warnings = r.get("warnings") or []

    # ---------- 三个仪表 ----------
    gauge_html = []
    for key, mm in (("pe_ttm", pe), ("pb_mrq", pb), ("ps_ttm", ps)):
        label = mm.get("label", key)
        gauge_html.append(
            '<div class="gbox">'
            + _gauge(mm.get("percentile"), label)
            + f'<div class="cap">当前 {_n(mm.get("current"), 2)}</div>'
            + f'<div class="cur">中位 {_n(mm.get("median"), 2)}</div>'
            + '</div>'
        )

    # ---------- 指标明细表 ----------
    detail_rows = []
    for key in ("pe_ttm", "pb_mrq", "ps_ttm"):
        mm = m.get(key, {})
        p = mm.get("percentile")
        bucket, bcls = _bucket(p)
        detail_rows.append(
            f"<tr><td>{_esc(mm.get('label', key))}</td>"
            f"<td class='num'>{_n(mm.get('current'), 2)}</td>"
            f"<td class='num'>{_n(mm.get('median'), 2)}</td>"
            f"<td class='num'>{_n(mm.get('min'), 2)}</td>"
            f"<td class='num'>{_n(mm.get('max'), 2)}</td>"
            f"<td class='num'>{_pct(p)}</td>"
            f"<td><span class='badge {bcls}'>{_esc(bucket)}</span></td>"
            f"<td>{_strip(p)}</td></tr>"
        )
        # 注：这里曾收集 excluded_rows 用于表下说明，但那段说明最终
        # 改成直接读 analysis 的 exclusion_note（单一来源），
        # 它就成了无调用者的死代码，按 M40 的教训（测死代码=假绿）删除。

    # ---------- 背离提示 ----------
    # 2026-11-10 视觉核查：这里原本单独渲染一个整宽「估值信号背离」框，
    # 而紧邻的「该看哪个指标」框讲的是**同一个判断**（PE/PB 背离→周期特征→用 PB）。
    # 两个框并排出现，读者要读两遍才发现不是两件事。
    # 背离的具体数字已并入 recommendation.reason，这里不再单独渲染。
    # 判据：tests/TestReportRender.test_divergence_and_recommendation_not_redundant
    # 按「周期」在页面上的**出现次数**钉住（不是有没有出现）。

    # ---------- 指标选择建议 ----------
    rec = r.get("recommendation") or {}
    rec_map = {"pe_ttm": "PE(TTM)", "pb_mrq": "PB(MRQ)", "ps_ttm": "PS(TTM)", None: "无"}
    if rec.get("primary"):
        rec_html = (f'<div class="note"><b>该看哪个指标：以 '
                    f'{_esc(rec_map.get(rec.get("primary"), rec.get("primary")))} 为主</b>'
                    + (f'，{_esc(rec_map.get(rec.get("secondary"), rec.get("secondary")))} 作交叉验证'
                       if rec.get("secondary") else "")
                    + f'<br>{_esc(rec.get("reason",""))}</div>')
    else:
        rec_html = (f'<div class="warn"><b>不对估值位置下结论</b><br>'
                    f'{_esc(rec.get("reason",""))}</div>')

    # ---------- 警示 ----------
    warn_html = ""
    if warnings:
        warn_html = ('<div class="warn"><b>本报告的适用性限制</b><ul>'
                     + "".join(f"<li>{_esc(w)}</li>" for w in warnings)
                     + "</ul></div>")

    # ---------- 亏损剔除披露 ----------
    # 「算得对」里最容易被藏起来的一条：分位是在**剔除亏损日之后**
    # 的样本上算的。不把剔除量讲出来，读者就无法判断这个分位可不可信。
    #
    # ⚠️ 文案只从 analysis 的 exclusion_note 取，**不在渲染层自己拼**：
    # 2026-11-10 第一版在这里另写了一段「样本是怎么变少的」，
    # 而 analysis 又把归因并进了 warnings，结果同一件事在页面上讲了两遍。
    # 现在责任划分清楚：analysis 决定说什么，report 决定放哪。
    excluded_note = ""
    notes = []
    for key in ("pe_ttm", "pb_mrq", "ps_ttm"):
        note = (m.get(key) or {}).get("exclusion_note")
        if note and note not in notes:
            notes.append(note)
    if notes:
        excluded_note = (
            "<div class='sub' style='margin-top:10px;color:#92400e'>"
            "<b>样本是怎么变少的</b>：" + " ".join(_esc(x) for x in notes)
            + " 亏损期的负倍数若混进基准，会把「亏损」算成「极度便宜」。"
            "</div>")

    # ---------- 行业横截面 ----------
    ind_rows = []
    shrunk = []          # 有效样本少于同业公司数的指标，用于下方解释
    im = ind.get("metrics") or {}
    peer_total_n = ind.get("peer_count", 0)
    for key in ("pe_ttm", "pb_mrq"):
        mm = im.get(key)
        if not mm:
            continue
        p = mm.get("percentile")
        # 该指标有效家数少于同业公司数时，在指标名后标出真实家数。
        # 否则标题「同业 4 家」与 PE 行排名分母 4、PB 行分母 5 并列，
        # 读者读不出为什么两行分母不一样。
        #
        # ⚠️ 判据用 peer_n < peer_count 现场算，而不是只信 sample_shrunk：
        # 本字段是本轮新加的，而 reports/ 里的历史 JSON 由旧代码产出，
        # 没有这个键。只信新字段会让「用当前渲染器重出历史报告」时
        # 缺陷原样回来（AGENTS.md 第七条之二）。
        peer_n_eff = mm.get("peer_n")
        is_shrunk = mm.get("sample_shrunk")
        if is_shrunk is None:
            # 旧 JSON 没有该字段：用已有数据现场判断，保证「重出历史报告」同口径
            is_shrunk = bool(peer_total_n) and peer_n_eff is not None \
                and peer_n_eff < peer_total_n
        label = _esc(mm.get('label', key))
        if is_shrunk:
            label += (f"<span class='sub'>（有效 {peer_n_eff} 家）</span>")
            shrunk.append((mm.get("label", key), peer_n_eff,
                           mm.get("peer_total", peer_total_n),
                           mm.get("negative_peers", 0)))
        ind_rows.append(
            f"<tr><td>{label}</td>"
            f"<td class='num'>{_n(mm.get('value'), 2)}</td>"
            f"<td class='num'>{_n(mm.get('median'), 2)}</td>"
            f"<td class='num'>{_pct(p)}</td>"
            f"<td class='num'>{mm.get('rank','—')}/{mm.get('rank_of','—')}</td>"
            f"<td class='num'>{mm.get('negative_peers', 0)} 家</td></tr>"
        )
    if ind_rows:
        peer_n = ind.get("peer_count", 0)
        # 同业样本小时，分位只有「家数+1」档精度。
        # 3 家同业 = 0/33/67/100 四档，页面却把 `100.0%` 印成精确读数，
        # 读者会以为样本远比 3 家大（2026-11-10 勘查发现）。
        # 兜底：旧 JSON 没有 peer_count_small 时，用 peer_count 现场判断，
        # 保证「用当前渲染器重出历史报告」同口径（AGENTS.md 第七条之二）。
        is_small = ind.get("peer_count_small")
        if is_small is None:
            is_small = bool(peer_n) and peer_n < 10
        small_note = ""
        if is_small:
            small_note = (
                f"<br><b>注意样本量</b>：同业只有 {peer_n} 家，"
                f"分位仅能分辨 {peer_n + 1} 档，精度有限，"
                f"不宜当作精确读数——把它理解为「大致排在行业里的什么位置」即可。")
        self_note = (
            "<br>快照里含本公司自己，已从同业样本中<b>剔除本公司</b>，"
            "故上表「分位」「中位」的分母是同业家数，「排名」的分母是同业家数 +1（本公司）。"
            if ind.get("self_excluded") else "")
        shrunk_note = ""
        if shrunk:
            parts = []
            for lbl, n_eff, n_total, n_neg in shrunk:
                if n_neg and n_neg == n_total - n_eff:
                    parts.append(
                        f"{_esc(lbl)} 有 {n_neg} 家亏损（该指标为负），已剔除，"
                        f"故其分母为 {n_eff} 家而非 {n_total} 家")
                else:
                    parts.append(
                        f"{_esc(lbl)} 仅 {n_eff} 家有效（共 {n_total} 家），"
                        f"缺数据的同业不参与分位")
            shrunk_note = (
                "<br><b>各行分母为何不同</b>："
                + "；".join(parts)
                + "。亏损或缺失的同业不参与该指标的分位计算，否则会把亏损误算成「极度便宜」。")
        industry_html = (
            f"<section><h2>同行业横截面（同业 {peer_n} 家 · 同一交易日）</h2>"
            "<table><thead><tr><th>指标</th><th class='num'>本公司</th><th class='num'>行业中位</th>"
            "<th class='num'>分位</th><th class='num'>排名</th><th class='num'>同业中该指标为负</th>"
            "</tr></thead><tbody>" + "".join(ind_rows) + "</tbody></table>"
            "<p class='sub' style='margin-top:10px'>"
            "横截面分位 = 在同行业当日有估值数据的公司里，有多少家比本公司更便宜。"
            "「同业中该指标为负」一列说明这个行业里有多少家公司处于亏损（PE 为负）"
            "——这类公司不参与分位计算，否则会把亏损误算成「极度便宜」。"
            + self_note + small_note + shrunk_note + "</p></section>"
        )

    else:
        note = ind.get("note") or "未获取同业数据（使用本地 CSV 时不含行业横截面）"
        industry_html = (f"<section><h2>同行业横截面</h2>"
                         f"<p class='sub'>{_esc(note)}</p></section>")

    # ---------- 数据来源说明（必须显著披露，不允许含糊）----------
    src_line = _esc(source or "未声明")
    if r.get("offline"):
        src_line += "（本报告生成过程未访问任何网络服务）"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(r.get('name'))} {_esc(r.get('code'))} 估值分位</title>
<style>{CSS}</style></head><body><div class="wrap">

<header>
  <h1>{_esc(r.get('name') or '本地数据')} <span style="color:#94a3b8;font-weight:400">{_esc(r.get('code'))}</span>
    <span class="tag">{_esc(r.get('industry') or '未分类')}</span></h1>
  <div class="sub">数据日期 {_esc(r.get('trade_date'))} · 历史区间 {_esc(r.get('history_first'))} 至 {_esc(r.get('history_last'))}
    （{r.get('history_count', 0)} 个交易日）</div>
</header>

<div class="disclaimer">
  <b>本报告为估值数据分析工具输出，不构成任何投资建议</b>，不包含也不应被理解为买卖推荐、目标价或收益预测。
  历史分位只描述「当前估值倍数在过去若干年中的相对位置」，<b>不预示未来涨跌</b>。
  低分位不等于会涨，高分位不等于会跌。
</div>

<div class="note">
  本技能<b>不提供、不抓取、不转发</b>任何行情或估值数据；全部数据由调用方提供并对其准确性负责，
  本报告只对已提供的数据做计算。<br>
  <b>数据来源：</b>{src_line}
</div>

{warn_html}
{rec_html}

<section>
  <h2>历史估值分位</h2>
  <div class="gauges">{''.join(gauge_html)}</div>
  <p class="sub" style="margin-top:14px">
    分位 = 当前估值倍数在自身历史中高于多少比例的交易日。
    分位越低表示当前倍数相对自身历史越便宜。<b>这是纵向比较，与行业之间不可比。</b>
  </p>
</section>

<section>
  <h2>指标明细</h2>
  <table>
    <thead><tr><th>指标</th><th class="num">当前</th><th class="num">历史中位</th>
      <th class="num">历史最低</th><th class="num">历史最高</th>
      <th class="num">分位</th><th>档位</th><th>刻度</th></tr></thead>
    <tbody>{''.join(detail_rows)}</tbody>
  </table>
  <div class="sub" style="margin-top:10px">
    「历史最低/最高」只统计<b>有效值</b>：PE 为负（亏损）的交易日不计入，
    否则亏损期的负 PE 会被当成「比任何正值都便宜」，直接颠覆分位结论。
  </div>
  {excluded_note}
</section>

{industry_html}

<section>
  <h2>口径说明</h2>
  <table class="glossary">
    <tr><th>PE(TTM)</th><td>滚动市盈率，用最近四个季度归母净利润。公司亏损时该值为负，此时不适用。</td></tr>
    <tr><th>PB(MRQ)</th><td>市净率，用最近一期归母净资产。<b>亏损公司仍可用 PB 判断估值位置</b>，这是 PE 失效时的主要替代。</td></tr>
    <tr><th>PS(TTM)</th><td>滚动市销率。对尚未盈利但有收入的公司比 PE 有意义。</td></tr>
    <tr><th>分位计算</th><td>只取大于 0 的历史值作为基准。负值代表指标失效，不是「更便宜」。</td></tr>
    <tr><th>样本门槛</th><td>有效样本少于 250 个交易日（约 1 年）不出分位；少于 750 个（约 3 年）标注「仅供参考」。</td></tr>
  </table>
</section>

<footer>
  {_esc(r.get('code'))} · {_esc(r.get('name') or '本地数据')} · 数据日期 {_esc(r.get('trade_date'))}<br>
  数据来源：{_esc(src_line)}（由调用方提供，本技能不提供数据）。<br>
  <b>本报告不构成投资建议。</b>
</footer>
</div></body></html>"""
