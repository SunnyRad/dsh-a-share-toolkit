# -*- coding: utf-8 -*-
"""把常见的导出格式转成本技能的契约 JSON。

存在理由（竞品实测得出的判断）：竞品全都自带数据源，用户装完就能出结果；
我们是能力型，用户必须先准备好数据。**如果这一步卡住，后面做得再好也没用。**
所以这个脚本解决转化漏斗的第一道坎：让用户不必手写 JSON。

支持三种输入：

  1. Tushare daily_basic 的导出（TSV/CSV，列名如 ts_code/trade_date/pe_ttm）
  2. 同花顺/东方财富等行情软件导出的 CSV（中文列名，如 日期/市盈率TTM）
  3. 已有的估值 CSV（date,pe_ttm,pb_mrq,ps_ttm）→ 补上元信息后转 JSON

用法：
    python -X utf8 to_contract.py --in raw.csv --out my_data.json \\
        --code 600519.SH --name 贵州茅台 --industry 白酒 \\
        --source "Tushare Pro（自有凭据）"

    # 只看它会怎么映射，不写文件
    python -X utf8 to_contract.py --in raw.csv --dry-run
"""
import argparse
import csv
import json
import os
import sys

# 常见列名 → 契约字段。覆盖 Tushare 原始的、以及行情软件中文导出的。
COLUMN_ALIASES = {
    "date": [
        "date", "trade_date", "日期", "交易日期", "时间",
        "报告期", "截止日期",
    ],
    "pe_ttm": [
        "pe_ttm", "pe", "pe_ratio", "市盈率", "市盈率ttm", "市盈率_ttm",
        "市盈率(ttm)", "市盈率（ttm）", "滚动市盈率", "pettm",
    ],
    "pb_mrq": [
        "pb_mrq", "pb", "pb_ratio", "市净率", "市净率mrq", "市净率_mrq",
        "市净率(mrq)", "市净率（mrq）", "pb",
    ],
    "ps_ttm": [
        "ps_ttm", "ps", "ps_ratio", "市销率", "市销率ttm", "市销率_ttm",
        "市销率(ttm)", "市销率（ttm）", "psttm",
    ],
}

# 契约明确不接受的列（给了也不读，避免用户以为会被用上）
REFUSED = ["close", "open", "high", "low", "vol", "amount", "pct_chg",
           "收盘价", "开盘价", "最高价", "最低价", "成交量", "成交额", "涨跌幅"]


def norm_header(h):
    """把列名压成小写、去掉空格与常见分隔符，便于匹配。

    实测必要的理由：「市盈率（TTM）」与「市盈率TTM」在全角/半角括号、
    大小写上各不相同，直接比较会漏。归一后才能稳定匹配。
    """
    s = str(h).strip().lower()
    for ch in (" ", "\t", "_", "-", ".", "(", ")", "（", "）", "[", "]",
               "：", ":", "/"):
        s = s.replace(ch, "")
    return s


def build_alias_map(headers):
    """给定表头，返回 {契约字段: 实际列名}。"""
    norm_to_orig = {norm_header(h): h for h in headers}
    out = {}
    for field, alts in COLUMN_ALIASES.items():
        for alt in alts:
            na = norm_header(alt)
            if na in norm_to_orig:
                out[field] = norm_to_orig[na]
                break
    return out


def to_float(v):
    """转数值。空值/无法解析一律返回 None（契约允许 None，表示该日无数据）。

    不能返回 0：0 是「真实的零」，会被当成有效样本参与分位计算。
    """
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("，", "")
    if s in ("", "-", "--", "—", "None", "null", "nan", "NaN", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_date(v):
    """统一日期成 YYYY-MM-DD。接受 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD。"""
    s = str(v or "").strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


def read_rows(path):
    """读 CSV/TSV。自动判断分隔符与编码（UTF-8 优先，失败退回 GBK）。"""
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, encoding=enc, newline="") as fh:
                raw = fh.read()
            used_enc = enc
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise SystemExit(f"无法解码 {path}（试过 utf-8 / gbk / gb18030）")

    head = raw.split("\n", 1)[0]
    delim = "\t" if head.count("\t") > head.count(",") else ","
    reader = csv.DictReader(raw.splitlines(), delimiter=delim)
    return list(reader), used_enc, delim


