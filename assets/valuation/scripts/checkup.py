# -*- coding: utf-8 -*-
"""A 股估值分位体检 · CLI 入口（能力型：不取数，只算数与出成品）

用法：
    # 先看输入格式
    python checkup.py --spec

    # 用自己准备的数据出报告
    python checkup.py --data my_valuation.csv
    python checkup.py --data my_valuation.json
    python checkup.py --data my.json --out ../reports
    python checkup.py --data my.json --json          # 只输出 JSON，不渲染 HTML
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analysis            # noqa: E402
import ingest              # noqa: E402
import report as report_mod  # noqa: E402


def run(data_path: str, out_dir: str = "", want_html: bool = True,
        want_json: bool = True) -> dict:
    """装载用户数据 → 计算 → 落盘。全程零网络。

    任何一个环节缺数据都明确报错，不降级、不编造。
    """
    data = ingest.load_file(data_path)
    result = analysis.compute(data)
    result["source"] = data.get("source", "")
    result["offline"] = bool(data.get("offline"))

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        base = f"{result['code'].replace('.', '_')}_{result['trade_date'].replace('-', '')}"
        paths = {}
        if want_json:
            jp = os.path.join(out_dir, base + ".json")
            with open(jp, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)
            paths["json"] = jp
        if want_html:
            hp = os.path.join(out_dir, base + ".html")
            with open(hp, "w", encoding="utf-8") as fh:
                fh.write(report_mod.render(result, source=result["source"]))
            paths["html"] = hp
        result["_paths"] = paths

    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A 股估值分位体检 —— 只做计算与出报告，数据由调用方提供")
    ap.add_argument("--data", help="估值数据文件（.csv 或 .json，格式见 --spec）")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..", "reports"))
    ap.add_argument("--json", action="store_true", help="只输出 JSON（不生成 HTML）")
    ap.add_argument("--spec", action="store_true", help="打印输入数据格式说明后退出")
    args = ap.parse_args()

    if args.spec or not args.data:
        print(ingest.SPEC_TEXT)
        return 0 if args.spec else 2

    try:
        r = run(os.path.abspath(args.data), out_dir=os.path.abspath(args.out),
                want_html=not args.json, want_json=True)
    except ingest.InputError as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    m = r["metrics"]
    print(f"{r['code']} {r['name']} [{r['industry'] or '-'}]  数据日期 {r['trade_date']}")
    print(f"  数据来源 {r['source']}")
    print(f"  历史 {r['history_first']} ~ {r['history_last']}（{r['history_count']} 个交易日）")
    for key in ("pe_ttm", "pb_mrq", "ps_ttm"):
        mm = m[key]
        p = mm["percentile"]
        p_s = f"{p:.1f}%" if p is not None else "不适用"
        # 当前值必须走格式化，不能直接 print 原始 float。
        # 2026-11-10 实测：PE 序列末值是 20+799*0.01，二进制算出来
        # 27.990000000000002，于是工具摘要里就印出一长串尾数。
        # HTML 报告走的是 report._n()（两位小数），两边不一致 ——
        # 用户在对话里看到的是摘要，看到的恰恰是没格式化那个。
        try:
            cur = f"{float(mm['current']):,.2f}"
        except (TypeError, ValueError):
            cur = "无数据"
        print(f"  {mm['label']:<8} 当前 {cur}  分位 {p_s}")
    if r["divergence"].get("diverged"):
        print(f"  ⚠ 估值信号背离，差 {r['divergence']['gap']:+.0f} 个百分点")
    if r.get("warnings"):
        print(f"  适用性限制 {len(r['warnings'])} 条：")
        for w in r["warnings"]:
            print(f"    - {w}")
    for p, v in (r.get("_paths") or {}).items():
        print(f"  {p}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
