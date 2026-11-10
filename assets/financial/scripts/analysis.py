# -*- coding: utf-8 -*-
"""A 股个股财务体检 · 分析层

输入：ingest.load_*() 产出的规范化字典（字段名与任何具体数据源无关）。
输出：结构化指标 + 红旗清单。

所有计算只依赖规范字段名，因此换数据源不影响本层，也不需要改。

重要口径说明：
- 定期报告是**年初至今累计（YTD）**，所以趋势比较一律用**同比**（同期对同期），
  不能拿 Q3 累计和 Q2 累计直接比。
- 任何字段缺失返回 None，并在 missing 列表里记录，**绝不填 0 冒充数据**。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(row: Dict[str, Any], key: str) -> Optional[float]:
    """安全取数：缺失/非数值返回 None。"""
    if not row:
        return None
    v = row.get(key)
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """(a/b)*100，b 为 0 或缺失返回 None。"""
    d = _div(a, b)
    return None if d is None else d * 100.0


def _yoy(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    """同比增速（%）。基期为负或零时无意义，返回 None。"""
    if cur is None or prev is None or prev <= 0:
        return None
    return (cur - prev) / prev * 100.0


def _yoy_note(cur: Optional[float], prev: Optional[float]) -> Optional[str]:
    """同比算不出来时，说明**为什么**算不出来。

    「无数据」把两件不同的事混成了一句：
      - 基期为负 → 数学上不可比（亏损基数上的增速没有意义），无解；
      - 基期缺失 → 调用方没给去年同期，补数据即可。
    两者的处置完全相反，必须分开说。
    """
    if cur is None:
        return "本期数据缺失"
    if prev is None:
        return "去年同期数据缺失，无法计算同比"
    if prev == 0:
        return "去年同期为 0，同比无意义"
    if prev < 0:
        return "基期为负（去年同期亏损），同比不可比"
    return None


def _quality_verdict(surplus: Optional[float], net_profit: Optional[float]) -> str:
    """盈利质量结论——按现金流富余/缺口方向分档。

    ⚠️ 绝不使用「异常 / 通过」：那两个词在这张表里表示报表勾稽是否成立。
    盈利质量差 ≠ 报表做错了。用错词会让用户以为账目有问题。

    分档依据是「经营现金流相对净利润是富余还是缺口」，
    而不是「公司是否盈利」——后者由财务红旗单独报警，
    在这里重复一遍会让判据失去区分能力。
    """
    if surplus is None:
        return "不可比"
    if surplus >= 0:
        return "现金富余"
    if surplus >= -30:
        return "现金小幅缺口"
    return "现金显著缺口"


# 金融业（银行/保险/证券）的报表结构与工商企业根本不同：
# 没有"营业成本/毛利率"概念，总资产周转率天然极低（0.01 量级是正常的）。
# 用工商企业的指标去套金融股，会得到一片 None 和看似异常的低周转率。
FINANCIAL_INDUSTRY_HINTS = ("银行", "保险", "证券", "多元金融", "信托", "期货")


def detect_profile(data: Dict[str, Any]) -> str:
    """返回 'financial' 或 'industrial'。"""
    industry = str(data.get("industry") or "")
    for hint in FINANCIAL_INDUSTRY_HINTS:
        if hint in industry:
            return "financial"
    return "industrial"


def _same_period(rows: List[Dict[str, Any]], idx: int) -> Optional[Dict[str, Any]]:
    """找 idx 这一期的**去年同期**（同月份），没有则返回 None。"""
    if idx >= len(rows):
        return None
    base = str(rows[idx].get("report_date") or "")
    if len(base) < 7:
        return None
    target_year = str(int(base[:4]) - 1)
    want = target_year + base[4:7]          # 只比到年月
    for r in rows:
        rd = str(r.get("report_date") or "")
        if rd[:7] == want:
            return r
    return None


def _period_label(cur: str, prev: str) -> str:
    """识别是「同比」还是「环比」——两者含义不同，报告里必须如实标注。

    有去年同期 → 同比（这是正确口径）；
    没有去年同期但有上一期 → 只能算环比，且必须在报告里说明不可直接比较。
    """
    if not cur or not prev:
        return "未知"
    return "同比" if cur[:4] != prev[:4] else "环比"


def compute(data: Dict[str, Any]) -> Dict[str, Any]:
    bal_rows = data.get("balance") or []
    inc_rows = data.get("income") or []
    cf_rows = data.get("cashflow") or []

    profile = detect_profile(data)
    is_financial = profile == "financial"

    bal = bal_rows[0] if bal_rows else {}
    inc = inc_rows[0] if inc_rows else {}
    cf = cf_rows[0] if cf_rows else {}

    bal_prev = _same_period(bal_rows, 0)
    inc_prev = _same_period(inc_rows, 0)
    cf_prev = _same_period(cf_rows, 0)

    missing: List[str] = []

    def need(name: str, *vals) -> None:
        if any(v is None for v in vals):
            missing.append(name)

    # ---------- 规模 ----------
    revenue = _f(inc, "revenue")
    net_profit = _f(inc, "net_profit")
    deduct_np = _f(inc, "deduct_net_profit")
    total_assets = _f(bal, "total_assets")
    total_liab = _f(bal, "total_liab")
    total_equity = _f(bal, "total_equity")
    need("营业收入", revenue)
    need("归母净利润", net_profit)
    need("总资产", total_assets)
    need("总负债", total_liab)
    need("股东权益", total_equity)

    # ---------- 校验项 ----------
    # ⚠️ 这里有两类**性质不同**的检查，早期版本把它们混在同一张表里，
    #    并共用「通过 / 异常」两个结论词，导致页面自相矛盾：
    #
    #    kind="reconcile"（勾稽）：两张报表能否对上。答的是「报表平不平」。
    #    kind="quality"  （质量）：利润里有多少是真现金。答的是「赚的是不是真钱」。
    #
    #    实测隆基绿能（2026-09-30）：勾稽行 0.00% 通过（报表是平的），
    #    紧跟着「经营现金流 vs 归母净利润」+26.43% 被判红「异常」。
    #    读者会以为报表对不上 —— 实际那是「亏损但现金消耗可控」。
    #    更根本的问题：原判据 ok = cfo>0 and net_profit>0 只复述了「是否亏损」，
    #    对富余率是正是负毫无区分能力（+26% 和 -91% 给出同一个结论）。
    #    所以两类检查必须分开存放、各自用各自的结论词。
    checks = []
    if None not in (total_assets, total_liab, total_equity):
        diff = total_assets - (total_liab + total_equity)
        rel = _pct(diff, total_assets)
        balanced = rel is not None and abs(rel) < 0.5
        checks.append({
            "kind": "reconcile",
            "name": "资产 = 负债 + 所有者权益",
            "left": total_assets, "right": total_liab + total_equity,
            "diff": diff, "diff_pct": rel,
            "metric_label": "差异率",
            "ok": balanced,
            "verdict": "通过" if balanced else "报表不平",
        })
    else:
        missing.append("勾稽校验(资产=负债+权益)")

    cfo = _f(cf, "cfo")
    if None not in (net_profit, cfo):
        # 这不是"差异率"，而是 CFO 相对净利润的富余/缺口比例。
        # 写成"差异"会让人误以为报表对不上 —— 它是盈利质量的度量。
        surplus = _pct(cfo - net_profit, abs(net_profit)) if net_profit else None
        checks.append({
            "kind": "quality",
            "name": "经营现金流 vs 归母净利润",
            "left": cfo, "right": net_profit,
            "diff": cfo - net_profit, "diff_pct": surplus,
            "metric_label": "现金流富余率",
            # ok 只表示「利润有现金支撑」，与报表是否平衡无关。
            "ok": surplus is not None and surplus >= 0,
            # 结论词必须按**方向**分档，不能只反映是否亏损。
            "verdict": _quality_verdict(surplus, net_profit),
        })
    else:
        missing.append("盈利质量(CFO vs 净利润)")

    # ---------- 杜邦拆解 ----------
    # ROE = 净利率 × 总资产周转率 × 权益乘数
    net_margin = _pct(net_profit, revenue)
    asset_turnover = _div(revenue, total_assets)
    equity_multiplier = _div(total_assets, total_equity)
    roe_dupont = None
    if None not in (net_margin, asset_turnover, equity_multiplier):
        roe_dupont = (net_margin / 100.0) * asset_turnover * equity_multiplier * 100.0
    else:
        missing.append("杜邦拆解")
    roe_direct = _pct(net_profit, total_equity)
    if roe_direct is None:
        missing.append("ROE")

    # ---------- 盈利能力 ----------
    operate_cost = _f(inc, "operate_cost")
    gross_margin = None
    if not is_financial and revenue and operate_cost is not None:
        gross_margin = _pct(revenue - operate_cost, revenue)
    if gross_margin is None and not is_financial:
        missing.append("毛利率")
    elif is_financial:
        missing.append("毛利率(金融业不适用)")

    sale_exp = _f(inc, "sale_expense")
    manage_exp = _f(inc, "manage_expense")
    fin_exp = _f(inc, "finance_expense")
    expense_parts = [sale_exp, manage_exp, fin_exp]
    period_expense_ratio = None
    if revenue and all(v is not None for v in expense_parts):
        period_expense_ratio = _pct(sum(expense_parts), revenue)
    else:
        missing.append("期间费用率")

    deduct_ratio = _pct(deduct_np, net_profit) if None not in (deduct_np, net_profit) else None
    if deduct_ratio is None:
        missing.append("扣非/归母")

    # ---------- 偿债与资产质量 ----------
    debt_ratio = _pct(total_liab, total_assets)
    current_ratio = _f(bal, "current_ratio")
    monetary = _f(bal, "monetary")
    accounts_rece = _f(bal, "accounts_rece")
    inventory = _f(bal, "inventory")

    # ---------- 同比 ----------
    rev_yoy = _yoy(revenue, _f(inc_prev, "revenue")) if inc_prev else None
    np_yoy = _yoy(net_profit, _f(inc_prev, "net_profit")) if inc_prev else None
    cfo_yoy = _yoy(cfo, _f(cf_prev, "cfo")) if cf_prev else None
    rece_prev = _f(bal_prev, "accounts_rece") if bal_prev else None
    rece_yoy = _yoy(accounts_rece, rece_prev)
    inv_prev = _f(bal_prev, "inventory") if bal_prev else None
    inv_yoy = _yoy(inventory, inv_prev)
    assets_yoy = _yoy(total_assets, _f(bal_prev, "total_assets")) if bal_prev else None

    # 同比算不出来时记录**原因**，不能只留一句「无数据」。
    # 基期为负（去年同期亏损）与基期缺失是两件事，处置方式相反。
    yoy_notes: Dict[str, str] = {}
    _yoy_pairs = (
        ("revenue_yoy", revenue, _f(inc_prev, "revenue") if inc_prev else None),
        ("net_profit_yoy", net_profit, _f(inc_prev, "net_profit") if inc_prev else None),
        ("cfo_yoy", cfo, _f(cf_prev, "cfo") if cf_prev else None),
        ("accounts_rece_yoy", accounts_rece, rece_prev),
        ("inventory_yoy", inventory, inv_prev),
        ("total_assets_yoy", total_assets,
         _f(bal_prev, "total_assets") if bal_prev else None),
    )
    for _key, _cur, _prev in _yoy_pairs:
        _note = _yoy_note(_cur, _prev)
        if _note:
            yoy_notes[_key] = _note

    # 比较口径（同比 / 环比 / 未知）——没有去年同期时必须如实标注
    prev_date = str((inc_prev or {}).get("report_date") or "")
    comparison_basis = _period_label(str(inc.get("report_date") or ""), prev_date) \
        if inc_prev else "缺失(无去年同期数据)"
    if not inc_prev:
        missing.append("同比基期")

    # ---------- 财务红旗 ----------
    flags: List[Dict[str, Any]] = []

    def flag(level: str, name: str, detail: str, value: Any = None) -> None:
        flags.append({"level": level, "name": name, "detail": detail, "value": value})

    # 1) 亏损本身必须报警
    #    实测踩到的坑：诺思兰德归母 -4590 万，红旗却是 0 条——
    #    因为下面所有规则都是"比率异常"，而亏损时很多比率为 None 直接被跳过。
    #    结果一份"零红旗"的报告发给用户，等于告诉人家没问题。这是最坏的假阴性。
    if net_profit is not None and net_profit < 0:
        flag("高危", "归母净利润为负", f"本期归母净利润 {net_profit/1e8:,.2f} 亿元", net_profit)
    if deduct_np is not None and deduct_np < 0 and (net_profit is None or net_profit >= 0):
        flag("高危", "扣非净利润为负", f"本期扣非净利润 {deduct_np/1e8:,.2f} 亿元（主营尚未盈利）", deduct_np)
    if cfo is not None and cfo < 0:
        flag("高危", "经营活动现金流为负", f"本期经营现金流 {cfo/1e8:,.2f} 亿元", cfo)

    # 2) 经营现金流与净利润背离
    #    ⚠️ 口径必须与「盈利质量」表一致。
    #    表里富余率 = (CFO - NI) / NI，而这里旧代码写的是 CFO / NI，
    #    于是工行 601398 同一页上出现「富余率 440.77%」与
    #    「经营现金流为净利润的 541%」——同一组数字两个口径，
    #    读者只会以为其中一个是错的（2026-11-10 截图核验发现）。
    #    现在统一用富余率，并同时给出原始比值供对照。
    if cfo is not None and net_profit is not None and net_profit > 0:
        ratio = cfo / net_profit
        surplus = (cfo - net_profit) / net_profit
        if ratio < 0.5:
            flag("高危", "经营现金流与净利润背离",
                 f"经营现金流仅为净利润的 {ratio:.0%}"
                 f"（富余率 {surplus:.0%}），盈利含金量偏低", ratio)
        elif ratio < 0.8:
            flag("关注", "经营现金流弱于净利润",
                 f"经营现金流为净利润的 {ratio:.0%}"
                 f"（富余率 {surplus:.0%}），需关注应计质量", ratio)
        else:
            flag("正常", "盈利含金量",
                 f"富余率 {surplus:.0%}"
                 f"（经营现金流为净利润的 {ratio:.0%}）", ratio)

    # 3) 应收账款增速 > 营收增速
    #    ⚠️ 必须同时看绝对占比：茅台应收同比 +1327% 看似爆炸，
    #    但绝对额仅 3.6 亿（占总资产 0.11%），属于小基数噪声，不是风险。
    #    只按百分比报警会产生大量误报，这比漏报更损害产品可信度。
    rece_share = _pct(accounts_rece, total_assets)
    if rece_yoy is not None and rev_yoy is not None and accounts_rece is not None:
        material = (rece_share is not None and rece_share >= 5.0) or \
                   (revenue is not None and revenue > 0 and accounts_rece / revenue >= 0.05)
        if not material:
            flag("正常", "应收账款规模不显著",
                 f"应收占总资产 {rece_share:.2f}%，同比 {rece_yoy:+.1f}% 属小基数波动"
                 if rece_share is not None else "应收绝对规模很小，增速波动无实质意义",
                 rece_yoy)
        elif rece_yoy > rev_yoy + 20 and rece_yoy > 0:
            flag("高危", "应收账款异常增长",
                 f"应收同比 {rece_yoy:+.1f}%，显著快于营收同比 {rev_yoy:+.1f}%（应收占总资产 {rece_share:.1f}%）",
                 rece_yoy - rev_yoy)
        elif rece_yoy > rev_yoy and rece_yoy > 0:
            flag("关注", "应收增速快于营收",
                 f"应收同比 {rece_yoy:+.1f}% > 营收同比 {rev_yoy:+.1f}%", rece_yoy - rev_yoy)

    # 4) 存货异常增长
    if inv_yoy is not None and rev_yoy is not None and inv_yoy > rev_yoy + 30 and inv_yoy > 0:
        flag("关注", "存货增速显著快于营收",
             f"存货同比 {inv_yoy:+.1f}% vs 营收同比 {rev_yoy:+.1f}%", inv_yoy - rev_yoy)

    # 5) 扣非/归母偏低（依赖非经常性损益）
    if deduct_ratio is not None and deduct_ratio < 80:
        lvl = "高危" if deduct_ratio < 50 else "关注"
        flag(lvl, "非经常性损益占比偏高",
             f"扣非净利润/归母净利润 = {deduct_ratio:.1f}%，低于 80%", deduct_ratio)

    # 6) 存贷双高（有大量货币资金却仍高负债）
    if monetary is not None and total_liab is not None and total_assets:
        m_pct = _pct(monetary, total_assets)
        l_pct = debt_ratio
        if m_pct is not None and l_pct is not None and m_pct > 25 and l_pct > 50:
            flag("高危", "存贷双高",
                 f"货币资金占总资产 {m_pct:.1f}%，同时资产负债率 {l_pct:.1f}%", None)

    # 7) 营收或净利同比下滑
    if rev_yoy is not None and rev_yoy < -10:
        flag("关注", "营业收入同比下滑", f"营收同比 {rev_yoy:+.1f}%", rev_yoy)
    if np_yoy is not None and np_yoy < -30:
        flag("高危", "净利润同比大幅下滑", f"归母净利润同比 {np_yoy:+.1f}%", np_yoy)

    # 8) 毛利率异常（难以验证是否有操纵，只提示极值）
    if gross_margin is not None and gross_margin > 90:
        flag("关注", "毛利率极高", f"毛利率 {gross_margin:.1f}%，需核实收入确认口径", gross_margin)

    # ---------- 评分 ----------
    # ⚠️ 曾被删除的绝对评分（保留说明，防止有人再把它加回来）
    #
    # 早期版本用绝对阈值给 0-100 分：ROE>=20 满分、毛利率>=50 满分、负债率越低越好……
    # 实测出两个致命误判：
    #   1) 比亚迪（汽车，负债率 70% 是行业常态）被打到 19.1 分；
    #   2) 诺思兰德（净利 -4590 万，ROE -16.5%）却拿到 51.65 分——
    #      因为亏损时很多指标变 None 被跳过，剩下的毛利率/扣非占比反而高分。
    #
    # 结论：绝对阈值评分在跨行业、跨盈亏状态下必然失真，会给出反向结论。
    # **最终评分只采用「同行业横截面百分位」（见 peers.peer_score），不提供绝对分。**
    # 这是有意的设计取舍：宁可只给相对位置，也不给会误导的绝对分。

    return {
        "code": data.get("code", ""),
        "name": data.get("name", ""),
        "industry": data.get("industry", ""),
        "industry_code": data.get("industry_code", ""),
        "source": data.get("source", ""),
        # fetched_at 必须透传：早期版本没带出来，导致页头印出「生成于 」后断掉，
        # 读者以为渲染坏了。生成时间是数据可追溯性的一部分，不能丢。
        "fetched_at": data.get("fetched_at", "") or "",
        "profile": profile,
        "report_date": data.get("report_date", ""),
        "comparison_basis": comparison_basis,
        "scale": {
            "revenue": revenue, "net_profit": net_profit, "deduct_np": deduct_np,
            "total_assets": total_assets, "total_liab": total_liab, "total_equity": total_equity,
            "revenue_yoy": rev_yoy, "net_profit_yoy": np_yoy, "assets_yoy": assets_yoy,
        },
        "dupont": {
            "net_margin": net_margin, "asset_turnover": asset_turnover,
            "equity_multiplier": equity_multiplier,
            "roe_dupont": roe_dupont, "roe_direct": roe_direct,
        },
        "profitability": {
            "gross_margin": gross_margin, "period_expense_ratio": period_expense_ratio,
            "deduct_ratio": deduct_ratio,
        },
        "solvency": {
            "debt_ratio": debt_ratio, "current_ratio": current_ratio,
            "monetary": monetary, "accounts_rece": accounts_rece, "inventory": inventory,
            "rece_yoy": rece_yoy, "inv_yoy": inv_yoy,
        },
        "cashflow": {
            "cfo": cfo, "cfo_yoy": cfo_yoy,
            "net_invest": _f(cf, "net_invest"),
            "net_finance": _f(cf, "net_finance"),
            "cfo_to_np": (cfo / net_profit) if (cfo is not None and net_profit) else None,
        },
        "checks": checks,
        "flags": flags,
        "missing": sorted(set(missing)),
        "yoy_notes": yoy_notes,
    }


def own_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    """从分析结果里抽出用于同业对比的 7 项指标。"""
    return {
        "gross_margin": result["profitability"]["gross_margin"],
        "net_margin": result["dupont"]["net_margin"],
        "roe": result["dupont"]["roe_direct"],
        "revenue_yoy": result["scale"]["revenue_yoy"],
        "profit_yoy": result["scale"]["net_profit_yoy"],
        "debt_ratio": result["solvency"]["debt_ratio"],
        "deduct_ratio": result["profitability"]["deduct_ratio"],
    }
