# -*- coding: utf-8 -*-
"""A 股估值分位体检 · 数据装载层（能力型，零网络）

═══════════════════════════════════════════════════════════════════
架构修正（2026-11-10）：本技能不提供、不抓取、不转发任何行情或估值数据。
═══════════════════════════════════════════════════════════════════

数据由调用方（用户的智能体 / 用户本人）通过其**自有数据源**取得后，
按本模块定义的契约传入。原因有两条，都不是口味问题：

1) **合规**。平台「股票信息服务平台」类目要求「增值电信业务经营许可证」
   + 深交所《专有信息经营许可合同》+ 上交所《证券信息经营许可合同》，
   个人主体拿不到。技能若自行抓取证券数据并交付给用户，就是以个人身份
   从事需要许可的证券信息服务 —— 这不只是过不了审，是本来就不该做。

2) **产品边界**。取数能力已有专门技能承担（如 tushare-data / common 数据网关，
   凭据存在**用户自己**的 secrets.env 里）。本技能只负责「算对、校验、出成品」。

⚠️ 本模块原先直接请求东方财富 datacenter 接口（`RPT_VALUEANALYSIS_DET`）。
   那个实现与上架定位冲突，已整体删除，改为下述双通道输入：

     通道 A  CSV   —— 只有估值历史（4 列），最轻
     通道 B  JSON  —— 估值历史 + 行业横截面 + 元信息，最全

两条通道都**不联网**，可离线审计：

    grep -Ei "urllib|requests|socket|http|tushare|akshare" scripts/*.py

应当零命中（注释与文档串里的说明除外，测试会先剥离注释再判）。
"""
from __future__ import annotations

import csv as _csv
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════════
# 规范字段名
# ══════════════════════════════════════════════════════════════════
# 内部一律用大写规范名（与上游数据源的常见写法一致），
# 这样换数据源（Tushare / 东财 / 自建 / 手工整理）不需要改分析层。
FIELD_SPEC: Dict[str, List[str]] = {
    "date": ["date", "trade_date", "TRADE_DATE", "end_date", "日期",
             "交易日期", "估值日期", "数据日期"],
    "pe_ttm": ["pe_ttm", "PE_TTM", "pe", "PE", "市盈率", "市盈率ttm",
               "市盈率(TTM)", "市盈率TTM", "滚动市盈率"],
    "pb_mrq": ["pb_mrq", "PB_MRQ", "pb", "PB", "市净率", "市净率mrq",
               "市净率(MRQ)", "市净率MRQ"],
    "ps_ttm": ["ps_ttm", "PS_TTM", "ps", "PS", "市销率", "市销率ttm",
               "市销率(TTM)", "市销率TTM", "滚动市销率"],
}

# 规范输出键（分析层按这三个大写键取值）
OUT_FIELDS = {"pe_ttm": "PE_TTM", "pb_mrq": "PB_MRQ", "ps_ttm": "PS_TTM"}
METRIC_KEYS = tuple(OUT_FIELDS)

_NULLISH = {"", "-", "--", "—", "none", "null", "nan", "n/a", "na",
            "无", "无数据", "缺失", "不适用"}


class InputError(RuntimeError):
    """输入契约不满足。绝不静默用默认值兜底——那会让用户拿到一份错报告。"""


