/**
 * 产物不串号：两个产品对同一只股票、同一天会产出**同名文件**。
 *
 * 真实缺陷（2026-11-10 实测确证，已修）：
 *   估值体检的报告名是 `<code>_<数据最新日期>.html`，
 *   财务体检的报告名是 `<code>_<报告期>.html`。
 *   当同一天两者都跑，文件名**完全相同**（如 600519_SH_20250930.html）。
 *   早先两个产品共用同一个 outDir，于是后跑的 `copyFile` 直接把先跑的覆盖掉：
 *
 *     估值 → 7737 字节
 *     财务 → 7174 字节   ← 同一路径，估值那份没了
 *
 *   两份都是真报告、都能打开、都不报错，**只是内容被换掉了**。
 *   工具返回的 `HTML 报告:` 路径也还是同一个，用户拿到的不是自己那次的结果。
 *   这与本目录 AGENTS.md 第四节②记的「产物放共享目录里按文件名挑」是同类，
 *   但那一条修的是「读回串号」，**写回串号没修** —— 这才是本条要钉的。
 *
 * 判据为什么必须区分两份报告的内容，而不是只比路径：
 *   只要两页都含「分位」这个词（财务页也有同业分位），
 *   `html.includes('分位')` 恒真，覆盖了也照样通过 —— 那就是假绿。
 *   能区分的标记是各自独有的：估值页说「历史分位」，财务页说「三表勾稽」。
 *
 * 修法：每个产品落自己的子目录（<outDir>/valuation、<outDir>/financial）。
 * 判据瞄准「后跑的那次有没有毁掉先跑的那份」，不瞄准「代码里有没有 join」。
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const argRoot = process.argv[2]
const pluginRoot =
  argRoot && existsSync(join(resolve(argRoot), 'index.js')) ? resolve(argRoot) : dirname(here)

let pass = 0
let fail = 0
const ok = (m) => { pass++; console.log(`  ok  ${m}`) }
const bad = (m, d) => { fail++; console.log(`  FAIL ${m}`); if (d) console.log(d) }

// 两个产品各自独有的措辞。缺一个就说明这页不是它自己。
const VAL_MARK = /历史分位|估值分位/
const FIN_MARK = /三表勾稽|勾稽校验/

const SAME_DAY = '2025-09-30'

/** 800 个交易日，最后一天正好是 SAME_DAY，保证报告名与财务报告期撞上。 */
function valuationHistory() {
  const hist = []
  for (let i = 799; i >= 0; i--) {
    const d = new Date(Date.UTC(2025, 8, 30) - i * 864e5)
    hist.push({
      date: d.toISOString().slice(0, 10),
      pe_ttm: 20 + 6 * Math.sin(i / 40),
      pb_mrq: 5 + Math.sin(i / 55),
    })
  }
  return hist
}

function loadPlugin() {
  return import(pathToFileURL(join(pluginRoot, 'index.js')).href)
}

/** 桩 ctx：够 apply 注册用。 */
function makeCtx(tools) {
  return {
    logger: () => ({ info() {}, warn() {}, error() {} }),
    effect: (fn) => { fn(); return () => {} },
    tools: { register: (d) => { tools.push(d); return () => {} } },
    skills: { register: () => () => {} },
  }
}

/** 从工具返回的纯文本里取报告路径。 */
function grabHtmlPath(text) {
  const m = String(text).match(/HTML 报告:\s*(.+)/)
  return m ? m[1].trim() : null
}

