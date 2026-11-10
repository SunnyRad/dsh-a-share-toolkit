# -*- coding: utf-8 -*-
"""A 股个股财务体检 · 报告渲染

输出单文件自包含 HTML：内联 CSS + 内联 SVG，零外链、零图片文件。
（平台禁用 .png 等扩展名，所以图只能内联 SVG。）

设计原则：
- **如实披露数据来源**：数据由调用方提供，报告必须原样印出来源说明，
  不能让人误以为本技能自带数据。这是合规要求，也是可追溯要求。
- 不做「推荐买卖」表述，只呈现事实与同业位置
- 无数据的地方显式写「无数据」，不留空白不留假值
"""
from __future__ import annotations

import html
import math
from typing import Any, Dict, List, Optional

import peers as peers_mod


def _n(v, nd: int = 2, suffix: str = "") -> str:
    if v is None:
        return '<span class="na">无数据</span>'
    try:
        return f"{float(v):,.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return '<span class="na">无数据</span>'


def _yi(v) -> str:
    """元 → 亿元"""
    if v is None:
        return '<span class="na">无数据</span>'
    try:
        return f"{float(v)/1e8:,.2f} 亿"
    except (TypeError, ValueError):
        return '<span class="na">无数据</span>'


def _pct(v, nd: int = 2) -> str:
    return _n(v, nd, "%")


def _band_strip(b: Optional[dict], own: Optional[float], width: int = 150) -> str:
    """行业分位带：p10-p90 灰带 + p25-p75 深带 + 中位线 + 本公司标记。

    只给一个中位数，读者看不出自己在分布的哪一侧、离中位多远、
    以及这个分布本身宽不宽（宽 = 行业差异大，窄 = 指标高度同质）。
    """
    if not b or own is None:
        return '<span class="na">—</span>'
    lo, hi = b.get("p10"), b.get("p90")
    if lo is None or hi is None:
        return '<span class="na">—</span>'
    # 用 p10..p90 当可视范围，中位线和本公司点都按此映射
    span = hi - lo
    if span <= 0:
        span = max(abs(hi), 1e-9) * 0.1

    def x_of(v):
        r = (float(v) - lo) / span
        return max(0.0, min(1.0, r)) * width

    x_lo, x_hi = 0.0, float(width)
    x_p25 = x_of(b["p25"]) if b.get("p25") is not None else x_lo
    x_p75 = x_of(b["p75"]) if b.get("p75") is not None else x_hi
    x_med = x_of(b["p50"]) if b.get("p50") is not None else (x_lo + x_hi) / 2
    x_own = x_of(own)
    # 落在 p10-p90 之外时，把点贴边并换色，提示「已出头部区间」
    outside = not (lo <= float(own) <= hi)
    dot = "#dc2626" if outside else "#2563eb"
    return (
        f'<svg class="band" width="{width}" height="18" viewBox="0 0 {width} 18">'
        f'<rect x="0" y="6" width="{width}" height="6" rx="3" fill="#f1f5f9"/>'
        f'<rect x="{x_lo:.1f}" y="6" width="{max(x_hi-x_lo,0):.1f}" height="6" rx="3" fill="#e2e8f0"/>'
        f'<rect x="{x_p25:.1f}" y="6" width="{max(x_p75-x_p25,0):.1f}" height="6" rx="3" fill="#cbd5e1"/>'
        f'<line x1="{x_med:.1f}" y1="3" x2="{x_med:.1f}" y2="15" stroke="#94a3b8" stroke-width="1.5"/>'
        f'<circle cx="{x_own:.1f}" cy="9" r="4" fill="{dot}"/>'
        f'</svg>'
    )