# ══════════════════════════════════════════════════════════════════
# 代码归一化（纯函数，不猜映射）
# ══════════════════════════════════════════════════════════════════
def normalize_code(code: str) -> str:
    """'600519' / 'sh600519' / '600519.SH' / '600519_SH' -> '600519.SH'

    这是**纯字符串变换**，只决定报告文件名与标题怎么显示，
    不用于查询任何接口——本技能不发请求。

    北交所注意：老代码段（430xxx / 830xxx）已迁移到 920xxx。
    这里**不猜映射**：猜错会张冠李戴，宁可原样保留让用户自己确认。

    ⚠️ 两种常见写法都要认（2026-11-10 修）：
      - `600519.SH`  点号，Tushare 接口口径
      - `600519_SH`  下划线，**Tushare 导出文件名口径**，也是本技能
                     CSV 通道从文件名推断时最常遇到的形态
    早先只认点号，下划线形态会走到「按首位数字猜市场」的兜底，
    于是 `600519_SH` 变成 `600519_SH.SH`、`000001_SZ` 更糟——
    被猜成 `000001_SZ.SH`，**把深市标成沪市**。两者都不报错，只是错。
    """
    c = str(code or "").strip().upper().replace(" ", "")
    if not c:
        return ""

    # 分隔符统一：下划线 / 点号都当作「代码 + 市场」的分隔
    for sep in (".", "_"):
        if sep in c:
            num, mkt = c.split(sep, 1)
            num, mkt = num.strip(), mkt.strip()
            # 只接受「6 位数字 + 已知市场后缀」，其余原样返回，不硬拼
            if num.isdigit() and len(num) == 6 and mkt in ("SH", "SZ", "BJ"):
                return f"{num}.{mkt}"
            return c

    if c.startswith(("SH", "SZ", "BJ")) and c[2:].isdigit() and len(c[2:]) == 6:
        return f"{c[2:]}.{c[:2]}"
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("4", "8", "9")):
        return f"{c}.BJ"
    return f"{c}.SH"


BSE_LEGACY_HINT = (
    "北交所部分证券已启用新代码段（920xxx）。若你用的是 430xxx / 830xxx 老代码，"
    "请先到交易所确认其现行代码；本技能不会替你猜映射。"
)


# ══════════════════════════════════════════════════════════════════
# 基础解析
# ══════════════════════════════════════════════════════════════════
def to_number(v: Any) -> Optional[float]:
    """把各种脏形态转成 float；无法解析返回 None。

    必须处理的真实形态（来自用户手工整理或不同数据源）：
      "1,234.56" / "１２３"（全角）/ "" / "-" / "—" / "None" / "nan" / 布尔
    返回 None 而不是 0 —— 0 是有效数值，填 0 冒充缺失会直接改变分位结论。
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
    for suf in ("倍", "元", "%", "％"):
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
    # 大小写/首尾空格不敏感兜底（不同源写法差异大）
    lowered = {str(k).strip().lower(): k for k in row}
    for alias in FIELD_SPEC.get(canon, []):
        k = lowered.get(alias.lower())
        if k is not None and row[k] is not None:
            return row[k]
    return None


def normalize_date(v: Any) -> Optional[str]:
    """'20261109' / '2026-11-09 00:00:00' / '2026/11/9' -> '2026-11-09'。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


# ══════════════════════════════════════════════════════════════════
# 行规范化
# ══════════════════════════════════════════════════════════════════
def normalize_row(row: Dict[str, Any], where: str = "") -> Dict[str, Any]:
    """一行原始数据 -> 一行规范数据（大写键，未出现的字段为 None）。"""
    d = normalize_date(pick(row, "date"))
    if d is None:
        raise InputError(
            f"{where}缺少可解析的日期（date / 交易日期 / TRADE_DATE）。"
            f"估值分位的每一步都按日期排序，没有日期的行无法参与计算，"
            f"必须补上或删除该条。"
        )
    out: Dict[str, Any] = {"TRADE_DATE": d}
    for canon, okey in OUT_FIELDS.items():
        out[okey] = to_number(pick(row, canon))
    return out


def normalize_peer_rows(rows: Any, where: str = "industry_rows") -> List[Dict[str, Any]]:
    """同业横截面：**不是时间序列**，每天一行/每家一行，不需要 date。

    这一点与 history 不同，不能共用 normalize_rows——
    早前把行业快照也过 normalize_rows，会因为「缺少日期」直接报错，
    把一份完全可用的数据判成非法。同业快照的日期由外层 trade_date 统一给定。
    """
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise InputError(
            f"{where} 必须是数组（每家同业一条记录），"
            f"实际收到 {type(rows).__name__}")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise InputError(f"{where}[{i}] 不是对象")
        item: Dict[str, Any] = {
            "SECUCODE": str(r.get("code") or r.get("SECUCODE") or "").strip(),
            "SECURITY_NAME_ABBR": str(r.get("name") or
                                      r.get("SECURITY_NAME_ABBR") or "").strip(),
        }
        for canon, okey in OUT_FIELDS.items():
            item[okey] = to_number(pick(r, canon))
        out.append(item)
    return out


