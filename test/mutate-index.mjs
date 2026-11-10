/**
 * 变异验证 index.js 的注册逻辑：证明 host-sim 的断言真的在看注册动作。
 *
 * 教训（2026-11-10 踩到）：第一次用 PowerShell 的 `Get-Content`/`Set-Content`
 * 做替换，中文注释被按 cp936 读写，变异体直接语法错误——**看起来"变红"了，
 * 但红的是编码崩溃，不是判据**。那种红是假红，和假绿一样有毒。
 * 现在整条链走 Node 的 utf8 Buffer，并在变异后先做一次语法检查，
 * 语法不过就判定「变异无效」而不是「击杀成功」。
 *
 *   node test/mutate-index.mjs
 */
import { readFile, writeFile, rm } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const TARGET = join(root, 'index.js');
const BACKUP = join(root, 'index.js.mutbak');

const sha = (b) => createHash('sha256').update(b).digest('hex');

async function exec(args) {
  try {
    const { stdout } = await run(process.execPath, args, {
      cwd: root,
      timeout: 180_000,
      maxBuffer: 8 * 1024 * 1024,
    });
    return { ok: true, out: stdout };
  } catch (error) {
    return { ok: false, out: String(error.stdout ?? '') + String(error.stderr ?? error.message) };
  }
}

const MUTANTS = [
  {
    name: 'M3 工具注册循环不执行',
    find: 'const disposers = DEFINITIONS.map((definition) => ctx.tools.register(definition));',
    replace: 'const disposers = [];',
    expect: /注册了 4 个工具/,
  },
  {
    name: 'M4 技能注册被跳过',
    find: 'for (const child of readdirSync(skillsDir, { withFileTypes: true })) {',
    replace: 'for (const child of []) {',
    expect: /注册了 2 个技能/,
  },
  {
    name: 'M5 工具漏掉一个（4→3）',
    find: 'const disposers = DEFINITIONS.map((definition) => ctx.tools.register(definition));',
    replace: 'const disposers = DEFINITIONS.slice(0, 3).map((definition) => ctx.tools.register(definition));',
    expect: /注册了 4 个工具/,
  },
  {
    name: 'M6 把 data 改回必填（data_path 用户被 schema 直接拒掉）',
    find: "data: { type: 'object', additionalProperties: true, required: false, description },",
    replace: "data: { type: 'object', additionalProperties: true, required: true, description },",
    expect: /把 data 标成必填/,
  },
  {
    name: 'M7 体检工具不再暴露 data_path（退回只收内联对象）',
    find: "    data_path: {\n      type: 'string',",
    replace: "    data_path_X: {\n      type: 'string',",
    expect: /缺 data_path 参数/,
  },
  {
    // 2026-11-10：本轮真实假绿。frontmatter 解析失败时 index.js 静默兜底成
    // `?? child.name`，技能照常注册、描述退化成名字；而旧判据只查 length<20，
    // 'a-share-financial-checkup' 长 26 个字符，于是全绿通过。
    // 这条变异证明新判据（描述不等于技能名）真的会红。
    name: 'M8 frontmatter 解析失败（描述静默退化成技能名）',
    find: '  const block = raw.match(/^---\\r?\\n([\\s\\S]*?)\\r?\\n---/);\n  if (!block) return undefined;',
    replace: '  return undefined;\n  const block = raw.match(/^---\\r?\\n([\\s\\S]*?)\\r?\\n---/);\n  if (!block) return undefined;',
    expect: /描述未退化成技能名/,
  },
];

const original = await readFile(TARGET);
const originalHash = sha(original);
await writeFile(BACKUP, original);

let killed = 0;
const survivors = [];
const invalid = [];

try {
  console.log('基线（未变异）…');
  const base = await exec([join(here, 'host-sim.mjs')]);
  if (!base.ok) {
    console.log('  基线就是红的 —— 变异结论一律作废。');
    console.log(base.out.split('\n').slice(-10).join('\n'));
    process.exitCode = 1;
  } else {
    console.log('  基线全绿 ✔\n');

    for (const m of MUTANTS) {
      const src = original.toString('utf8');
      if (!src.includes(m.find)) {
        invalid.push(`${m.name}（找不到替换片段）`);
        console.log(`${m.name}\n  ⚠️ 片段未匹配，变异无效\n`);
        continue;
      }
      await writeFile(TARGET, src.replace(m.find, m.replace));

      // 先语法检查：语法都不过的红，不能算判据抓到的
      const syntax = await exec(['--check', TARGET]);
      if (!syntax.ok) {
        invalid.push(`${m.name}（变异体语法错误）`);
        console.log(`${m.name}\n  ⚠️ 变异体自身语法错误，此条作废\n`);
        await writeFile(TARGET, original);
        continue;
      }

      const result = await exec([join(here, 'host-sim.mjs')]);
      if (result.ok) {
        survivors.push(m.name);
        console.log(`${m.name}\n  ✘ 测试仍全绿 —— 没抓住\n`);
      } else {
        const hit = m.expect.test(result.out);
        const failLines = result.out
          .split('\n')
          .filter((l) => l.trim().startsWith('FAIL'))
          .map((l) => l.trim());
        if (hit || failLines.length) {
          killed++;
          console.log(`${m.name}\n  ✔ 变红：${failLines.join('；') || '整体失败'}\n`);
        } else {
          invalid.push(`${m.name}（红了，但不是预期的那条判据）`);
          console.log(`${m.name}\n  ⚠️ 变红原因与预期判据不符，此条作废\n`);
        }
      }

      await writeFile(TARGET, original);
    }
  }
} finally {
  await writeFile(TARGET, original);
  await rm(BACKUP, { force: true });
  const nowHash = sha(await readFile(TARGET));
  const intact = nowHash === originalHash;
  console.log(
    intact
      ? `源文件自证：index.js sha256 ${nowHash.slice(0, 12)}… 未改动 ✔`
      : `源文件自证：index.js sha256 ${nowHash.slice(0, 12)}… 已改动 ✘ —— 本轮结论作废`,
  );
  if (!intact) process.exitCode = 1;
}

console.log(`\n击杀 ${killed}，存活 ${survivors.length}，无效 ${invalid.length}`);
if (survivors.length || invalid.length) process.exitCode = 1;
