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
python -X utf8 check.py          # 十二层闸，全跑约 3 分钟
python -X utf8 check.py --fast   # 跳过变异，只跑前八层
node test/smoke.mjs              # 不依赖真实 profile 的冒烟
```

`check.py` 的十二层各答一个问题，顺序就是「先证据、再证伪」：

| 层 | 在防什么 |
|---|---|
| 1 | 插件里的 Python 运行时 == 产品源（按内容哈希），防「手工拷一次就钉在旧版本」 |
| 2–3 | 语法、执行层（真跑 Python、真读产物） |
| 4–5 | 注册逻辑（真 cordis Context）、端到端（按工具 schema 调用，含 `data_path` 文件入口） |
| 6–8 | 发布包闸（tarball 里到底有什么）、产物不串号、样本量门槛三档 |
| 9–12 | 变异：上面每一条判据是不是真的会红 |

只跑前两层等于只看「实现能过用例」，不证明「用例真在检查那条判据」。第 9–12 层是唯一能
证伪这件事的东西——`test/mutate*.mjs` 把缺陷改回原始形态，测试必须红；不红就说明判据是恒真的。

改动 `assets/` 里的 Python 后必须跑 `python -X utf8 sync_assets.py`——
它按内容哈希核对插件运行时与产品源是否同源，手工拷一次就会让插件钉在旧版本上。

## 边界

- 不提供数据，不内置任何数据源域名。
- 不生成交易建议，输出的是统计口径下的分位与校验结果。
- 报告是单文件自包含 HTML，双击可打开，无外部请求。

## 仓库里那些看着像代码但不是的东西

| 路径 | 是什么 |
|---|---|
| `storefront/` | 市场商品页素材：`icon-256.png` 是卡片图标（从 `icon.svg` 渲染，官方只收位图），另两张是报告真实渲染的截图 |
| `screenshots.json` | 截图声明（1–8 张，路径相对该文件）。市场每晚构建时读一次 |
| `assets/` | 插件运行时真正执行的 Python |
| `skills/` | runtime skill 本体 |

`storefront/` 与 `screenshots.json` 都在 `files` 白名单外，不进 npm 包
（发布包闸的 `FORBIDDEN` 与 `REQUIRED` 双向核对着这件事，实包 74.1 KB / 21 条目）。

## License

MIT