def normalize_rows(rows: Any, where: str = "history") -> List[Dict[str, Any]]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise InputError(
            f"{where} 必须是数组（每个交易日一条记录），"
            f"实际收到 {type(rows).__name__}")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise InputError(f"{where}[{i}] 不是对象")
        out.append(normalize_row(r, where=f"{where}[{i}] "))
    # 按日期倒序（最新在前）。调用方不必自己排序，也**不允许**依赖行序：
    # 分析层把 history[0] 当作「当前值」，行序错就会取错当前值。
    out.sort(key=lambda x: x["TRADE_DATE"], reverse=True)
    return out


# ══════════════════════════════════════════════════════════════════
# 通道 A：CSV（只有估值历史）
# ══════════════════════════════════════════════════════════════════
def load_csv(path: str) -> Dict[str, Any]:
    """读取用户自带的估值历史 CSV，**全程不联网**。

    期望表头（中英文、大小写、顺序都不敏感）：
        date, pe_ttm, pb_mrq, ps_ttm        必需 date，其余至少一列
    日期支持 2026-11-09 / 2026/11/09 / 20261109。

    CSV 不放行业横截面（那需要同行业全量快照），
    自带数据时报告会明确说明「同业对比已跳过」。
    """
    if not os.path.exists(path):
        raise InputError(f"找不到输入文件：{path}")

    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            if not reader.fieldnames:
                raise InputError("CSV 缺少表头")
            header = {(c or "").strip(): c for c in reader.fieldnames}
            # 用别名表判断「这一列是什么」，而不是要求列名完全一致
            probe = {k: k for k in header}
            if pick(probe, "date") is None:
                raise InputError(
                    "CSV 必须包含日期列（date / 交易日期 / TRADE_DATE 任一写法）")
            if all(pick(probe, k) is None for k in METRIC_KEYS):
                raise InputError(
                    "CSV 至少需要 pe_ttm / pb_mrq / ps_ttm 中的一列"
                    "（也可写 市盈率 / 市净率 / 市销率）")
            raw = []
            for rec in reader:
                if not any(str(v or "").strip() for v in rec.values()):
                    continue          # 整行空白
                raw.append(rec)
    except UnicodeDecodeError as exc:
        raise InputError(
            f"{path} 不是 UTF-8 编码：{exc}。请另存为「CSV UTF-8」后重试。"
        ) from exc

    rows = normalize_rows(raw, where="CSV")
    if not rows:
        raise InputError("CSV 里没有有效数据行")

    latest = rows[0]
    # 只要求「至少有一列给出」——用户可能只想看 PB（亏损股只给市净率），
    # 强制三列齐全属于替用户决定该看哪个指标，正是本技能反对的做法。
    # 报告会逐个指标判断「有数据/无数据」，而不是在这里一刀切拒绝。
    given = [k for k in METRIC_KEYS if latest.get(OUT_FIELDS[k]) is not None]
    if not given:
        raise InputError(
            f"CSV 最新一行（{latest['TRADE_DATE']}）的 PE/PB/PS 三个值全为空。"
            f"当前值缺失时无法给出任何分位——请补上最新一期数据，"
            f"或删除该行让上一期成为最新。")

    # 与 JSON 通道共用同一判据：三列正值全为 0 时不给报告。
    require_actionable(rows)

    base = os.path.splitext(os.path.basename(path))[0]
    code = normalize_code(base) if re.match(r"^[A-Za-z]{0,2}\d{6}", base) else ""
    return {
        "code": code or base or "CSV",
        "name": "本地数据",
        "industry": "",
        "board_code": "",
        "trade_date": latest["TRADE_DATE"],
        "history": rows,
        "industry_rows": [],
        "source": "用户自带 CSV 文件",
        "offline": True,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def positive_sample_counts(history: List[Dict[str, Any]]) -> Dict[str, int]:
    """统计每个指标的正值样本数（分位基准的大小）。"""
    return {k: sum(1 for r in history if (r.get(OUT_FIELDS[k]) or 0) > 0)
            for k in METRIC_KEYS}


def require_actionable(history: List[Dict[str, Any]]) -> None:
    """三列正值全为 0 时直接报错，不生成报告。

    ⚠️ 这条判据必须**两个通道共用**。
    2026-11-10 实测发现不一致：同一份「PE 全为负、PB/PS 为空」的数据，
    CSV 通道一路跑通、生成了一份三个指标全是「不适用」的报告，
    而 JSON 通道直接拒绝。同一份内容两种结局，用户会以为工具在抽风。

    为什么选「拒绝」而不是「给空报告」：
    分位的全部前提是「有一段可比较的正值序列」。三列一个正值都没有时，
    本工具算不出任何分位，交付一份全是「不适用」的报告等于让用户
    自己发现它什么也没做——报错更早、更诚实，也更好改。
    """
    counts = positive_sample_counts(history)
    if max(counts.values()) == 0:
        raise InputError(
            "所提供的 PE/PB/PS 正值样本数为 0。"
            "分位只能基于正倍数计算：负 PE 代表公司亏损，该指标此刻失效，"
            "把它当普通数值排序会得出「极度低估」这种反向结论。"
            "请至少提供 250 个交易日的有效正值（某一项即可）。")


# ══════════════════════════════════════════════════════════════════
# 通道 B：JSON（估值历史 + 行业横截面 + 元信息）
# ══════════════════════════════════════════════════════════════════
def load_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把一个已解析的 dict 规范化为分析层可用的结构。"""
    if not isinstance(payload, dict):
        raise InputError("输入根节点必须是 JSON 对象")

    code = normalize_code(payload.get("code") or "")
    if not code:
        raise InputError("缺少 code 字段（如 600519.SH）")

    source = str(payload.get("source") or "").strip()
    if not source:
        # 强制披露数据来源：报告要把这句话原样印出来。
        # 没有来源的报告在合规上无法自证「数据由用户提供」，也不利用户自查。
        raise InputError(
            "缺少 source 字段。必须写明数据来源（例如「Tushare Pro，用户自有凭据」"
            "或「用户手工整理自公开年报与行情软件」），报告会原样展示该说明。"
        )

    history = normalize_rows(payload.get("history"), where="history")
    if not history:
        raise InputError(
            "history 为空。估值分位需要一段历史序列，单点数值无法给出「处于历史什么位置」——"
            "这是本技能与普通估值计算器的根本区别，不能用默认值补齐。")

    # ── 关键校验：正样本数够不够（与 CSV 通道共用同一判据）──
    # 提前判一次的理由不是重复劳动，而是**错误信息要说人话**：
    # 分析层那一刻只能说「样本不足」，而用户真正需要知道的是
    # 「你只给了 30 行，但分位至少需要 250 个交易日」。
    require_actionable(history)

    trade_date = str(payload.get("trade_date") or "").strip()[:10]
    if not trade_date:
        trade_date = history[0]["TRADE_DATE"]

    industry_rows = normalize_peer_rows(payload.get("industry_rows")) \
        if payload.get("industry_rows") else []

    name = str(payload.get("name") or "").strip()
    return {
        "code": code,
        "name": name or code,
        "industry": str(payload.get("industry") or "").strip(),
        "board_code": str(payload.get("board_code") or "").strip(),
        "trade_date": trade_date,
        "history": history,
        "industry_rows": industry_rows,
        "source": source,
        "offline": bool(payload.get("offline")),
        "fetched_at": str(payload.get("fetched_at") or
                          time.strftime("%Y-%m-%d %H:%M:%S")),
    }


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise InputError(f"数据文件不存在：{path}")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} 不是合法 JSON：{exc}") from exc
    return load_payload(payload)


def load_file(path: str) -> Dict[str, Any]:
    """按扩展名分派。.json → 完整契约；.csv → 只有估值历史。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".json",):
        return load_json(path)
    if ext in (".csv", ".txt", ""):
        return load_csv(path)
    raise InputError(
        f"不支持的文件类型 {ext!r}。本技能只接受 .csv（估值历史）"
        f"或 .json（完整契约）。")


