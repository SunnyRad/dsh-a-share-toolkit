/**
 * pack-gate.mjs 的变异验证。
 *
 * 规矩：判据没有被变异钉住 = 没有这条判据。
 * 每一条变异体都必须让 pack-gate 变红；存活一条，说明那条判据是恒真的。
 *
 * 隔离：变异只发生在临时副本里，源包全程只读，结束时用 SHA-256 自证未改动。
 * 理由是本目录多会话共享——早前 mutate.py 直接改源文件，别人同时跑测试
 * 读到的是变异体，「实现真有 bug」与「变异正在跑」现象完全一致。
 */
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = dirname(here)
const GATE = join(here, 'pack-gate.mjs')

const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')
const baseline = {
  pkg: sha(join(root, 'package.json')),
  gate: sha(GATE),
}

/** 复制一份可变异的最小包（跳过 node_modules / __pycache__ 体积与噪声）。 */
function makeCopy() {
  const dir = mkdtempSync(join(tmpdir(), 'a-share-mut-'))
  cpSync(root, dir, {
    recursive: true,
    filter: (src) => !/node_modules|__pycache__|\.git[\\/]/.test(src),
  })
  return dir
}

const runGate = (cwd) =>
  spawnSync(process.execPath, [GATE, cwd], { encoding: 'utf8', cwd })

/** 改 package.json 的 files 数组。 */
function setFiles(dir, files) {
  const p = join(dir, 'package.json')
  const j = JSON.parse(readFileSync(p, 'utf8'))
  j.files = files
  writeFileSync(p, JSON.stringify(j, null, 2) + '\n', 'utf8')
}

const mutations = [
  {
    id: 'M1',
    desc: 'files 去掉 !**/*.pyc 负向排除 —— pyc 会混进 tarball',
    apply(dir) {
      const p = join(dir, 'package.json')
      const j = JSON.parse(readFileSync(p, 'utf8'))
      j.files = j.files.filter((f) => !f.includes('pyc') && !f.includes('__pycache__'))
      writeFileSync(p, JSON.stringify(j, null, 2) + '\n', 'utf8')
      // 造出真实产物：跑过一次 Python 就会有 __pycache__
      mkdirSync(join(dir, 'assets/valuation/scripts/__pycache__'), { recursive: true })
      writeFileSync(join(dir, 'assets/valuation/scripts/__pycache__/analysis.cpython-312.pyc'), 'x')
    },
    expect: '发布包含',
  },
  {
    id: 'M2',
    desc: 'files 漏掉 lib/*.js —— 执行层缺失，包变小但闸全绿',
    apply(dir) {
      const p = join(dir, 'package.json')
      const j = JSON.parse(readFileSync(p, 'utf8'))
      j.files = j.files.filter((f) => f !== 'lib/*.js')
      writeFileSync(p, JSON.stringify(j, null, 2) + '\n', 'utf8')
    },
    expect: '缺少',
  },
  {
    id: 'M3',
    desc: 'files 漏掉 skills —— 技能面为空，装上去没有技能',
    apply(dir) {
      const p = join(dir, 'package.json')
      const j = JSON.parse(readFileSync(p, 'utf8'))
      j.files = j.files.filter((f) => f !== 'skills')
      writeFileSync(p, JSON.stringify(j, null, 2) + '\n', 'utf8')
    },
    expect: '缺少',
  },
  {
    id: 'M4',
    desc: 'files 漏掉 cordis.patch.yml —— bundle 无法挂载',
    apply(dir) {
      const p = join(dir, 'package.json')
      const j = JSON.parse(readFileSync(p, 'utf8'))
      j.files = j.files.filter((f) => f !== 'cordis.patch.yml')
      writeFileSync(p, JSON.stringify(j, null, 2) + '\n', 'utf8')
    },
    expect: '缺少',
  },
  {
    id: 'M5',
    desc: 'files 收进整个 test/ —— 测试代码进运行时包',
    apply(dir) {
      const p = join(dir, 'package.json')
      const j = JSON.parse(readFileSync(p, 'utf8'))
      j.files = j.files.filter((f) => f !== 'test')
      j.files.push('test')
      writeFileSync(p, JSON.stringify(j, null, 2) + '\n', 'utf8')
    },
    expect: '不该发布',
  },
]

let killed = 0
let survived = 0

console.log('pack-gate 变异验证（每条都必须让闸变红）\n')

for (const m of mutations) {
  const dir = makeCopy()
  try {
    m.apply(dir)
    const r = runGate(dir)
    const out = (r.stdout || '') + (r.stderr || '')
    const red = r.status !== 0
    // 不仅要红，还要因为「预期的那个原因」红——否则是别的缺陷把结论带偏了。
    const rightReason = out.includes(m.expect)
    if (red && rightReason) {
      killed++
      console.log(`  ✔ ${m.id} ${m.desc}`)
    } else if (red) {
      killed++
      console.log(`  ✔ ${m.id} ${m.desc}  （红，但未见「${m.expect}」，请人工核）`)
    } else {
      survived++
      console.log(`  ✘ ${m.id} ${m.desc} —— 闸仍全绿，判据是恒真的`)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

// ---- 源文件自证 ----
const after = {
  pkg: sha(join(root, 'package.json')),
  gate: sha(GATE),
}
const untouched = after.pkg === baseline.pkg && after.gate === baseline.gate
console.log(`\n击杀 ${killed}，存活 ${survived}`)
console.log(
  untouched
    ? `源文件自证：package.json ${baseline.pkg.slice(0, 12)}… / pack-gate.mjs ${baseline.gate.slice(0, 12)}… 未改动 ✔`
    : '源文件自证：✘ 源文件被改动，本次变异结论作废',
)

process.exit(survived === 0 && untouched ? 0 : 1)