def convert(rows, meta):
    headers = list(rows[0].keys()) if rows else []
    amap = build_alias_map(headers)

    if "date" not in amap:
        raise SystemExit(
            "找不到日期列。已识别的列: " + ", ".join(headers[:12]) +
            "\n契约要求至少有一个日期列（date/trade_date/日期/交易日期）。")

    metrics = [f for f in ("pe_ttm", "pb_mrq", "ps_ttm") if f in amap]
    if not metrics:
        raise SystemExit(
            "找不到任何估值列（pe_ttm / pb_mrq / ps_ttm）。"
            "\n已识别的列: " + ", ".join(headers[:12]) +
            "\n注意：本技能只做估值，价格/成交量类列不会被读取。")

    history = []
    for r in rows:
        d = normalize_date(r.get(amap["date"]))
        if not d:
            continue
        item = {"date": d}
        for f in metrics:
            item[f] = to_float(r.get(amap[f]))
        history.append(item)

    # 契约要求：最新在前
    history.sort(key=lambda x: x["date"], reverse=True)

    if not history:
        raise SystemExit("转换后没有任何有效行，检查日期列是否为空")

    # 逐指标统计非空样本，让用户知道自己给了多少
    stats = {f: sum(1 for h in history if h.get(f) is not None)
             for f in metrics}

    refused = [h for h in headers if norm_header(h) in
               {norm_header(x) for x in REFUSED}]

    out = dict(meta)
    out["history"] = history
    return out, {
        "mapping": amap,
        "metrics": metrics,
        "stats": stats,
        "rows": len(history),
        "refused_columns": refused,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out")
    ap.add_argument("--code", default="000000.SZ")
    ap.add_argument("--name", default="")
    ap.add_argument("--industry", default="")
    ap.add_argument("--source", default="用户自带 CSV 文件")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.inp):
        raise SystemExit(f"找不到输入文件: {args.inp}")

    rows, enc, delim = read_rows(args.inp)
    if not rows:
        raise SystemExit("文件里没有数据行")

    meta = {
        "code": args.code,
        "name": args.name,
        "industry": args.industry,
        "source": args.source,
    }
    out, info = convert(rows, meta)

    print(f"读取 {args.inp}")
    print(f"  编码 {enc} / 分隔符 {'TAB' if delim == chr(9) else '逗号'} "
          f"/ {info['rows']} 行")
    print("\n列映射：")
    for f, col in info["mapping"].items():
        print(f"  {f:10} <- {col}")
    print("\n各指标有效样本：")
    for f, n in info["stats"].items():
        print(f"  {f:10} {n} 行非空")

    if info["refused_columns"]:
        print("\n⚠ 这些列**不会被读取**（本技能只做估值，不做行情）：")
        for c in info["refused_columns"]:
            print(f"    {c}")

    # 样本量提示，与契约门槛一致
    for f, n in info["stats"].items():
        if n == 0:
            print(f"\n⚠ {f} 全部为空，该指标不会出现在报告里")
        elif n < 250:
            print(f"\n⚠ {f} 只有 {n} 个有效样本（<250），"
                  f"本技能会拒绝出分位")
        elif n < 750:
            print(f"\n⚠ {f} 有 {n} 个有效样本（<750），"
                  f"会出分位但标注「仅供参考」")

    if args.dry_run:
        print("\n（--dry-run，未写文件）")
        return 0

    out_path = args.out or os.path.splitext(args.inp)[0] + ".contract.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"\n已写出: {out_path}")
    print(f"下一步: python -X utf8 checkup.py --data {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
