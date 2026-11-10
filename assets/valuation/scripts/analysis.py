# -*- coding: utf-8 -*-
"""A 股估值分位体检 · 分析层

核心职责：把「历史估值序列」变成**可信的分位**。

这一层之所以值得单独存在，是因为分位算法有四个会给出**反向结论**的坑，
每一个都在真实数据上验证过（见 tests/test_valuation.py 的回归用例）：

1. **亏损股 PE 为负**
   隆基绿能 601012 当前 PE_TTM = -11.78。
   朴素分位 = 22.9% → 报告会写「处于历史极低位、极度低估」。
   真相是公司亏损，PE 本身无意义。⇒ 负 PE 一律判为「不适用」，不出分位。

2. **全史亏损**
   诺思兰德 920047 全史 1444 期 PE 全为负。任何分位都是噪声。
   ⇒ 正 PE 样本不足时不给分位，并说明原因。

3. **PE 与 PB 分位严重背离**
   海螺水泥 600585：PE 分位 100.0%（史上最贵），PB 分位 4.7%（净资产端史上最便宜）。
   只报 PE 会把周期股在盈利顶部误判成「极度高估」。
   ⇒ 背离超阈值时必须在报告里显式提示，不能只给单一结论。

4. **新股历史过短**
   301601 只有 438 个交易日（约 1.7 年）。三年的「历史分位」没有统计意义。
   ⇒ 有效样本年数不足时降级为「参考」，并给出实际覆盖区间。

原则：任何指标无法可靠计算时返回 None 并说明原因，
**绝不填 0、绝不静默跳过、绝不用看起来合理的假数糊过去**。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 分位计算所需的最小正样本数（再少就没有统计意义）
MIN_SAMPLES = 250           # 约 1 年交易日
# 建议的可信样本天数（约 3 年），低于此值标记为「样本偏短」
RECOMMENDED_SAMPLES = 750

# PE/PB 分位差超过这个值，判定为「估值信号背离」
DIVERGENCE_PCT = 40.0

# 亏损剔除占比超过这个阈值时，必须在归因里点明「亏损」而不是只说「历史较短」。
# 为什么不是 0：零星亏损造成的样本减少用不着单独解释，加进去只增噪声。
LOSS_NOTE_MIN_SHARE = 0.20

# 同业样本少于这个家数时，横截面分位的精度很低，报告必须提示。
# 依据：N 家同业的分位只有 N+1 档。3 家 = 0/33/67/100 四档，
# 把 `100.0%` 呈现成精确读数会让读者以为样本远比 3 家大。
# 10 家 = 11 档，已经够看，不再提示。
PEER_SMALL_THRESHOLD = 10

METRIC_LABELS = {
    "pe_ttm": "PE(TTM)",
    "pb_mrq": "PB(MRQ)",
    "ps_ttm": "PS(TTM)",
}

# 内部用小写键做逻辑，接口返回大写字段。
# ⚠️ 实测踩过：这里如果不做映射，analyze_metric 会用 "pe_ttm" 去 row.get()，
# 而接口给的是 "PE_TTM"，结果所有指标静默变成「无数据」——
# 报告能正常生成、页面也正常，但整份报告没有一个数字。
# 这类「静默全空」是最难发现的缺陷，所以由回归测试 test_field_mapping 钉住。
FIELD_MAP = {
    "pe_ttm": "PE_TTM",
    "pb_mrq": "PB_MRQ",
    "ps_ttm": "PS_TTM",
}


def _f(v) -> Optional[float]:
    """安全取数：缺失/非数值返回 None。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _positive(values: List[Optional[float]]) -> List[float]:
    """保留严格大于 0 的有效值。

    负值不是「更低」，而是「该指标在此刻失效」（亏损 / 净资产为负）。
    把负值当普通数值参与排序，就是坑 1 的根源。
    """
    out = []
    for v in values:
        x = _f(v)
        if x is not None and x > 0:
            out.append(x)
    return out