def _gauge(percentile: Optional[float], size: int = 120) -> str:
    """半圆仪表盘：percentile 0-100。"""
    if percentile is None:
        return ('<svg width="%d" height="%d" viewBox="0 0 %d %d">'
                '<text x="%d" y="%d" text-anchor="middle" font-size="13" fill="#9ca3af">无数据</text>'
                '</svg>') % (size, size // 2 + 10, size, size // 2 + 10, size // 2, size // 2)
    p = max(0.0, min(100.0, float(percentile)))
    r = size / 2 - 10
    cx, cy = size / 2, size / 2
    # 从 180° 到 0°（半圆）
    ang = math.pi * (1 - p / 100.0)
    x = cx + r * math.cos(ang)
    y = cy - r * math.sin(ang)
    if p >= 70:
        color = "#059669"
    elif p >= 40:
        color = "#d97706"
    else:
        color = "#dc2626"
    return (
        f'<svg width="{size}" height="{int(cy)+16}" viewBox="0 0 {size} {int(cy)+16}">'
        f'<path d="M {cx-r} {cy} A {r} {r} 0 0 1 {cx+r} {cy}" fill="none" '
        f'stroke="#e5e7eb" stroke-width="10" stroke-linecap="round"/>'
        f'<path d="M {cx-r} {cy} A {r} {r} 0 0 1 {x:.1f} {y:.1f}" fill="none" '
        f'stroke="{color}" stroke-width="10" stroke-linecap="round"/>'
        f'<text x="{cx}" y="{cy-6}" text-anchor="middle" font-size="22" font-weight="700" '
        f'fill="{color}">{p:.0f}</text>'
        f'<text x="{cx}" y="{cy+10}" text-anchor="middle" font-size="11" fill="#6b7280">同业分位</text>'
        f'</svg>'
    )


def _quality_verdict_fallback(surplus) -> str:
    """盈利质量结论——旧 JSON 无 verdict 字段时的兜底，口径与 analysis 一致。

    ⚠️ 绝不返回「通过 / 异常」：那两个词在报告里表示报表勾稽是否成立。
    盈利质量差 ≠ 报表做错了，用错词会让用户以为账目有问题。
    """
    if surplus is None:
        return "不可比"
    if surplus >= 0:
        return "现金富余"
    if surplus >= -30:
        return "现金小幅缺口"
    return "现金显著缺口"


def _flag_badge(level: str) -> str:
    cls = {"高危": "badge-high", "关注": "badge-watch", "正常": "badge-ok"}.get(level, "badge-ok")
    return f'<span class="badge {cls}">{html.escape(level)}</span>'


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
     background:#f8fafc;color:#0f172a;line-height:1.6;font-size:14px}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
header{border-bottom:3px solid #0f172a;padding-bottom:18px;margin-bottom:28px}
h1{margin:0 0 6px;font-size:26px;letter-spacing:-.02em}
.sub{color:#64748b;font-size:13px}
.tag{display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:4px;padding:1px 7px;font-size:12px;margin-left:8px}
.src{color:#334155;font-weight:600}
.tag-src{display:inline-block;background:#f1f5f9;color:#475569;border-radius:4px;padding:0 6px;font-size:11px;margin-left:6px;border:1px solid #e2e8f0}
section{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:20px 22px;margin-bottom:18px}
h2{margin:0 0 16px;font-size:16px;font-weight:600;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:3px;height:16px;background:#2563eb;border-radius:2px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px}
.card .k{color:#64748b;font-size:12px;margin-bottom:4px}
.card .v{font-size:19px;font-weight:650;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #f1f5f9}
th{color:#64748b;font-weight:500;font-size:12px;text-transform:none}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:1px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-high{background:#fee2e2;color:#991b1b}
.badge-watch{background:#fef3c7;color:#92400e}
.badge-ok{background:#d1fae5;color:#065f46}
.na{color:#cbd5e1;font-style:italic;font-size:12px}
.bar{vertical-align:middle}
.dupont{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;text-align:center}
.dupont .box{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px}
.dupont .box .lbl{font-size:12px;color:#64748b}
.dupont .box .val{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.dupont .op{font-size:20px;color:#94a3b8;display:flex;align-items:center;justify-content:center}
.flex2{display:grid;grid-template-columns:200px 1fr;gap:20px;align-items:center}
footer{color:#94a3b8;font-size:12px;text-align:center;margin-top:32px;line-height:1.8;
       border-top:1px solid #e2e8f0;padding-top:18px}
.disclaimer{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px 16px;
            color:#78350f;font-size:13px;margin-bottom:18px}
@media(max-width:720px){.flex2,.dupont{grid-template-columns:1fr}}
"""


def render(result: Dict[str, Any], comparison: Dict[str, Any],
           peer_name: Optional[str] = None) -> str:
    r = result
    cmp = comparison or {}
    metrics = cmp.get("metrics", {})
    peer_count = cmp.get("peer_count", 0)

    pcts = [m["percentile"] for m in metrics.values() if m.get("percentile") is not None]
    peer_score = sum(pcts) / len(pcts) if pcts else None

    # 指标键 → yoy_notes 的键（用于解释「本公司值为何算不出来」）
    _YOY_KEY_BY_METRIC = {
        "revenue_yoy": "revenue_yoy",
        "profit_yoy": "net_profit_yoy",
    }
    yoy_notes = r.get("yoy_notes") or {}
    # 不适用指标：优先取 compare() 给的 inapplicable；但**旧 JSON 没有这个字段**，
    # 那时必须由渲染器自己按 profile 兜底重算——否则历史数据重新出报告，
    # 银行负债率又会带着红点回到表里（实测 601398 复现过）。
    # 判据来源只有一个：peers.INAPPLICABLE_BY_PROFILE，避免两处口径分叉。
    inapplicable = dict(cmp.get("inapplicable") or {})
    _prof = r.get("profile") or "industrial"
    for _k in (peers_mod.INAPPLICABLE_BY_PROFILE.get(_prof) or set()):
        if _k in metrics:
            inapplicable.setdefault(_k, "")
    # 合并成一句话，不逐条重复「对该类公司不适用」
    inapp_sentence = peers_mod.inapplicable_reason(_prof, inapplicable.keys())

    def metric_row(key: str) -> str:
        m = metrics.get(key)
        if not m:
            return ""
        label = m.get("label", key)
        val = m.get("value")
        med = m.get("peer_median")
        pct = m.get("percentile")
        rank = m.get("rank")
        rank_of = m.get("rank_of")
        rank_s = f"{rank}/{rank_of}" if rank else '<span class="na">—</span>'
        pct_s = f"{pct:.0f}%" if pct is not None else '<span class="na">—</span>'
        strip = _band_strip(m.get("band"), val)
        # 本公司值算不出来时，给出**原因**而不只是「无数据」：
        # 「基期为负不可比」与「缺去年同期」处置方式完全不同。
        if val is None:
            note = yoy_notes.get(_YOY_KEY_BY_METRIC.get(key, ""), "")
            own_cell = (f'<span class="na">{html.escape(note)}</span>' if note
                        else '<span class="na">无数据</span>')
        else:
            own_cell = _n(val, 2)
        return (f"<tr><td>{html.escape(label)}</td>"
                f"<td class='num'>{own_cell}</td>"
                f"<td class='num'>{_n(med, 2)}</td>"
                f"<td class='num'>{pct_s}</td>"
                f"<td class='num'>{rank_s}</td>"
                f"<td>{strip}</td></tr>")

    # ---- 校验项：勾稽 与 盈利质量 必须**分开两张表** ----
    # 早期版本混在一张表、共用「通过/异常」，导致：
    #   勾稽行 0.00% 通过（报表是平的），紧跟着盈利质量行判「异常」，
    #   读者以为报表对不上。两类检查问的是不同问题，结论词也必须不同。
    all_checks = r.get("checks", [])
    recon_checks = [c for c in all_checks
                    if c.get("kind") == "reconcile" or "资产 =" in c.get("name", "")]
    quality_checks = [c for c in all_checks if c.get("kind") == "quality"]
    # 兼容旧 JSON（无 kind 字段）：按名称兜底归入质量类
    quality_checks += [c for c in all_checks
                       if not c.get("kind") and "经营现金流 vs" in c.get("name", "")]

    def _check_row(c: dict) -> str:
        is_quality = (c.get("kind") == "quality"
                      or "经营现金流 vs" in c.get("name", ""))
        if is_quality:
            # 盈利质量绝不使用「通过/异常」——那两个词表示勾稽是否成立。
            # 旧 JSON 没有 verdict 字段时必须按方向重算，不能回退到 ok/异常：
            # 否则「富余率 +26%」仍会被渲染成红色「异常」，与同一节的
            # 「利润有现金支撑」自相矛盾（实测 601012 复现过）。
            surplus = c.get("diff_pct")
            verdict = c.get("verdict") or _quality_verdict_fallback(surplus)
            cls = ("badge-ok" if (surplus or 0) >= 0
                   else "badge-high" if (surplus or 0) < -30 else "badge-watch")
        else:
            verdict = c.get("verdict") or ("通过" if c.get("ok") else "报表不平")
            cls = "badge-ok" if c.get("ok") else "badge-high"
        return (f"<tr><td>{html.escape(c['name'])}</td>"
                f"<td class='num'>{_n(c['left'], 2)}</td>"
                f"<td class='num'>{_n(c['right'], 2)}</td>"
                f"<td class='num'>{_n(c['diff_pct'], 2, '%')}</td>"
                f"<td><span class='badge {cls}'>{html.escape(verdict)}</span></td></tr>")

    recon_rows = "".join(_check_row(c) for c in recon_checks) or \
        "<tr><td colspan='5' class='na'>无可执行的勾稽校验</td></tr>"
    quality_rows = "".join(_check_row(c) for c in quality_checks)

    flag_rows = "".join(
        f"<tr><td>{_flag_badge(f['level'])}</td>"
        f"<td>{html.escape(f['name'])}</td>"
        f"<td>{html.escape(f['detail'])}</td></tr>"
        for f in sorted(r.get("flags", []),
                        key=lambda x: {"高危": 0, "关注": 1, "正常": 2}.get(x["level"], 3))
    ) or "<tr><td colspan='3' class='na'>未触发任何红旗规则</td></tr>"

    missing = r.get("missing") or []
    missing_html = ""
    if missing:
        missing_html = ("<p class='sub' style='margin-top:12px'>⚠️ 以下指标因数据缺失或行业不适用而未能计算："
                        + "、".join(html.escape(m) for m in missing) + "</p>")

    s, dp, pf, so, cf = (r["scale"], r["dupont"], r["profitability"],
                          r["solvency"], r["cashflow"])

    prof_note = ("本报告识别为<b>金融业</b>，毛利率与资产负债率不适用，"
                 "已改用同业净利率/ROE 相对位置评估。") if r.get("profile") == "financial" else ""
    # 注意：prof_note 内含 <b> 标签，是受控的本地字符串，不能 escape
    # （第一版整段 escape 了，页面上直接显示出 "<b>金融业</b>" 字面文字）

    gauge = _gauge(peer_score)
    # 三种情形必须分开说，不能把「没给样本」写成「样本量不够」：
    #   0 家   = 调用方没提供同业数据（事实缺失，不是统计问题）
    #   1-2 家 = 给了但太少，不构成横截面
    #   ≥3 家  = 正常出分位
    if peer_count <= 0:
        peer_note = ('未提供同业样本，本报告<b>不做</b>横截面排名。'
                     '同业数据需由调用方通过 <code>--peers</code> 传入。')
    elif peer_count < 3:
        peer_note = f'同业样本仅 <b>{peer_count}</b> 家（需 ≥3 家），不足以下横截面结论，故不给同业分。'
    else:
        peer_note = f'与调用方提供的 <b>{peer_count}</b> 家同业样本做横截面比较。'

    # 没有逐指标结果（0 家样本，或 1-2 家导致 compare() 提前返回）时，
    # 一律不出表——只有表头的空表会被读成"每个指标都缺失/都是空值"。
    if not metrics:
        if peer_count <= 0:
            reason = ('未提供同业样本，本节无内容。'
                      '在调用方传入 ≥3 家同业数据（<code>--peers peers.json</code>）后'
                      '重新生成即可看到逐指标分位与排名。')
            peer_hint_body = ('没有样本就没有横截面位置——'
                              '这里的空白是<b>缺数据</b>，不是<b>表现差</b>。')
        else:
            reason = (f'同业样本仅 <b>{peer_count}</b> 家，不足 3 家，'
                      '不构成横截面统计意义，故不出逐指标分位表。')
            peer_hint_body = ('样本太少时算出来的分位不稳定，'
                              '宁可不出，也不要给一个看起来精确的假结论。')
        peer_table = (f'<section>\n<h2>同业对比</h2>\n'
                      f'<p class="sub" style="margin:0">{reason}</p>\n</section>')
        peer_hint = f'<p class="sub" style="margin:0">{prof_note}{peer_hint_body}</p>'
    else:
        # 不适用指标不得进表——页面已经在 prof_note 里宣称它不适用，
        # 再拿来排名并标红就是自相矛盾（工行负债率 92% 被标红点）。
        row_keys = [k for k in ('gross_margin', 'net_margin', 'roe', 'revenue_yoy',
                                'profit_yoy', 'debt_ratio', 'deduct_ratio')
                    if k not in inapplicable]
        inapp_note = ""
        if inapplicable:
            inapp_note = ('<p class="sub" style="margin:10px 0 0">'
                          + html.escape(inapp_sentence) + '</p>')
        # 整列分布条都画不出来时，必须说明「不是数据坏了，是这份结果没带分位带」。
        # 表头写着「行业分布（p10—p90…）」、下方还教怎么读，却只给一排「—」，
        # 读者会以为同业数据缺失或工具坏了。历史 JSON 由旧版 peers.py 产出时
        # 就是这个样子（2026-11-10 视觉核查发现）。
        band_none = ""
        if row_keys and all(
                not (cmp.get("metrics", {}).get(k) or {}).get("band")
                for k in row_keys):
            band_none = (
                '<p class="sub" style="margin:10px 0 0;color:#92400e">'
                '⚠️ 本报告的行业分布条为空：这份结果数据未包含同业分位带'
                '（p10/p25/p75/p90）。分位与排名仍然有效，但无法展示离散程度。'
                '重新运行一次体检即可生成完整分布条。</p>')
        peer_table = (
            f'<section>\n<h2>同业对比（{peer_count} 家）</h2>\n'
            '<table>\n'
            '<thead><tr><th>指标</th><th class="num">本公司</th><th class="num">同业中位</th>\n'
            '  <th class="num">分位</th><th class="num">排名</th>'
            '<th>行业分布（p10—p90，深带 p25—p75，竖线中位，圆点本公司）</th></tr></thead>\n'
            "<tbody>"
            + ''.join(metric_row(k) for k in row_keys)
            + '</tbody>\n</table>\n' + band_none + inapp_note + '</section>'
        )
        peer_hint = (f'<p class="sub" style="margin:0">{prof_note}'
                     '分位越高表示在<b>同业样本</b>中越靠前。跨行业不可比——'
                     '不同行业的负债率、毛利率基准差异极大。'
                     '<br>分布条说明的是<b>离散程度</b>：深带窄表示行业普遍如此，'
                     '本公司偏离中位就更有信息量；深带宽表示行业本身就参差不齐，'
                     '偏离中位不一定是个体特征。</p>')

    # 数据来源必须显著披露：本技能不提供数据，只做计算。
    src = html.escape(r.get("source") or "未声明")
    basis = html.escape(r.get("comparison_basis") or "未知")
    basis_warn = ""
    if basis.startswith("缺失"):
        basis_warn = ("<p class='sub' style='margin:8px 0 0;color:#92400e'>"
                      "⚠️ 缺少去年同期数据，本期无法计算同比。报告中的增速栏目为空，"
                      "不当成 0 处理。</p>")
    elif basis == "环比":
        basis_warn = ("<p class='sub' style='margin:8px 0 0;color:#92400e'>"
                      "⚠️ 仅有上一期数据，增速口径为<b>环比</b>（季度累计对季度累计），"
                      "不可与常见的同比数据直接比较。</p>")

    # 盈利质量独立成节。它回答「利润里有多少是真现金」，
    # 与「报表平不平」是两个问题，不能共用勾稽表的结论词。
    if quality_checks:
        q = quality_checks[0]
        surplus = q.get("diff_pct")
        if surplus is None:
            q_explain = "净利润为 0 或数据缺失，无法判断现金含量。"
        elif surplus >= 0:
            q_explain = (f"经营现金流比净利润多 <b>{surplus:.1f}%</b>，"
                         f"利润有现金支撑。")
        else:
            q_explain = (f"经营现金流比净利润少 <b>{abs(surplus):.1f}%</b>，"
                         f"部分利润未转化为现金，需关注应计质量。")
        quality_section = f"""<section>
  <h2>盈利质量</h2>
  <table>
    <thead><tr><th>指标</th><th class="num">经营现金流</th><th class="num">归母净利润</th>
      <th class="num">现金流富余率</th><th>结论</th></tr></thead>
    <tbody>{quality_rows}</tbody>
  </table>
  <p class="sub" style="margin-top:10px">
    {q_explain}
    本节衡量的是<b>盈利含金量</b>，不是报表勾稽——上面的勾稽表通过，只说明账目能对上。
    公司即使亏损，只要现金流消耗小于账面亏损，这里的富余率仍为正。
  </p>
</section>"""
    else:
        quality_section = ""

    # 生成时间：空值时不能留「生成于 」半句话——读者会以为渲染坏了。
    _fa = (r.get("fetched_at") or "").strip()
    fetched_s = (f"生成于 {html.escape(_fa)}" if _fa else "生成时间未记录")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(r['name'])} {html.escape(r['code'])} 财务体检</title>
<style>{CSS}</style></head><body><div class="wrap">

<header>
  <h1>{html.escape(r['name'])} <span style="color:#94a3b8;font-weight:400">{html.escape(r['code'])}</span>
    <span class="tag">{html.escape(r['industry'] or '未分类')}</span></h1>
  <div class="sub">报告期 {html.escape(r['report_date'])} · 增速口径 {basis} · {fetched_s}</div>
  <div class="sub" style="margin-top:6px">
    <b>数据来源：</b><span class="src">{src}</span>
    <span class="tag-src">由调用方提供</span>
  </div>
  {basis_warn}
</header>

<div class="disclaimer">
  <b>本报告为财务数据分析工具输出，不构成任何投资建议</b>，不包含也不应被理解为买卖推荐、目标价或收益预测。
  本技能<b>不提供、不抓取、不转发</b>任何行情或财务数据；全部数据由调用方提供并对其准确性负责，
  所有指标基于该数据计算，请以交易所披露原文为准。
</div>

<section>
  <h2>同业位置</h2>
  <div class="flex2">
    <div style="text-align:center">{gauge}</div>
    <div>
      <p style="margin:0 0 10px">{peer_note}</p>
      {peer_hint}
    </div>
  </div>
</section>

<section>
  <h2>规模与成长</h2>
  <div class="cards">
    <div class="card"><div class="k">营业总收入</div><div class="v">{_yi(s['revenue'])}</div></div>
    <div class="card"><div class="k">归母净利润</div><div class="v">{_yi(s['net_profit'])}</div></div>
    <div class="card"><div class="k">扣非净利润</div><div class="v">{_yi(s['deduct_np'])}</div></div>
    <div class="card"><div class="k">总资产</div><div class="v">{_yi(s['total_assets'])}</div></div>
    <div class="card"><div class="k">营收同比</div><div class="v">{_pct(s['revenue_yoy'])}</div></div>
    <div class="card"><div class="k">净利同比</div><div class="v">{_pct(s['net_profit_yoy'])}</div></div>
  </div>
</section>

<section>
  <h2>杜邦拆解</h2>
  <div class="dupont">
    <div class="box"><div class="lbl">净利率</div><div class="val">{_pct(dp['net_margin'])}</div></div>
    <div class="box"><div class="lbl">总资产周转率</div><div class="val">{_n(dp['asset_turnover'],3)}</div></div>
    <div class="box"><div class="lbl">权益乘数</div><div class="val">{_n(dp['equity_multiplier'],2)}</div></div>
  </div>
  <p class="sub" style="margin-top:14px">
    ROE（直接计算）<b>{_pct(dp['roe_direct'])}</b> ·
    ROE（杜邦还原）<b>{_pct(dp['roe_dupont'])}</b> ·
    两者应当接近；显著不一致说明报表存在口径差异。
  </p>
</section>

{peer_table}

<section>
  <h2>三表勾稽校验</h2>
  <table>
    <thead><tr><th>校验项</th><th class="num">左值</th><th class="num">右值</th>
      <th class="num">差异率</th><th>结论</th></tr></thead>
    <tbody>{recon_rows}</tbody>
  </table>
  <p class="sub" style="margin-top:10px">
    「资产 = 负债 + 所有者权益」是<b>真勾稽</b>：差异率应接近 0，否则报表不会平。
    本表只回答「报表平不平」，不涉及公司经营好坏。
  </p>
</section>

{quality_section}

<section>
  <h2>财务红旗</h2>
  <table>
    <thead><tr><th>级别</th><th>信号</th><th>说明</th></tr></thead>
    <tbody>{flag_rows}</tbody>
  </table>
  {missing_html}
</section>

<footer>
  {html.escape(r['code'])} · {html.escape(r['name'])} · 报告期 {html.escape(r['report_date'])}<br>
  <b>数据来源：</b>{src}（由调用方提供，本技能不提供数据）<br>
  所有指标由调用方提供的数据计算，可能存在口径差异，请核对原始披露文件。<br>
  <b>本报告不构成投资建议。</b>
</footer>
</div></body></html>"""
