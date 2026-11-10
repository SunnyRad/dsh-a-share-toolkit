# dsh-a-share-toolkit

A 股估值分位体检 + 个股财务体检，做成 4 个可直接调用的 DeepSeek Harness 工具。

**能力型，不取数。** 数据由调用方自带（用户自己的 Tushare / 问财 / 导出的 CSV），
本插件不抓取、不转发、不缓存任何行情或估值数据，全程离线，纯 Python 标准库。

## 装

```sh
dsh plugin --profile <name> add github:SunnyRad/dsh-a-share-toolkit
```

或者用 `plugin_manager install_bundle`，`target` 传包目录绝对路径。

## 提供什么

| 工具 | 作用 |
|---|---|
| `a_share_valuation_checkup` | 把 PE/PB/PS 历史序列算成历史分位，出 HTML 报告 |
| `a_share_valuation_contract` | 打印估值分析要求的输入契约 |
| `a_share_financial_checkup` | 读三张报表，出财务体检 HTML 报告 |
| `a_share_financial_contract` | 打印财务体检要求的输入契约 |

外加 2 个 runtime skill：`a-share-valuation-percentile`、`a-share-financial-checkup`。

## 为什么不是又一个行情看板

生态里 A 股相关插件大多自带行情数据与看板。这个插件反过来——**它一个数据都不取**，
只做计算。原因是它要解决的是「算错」，不是「没数据」：

| 会让结论反转的陷阱 | 普通实现的默认行为 | 本插件的处理 |
|---|---|---|
| 亏损公司的负 PE | 当成「估值极低、历史最低位」 | 判为指标不适用，引导改看 PB |
| 历史样本过短 | 照样出精确分位 | <250 交易日拒绝出分位，<750 标注仅供参考 |
| 周期股 PE 失效 | 永远报 PE | PE 与 PB 分位差 >40pp 判为周期特征，自动切主判断指标 |
| 净利为负时的红旗 | 报「红旗 0 条」= 没问题 | 亏损本身计入红旗 |
| 小基数同比 | 应收 +1327% 报高危 | 占比过低时不计为真缺陷 |
| 金融业毛利率 | `None` 参与格式化直接崩 | 识别行业后跳过不适用指标 |

这些不是设计偏好上的差异，是「同一份数据，两个插件给出方向相反的结论」。

## 数据契约

两个工具都不取数，所以每个都配了一个 `*_contract` 工具，调用它就能打印完整契约。
简要版：

**估值** —— 一段 PE/PB/PS 历史序列，建议 750 个交易日以上：

```json
{
  "code": "600519.SH",
  "source": "Tushare Pro（用户自有凭据）",
  "history": [
    {"date": "2026-11-09", "pe_ttm": 20.5, "pb_mrq": 6.1}
  ]
}
```

亏损公司的 PE 是负数，**照原样抄**，不要改 0 或删掉——本插件会识别为指标失效并剔除。

**财务** —— 利润表 / 资产负债表 / 现金流量表，建议 8 期以上：

```json
{
  "code": "600519.SH",
  "source": "Tushare Pro（用户自有凭据）",
  "unit": "yuan",
  "income": [{"report_date": "2026-09-30", "revenue": 1.231e11, "net_profit": 6.08e10}],
  "balance": [{"report_date": "2026-09-30", "total_assets": 3.0e11, "total_liab": 6.0e10}],
  "cashflow": [{"report_date": "2026-09-30", "cfo": 5.2e10}]
}
```

`source` 必填——报告会原样印出这句话，用它自证数据由谁提供。

## 开发

```powershell
python -X utf8 check.py          # 资产同步闸 + 执行层 + 注册层 + 变异验证
node test/smoke.mjs              # 不依赖真实 profile 的冒烟
```

`check.py` 四层各答一个问题：执行层（Python 跑得通吗）→ 注册层（apply 真注册了吗）
→ 变异（上面两条判据真的会红吗）。只跑前两层等于只看「实现能过用例」。

改动 `assets/` 里的 Python 后必须跑 `python -X utf8 sync_assets.py`——
它按内容哈希核对插件运行时与产品源是否同源，手工拷一次就会让插件钉在旧版本上。

## 边界

- 不提供数据，不内置任何数据源域名。
- 不生成交易建议，输出的是统计口径下的分位与校验结果。
- 报告是单文件自包含 HTML，双击可打开，无外部请求。

## License

MIT