def percentile(values: List[float], x: Optional[float]) -> Optional[float]:
    """x 在 values 中的百分位（0-100），values 必须已剔除无效值。

    采用「优于及等于取中位」的处理，避免并列值造成边界抖动。
    返回 None 表示样本不足或 x 本身不可比（如 x <= 0）。
    """
    if x is None:
        return None
    x = _f(x)
    if x is None or x <= 0:
        return None          # 非正值不可分位——这是刻意的
    if not values or len(values) < MIN_SAMPLES:
        return None
    better = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return (better + 0.5 * equal) / len(values) * 100.0


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _clean_series(rows: List[Dict[str, Any]], field: str) -> List[float]:
    """从原始行里抽出某字段的全部正值（用于分位基准）。"""
    col = FIELD_MAP.get(field, field)
    return _positive([r.get(col) for r in rows])


def _col(rows: List[Dict[str, Any]], field: str) -> List[Optional[float]]:
    """按字段映射取整列（含无效值），内部逻辑一律用小写键。"""
    col = FIELD_MAP.get(field, field)
    return [_f(r.get(col)) for r in rows]


def analyze_metric(rows: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    """单个估值指标的历史分位分析。"""
    label = METRIC_LABELS.get(field, field)
    raw_all = _col(rows, field)
    valid = _positive(raw_all)
    cur = raw_all[0] if raw_all else None

    total = len(rows)
    # ⚠️ 「负值」与「缺失」必须分开计数（2026-11-10 修）。
    #
    # 早先把两者合并成一个 n_invalid，于是页面只能说「有效样本 401 天，
    # 历史较短」——而真相可能是「1800 天里有 1399 天亏损」。
    # 「上市晚」和「长期亏损」对估值的含义完全相反：
    # 前者是没经历完整周期，后者是盈利基数本身失效。
    # 合并计数就等于把后者悄悄说成前者。
    n_negative = sum(1 for v in raw_all
                     if (_f(v) is not None and _f(v) <= 0))
    n_missing = sum(1 for v in raw_all if _f(v) is None)
    n_invalid = n_negative + n_missing
    invalid_pct = (n_invalid / total * 100.0) if total else None

    out: Dict[str, Any] = {
        "field": field,
        "label": label,
        "current": cur,
        "samples": len(valid),
        "total_rows": total,
        "invalid_count": n_invalid,
        # 分项：亏损期（该指标为负）与数据缺失
        "negative_count": n_negative,
        "missing_count": n_missing,
        "invalid_pct": invalid_pct,
        "median": _median(valid),
        "min": min(valid) if valid else None,
        "max": max(valid) if valid else None,
        "percentile": None,
        "status": "ok",
        "reason": "",
    }

    # 当前值本身不可比：亏损或净资产为负
    if cur is None:
        out["status"] = "no_data"
        out["reason"] = "该指标当期无数据"
        return out
    if cur <= 0:
        out["status"] = "not_applicable"
        if field in ("pe_ttm", "pe_lar"):
            out["reason"] = ("公司当前处于亏损状态（PE 为负），PE 分位没有意义。"
                             "应改用 PB 或 PS 观察估值位置。")
        else:
            out["reason"] = "该公司当期该指标为负（净资产或营收为负），分位不适用。"
        return out

    # 有效样本不足
    if len(valid) < MIN_SAMPLES:
        out["status"] = "insufficient"
        out["reason"] = (f"有效历史样本仅 {len(valid)} 个交易日"
                         f"（少于 {MIN_SAMPLES}），不足以构成历史分位。")
        out["exclusion_note"] = _loss_note(n_negative, total)
        return out

    pct = percentile(valid, cur)
    out["percentile"] = pct
    if len(valid) < RECOMMENDED_SAMPLES:
        out["status"] = "short_history"
        # 措辞不能写「历史较短」——当样本是被亏损剔掉的时候，
        # 「历史较短」会把「长期亏损」误说成「上市晚」，方向完全相反。
        # 这里只说事实（有效样本 N 天、仅供参考），
        # 为什么变少由 exclusion_note 讲，两者分工不重叠。
        if n_negative and total and n_negative / total >= LOSS_NOTE_MIN_SHARE:
            out["reason"] = (f"可参与计算的交易日仅 {len(valid)} 个，"
                             f"分位仅供参考（建议至少 {RECOMMENDED_SAMPLES} 个）。")
        else:
            out["reason"] = (f"有效样本 {len(valid)} 个交易日，历史较短，"
                             f"分位仅供参考（建议至少 {RECOMMENDED_SAMPLES} 个交易日）。")
    out["exclusion_note"] = _loss_note(n_negative, total)
    return out


def _loss_note(n_negative: int, total: int) -> str:
    """样本被「亏损剔除」时的**独立**归因说明，不复述「历史较短」。

    ⚠️ 这是 2026-11-10 抓到的真实误导：某股 1800 个交易日里有 1399 天
    因亏损使 PE 为负而被剔除，分位只在 401 天上算出，页面却只说
    「历史较短」——读者会以为它上市才一年半，真相是它亏了将近四年。

    刻意**不并进 reason**：reason 回答「样本够不够」，
    这里回答「样本为什么变少」。两句话若挤进同一个字段，
    报告端就会在两个位置各渲染一遍（2026-11-10 实测：警示框 + 表格下方
    各出现一次，正是 §7 那类冗余）。分成两个字段，渲染层才能只讲一次。

    只在亏损剔除占比显著（>20%）时才给这句话：
    零星亏损造成的样本减少用不着单独解释，加进去反而变成噪声。
    """
    if n_negative <= 0 or total <= 0:
        return ""
    share = n_negative / total
    if share < LOSS_NOTE_MIN_SHARE:
        return ""
    return (f"历史 {total} 个交易日中有 {n_negative} 天"
            f"（{share*100:.0f}%）因公司亏损、该指标为负而被剔除，"
            f"分位是在剩余交易日上算出的。这不是上市时间短，是盈利基数本身失效。")


def _history_span(rows: List[Dict[str, Any]]) -> Tuple[str, str, int]:
    """返回 (最早日, 最新日, 交易日条数)。"""
    dates = [str(r.get("TRADE_DATE") or "")[:10] for r in rows]
    dates = [d for d in dates if d]
    if not dates:
        return "", "", 0
    return min(dates), max(dates), len(dates)


def detect_divergence(metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """检测 PE 与 PB 分位的严重背离。

    实测：海螺水泥 600585 PE 分位 100.0% / PB 分位 4.7%（差 95.3）。
    这是周期股在盈利顶部的典型形态——利润高导致 PE 看似贵，
    但净资产角度股价其实在历史低位。只报 PE 会给出错误结论。

    圣龙发展 601857 反过来：PE 53.8% / PB 98.6%（差 -44.9）。

    ⚠️ reason 里的文字会**原样渲染进 HTML**，所以不能写 markdown 加粗
    （`**xxx**` 会在页面上显示成两个星号）。强调靠措辞，不靠符号。
    """
    pe = metrics.get("pe_ttm") or {}
    pb = metrics.get("pb_mrq") or {}
    a, b = pe.get("percentile"), pb.get("percentile")
    if a is None or b is None:
        return {"diverged": False, "gap": None,
                "reason": "PE 或 PB 分位不可得，无法比较"}
    gap = a - b
    out = {"pe_pct": a, "pb_pct": b, "gap": gap,
           "diverged": abs(gap) >= DIVERGENCE_PCT}
    if not out["diverged"]:
        out["reason"] = "PE 与 PB 分位基本一致，估值信号无内部矛盾"
        return out
    if gap > 0:
        out["reason"] = (
            f"PE 分位 {a:.1f}% 显著高于 PB 分位 {b:.1f}%（差 {gap:+.1f} 个百分点）。"
            "常见于盈利能力处于周期高位：利润被高估，PE 显得贵，"
            "而净资产端（PB）显示股价仍在历史低位。此时单看 PE 会误判为高估。"
        )
    else:
        out["reason"] = (
            f"PE 分位 {a:.1f}% 显著低于 PB 分位 {b:.1f}%（差 {gap:+.1f} 个百分点）。"
            "常见于盈利能力处于周期低位或一次性损失：利润被压缩，PE 显得便宜，"
            "但净资产端（PB）显示股价并不便宜。此时单看 PE 会误判为低估。"
        )
    return out


def _strip_self(rows: List[Dict[str, Any]], own_code: str) -> Tuple[List[Dict[str, Any]], bool]:
    """从句柄快照里剔除「本公司自己」，返回 (同业行, 是否剔除了)。

    为什么必须做这件事：最自然的输入形态是「某板块某日全部公司估值」，
    那里面**必然包含本公司**。旧实现把所有行都当同业，于是
    peer_n、分位分母、中位数全被本公司污染，而 rank_of 又额外 +1，
    导致页面标题写「4 家」、排名写「1/5」，同一张表两个数对不上。
    """
    code = str(own_code or "").strip().upper()
    if not code:
        return rows, False
    kept = [r for r in rows
            if str(r.get("SECUCODE") or "").strip().upper() != code]
    return kept, len(kept) != len(rows)


def industry_position(rows: List[Dict[str, Any]], own_pe: Optional[float],
                      own_pb: Optional[float],
                      own_code: str = "") -> Dict[str, Any]:
    """个股在同行业当日横截面中的位置。

    own_code 给出「本公司代码」时，快照里若含本公司会被剔除——
    否则本公司的估值会同时充当「被比价者」与「基准样本」，
    分位被自己拉向 50%，中位数被自己带偏。
    """
    peers, self_excluded = _strip_self(rows, own_code)
    out: Dict[str, Any] = {"peer_count": len(peers), "metrics": {},
                           "self_excluded": self_excluded,
                           # 同业样本少到什么程度，分位就粗到什么程度。
                           # 3 家同业只能取 0/33/67/100 四档——渲染层必须据此
                           # 提示精度，不能把 `100.0%` 当精确读数直接呈现。
                           "peer_count_small": len(peers) < PEER_SMALL_THRESHOLD,
                           "peer_count_threshold": PEER_SMALL_THRESHOLD}
    if len(peers) < 3:
        out["note"] = (f"同行业当日仅 {len(peers)} 家"
                       f"{'（快照已剔除本公司）' if self_excluded else ''}"
                       f"有估值数据，不足以横截面对比")
        return out

    for field, own in (("pe_ttm", own_pe), ("pb_mrq", own_pb)):
        col = FIELD_MAP.get(field, field)
        vals = _positive([r.get(col) for r in peers])
        label = METRIC_LABELS.get(field, field)
        n_neg = sum(1 for r in peers
                    if (_f(r.get(col)) is not None and _f(r.get(col)) <= 0))
        if own is None or own <= 0 or len(vals) < 3:
            out["metrics"][field] = {
                "label": label, "value": own, "percentile": None,
                "median": _median(vals), "peer_n": len(vals),
                "negative_peers": n_neg, "note": "本公司该指标不可比",
            }
            continue
        better = sum(1 for v in vals if v < own)
        # ⚠️ 排名必须与分母同口径。
        # 原先写的是 rank=better+1 / rank_of=len(vals)，但 better 统计的是
        # 「比你便宜的同业数」，+1 之后本公司也算进去了一名，而分母没算——
        # 于是出现 rank 4 / rank_of 3 这种分子大于分母的排名
        # （2026-11-10 视觉核查在页面上真实看到「4/3」）。
        # 现在把分母也加上本公司：位次是「本公司在本组（同业+自己）里排第几」。
        out["metrics"][field] = {
            "label": label, "value": own,
            "percentile": better / len(vals) * 100.0,
            "median": _median(vals), "peer_n": len(vals),
            "negative_peers": n_neg,
            "rank": better + 1,
            "rank_of": len(vals) + 1,
            # 该指标的有效样本是否少于同业公司数。
            # 同业里有亏损公司时 PE 会被剔除，于是 PE 行分母（3+1=4）与
            # PB 行分母（4+1=5）不同，而标题只写一个公司数。
            # 不显式标记，页面就会在同一张表里出现两个样本量却无解释。
            "sample_shrunk": len(vals) < len(peers),
            "peer_total": len(peers),
        }
    return out


def recommend_metric(metrics: Dict[str, Dict[str, Any]],
                     divergence: Dict[str, Any]) -> Dict[str, Any]:
    """给出「该看哪个指标」的建议，并说明为什么。

    这一节的存在理由：上游成熟项目（valuation-model 技能）的 SKILL.md
    在估值陷阱 Top10 里明确写了「周期股用 PB，避免用 PE」，
    但它的 PE-Band 脚本并没有实现这个判断——只输出 PE 分位就算了事。
    文档承诺了、代码没做，这是常见的产品缺口。

    判断规则（按优先级）：
    1. PE 不可用（亏损）→ 用 PB，次选 PS
    2. PE 与 PB 分位严重背离 → 周期特征，以 PB 为准并明确说明
    3. 其余 → 以 PE 为主，PB/PS 交叉验证
    """
    pe = metrics.get("pe_ttm") or {}
    pb = metrics.get("pb_mrq") or {}
    ps = metrics.get("ps_ttm") or {}

    pe_ok = pe.get("status") in ("ok", "short_history") and pe.get("percentile") is not None
    pb_ok = pb.get("status") in ("ok", "short_history") and pb.get("percentile") is not None
    ps_ok = ps.get("status") in ("ok", "short_history") and ps.get("percentile") is not None

    if not pe_ok:
        if pb_ok:
            return {
                "primary": "pb_mrq",
                "secondary": "ps_ttm" if ps_ok else None,
                "reason": ("PE 不可用（公司亏损，PE 为负），"
                           "应以 PB 为主判断估值位置——亏损公司的 PE 没有意义，"
                           "但净资产端仍能反映市场愿意给的溢价。"),
            }
        if ps_ok:
            return {
                "primary": "ps_ttm", "secondary": None,
                "reason": ("PE 与 PB 均不可用，改用 PS（市销率）。"
                           "对有收入但尚未盈利的公司，PS 比 PE 更有意义。"),
            }
        return {"primary": None, "secondary": None,
                "reason": "PE / PB / PS 三者均无法可靠计算，本报告不对估值位置下结论。"}

    if divergence.get("diverged"):
        # ⚠️ 这段文案与背离检测（detect_divergence）讲的是**同一个判断**。
        # 2026-11-10 视觉核查：两个整宽框并排出现，一个说「应以 PB 为主判断」，
        # 一个说「此时单看 PE 会误判」，读者要读两遍才发现不是两件事。
        # 现在把背离的具体数字并进这条建议里，报告端不再单独渲染背离框。
        # 文案里「周期」一词只出现一次——由测试按出现次数钉住。
        gap = divergence.get("gap")
        a, b = divergence.get("pe_pct"), divergence.get("pb_pct")
        detail = ""
        if gap is not None and a is not None and b is not None:
            detail = (f"（PE 分位 {a:.1f}% 对 PB 分位 {b:.1f}%，"
                      f"差 {gap:+.1f} 个百分点）")
        return {
            "primary": "pb_mrq" if pb_ok else "pe_ttm",
            "secondary": "ps_ttm" if ps_ok else None,
            "reason": ("PE 与 PB 分位严重背离" + detail + "，呈现典型周期股特征。"
                       "盈利处于高位时利润推高分母，PE 反而显得便宜（看似便宜实为顶部），"
                       "而净资产端仍反映真实位置。因此应以 PB 为主判断，PE 分位仅供参考。"),
        }

    # 走到这里说明 PE 可用，且（若有分位）与 PB 没有严重背离。
    # ⚠️ 但 PB 不一定可用：净资产为负时 PB 为负，status=not_applicable，
    # 没有分位。此时若照旧写「PE 与 PB 分位一致，PB 作交叉验证」，
    # 就是在描述一件没发生过的事，还让读者去看一个不可用的指标
    # （2026-11-10 勘查发现的真实缺陷）。
    # ⇒ 文案必须跟着 secondary 走，不能写死。
    if pb_ok:
        return {
            "primary": "pe_ttm",
            "secondary": "pb_mrq",
            "reason": "PE 与 PB 分位一致，以 PE 为主，PB 作交叉验证。",
        }
    if ps_ok:
        return {
            "primary": "pe_ttm",
            "secondary": "ps_ttm",
            "reason": ("PE 可用，但 PB 不可用（净资产为负，PB 无意义），"
                       "交叉验证改用 PS（市销率）。以 PE 为主判断估值位置。"),
        }
    return {
        "primary": "pe_ttm",
        "secondary": None,
        "reason": "PE 可用；PB 与 PS 均不可用，本报告只能基于 PE 一个维度下判断。",
    }


def compute(data: Dict[str, Any]) -> Dict[str, Any]:
    """主入口：原始数据 -> 结构化结论。"""
    rows = data.get("history") or []
    if not rows:
        raise ValueError("history 为空，无法分析")

    metrics = {f: analyze_metric(rows, f) for f in ("pe_ttm", "pb_mrq", "ps_ttm")}
    first, last, count = _history_span(rows)

    divergence = detect_divergence(metrics)
    recommendation = recommend_metric(metrics, divergence)
    # 注意：行业横截面的结果必须放在独立键里。
    # 早前这里复用了 "industry" 键，把行业名称字符串覆盖成了 dict，
    # 导致报告标题显示 "[object Object]" 之类的东西。
    industry_cmp = industry_position(
        data.get("industry_rows") or [],
        metrics["pe_ttm"]["current"], metrics["pb_mrq"]["current"],
        own_code=data.get("code", ""),
    )

    warnings: List[str] = []
    for m in metrics.values():
        if m["status"] in ("not_applicable", "insufficient", "no_data"):
            warnings.append(f"{m['label']}：{m['reason']}")
        elif m["status"] == "short_history":
            warnings.append(f"{m['label']}：{m['reason']}")
    # 注意：**不要**把「估值信号背离」也塞进 warnings。
    # 它已经在返回值的 divergence 字段里，报告有专门的位置渲染它，
    # CLI 也单独输出。放两处会让同一段结论在页面上整段重复一遍
    # ——2026-11-10 视觉核查时真实看到过（背离文案出现 2 次）。
    # warnings 的语义是「这份报告的适用性限制」，背离是分析发现，不是限制。

    return {
        "code": data["code"],
        "name": data.get("name", ""),
        "industry": data.get("industry", ""),
        "board_code": data.get("board_code", ""),
        "trade_date": data.get("trade_date", ""),
        # 能力型改造后，输入契约里根本不存在价格字段（见 ingest.OUT_FIELDS）：
        # 只有 TRADE_DATE + PE/PB/PS 三列。所以这里不再读 CLOSE_PRICE——
        # 不是「读了但不渲染」，而是「连读的机会都没有」。
        # 判据由 tests 的 TestCapabilityOnly 直接检查 ingest 的输出键集合。
        "history_first": first,
        "history_last": last,
        "history_count": count,
        "metrics": metrics,
        "divergence": divergence,
        "recommendation": recommendation,
        "industry_comparison": industry_cmp,
        "warnings": warnings,
        "fetched_at": data.get("fetched_at", ""),
    }
