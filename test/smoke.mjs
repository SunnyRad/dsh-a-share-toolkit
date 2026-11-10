/**
 * 离线干跑：不经 DSH 宿主，直接测 lib/python.js 的执行层。
 *
 * 为什么要这一步：插件装进 profile 之后，如果工具坏了，报错会被包在
 * 宿主日志里，排查要绕一大圈。先在裸 Node 里把执行层跑通，
 * 就能把「Python 找不到 / 契约不对 / 产物读不回来」这类问题挡在安装之前。
 *
 *   node --test test/  # 或直接 node test/smoke.mjs
 */
import { strict as assert } from 'node:assert';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { findPython, runCheckup, printSpec } from '../lib/python.js';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

let pass = 0;
const failures = [];

async function test(name, fn) {
  try {
    await fn();
    pass++;
    console.log(`  ok  ${name}`);
  } catch (error) {
    failures.push(`${name}: ${error.message}`);
    console.log(`  FAIL ${name}\n       ${error.message}`);
  }
}

// ---------- 最小合规样本 ----------
// 刻意让 PE 走到历史高位、PB 走到历史低位，好让「周期特征」这条分支真的被走到。
function cyclicalValuation() {
  const history = [];
  const base = new Date('2026-11-09T00:00:00Z');
  for (let i = 0; i < 1800; i++) {
    const d = new Date(base.getTime() - i * 86400000).toISOString().slice(0, 10);
    history.push(i < 60 ? { date: d, pe_ttm: 50, pb_mrq: 0.5, ps_ttm: 2 } : { date: d, pe_ttm: 10, pb_mrq: 5.5, ps_ttm: 2 });
  }
  return {
    code: '600585.SH',
    name: '海螺水泥',
    industry: '水泥',
    source: '冒烟测试构造数据',
    trade_date: '2026-11-09',
    history,
  };
}

function basicFinancial() {
  return {
    code: '600519.SH',
    name: '贵州茅台',
    industry: '白酒',
    source: '冒烟测试构造数据',
    unit: 'yuan',
    income: [
      { report_date: '2026-09-30', revenue: 123_100_000_000, net_profit: 60_800_000_000, deduct_net_profit: 60_600_000_000, operate_cost: 10_100_000_000 },
      { report_date: '2025-09-30', revenue: 110_000_000_000, net_profit: 55_000_000_000, deduct_net_profit: 54_800_000_000, operate_cost: 9_500_000_000 },
    ],
    balance: [
      { report_date: '2026-09-30', total_assets: 300_000_000_000, total_liab: 60_000_000_000, total_equity: 240_000_000_000, monetary: 150_000_000_000, accounts_rece: 360_000_000, inventory: 45_000_000_000 },
      { report_date: '2025-09-30', total_assets: 280_000_000_000, total_liab: 58_000_000_000, total_equity: 222_000_000_000, monetary: 140_000_000_000, accounts_rece: 350_000_000, inventory: 43_000_000_000 },
    ],
    cashflow: [
      { report_date: '2026-09-30', cfo: 52_000_000_000, net_invest: -3_000_000_000, net_finance: -48_000_000_000 },
      { report_date: '2025-09-30', cfo: 47_000_000_000, net_invest: -2_800_000_000, net_finance: -44_000_000_000 },
    ],
  };
}

const outDir = await mkdtemp(join(tmpdir(), 'a-share-smoke-'));

