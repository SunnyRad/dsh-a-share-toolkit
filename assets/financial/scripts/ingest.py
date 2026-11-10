# -*- coding: utf-8 -*-
"""A 股个股财务体检 · 数据装载层（能力型，零网络）

═══════════════════════════════════════════════════════════════════
架构修正（2026-11-09）：本技能不提供、不抓取、不转发任何行情或财务数据。
═══════════════════════════════════════════════════════════════════

数据由调用方（用户的智能体 / 用户本人）通过其**自有数据源**取得后，
按本模块定义的 JSON 契约传入。原因有两条，都不是口味问题：

1) **合规**。平台「股票信息服务平台」类目要求「增值电信业务经营许可证」
   + 深交所《专有信息经营许可合同》+ 上交所《证券信息经营许可合同》，
   个人主体拿不到。技能若自行抓取证券数据并交付给用户，就是以个人身份
   从事需要许可的证券信息服务 —— 这不只是过不了审，是本来就不该做。

2) **产品边界**。取数能力已有专门技能承担（如 tushare-data / common 数据网关，
   凭据存在**用户自己**的 secrets.env 里）。本技能只负责「算对、校验、出成品」。
   把取数与算数混在一个包里，等于用我们的名义替用户向第三方取数。

因此本模块**只依赖标准库、不含任何网络调用**，可离线审计：

    grep -Ei "urllib|requests|socket|http|tushare|akshare" scripts/*.py

应当零命中（SKILL.md / references 中的文字说明除外）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════════
# 规范字段名
# ══════════════════════════════════════════════════════════════════
# 内部一律使用下表左列的规范名，与任何具体数据源解耦。
# 这样换数据源（Tushare / 东财 / 自建 / 手工整理）不需要改分析层。
FIELD_SPEC: Dict[str, List[str]] = {
    # ── 利润表 ──
    "revenue": ["revenue", "total_revenue", "operate_income",
                "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "营业总收入", "营业收入"],
    "operate_cost": ["operate_cost", "OPERATE_COST", "营业成本", "营业总成本"],
    "net_profit": ["net_profit", "n_income_attr_p", "n_income", "parent_netprofit",
                   "PARENT_NETPROFIT", "归母净利润", "净利润"],
    "deduct_net_profit": ["deduct_net_profit", "deduct_parent_netprofit",
                          "DEDUCT_PARENT_NETPROFIT", "扣非净利润", "扣除非经常性损益后的净利润"],
    "sale_expense": ["sale_expense", "SALE_EXPENSE", "销售费用"],
    "manage_expense": ["manage_expense", "MANAGE_EXPENSE", "管理费用"],
    "finance_expense": ["finance_expense", "FINANCE_EXPENSE", "财务费用"],
    # ── 资产负债表 ──
    "total_assets": ["total_assets", "TOTAL_ASSETS", "资产总计", "总资产"],
    "total_liab": ["total_liab", "total_liabilities", "TOTAL_LIABILITIES",
                   "负债合计", "总负债"],
    "total_equity": ["total_equity", "total_hldr_eqy_exc_min_int", "TOTAL_EQUITY",
                     "所有者权益合计", "股东权益合计", "股东权益"],
    "monetary": ["monetary", "money_cap", "MONETARYFUNDS", "货币资金"],
    "accounts_rece": ["accounts_rece", "accounts_receiv", "ACCOUNTS_RECE", "应收账款"],
    "inventory": ["inventory", "inventories", "INVENTORY", "存货"],
    "current_ratio": ["current_ratio", "CURRENT_RATIO", "流动比率"],
    # ── 现金流量表 ──
    "cfo": ["cfo", "n_cashflow_act", "NETCASH_OPERATE", "经营活动产生的现金流量净额",
            "经营现金流"],
    "net_invest": ["net_invest", "n_cashflow_inv_act", "NETCASH_INVEST",
                   "投资活动产生的现金流量净额"],
    "net_finance": ["net_finance", "n_cash_flows_fnc_act", "NETCASH_FINANCE",
                    "筹资活动产生的现金流量净额"],
    # ── 通用 ──
    "report_date": ["report_date", "end_date", "REPORT_DATE", "报告期", "报告日期", "截止日期"],
}

# 金额类字段（会让 unit 缩放生效；比率类字段绝不能缩放）
MONEY_FIELDS = {
    "revenue", "operate_cost", "net_profit", "deduct_net_profit",
    "sale_expense", "manage_expense", "finance_expense",
    "total_assets", "total_liab", "total_equity", "monetary",
    "accounts_rece", "inventory", "cfo", "net_invest", "net_finance",
}

REQUIRED_STATEMENTS = ("income", "balance", "cashflow")

# 单位 → 换算到「元」的乘数
UNIT_SCALE = {"yuan": 1.0, "元": 1.0, "wan": 1e4, "万元": 1e4,
              "yi": 1e8, "亿元": 1e8, "qian": 1e3, "千元": 1e3}

# 单位字符串里出现这些词就直接判定是整数元（防止 "元" 误命中 "万元"）
_NULLISH = {"", "-", "--", "—", "none", "null", "nan", "n/a", "na", "无", "无数据"}


class InputError(RuntimeError):
    """输入契约不满足。绝不静默用默认值兜底——那会让用户拿到一份错报告。"""


# ══════════════════════════════════════════════════════════════════
# 基础解析
# ══════════════════════════════════════════════════════════════════
def to_number(v: Any) -> Optional[float]:
    """把各种脏形态转成 float；无法解析返回 None。

    必须处理的真实形态（来自用户手工整理或不同数据源）：
      "1,234.56" / "１２３"（全角）/ "" / "-" / "—" / "None" / "nan" / 布尔
    返回 None 而不是 0 —— 0 是有效数值，填 0 冒充缺失会直接改变结论。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if f != f else f          # NaN
    s = str(v).strip()
    if s.lower() in _NULLISH:
        return None
    # 全角数字 → 半角
    s = s.translate(str.maketrans("０１２３４５６７８９．－", "0123456789.-"))
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    for suf in ("元", "%", "％"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    try:
        f = float(s)
    except ValueError:
        return None
    return None if f != f else f


def pick(row: Dict[str, Any], canon: str) -> Any:
    """按别名表从一行里取规范字段。未命中返回 None。"""
    for alias in FIELD_SPEC.get(canon, []):
        if alias in row and row[alias] is not None:
            return row[alias]
    # 大小写不敏感兜底（不同源写法差异大）
    lowered = {str(k).lower(): k for k in row}
    for alias in FIELD_SPEC.get(canon, []):
        k = lowered.get(alias.lower())
        if k is not None and row[k] is not None:
            return row[k]
    return None


def normalize_date(v: Any) -> Optional[str]:
    """'20260930' / '2026-09-30 00:00:00' / '2026/9/30' -> '2026-09-30'。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(digits) == 6:                       # 202609 → 期末
        y, m = digits[0:4], digits[4:6]
        return f"{y}-{m}-{ {'03':'31','06':'30','09':'30','12':'31'}.get(m, '31') }"
    return None


def unit_scale(unit: Any) -> float:
    """单位字符串 → 换算到元的乘数。无法识别时**报错**而不是猜。"""
    if unit is None:
        return 1.0
    key = str(unit).strip().lower()
    if key in UNIT_SCALE:
        return UNIT_SCALE[key]
    for k, v in UNIT_SCALE.items():
        if k in key:
            return v
    raise InputError(
        f"无法识别的金额单位 {unit!r}。请在输入的 unit 字段里写 "
        f"yuan / wan / yi 之一（或 元 / 万元 / 亿元）。"
        f"单位猜错会让整份报告的数量级全错，所以这里选择报错而不是默认。"
    )


# ══════════════════════════════════════════════════════════════════
# 报表规范化
# ══════════════════════════════════════════════════════════════════
def normalize_row(row: Dict[str, Any], scale: float) -> Dict[str, Any]:
    """一行原始数据 → 一行规范数据（未出现的字段保持 None）。"""
    out: Dict[str, Any] = {}
    for canon in FIELD_SPEC:
        raw = pick(row, canon)
        if canon == "report_date":
            out["report_date"] = normalize_date(raw)
        elif canon in MONEY_FIELDS:
            n = to_number(raw)
            out[canon] = None if n is None else n * scale
        else:
            out[canon] = to_number(raw)        # 比率类，不缩放
    return out


def normalize_statement(rows: Any, scale: float, kind: str) -> List[Dict[str, Any]]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise InputError(f"{kind} 必须是数组（每期一条记录），实际收到 {type(rows).__name__}")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise InputError(f"{kind}[{i}] 不是对象")
        norm = normalize_row(r, scale)
        if norm.get("report_date") is None:
            raise InputError(
                f"{kind}[{i}] 缺少可解析的报告期（report_date / end_date / REPORT_DATE）。"
                f"没有报告期的记录无法做同比，必须补上或删除该条。"
            )
        out.append(norm)
    # 按报告期倒序，调用方不必自己排序
    out.sort(key=lambda x: x["report_date"], reverse=True)
    return out


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════
def load_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把一个已解析的 dict 规范化为分析层可用的结构。"""
    if not isinstance(payload, dict):
        raise InputError("输入根节点必须是 JSON 对象")

    code = str(payload.get("code") or "").strip()
    if not code:
        raise InputError("缺少 code 字段（如 600519.SH）")

    source = str(payload.get("source") or "").strip()
    if not source:
        # 强制披露数据来源：报告要把这句话原样印出来。
        # 没有来源的报告在合规上无法自证「数据由用户提供」，也不利用户自查。
        raise InputError(
            "缺少 source 字段。必须写明数据来源（例如「Tushare Pro，用户自有凭据」"
            "或「用户手工整理自 2026 年三季报」），报告会原样展示该说明。"
        )

    scale = unit_scale(payload.get("unit"))

    out: Dict[str, Any] = {
        "code": code,
        "name": str(payload.get("name") or "").strip(),
        "industry": str(payload.get("industry") or "").strip(),
        "industry_code": str(payload.get("industry_code") or "").strip(),
        "source": source,
        "unit": str(payload.get("unit") or "yuan"),
    }
    for kind in REQUIRED_STATEMENTS:
        out[kind] = normalize_statement(payload.get(kind), scale, kind)

    missing = [k for k in REQUIRED_STATEMENTS if not out[k]]
    if missing:
        raise InputError(
            f"缺少报表数据：{'、'.join(missing)}。"
            f"三张报表缺一不可 —— 缺表的「财务体检」会给出一份看似完整、实则失真的结论。"
        )

    out["report_date"] = out["income"][0]["report_date"]
    if not out["name"]:
        out["name"] = code
    return out


def load_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise InputError(f"数据文件不存在：{path}")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} 不是合法 JSON：{exc}") from exc
    return load_payload(payload)


