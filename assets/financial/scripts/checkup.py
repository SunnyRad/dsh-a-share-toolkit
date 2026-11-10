# -*- coding: utf-8 -*-
"""A 股个股财务体检 · CLI 入口（能力型：不取数，只算数与出成品）

用法：
    # 先看输入格式
    python checkup.py --spec

    # 用自己准备的数据出报告
    python checkup.py --data my_data.json
    python checkup.py --data my_data.json --peers my_peers.json
    python checkup.py --data my_data.json --out ../reports
    python checkup.py --data my_data.json --json          # 只输出 JSON，不渲染 HTML
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analysis            # noqa: E402
import ingest              # noqa: E402
import peers as peer_mod   # noqa: E402
import report as report_mod  # noqa: E402


def run(data_path: str, out_dir: str, peers_path: str = None,
        want_html: bool = True, want_json: bool = True) -> dict:
    """装载用户数据 → 计算 → 同业分位 → 落盘。

    全程零网络。任何一个环节缺数据都明确报错，不降级、不编造。
    """
    data = ingest.load_file(data_path)
    result = analysis.compute(data)
    result["source"] = data.get("source", "")

    peer_rows = ingest.load_peers(peers_path)
    comparison = peer_mod.compare(analysis.own_metrics(result), peer_rows,
                                  profile=result.get("profile") or "industrial")
    result["peer_score"] = peer_mod.peer_score(comparison)
    result["peer_count"] = comparison.get("peer_count", 0)
    result["comparison"] = comparison

    os.makedirs(out_dir, exist_ok=True)
    base = f"{result['code'].replace('.', '_')}_{result['report_date'].replace('-', '')}"
    paths = {}
    if want_json:
        jp = os.path.join(out_dir, base + ".json")
        with open(jp, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        paths["json"] = jp
    if want_html:
        hp = os.path.join(out_dir, base + ".html")
        with open(hp, "w", encoding="utf-8") as fh:
            fh.write(report_mod.render(result, comparison))
        paths["html"] = hp
    result["_paths"] = paths
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A 股个股财务体检 —— 只做计算与出报告，数据由调用方提供")
    ap.add_argument("--data", help="标的财务数据 JSON 文件（格式见 --spec）")
    ap.add_argument("--peers", help="同业样本 JSON 文件（可选，少于 3 家不给分位）")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..", "reports"))
    ap.add_argument("--json", action="store_true", help="只输出 JSON（不生成 HTML）")
    ap.add_argument("--spec", action="store_true", help="打印输入数据格式说明后退出")
    args = ap.parse_args()

    if args.spec or not args.data:
        print(ingest.SPEC_TEXT)
        return 0 if args.spec else 2

    try:
        r = run(os.path.abspath(args.data), os.path.abspath(args.out),
                peers_path=os.path.abspath(args.peers) if args.peers else None,
                want_html=not args.json, want_json=True)
    except ingest.InputError as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 2

    s = r["scale"]
    print(f"{r['code']} {r['name']} [{r['industry'] or '未分类'}]  报告期 {r['report_date']}")
    print(f"  数据来源 {r['source']}")
    rev = s['revenue']
    npf = s['net_profit']
    print(f"  营收 {rev/1e8:,.2f} 亿  归母 {npf/1e8:,.2f} 亿"
          if rev is not None and npf is not None else "  营收/归母：数据缺失")
    roe = r["dupont"]["roe_direct"]
    print(f"  ROE {roe:.2f}%" if roe is not None else "  ROE 缺失")
    if r["peer_score"] is not None:
        print(f"  同业分 {r['peer_score']:.1f}（样本 {r['peer_count']} 家）")
    else:
        note = r["comparison"].get("note", "未提供同业样本")
        print(f"  同业分 无 —— {note}")
    flags = r["flags"]
    print(f"  红旗 高危 {len([f for f in flags if f['level']=='高危'])}"
          f" / 关注 {len([f for f in flags if f['level']=='关注'])}"
          f" / 正常 {len([f for f in flags if f['level']=='正常'])}")
    if r.get("missing"):
        print(f"  数据缺失项 {len(r['missing'])}：{'、'.join(r['missing'])}")
    for p, v in r["_paths"].items():
        print(f"  {p}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
