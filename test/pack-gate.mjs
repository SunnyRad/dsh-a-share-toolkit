/**
 * 发布包闸：`npm pack` 产出的 tarball 里，只许有运行时该有的东西。
 *
 * 为什么需要这一条（真实踩过）：
 *   跑过测试之后磁盘上就有 `__pycache__/*.pyc`，而 `npm pack` 按 `files`
 *   白名单收文件时，`assets` 是目录级条目，目录里的一切都被收进去 ——
 *   包括 7 个 .pyc。tarball 从 130.3 KB 里含 7 个字节码文件，装到别人机器上
 *   就是一批与本机 Python 版本绑定的垃圾，且 `.gitignore` 挡不住它
 *   （npm 不看 .gitignore，只看 files / .npmignore）。
 *
 * 判据瞄准「发布包里到底有什么」，不瞄「package.json 里写了什么」：
 *   files 数组写对了不等于 tarball 干净，中间还有目录展开这一步。
 *
 * 同时钉住另一件事：tarball 里必须**包含**真正要跑的文件。
 *   只查「没有多余文件」会漏掉反向缺陷 —— files 少写一个目录，
 *   包变小、check 全绿，装上去技能面或 assets 面直接空。
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, rmSync, statSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// 可选参数：被测包根目录。变异测试把包复制到临时目录再改，源文件全程只读。
const argRoot = process.argv[2]
const root = argRoot && existsSync(join(resolve(argRoot), 'package.json')) ? resolve(argRoot) : dirname(here)

let pass = 0
let fail = 0
const ok = (msg) => { pass++; console.log(`  ok  ${msg}`) }
const bad = (msg, detail) => { fail++; console.log(`  FAIL ${msg}`); if (detail) console.log(detail) }

// 必须出现：少了任何一类，装上去就是空壳或跑不起来。
const REQUIRED = [
  ['package/package.json', '清单：没有它 npm 装不上'],
  ['package/index.js', '宿主入口：没有它 apply 不会执行'],
  ['package/cordis.patch.yml', 'bundle 补丁行：没有它 dsh 不知道挂哪一行'],
  ['package/lib/python.js', '执行层：解释器探测与脚本调用都在这里'],
  ['package/locale/en.json', '插件管理器的标题/描述'],
  ['package/locale/zh.json', '插件管理器的中文标题/描述'],
  ['package/skills/a-share-valuation-percentile/SKILL.md', '技能面：估值技能本体'],
  ['package/skills/a-share-financial-checkup/SKILL.md', '技能面：财务技能本体'],
  ['package/assets/valuation/scripts/checkup.py', '工具面：估值实际执行的脚本'],
  ['package/assets/financial/scripts/checkup.py', '工具面：财务实际执行的脚本'],
]

// 不许出现：构建产物与开发期资产。
const FORBIDDEN = [
  [/__pycache__/, 'Python 字节码缓存（跑过测试就会生成，与本机 Python 版本绑定）'],
  [/\.pyc$/, 'Python 字节码文件'],
  [/\.pyo$/, 'Python 优化字节码'],
  [/(^|\/)\.mutbak$/, '变异测试残留'],
  [/^package\/test\//, '测试代码不进运行时包'],
  [/^package\/check\.py$/, '开发期闸不进运行时包'],
  [/^package\/sync_assets\.py$/, '开发期同步脚本不进运行时包'],
  [/^package\/\.gitignore$/, '仓库配置不进包'],
  [/^package\/\.npmignore$/, '仓库配置不进包'],
]

const tmp = mkdtempSync(join(tmpdir(), 'a-share-pack-'))
let entries = []
try {
  execFileSync('npm', ['pack', '--pack-destination', tmp, '--silent'], {
    cwd: root,
    stdio: ['ignore', 'ignore', 'pipe'],
    shell: process.platform === 'win32',
  })
  const tgz = readdirSync(tmp).find((f) => f.endsWith('.tgz'))
  if (!tgz) {
    bad('npm pack 没有产出 tarball')
    process.exit(1)
  }
  const size = statSync(join(tmp, tgz)).size
  ok(`npm pack 产出 ${tgz}（${(size / 1024).toFixed(1)} KB）`)

  const out = execFileSync('tar', ['-tzf', join(tmp, tgz)], { encoding: 'utf8' })
  entries = out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
  ok(`tarball 共 ${entries.length} 个条目`)
} finally {
  rmSync(tmp, { recursive: true, force: true })
}

// ---- 反向：不该在包里的 ----
const present = []
for (const [re, why] of FORBIDDEN) {
  const hits = entries.filter((e) => re.test(e))
  if (hits.length) present.push({ why, hits })
}
if (present.length) {
  bad(
    `发布包含 ${present.reduce((n, p) => n + p.hits.length, 0)} 个不该发布的文件`,
    present.map((p) => `        ${p.why}\n` + p.hits.map((h) => `          - ${h}`).join('\n')).join('\n'),
  )
} else {
  ok('发布包不含构建产物与开发期脚本')
}

// ---- 正向：该在包里的 ----
const missing = REQUIRED.filter(([path]) => !entries.includes(path))
if (missing.length) {
  bad(
    `发布包缺少 ${missing.length} 个必需文件（files 少写了目录，包体变小但 check 全绿）`,
    missing.map(([p, why]) => `          - ${p}  ← ${why}`).join('\n'),
  )
} else {
  ok('发布包含全部必需文件（清单 / 入口 / 补丁行 / 执行层 / 技能面 / 脚本）')
}

// ---- 双向都查完才算过 ----
console.log(`\n${fail === 0 ? '通过' : '失败'}：${pass} 通过，${fail} 失败`)
process.exit(fail === 0 ? 0 : 1)