SPEC_TEXT = """本技能不提供数据，只做计算。请先按下面的契约准备好数据。

════════════════════════════════════════════════════════════════
输入文件格式（标准 JSON，UTF-8，不带注释 —— 可直接复制使用）
════════════════════════════════════════════════════════════════

{
  "code": "600519.SH",
  "name": "贵州茅台",
  "industry": "白酒",
  "source": "Tushare Pro（用户自有凭据）",
  "unit": "yuan",
  "income": [
    {"report_date": "2026-09-30", "revenue": 123100000000,
     "net_profit": 60800000000, "deduct_net_profit": 60600000000,
     "operate_cost": 10100000000},
    {"report_date": "2025-09-30", "revenue": 110000000000,
     "net_profit": 55000000000}
  ],
  "balance": [
    {"report_date": "2026-09-30", "total_assets": 300000000000,
     "total_liab": 60000000000, "total_equity": 240000000000,
     "monetary": 150000000000, "accounts_rece": 360000000,
     "inventory": 45000000000}
  ],
  "cashflow": [
    {"report_date": "2026-09-30", "cfo": 52000000000,
     "net_invest": -3000000000, "net_finance": -48000000000}
  ]
}

字段说明（前 3 个必填）：
  code     标的代码
  source   数据来源 —— 报告会原样印出这句话，没有它无法自证数据由谁提供
  unit     金额单位，yuan / wan / yi 三选一（也可写 元 / 万元 / 亿元）
  name / industry   选填；industry 用于识别金融业并跳过不适用的指标

════════════════════════════════════════════════════════════════
每行需要的字段（大小写、中英文写法都认，别名见 FIELD_SPEC）
════════════════════════════════════════════════════════════════

必有：report_date   报告期，如 "2026-09-30" / "20260930" / "2026-09-30 00:00:00"

利润表 income：
  revenue            营业总收入      （别名 total_revenue / TOTAL_OPERATE_INCOME / 营业总收入）
  net_profit         归母净利润      （别名 n_income_attr_p / PARENT_NETPROFIT）
  deduct_net_profit  扣非净利润      （选填但强烈建议，缺失则扣非红旗无法计算）
  operate_cost       营业成本        （金融业不用填）
  sale_expense / manage_expense / finance_expense  三项费用（选填）

资产负债表 balance：
  total_assets    资产总计
  total_liab      负债合计
  total_equity    所有者权益合计
  monetary        货币资金（选填）
  accounts_rece   应收账款（选填）
  inventory       存货（选填）
  current_ratio   流动比率（选填，百分数形式，如 250.0 表示 2.5 倍）

现金流量表 cashflow：
  cfo           经营活动产生的现金流量净额
  net_invest    投资活动现金流量净额（选填）
  net_finance   筹资活动现金流量净额（选填）

════════════════════════════════════════════════════════════════
建议提供 8 期以上（含去年同期），否则同比与趋势无法计算。
三张表缺一不可 —— 缺表的「财务体检」会给出看似完整、实则失真的结论。
════════════════════════════════════════════════════════════════

同业对比（可选）：再准备一个 peers.json，内容是数组，每项二选一：
  A. 已算好的指标：{"code","name","gross_margin","net_margin","roe",
                    "revenue_yoy","profit_yoy","debt_ratio","deduct_ratio"}
  B. 原始三表：    {"code","name","industry","income":[...],"balance":[...],"cashflow":[...]}
     —— 若用 B，本技能会用同一套口径替你算指标，口径一致性更有保证。

少于 3 家同业时不给分位（样本不足的百分位没有统计意义）。
"""


