/**
 * 变异验证：证明 smoke 的断言真的能挡住「报告串号」。
 *
 * 背景：test/smoke.mjs 曾抓到 lib/python.js 一个真缺陷——两个产品落同一目录时，
 * 按文件名排序取「最后一个产物」会让财务工具读回估值结果。修法是每次调用用
 * 独占 staging 目录。
 *
 * 但「修完测试绿了」不等于「这条测试真在检查那件事」。这里做反向验证：
 * 把修法退回旧行为（脚本直接写共享 outDir），看测试是否变红。
 *
 * 判据不靠人眼：跑完必须打印 `源文件自证 … 未改动`，出现 ✘ 则本轮结论作废
 * （说明变异泄漏到了源文件，测的可能是别的东西）。
 *
 *   node test/mutate.mjs
 */
import { readFile, writeFile, copyFile, rm } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const TARGET = join(root, 'lib', 'python.js');
const BACKUP = join(root, 'lib', 'python.js.mutbak');

const sha = (buf) => createHash('sha256').update(buf).digest('hex');

async function runSmoke() {
  try {
    const { stdout } = await run(process.execPath, [join(here, 'smoke.mjs')], {
      cwd: root,
      timeout: 180_000,
      maxBuffer: 8 * 1024 * 1024,
    });
    return { ok: true, out: stdout };
  } catch (error) {
    return { ok: false, out: String(error.stdout ?? error.message) };
  }
}

// ---------- 变异体 ----------
// M1: 退回旧行为——脚本直接写共享 outDir，产物按文件名排序取最后一个。
const M1 = {
  name: 'M1 回退 staging，改为按文件名取最后一个产物（旧 bug）',
  find: /const staging = join\(work, 'out'\);[\s\S]*?const args = \['--data', inputPath, '--out', staging\];/,
  replace: `const staging = outDir;
    const args = ['--data', inputPath, '--out', staging];`,
};

// M2: 读回 JSON 时越过 staging，回到共享 outDir 里挑（真正的串号路径）。
//     注：早先这条写的是「在 staging 内改排序方式」，那是**等价变异**——
//     staging 里只有一个 JSON，排序不改变结果，测试自然不会红。
//     等价变异不算击杀，也不该拿它冒充判据，所以换成真正会串号的那一行。
const M2 = {
  name: 'M2 从共享 outDir 而非 staging 读结果（串号）',
  find: /const jsonName = produced\.filter\(\(f\) => f\.endsWith\('\.json'\)\)\[0\];/,
  replace: `const jsonName = (await readdir(outDir)).filter((f) => f.endsWith('.json')).sort().at(-1);`,
};

// M3: 把 data_path 整体忽略掉 —— 退化成「只支持内联 data」的旧形态。
//     这条变异存在的原因是：工具曾经只收内联对象，而 SKILL.md 教用户的是
//     `--data 文件.csv`。schema 一改就绿很容易，但「文件入口真的还通吗」
//     只有这条变异能证伪。
const M3 = {
  name: 'M3 忽略 data_path（退回只支持内联 data）',
  find: /if \(dataPath\) \{\n      try \{\n        inputPath = resolveInputPath\(dataPath\);/,
  replace: `if (false) {
      try {
        inputPath = resolveInputPath(dataPath);`,
};

// M4: 输入文件不存在时静默放行（不报错，让 Python 自己去失败或产空报告）
const M4 = {
  name: 'M4 输入文件缺失时不报错（静默降级）',
  find: /  \} catch \{\n    throw new Error\(\n      `找不到数据文件/,
  replace: `  } catch {
    return p;
    throw new Error(
      \`找不到数据文件`,
};

const original = await readFile(TARGET);
const originalHash = sha(original);
await writeFile(BACKUP, original);

let killed = 0;
const survivors = [];

try {
  // 基线：未变异时必须绿，否则后面的红没有意义
  console.log('基线（未变异）…');
  const base = await runSmoke();
  if (!base.ok) {
    console.log('  基线就是红的 —— 变异结论一律作废。');
    console.log(base.out.split('\n').slice(-12).join('\n'));
    process.exitCode = 1;
  } else {
    console.log('  基线全绿 ✔\n');

    for (const m of [M1, M2, M3, M4]) {
      const src = original.toString('utf8');
      if (!m.find.test(src)) {
        console.log(`${m.name}\n  ⚠️ 找不到要替换的片段，变异未生效（此条作废）\n`);
        continue;
      }
      const mutated = src.replace(m.find, m.replace);
      await writeFile(TARGET, mutated);

      const result = await runSmoke();
      if (result.ok) {
        survivors.push(m.name);
        console.log(`${m.name}\n  ✘ 测试仍然全绿 —— 没抓住\n`);
      } else {
        killed++;
        const failed = result.out.split('\n').filter((l) => l.trim().startsWith('FAIL'));
        console.log(`${m.name}\n  ✔ 变红：${failed.map((l) => l.trim()).join('；') || '整体失败'}\n`);
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
      ? `源文件自证：lib/python.js sha256 ${nowHash.slice(0, 12)}… 未改动 ✔`
      : `源文件自证：lib/python.js sha256 ${nowHash.slice(0, 12)}… 已改动 ✘ —— 本轮结论作废`,
  );
  if (!intact) process.exitCode = 1;
}

console.log(`\n击杀 ${killed}，存活 ${survivors.length}`);
if (survivors.length) process.exitCode = 1;