try {
  const py = await findPython();

  console.log(`\nPython: ${py.bin} (${py.version})`);
  console.log(`输出目录: ${outDir}\n`);

  await test('能定位 Python 解释器', () => {
    assert.ok(py.bin, '应有解释器路径');
  });

  await test('估值契约可打印且含关键字段', async () => {
    const spec = await printSpec({
      python: py.bin,
      scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'valuation'),
    });
    assert.match(spec, /history/, 'contract should document history');
    assert.match(spec, /pe_ttm/, 'contract should document pe_ttm');
  });

  await test('估值体检跑通并产出两份产物', async () => {
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'valuation'),
      outDir,
      data: cyclicalValuation(),
    });
    assert.equal(out.ok, true, out.message ?? '');
    assert.ok(out.paths.html, '应产出 HTML');
    assert.ok(out.paths.json, '应产出 JSON');
    const html = await readFile(out.paths.html, 'utf8');
    assert.match(html, /600585/, 'report should mention the code');
    assert.ok(html.length > 3000, `report suspiciously small: ${html.length}`);
  });

  await test('周期特征分支真的被走到（PE 高 / PB 低）', async () => {
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'valuation'),
      outDir,
      data: cyclicalValuation(),
    });
    const m = out.result.metrics;
    assert.ok(m.pb_mrq.percentile < 20, `PB 分位应偏低，实际 ${m.pb_mrq.percentile}`);
    assert.ok(m.pe_ttm.percentile > 80, `PE 分位应偏高，实际 ${m.pe_ttm.percentile}`);
    assert.equal(out.result.divergence.diverged, true, '应判为背离');
  });

  await test('财务契约可打印且含三张表', async () => {
    const spec = await printSpec({
      python: py.bin,
      scriptPath: join(root, 'assets', 'financial', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'financial'),
    });
    for (const key of ['income', 'balance', 'cashflow']) {
      assert.match(spec, new RegExp(key), `contract should document ${key}`);
    }
  });

  await test('财务体检跑通并产出两份产物', async () => {
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'financial', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'financial'),
      outDir,
      data: basicFinancial(),
    });
    assert.equal(out.ok, true, out.message ?? '');
    assert.ok(out.paths.html, '应产出 HTML');
    const html = await readFile(out.paths.html, 'utf8');
    assert.match(html, /600519/, 'report should mention the code');
    assert.ok(html.length > 3000, `report suspiciously small: ${html.length}`);
  });

  await test('缺必填字段时明确报错，不产出假报告', async () => {
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'financial', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'financial'),
      outDir,
      data: { code: '000001.SZ', source: 'x' }, // 三张表全缺
    });
    assert.equal(out.ok, false, '缺表必须失败，不能降级出报告');
    assert.ok((out.message ?? '').trim().length > 0, '错误信息不能为空');
  });

  await test('负 PE 判为不适用，且不出分位', async () => {
    const data = cyclicalValuation();
    // 全序列负 PE：这是「全史亏损，任何分位都是噪声」那条坑
    for (const row of data.history) row.pe_ttm = -11.78;
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'valuation'),
      outDir,
      data,
    });
    assert.equal(out.ok, true, out.message ?? '');
    assert.equal(out.result.metrics.pe_ttm.percentile, null, '全负 PE 不应给出分位');
  });

  // ---- 文件路径入口（data_path）----
  // 为什么必须单独测：内联对象走的是 JSON.stringify 分支，文件路径走的是
  // resolveInputPath 分支，两条路只有后者会碰 cwd / ~ / 不存在文件。
  // 而 SKILL.md 教用户的就是 `--data my_valuation.csv`。

  await test('data_path 传 CSV 能跑通（与 SKILL.md 的文件形态一致）', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'a-share-csv-'));
    try {
      const csv = ['date,pe_ttm,pb_mrq,ps_ttm'];
      const base = new Date('2026-11-09T00:00:00Z');
      for (let i = 0; i < 1800; i++) {
        const d = new Date(base.getTime() - i * 86400000).toISOString().slice(0, 10);
        csv.push(i < 60 ? `${d},50,0.5,2` : `${d},10,5.5,2`);
      }
      const p = join(dir, '600585.csv');
      await writeFile(p, csv.join('\n'), 'utf8');

      const out = await runCheckup({
        python: py.bin,
        scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
        cwd: join(root, 'assets', 'valuation'),
        outDir,
        dataPath: p,
      });
      assert.equal(out.ok, true, out.message ?? '');
      assert.equal(out.input, p, '应回报实际读取的文件路径');
      assert.match(await readFile(out.paths.html, 'utf8'), /600585/);
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  });

  await test('data_path 传 JSON 能跑通', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'a-share-json-'));
    try {
      const p = join(dir, 'input.json');
      await writeFile(p, JSON.stringify(basicFinancial()), 'utf8');
      const out = await runCheckup({
        python: py.bin,
        scriptPath: join(root, 'assets', 'financial', 'scripts', 'checkup.py'),
        cwd: join(root, 'assets', 'financial'),
        outDir,
        dataPath: p,
      });
      assert.equal(out.ok, true, out.message ?? '');
      assert.match(await readFile(out.paths.html, 'utf8'), /600519/);
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  });

  await test('data_path 指向不存在的文件时明确报错，不静默降级', async () => {
    const missing = join(tmpdir(), 'definitely-not-here-9f2a', 'nope.csv');
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'valuation'),
      outDir,
      dataPath: missing,
    });
    assert.equal(out.ok, false, '不存在的输入文件必须失败');
    // ⚠️ 判据要瞄准行为，不是措辞（AGENTS.md 第八条）。
    //
    // 上一版写死 /找不到数据文件/，而 Python 侧抛的是「找不到输入文件」——
    // 两句话说的是同一件事，判据却红了（2026-11-10 假红）。
    //
    // 但只放宽成「含找不到」又会变假绿：M4 变异体让 resolveInputPath 不抛错、
    // 把原样路径丢给 Python，Python 自己也会说「找不到输入文件」，
    // 于是「插件没做前置校验」这个真缺陷照样逃过（实测 M4 存活）。
    //
    // ⇒ 判据必须能区分「这条错误是谁报的」：
    //   插件侧 resolveInputPath 的报错带「data_path」+「当前工作目录」说明，
    //   Python 侧的报错是「[输入错误] 找不到输入文件」。
    //   要求出现插件侧措辞，才证明前置校验真的执行了。
    assert.match(out.message, /找不到/, `错误信息未表明文件找不到：${out.message}`);
    assert.ok(out.message.includes(missing) || out.message.includes('nope.csv'),
      `错误信息必须指明是哪个文件，实际：${out.message}`);
    assert.match(out.message, /data_path/,
      `必须是插件侧前置校验报的错（含 data_path 提示），` +
      `而不是把路径丢给 Python 后由脚本报错。实际：${out.message}`);
    assert.doesNotMatch(out.message, /Traceback/,
      `不该把 Python 栈抛给用户：${out.message}`);
  });

  await test('data 与 data_path 都不传时明确报错', async () => {
    const out = await runCheckup({
      python: py.bin,
      scriptPath: join(root, 'assets', 'valuation', 'scripts', 'checkup.py'),
      cwd: join(root, 'assets', 'valuation'),
      outDir,
    });
    assert.equal(out.ok, false);
    assert.match(out.message, /data_path|data/);
  });
} finally {
  await rm(outDir, { recursive: true, force: true }).catch(() => {});
}

console.log(`\n${pass} 通过，${failures.length} 失败`);
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`);
  process.exitCode = 1;
}
