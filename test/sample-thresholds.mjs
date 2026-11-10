/**
 * 样本量门槛：契约承诺的三档行为必须真的生效。
 *
 * 契约（`a_share_valuation_contract` 打印的那段，用户据此准备数据）说的是：
 *   · 有效正样本 < 250 个交易日  → 不出分位
 *   · 250 ~ 749                  → 出分位，但标注「仅供参考」
 *   · >= 750                     → 正常出分位
 *
 * 为什么值得单独钉住：
 *   这是本插件与「普通估值计算器」的核心差别之一 —— 样本太短时，
 *   分位数看起来一样精确（比如 99.8%），但那个数字没有统计意义。
 *   250 个点算出的「历史 99.8% 分位」和 750 个点算出的不是一回事，
 *   用户看不出差别，就会拿它当结论。
 *
 * 判据锚在哪里（这一步很容易写错，本文件第一版就写错了）：
 *   ✗ 锚「有没有报错」—— n=100 时不报错，属于正常返回，看起来通过了，
 *     而实际上要检查的是「分位到底有没有算出来」。
 *   ✓ 锚「结果里有没有出现分位数值」+「有没有『仅供参考』」。
 *   恒真判据在这里特别容易发生：`html.includes('分位')` 恒真，
 *   因为页面说明文字里本身就有「分位」两个字。
 */
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const argRoot = process.argv[2]
const pluginRoot =
  argRoot && existsSync(join(resolve(argRoot), 'index.js')) ? resolve(argRoot) : dirname(here)

let pass = 0
let fail = 0
const ok = (m) => { pass++; console.log(`  ok  ${m}`) }
const bad = (m, d) => { fail++; console.log(`  FAIL ${m}`); if (d) console.log(d) }

const BASE = Date.UTC(2025, 0, 1)
const mkHistory = (n) =>
  Array.from({ length: n }, (_, i) => ({
    date: new Date(BASE + i * 864e5).toISOString().slice(0, 10),
    pe_ttm: 20 + i * 0.01,
    pb_mrq: 5 + i * 0.001,
  }))

// 判据：结果里真的出现了分位读数（形如「分位 47.2%」）。
// 不能用 includes('分位')——页面上「分位」二字恒在。
const PCT_SHOWN = /分位\s+[\d.]+%/g
const ADVISORY = /仅供参考/
const REFUSED = /不足以构成历史分位/

async function main() {
  const mod = await import(pathToFileURL(join(pluginRoot, 'index.js')).href)
  const def = mod.default ?? mod
  const tools = []
  await def.apply(
    {
      logger: () => ({ info() {}, warn() {}, error() {} }),
      effect: (fn) => { fn(); return () => {} },
      tools: { register: (d) => { tools.push(d); return () => {} } },
      skills: { register: () => () => {} },
    },
    {},
  )
  const t = tools.find((x) => x.name === 'a_share_valuation_checkup')
  if (!t) {
    bad('估值工具没有注册上')
    return
  }

  const run = async (n) =>
    String(await t.execute({ data: { code: '600519.SH', source: '样本门槛回归测试', history: mkHistory(n) } }))

  // ---- 门槛下：拒绝出分位 ----
  for (const n of [100, 249]) {
    const s = await run(n)
    const shown = PCT_SHOWN.test(s); PCT_SHOWN.lastIndex = 0
    const refused = REFUSED.test(s)
    if (refused && !shown) ok(`n=${n}（<250）：不出分位，并说明原因`)
    else bad(`n=${n}（<250）本应拒绝出分位`, `      refused=${refused} pctShown=${shown}\n      ${s.split('\n')[0]}`)
  }

  // ---- 门槛内：出分位但标注仅供参考 ----
  for (const n of [250, 500, 749]) {
    const s = await run(n)
    const shown = PCT_SHOWN.test(s); PCT_SHOWN.lastIndex = 0
    const advisory = ADVISORY.test(s)
    if (shown && advisory) ok(`n=${n}（250–749）：出分位且标注「仅供参考」`)
    else bad(`n=${n}（250–749）本应出分位并标注仅供参考`, `      pctShown=${shown} advisory=${advisory}`)
  }

  // ---- 达标：出分位且不再降级 ----
  const s750 = await run(750)
  const shown750 = PCT_SHOWN.test(s750); PCT_SHOWN.lastIndex = 0
  const advisory750 = ADVISORY.test(s750)
  if (shown750 && !advisory750) ok('n=750（达标）：出分位且不带「仅供参考」')
  else bad('n=750 本应正常出分位且不降级', `      pctShown=${shown750} advisory=${advisory750}`)
}

await main()
console.log(`\n${fail === 0 ? '通过' : '失败'}：${pass} 通过，${fail} 失败`)
process.exit(fail === 0 ? 0 : 1)