def load_peers(path: Optional[str]) -> List[Dict[str, Any]]:
    """装载同业样本。返回已经算好指标的列表。

    两种输入都收：
      A. 指标形式 —— 直接可用
      B. 原始三表 —— 用与本标的完全相同的口径现算（更推荐，避免口径不一致）
    """
    if not path:
        return []
    if not os.path.exists(path):
        raise InputError(f"同业数据文件不存在：{path}")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} 不是合法 JSON：{exc}") from exc

    if isinstance(payload, dict):
        payload = payload.get("peers") or []
    if not isinstance(payload, list):
        raise InputError('同业文件根节点必须是数组，或 {"peers": [...]}')

    out: List[Dict[str, Any]] = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            raise InputError(f"peers[{i}] 不是对象")
        entry: Dict[str, Any] = {
            "code": str(item.get("code") or "").strip() or f"peer{i}",
            "name": str(item.get("name") or "").strip(),
        }
        # B 形态：给了三表就现算（内部延迟 import，避免循环依赖）
        if any(isinstance(item.get(k), list) and item.get(k) for k in REQUIRED_STATEMENTS):
            entry.update(_metrics_from_statements(item))
        else:
            for k in ("gross_margin", "net_margin", "roe", "revenue_yoy",
                      "profit_yoy", "debt_ratio", "deduct_ratio"):
                entry[k] = to_number(item.get(k))
        out.append(entry)
    return out


