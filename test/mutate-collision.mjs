/**
 * collision.mjs 的变异验证：证明这条判据在「未修复的实现」上真的会红。
 *
 * 为什么要这一步：
 *   一条判据在自己刚修好的代码上通过，只证明「实现能过用例」，
 *   不证明「用例真在检查那件事」。把修法撤掉，判据必须变红 ——
 *   否则将来有人改回共用目录，这条测试会安静地继续绿。
 *
 * 隔离：变异只发生在临时副本里，源包全程只读，结束时 SHA-256 自证未改动。
 *   本目录多会话共享，直接改源文件会让别人同时读到的测试结果是变异体。
 */
import { createHash } from 'node:crypto'
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
const root = dirname(here)
const TEST = join(here, 'collision.mjs')

const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')
const INDEX = join(root, 'index.js')
const baseline = { index: sha(INDEX) }

function makeCopy() {
  const dir = mkdtempSync(join(tmpdir(), 'a-share-colmut-'))
  cpSync(root, dir, {
    recursive: true,
    filter: (src) => !/node_modules|__pycache__|\.git[\\/]/.test(src),
  })
  return dir
}

const runTest = (pkgRoot) =>
  spawnSync(process.execPath, [TEST, pkgRoot], { encoding: 'utf8', cwd: pkgRoot })

const mutations = [
  {
    id: 'M1',
    desc: '两个产品改回共用 outDir —— 同日同名文件互相覆盖（本缺陷的原始形态）',
    expect: '覆盖',
    apply(dir) {
      const p = join(dir, 'index.js')
      let s = readFileSync(p, 'utf8')
      // 把按产品分目录，改回共用目录
      s = s.replace(
        'outDir: join(outputDir, product.slug),',
        'outDir: outputDir,',
      )
      writeFileSync(p, s, 'utf8')
    },
  },
  {
    id: 'M2',
    desc: '两个产品用了同一个 slug —— 分目录等于没分',
    expect: '同一个报告路径',
    apply(dir) {
      const p = join(dir, 'index.js')
      let s = readFileSync(p, 'utf8')
      s = s.replace("slug: 'financial',", "slug: 'valuation',")
      writeFileSync(p, s, 'utf8')
    },
  },
]

let killed = 0
let survived = 0
console.log('collision 判据变异验证（每条都必须让判据变红）\n')

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

const untouched = sha(INDEX) === baseline.index
console.log(`\n击杀 ${killed}，存活 ${survived}`)
console.log(
  untouched
    ? `源文件自证：index.js ${baseline.index.slice(0, 12)}… 未改动 ✔`
    : '源文件自证：✘ 源文件被改动，本次变异结论作废',
)
process.exit(survived === 0 && untouched ? 0 : 1)