# ══════════════════════════════════════════════════════════════════
# 自描述
# ══════════════════════════════════════════════════════════════════
SPEC_TEXT = """本技能不提供数据，只做计算。请先按下面的契约准备好数据。

════════════════════════════════════════════════════════════════
两种输入，按需要选
════════════════════════════════════════════════════════════════

  A) CSV  —— 只要估值历史，最省事
       date,pe_ttm,pb_mrq,ps_ttm
       2026-11-09,20.5,6.1,9.2
       2026-11-08,20.1,6.0,9.0

  B) JSON —— 估值历史 + 行业横截面 + 元信息，最全

════════════════════════════════════════════════════════════════
JSON 契约（标准 JSON，UTF-8，不带注释 —— 可直接复制使用）
════════════════════════════════════════════════════════════════

{
  "code": "600519.SH",
  "name": "贵州茅台",
  "industry": "白酒",
  "source": "Tushare Pro（用户自有凭据）",
  "trade_date": "2026-11-09",
  "history": [
    {"date": "2026-11-09", "pe_ttm": 20.5, "pb_mrq": 6.1, "ps_ttm": 9.2},
    {"date": "2026-11-08", "pe_ttm": 20.1, "pb_mrq": 6.0, "ps_ttm": 9.0}
  ],
  "industry_rows": [
    {"code": "000858.SZ", "name": "五粮液", "pe_ttm": 18.0, "pb_mrq": 4.2},
    {"code": "000568.SZ", "name": "泸州老窖", "pe_ttm": 22.0, "pb_mrq": 7.5}
  ]
}

字段说明：
  code     标的代码，必填
  source   数据来源 —— 报告会原样印出这句话，没有它无法自证数据由谁提供
  history  估值历史，必填，**建议 750 个交易日以上**（约 3 年）
  name / industry / trade_date   选填
  industry_rows  选填，同一交易日的同行业估值快照；少于 3 家时不出横截面

════════════════════════════════════════════════════════════════
每一行的字段（中英文、大小写都认，别名见 FIELD_SPEC）
════════════════════════════════════════════════════════════════

必有：date   交易日，如 "2026-11-09" / "20261109" / "2026-11-09 00:00:00"
              也可写 交易日期 / TRADE_DATE

pe_ttm   滚动市盈率   别名 PE_TTM / pe / 市盈率 / 滚动市盈率
pb_mrq   市净率       别名 PB_MRQ / pb / 市净率
ps_ttm   滚动市销率   别名 PS_TTM / ps / 市销率

三列至少给一列。空值写 null 或留空，**不要写 0** ——
0 会被当成有效数值参与排序，直接改变分位结论。
亏损公司的 PE 是负数，**照原样抄**，不要自己改成 0 或删掉：
本技能会把它识别为「该指标失效」并从分位基准里剔除。

════════════════════════════════════════════════════════════════
样本量门槛（这是拒绝而不是警告）
════════════════════════════════════════════════════════════════

  有效正样本 < 250 个交易日   不出分位（约 1 年，再少没有统计意义）
  有效正样本 < 750 个交易日   出分位但标注「仅供参考」
  三列正值全为 0              直接报错，不生成报告

════════════════════════════════════════════════════════════════
不接受的字段
════════════════════════════════════════════════════════════════

  收盘价 / 开盘价 / 最高价 / 最低价 / 成交量 / 成交额 / 涨跌幅
  本技能是估值分析工具，不是行情工具。给了也不会被读取，也不会出现在报告里。
"""


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] in ("--spec", "-h", "--help"):
        print(SPEC_TEXT)
        sys.exit(0)
    d = load_file(sys.argv[1])
    print(f"{d['code']} {d['name']} [{d['industry'] or '-'}]  来源 {d['source']}")
    print(f"最新 {d['trade_date']} · 历史 {len(d['history'])} 行"
          f" · 同业 {len(d['industry_rows'])} 家")
