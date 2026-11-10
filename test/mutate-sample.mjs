/**
 * sample-thresholds.mjs 的变异验证。
 *
 * 这条判据防的是「门槛失效」：样本太短也照常出分位，用户拿着
 * 一个没有统计意义的百分位当结论。变异要还原的正是这个缺陷形态。
 *
 * 隔离：变异只发生在临时副本，源包全程只读，结束 SHA-256 自证未改动。
 */
import { createHash } from 'node:crypto'
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
const root = dirname(here)
const TEST = join(here, 'sample-thresholds.mjs')
const ASSET = join(root, 'assets', 'valuation', 'scripts', 'analysis.py')

const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')
const baseline = { asset: sha(ASSET) }

function makeCopy() {
  const dir = mkdtempSync(join(tmpdir(), 'a-share-sampmut-'))
  cpSync(root, dir, {
    recursive: true,
    filter: (src) => !/node_modules|__pycache__|\.git[\\/]/.test(src),
  })
  return dir
}

const runTest = (pkgRoot) =>
  spawnSync(process.execPath, [TEST, pkgRoot], { encoding: 'utf8', cwd: pkgRoot })

const assetOf = (dir) => join(dir, 'assets', 'valuation', 'scripts', 'analysis.py')

const mutations = [
  {
    id: 'M1',
    desc: 'MIN_SAMPLES 改成 0 —— 样本多短都出分位（门槛失效的原始缺陷形态）',
    expect: '本应拒绝出分位',
    apply(dir) {
      const p = assetOf(dir)
      let s = readFileSync(p, 'utf8')
      s = s.replace(/^MIN_SAMPLES = 250.*$/m, 'MIN_SAMPLES = 0')
      writeFileSync(p, s, 'utf8')
    },
  },
  {
    id: 'M2',
    desc: 'RECOMMENDED_SAMPLES 改成 250 —— 「仅供参考」永不触发，短样本被当成正常',
    expect: '仅供参考',
    apply(dir) {
      const p = assetOf(dir)
      let s = readFileSync(p, 'utf8')
      s = s.replace(/^RECOMMENDED_SAMPLES = 750.*$/m, 'RECOMMENDED_SAMPLES = 250')
      writeFileSync(p, s, 'utf8')
    },
  },
]

let killed = 0
let survived = 0
console.log('sample-thresholds 判据变异验证（每条都必须让判据变红）\n')

for (const m of mutations) {
  const dir = makeCopy()
  try {
    m.apply(dir)
    const r = runTest(dir)
    const out = (r.stdout || '') + (r.stderr || '')
    const red = r.status !== 0
    const rightReason = out.includes(m.expect)
    if (red) {
      killed++
      console.log(`  ✔ ${m.id} ${m.desc}${rightReason ? '' : `  （红，但未见「${m.expect}」，请人工核）`}`)
    } else {
      survived++
      console.log(`  ✘ ${m.id} ${m.desc} —— 判据仍全绿，它是恒真的`)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

const untouched = sha(ASSET) === baseline.asset
console.log(`\n击杀 ${killed}，存活 ${survived}`)
console.log(
  untouched
    ? `源文件自证：analysis.py ${baseline.asset.slice(0, 12)}… 未改动 ✔`
    : '源文件自证：✘ 源文件被改动，本次变异结论作废',
)
process.exit(survived === 0 && untouched ? 0 : 1)
