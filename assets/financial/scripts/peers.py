# -*- coding: utf-8 -*-
"""A 股个股财务体检 · 同业对比层（纯计算，零网络）

═══════════════════════════════════════════════════════════════════
为什么同业样本由调用方提供
═══════════════════════════════════════════════════════════════════
本技能自己去拉「同行业全部上市公司」的报表，等于以我们的名义向用户
交付证券信息服务（需要证券信息经营许可，见 SKILL.md「数据边界」一节）。
因此同业样本改由调用方提供：用户的智能体用其自有数据源取好，再传进来。

为什么必须有同业对比（不能省）：
绝对阈值评分一定会误判。比亚迪 70% 资产负债率在汽车行业是常态，
但用「负债率越低越好」的绝对标准会把它打到 19 分——这不是分析，是误导。

正确做法：**同一行业内做横截面排名**（百分位），再转成 0-100 分。
这样「宁德时代毛利率 22.7%」不是「不及格」，而是「在电池行业里排第 X 位」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 同业对比用的指标（字段名 → 中文名 → 方向）
# direction:  1 = 越大越好, -1 = 越小越好
PEER_METRICS = {
    "gross_margin":   ("毛利率", 1),
    "net_margin":     ("净利率", 1),
    "roe":            ("ROE", 1),
    "revenue_yoy":    ("营收增速", 1),
    "profit_yoy":     ("净利增速", 1),
    "debt_ratio":     ("资产负债率", -1),
    "deduct_ratio":   ("扣非占比", 1),
}

MIN_PEERS = 3        # 少于这个数不足做横截面

# 某些指标对某类公司**不适用**，不能拿来排名。
# 依据是页面自己已经写出口径（report.py 的 prof_note）：
#   「本报告识别为金融业，毛利率与资产负债率不适用」
# 既然对外宣称不适用，就不能转头在同业表里给它们打分、画红点。
# 银行负债率 92% 是经营常态（存款即负债），标红是纯粹的误读诱导。
#
# 键是 profile，值是**不适用的指标名**。
INAPPLICABLE_BY_PROFILE = {
    "financial": {"gross_margin", "debt_ratio"},
}

# profile 的中文名，用于生成人读的说明句子。
PROFILE_LABELS = {
    "financial": "金融业",
    "industrial": "工商企业",
}


def inapplicable_reason(profile: str, keys) -> str:
    """把不适用指标合并成**一句话**，而不是每个指标各说一遍。

    实测踩到的坑：逐条拼出来的句子是
      「毛利率对该类公司不适用，资产负债率对该类公司不适用，不参与横截面排名」
    同一句里重复两遍「对该类公司不适用」，读者要读两遍才发现是同一件事。

    顺序按 PEER_METRICS 的声明顺序，不跟 set 走——否则同一份数据
    每次渲染的指标顺序都可能不同（实际出现过「资产负债率、毛利率」）。
    """
    order = list(PEER_METRICS.keys())
    picked = [k for k in order if k in set(keys)]
    labels = [PEER_METRICS[k][0] for k in picked]
    if not labels:
        return ""
    prof_label = PROFILE_LABELS.get(profile, profile)
    return (f"{'、'.join(labels)}对{prof_label}不适用，"
            f"不参与横截面排名。")


def _as_float(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def percentile_rank(values: List[float], x: float, direction: int = 1) -> Optional[float]:
    """x 在 values 中的百分位（0-100）。

    direction=1：越大越好；direction=-1：越小越好。
    并列值按「优于及等于」的中位处理，避免边界抖动。
    """
    if not values or x is None:
        return None
    n = len(values)
    if n < MIN_PEERS:
        return None
    if direction == 1:
        better = sum(1 for v in values if v < x)
        equal = sum(1 for v in values if v == x)
    else:
        better = sum(1 for v in values if v > x)
        equal = sum(1 for v in values if v == x)
    return (better + 0.5 * equal) / n * 100.0


def quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    """线性插值分位数（与 numpy 默认 / 估值模型的 PE-Band 口径一致）。

    只给中位数的话，读者看不出「自己在中位的上方还是下方、分布有多宽」。
    分位带（10/25/50/75/90）才能说明「这是行业普遍现象还是个体异常」。
    """
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def band(vals: List[float]) -> Dict[str, Optional[float]]:
    """返回 p10/p25/p50/p75/p90 分位带。"""
    s = sorted(vals)
    return {
        "p10": quantile(s, 0.10),
        "p25": quantile(s, 0.25),
        "p50": quantile(s, 0.50),
        "p75": quantile(s, 0.75),
        "p90": quantile(s, 0.90),
        "min": s[0] if s else None,
        "max": s[-1] if s else None,
    }


def compare(own: Dict[str, Any], peers: List[Dict[str, Any]],
            profile: str = "industrial") -> Dict[str, Any]:
    """把单家公司的指标放到调用方提供的同业样本里排名。

    返回 {peer_count, metrics: {key: {label, value, percentile, peer_median,
          peer_n, rank, rank_of, direction}}, inapplicable: {key: reason}}

    profile 决定哪些指标不参与排名（见 INAPPROPRIABLE_BY_PROFILE）。
    不适用 ≠ 缺数据：前者是「这个指标对该类公司无意义」，
    后者是「调用方没给」。两者必须在结果里分开，不能都显示「无数据」。
    """
    peers = peers or []
    result: Dict[str, Any] = {"peer_count": len(peers), "metrics": {},
                              "inapplicable": {}}
    skip = INAPPLICABLE_BY_PROFILE.get(profile) or set()
    for key in skip:
        label = PEER_METRICS.get(key, (key, 1))[0]
        result["inapplicable"][key] = (
            f"{label}对{'金融业' if profile == 'financial' else profile}不适用，不参与横截面排名"
        )

    if not peers:
        result["note"] = "未提供同业样本，故不做横截面排名"
        return result
    if len(peers) < MIN_PEERS:
        result["note"] = (
            f"同业样本仅 {len(peers)} 家，不足 {MIN_PEERS} 家，"
            f"不构成横截面统计意义，故不给分位"
        )
        return result

    for key, (label, direction) in PEER_METRICS.items():
        if key in skip:
            continue
        own_v = _as_float(own.get(key))
        vals = [v for v in (_as_float(p.get(key)) for p in peers) if v is not None]
        if own_v is None or len(vals) < MIN_PEERS:
            result["metrics"][key] = {
                "label": label, "value": own_v, "percentile": None,
                "peer_median": None, "peer_n": len(vals), "direction": direction,
            }
            continue
        vals_sorted = sorted(vals)
        med = vals_sorted[len(vals_sorted) // 2]
        pct = percentile_rank(vals, float(own_v), direction)
        # 排名（1 = 最好）
        if direction == 1:
            rank = sum(1 for v in vals if v > own_v) + 1
        else:
            rank = sum(1 for v in vals if v < own_v) + 1
        result["metrics"][key] = {
            "label": label, "value": own_v, "percentile": pct,
            "peer_median": med, "peer_n": len(vals),
            "rank": rank, "rank_of": len(vals), "direction": direction,
            "band": band(vals),
        }
    return result


def peer_score(comparison: Dict[str, Any]) -> Optional[float]:
    """把同业百分位聚合成 0-100 分。"""
    pcts = [m["percentile"] for m in comparison.get("metrics", {}).values()
            if m.get("percentile") is not None]
    if not pcts:
        return None
    return sum(pcts) / len(pcts)


if __name__ == "__main__":
    import sys
    # 自测：不连任何网络，只用假数据演示
    demo_peers = [{"code": f"T{i}", "roe": v} for i, v in enumerate([5, 8, 12, 15, 20])]
    c = compare({"roe": 12}, demo_peers)
    print(f"样本 {c['peer_count']} 家")
    print("ROE 分位:", c["metrics"]["roe"]["percentile"],
          "排名:", c["metrics"]["roe"]["rank"], "/", c["metrics"]["roe"]["rank_of"])
    if len(sys.argv) > 1:
        print("（本技能不接受命令行拉数；同业样本请通过 --peers 传入 JSON 文件）")