def _metrics_from_statements(item: Dict[str, Any]) -> Dict[str, Any]:
    """用与标的一致的口径，从同业原始三表算指标。"""
    scale = unit_scale(item.get("unit"))
    data = {
        "code": str(item.get("code") or ""),
        "name": str(item.get("name") or ""),
        "industry": str(item.get("industry") or ""),
        "source": str(item.get("source") or "peer"),
        "unit": str(item.get("unit") or "yuan"),
    }
    for kind in REQUIRED_STATEMENTS:
        data[kind] = normalize_statement(item.get(kind), scale, kind)
    if not data["income"] or not data["balance"]:
        return {k: None for k in ("gross_margin", "net_margin", "roe",
                                  "revenue_yoy", "profit_yoy", "debt_ratio", "deduct_ratio")}
    # 延迟导入：analysis 只依赖本模块的规范名，不反向依赖
    import analysis
    r = analysis.compute(data)
    return {
        "gross_margin": r["profitability"]["gross_margin"],
        "net_margin": r["dupont"]["net_margin"],
        "roe": r["dupont"]["roe_direct"],
        "revenue_yoy": r["scale"]["revenue_yoy"],
        "profit_yoy": r["scale"]["net_profit_yoy"],
        "debt_ratio": r["solvency"]["debt_ratio"],
        "deduct_ratio": r["profitability"]["deduct_ratio"],
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] in ("--spec", "-h", "--help"):
        print(SPEC_TEXT)
        sys.exit(0)
    d = load_file(sys.argv[1])
    print(f"{d['code']} {d['name']} [{d['industry']}] 来源 {d['source']}")
    print(f"最新报告期 {d['report_date']}")
    for k in REQUIRED_STATEMENTS:
        print(f"  {k:<9} {len(d[k])} 期")