async function main() {
  const mod = await loadPlugin()
  const def = mod.default ?? mod
  const tools = []
  await def.apply(makeCtx(tools), {})

  const vt = tools.find((t) => t.name === 'a_share_valuation_checkup')
  const ft = tools.find((t) => t.name === 'a_share_financial_checkup')
  if (!vt || !ft) {
    bad('两个体检工具没有都注册上', `      实际注册：${tools.map((t) => t.name).join(', ')}`)
    return
  }
  ok('两个体检工具都已注册')

  const work = mkdtempSync(join(tmpdir(), 'a-share-collide-'))
  try {
    const vf = join(work, 'valuation.json')
    writeFileSync(
      vf,
      JSON.stringify({
        code: '600519.SH',
        name: '贵州茅台',
        industry: '白酒',
        source: '产物串号回归测试用构造数据',
        history: valuationHistory(),
      }),
      'utf8',
    )

    const ff = join(work, 'financial.json')
    writeFileSync(
      ff,
      JSON.stringify({
        code: '600519.SH',
        name: '贵州茅台',
        industry: '白酒',
        source: '产物串号回归测试用构造数据',
        unit: 'yuan',
        income: [{ report_date: SAME_DAY, revenue: 1.2e11, net_profit: 6e10, deduct_net_profit: 5.9e10, operate_cost: 1e10 }],
        balance: [{ report_date: SAME_DAY, total_assets: 3e11, total_liab: 6e10, total_equity: 2.4e11, monetary: 1.5e11, accounts_rece: 3.6e8, inventory: 4.5e10 }],
        cashflow: [{ report_date: SAME_DAY, cfo: 5.2e10, net_invest: -3e9, net_finance: -4.8e10 }],
      }),
      'utf8',
    )

    // ---- 先跑估值 ----
    const vPath = grabHtmlPath(await vt.execute({ data_path: vf }))
    if (!vPath || !existsSync(vPath)) {
      bad('估值体检没有产出 HTML 报告', `      返回路径：${vPath}`)
      return
    }
    const vBody = readFileSync(vPath, 'utf8')
    if (!VAL_MARK.test(vBody)) {
      bad('估值报告里找不到它自己的判别标记', `      期望匹配 ${VAL_MARK}，路径 ${vPath}`)
      return
    }
    ok(`估值报告已产出（${vBody.length} 字节，含「历史分位」）`)

    // ---- 再跑财务：同日同标的，文件名会撞 ----
    const fPath = grabHtmlPath(await ft.execute({ data_path: ff }))
    if (!fPath || !existsSync(fPath)) {
      bad('财务体检没有产出 HTML 报告', `      返回路径：${fPath}`)
      return
    }
    const fBody = readFileSync(fPath, 'utf8')
    if (!FIN_MARK.test(fBody)) {
      bad('财务报告里找不到它自己的判别标记', `      期望匹配 ${FIN_MARK}，路径 ${fPath}`)
      return
    }
    ok(`财务报告已产出（${fBody.length} 字节，含「三表勾稽」）`)

    // ---- 核心判据：估值那份还在不在、还是不是它 ----
    if (vPath === fPath) {
      // 同路径本身不一定错，错的是「后跑的把先跑的毁了」。
      // 但更稳妥的修法是不同目录，所以这里也报出来，让人确认是故意的。
      bad(
        '两个产品写到了同一个报告路径',
        `      路径：${vPath}\n      后跑的那次会覆盖先跑的，内容已经不可靠`,
      )
      return
    }
    ok('两个产品落在不同路径')

    if (!existsSync(vPath)) {
      bad('估值报告在财务体检跑完后消失了', `      原路径：${vPath}`)
      return
    }
    const vAfter = readFileSync(vPath, 'utf8')
    if (!VAL_MARK.test(vAfter) || FIN_MARK.test(vAfter)) {
      bad(
        '估值报告被财务报告覆盖了（后跑毁掉先跑）',
        `      路径：${vPath}\n      覆盖前 ${vBody.length} 字节 → 现在 ${vAfter.length} 字节`,
      )
      return
    }
    ok('估值报告未被财务报告覆盖，内容仍是它自己的')
  } finally {
    rmSync(work, { recursive: true, force: true })
  }
}

await main()
console.log(`\n${fail === 0 ? '通过' : '失败'}：${pass} 通过，${fail} 失败`)
process.exit(fail === 0 ? 0 : 1)
