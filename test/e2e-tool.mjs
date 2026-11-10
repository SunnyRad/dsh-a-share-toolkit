/**
 * 端到端：像模型那样调用工具，只走 tool definition，不碰 lib 内部函数。
 *
 * 为什么要这一层：smoke.mjs 直接调 runCheckup（执行层），host-sim.mjs 只验注册
 * 动作。两者都通过，仍然可能「模型按工具描述去用，用不起来」——这正是本轮
 * 修的那个缺陷：工具曾只收内联 data，而 SKILL.md 教用户的是文件路径。
 *
 * 这里从 ctx.tools.register 拿到的 definition 出发，用 definition.parameters
 * 的 schema 去构造调用参数，再走 definition.execute()，最后读它返回的文本。
 *
 *   node test/e2e-tool.mjs
 */
import { existsSync } from 'node:fs';
import { mkdtemp, writeFile, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { homedir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

async function loadCordis() {
  const home = process.env.DSH_HOME || join(homedir(), '.dsh');
  const candidates = [
    join(home, 'profiles', 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js'),
    join(home, 'profiles', 'desktop', 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js'),
  ];
  for (const c of candidates) if (existsSync(c)) return import(pathToFileURL(c).href);
  throw new Error('找不到 cordis');
}

const { Context } = await loadCordis();
const tools = [];
const ctx = new Context();
ctx.provide('skills', { register: () => () => {} });
ctx.provide('tools', {
  register(def) {
    tools.push(def);
    return () => {};
  },
});
ctx.provide('logger', Object.assign(() => ({ info() {}, warn() {}, error() {}, debug() {} }), {}));

const mod = await import(pathToFileURL(join(root, 'index.js')).href);
await ctx.plugin(mod, {}).start?.();
await new Promise((r) => setTimeout(r, 100));

let pass = 0;
const failures = [];
async function test(name, fn) {
  try {
    await fn();
    pass++;
    console.log(`  ok  ${name}`);
  } catch (e) {
    failures.push(`${name}: ${e.message}`);
    console.log(`  FAIL ${name}\n       ${e.message}`);
  }
}

const byName = (n) => tools.find((t) => t.name === n);
const work = await mkdtemp(join(tmpdir(), 'a-share-e2e-'));

try {
  // 按契约造一个真实的 CSV 文件（用户会这么做：从行情软件导出）
  const csvLines = ['date,pe_ttm,pb_mrq,ps_ttm'];
  const base = new Date('2026-11-09T00:00:00Z');
  for (let i = 0; i < 1800; i++) {
    const d = new Date(base.getTime() - i * 86400000).toISOString().slice(0, 10);
    csvLines.push(i < 60 ? `${d},50,0.5,2` : `${d},10,5.5,2`);
  }
  // CSV 通道没有 code 列 —— 产品从**文件名**推断标的代码（ingest.py load_csv）。
  // 所以这里用真实用户会用的命名，同时下面单独测「文件名不像代码」时的表现。
  const csvPath = join(work, '600585_SH.csv');
  await writeFile(csvPath, csvLines.join('\n'), 'utf8');

  await test('模型看到的 schema 里有 data_path 且 data 非必填', () => {
    const t = byName('a_share_valuation_checkup');
    if (!t) throw new Error('工具不存在');
    const p = t.parameters;
    if (!p.data_path) throw new Error('schema 里没有 data_path');
    if (p.data?.required === true) throw new Error('data 被标成必填，data_path 走不通');
  });

  await test('只传 data_path（模型最自然的用法）能出报告', async () => {
    const t = byName('a_share_valuation_checkup');
    const text = await t.execute({ data_path: csvPath }, {});
    if (/环境错误|输入或计算错误/.test(text)) throw new Error(`工具报错：\n${text}`);
    const m = text.match(/HTML 报告: (.+)/);
    if (!m) throw new Error(`返回文本里没有报告路径：\n${text}`);
    if (!existsSync(m[1].trim())) throw new Error(`报告路径不存在：${m[1]}`);
    const html = await readFile(m[1].trim(), 'utf8');
    if (!/600585/.test(html)) throw new Error('报告里没有 600585');
    if (html.length < 3000) throw new Error(`报告太小：${html.length}`);
  });

  await test('返回文本披露了输入文件，用户能核对读的是哪份数据', async () => {
    const t = byName('a_share_valuation_checkup');
    const text = await t.execute({ data_path: csvPath }, {});
    if (!text.includes('输入文件')) throw new Error('没回报输入文件路径');
  });

  await test('纯文本返回里带「不提供数据」声明（合规要求）', async () => {
    const t = byName('a_share_valuation_checkup');
    const text = await t.execute({ data_path: csvPath }, {});
    if (!/本插件不提供数据/.test(text)) throw new Error('缺数据责任声明');
  });

  await test('data_path 不存在时是人能看懂的报错，不是堆栈', async () => {
    const t = byName('a_share_valuation_checkup');
    const text = await t.execute({ data_path: join(work, 'nope.csv') }, {});
    if (!/找不到数据文件/.test(text)) throw new Error(`报错不对：\n${text}`);
    if (/at \w+ \(.*:\d+:\d+\)/.test(text)) throw new Error('泄露了 JS 堆栈');
  });

  await test('内联 data 仍然可用（向后兼容，老调用方不被破坏）', async () => {
    const t = byName('a_share_valuation_checkup');
    const history = [];
    for (let i = 0; i < 1800; i++) {
      const d = new Date(base.getTime() - i * 86400000).toISOString().slice(0, 10);
      history.push(i < 60 ? { date: d, pe_ttm: 50, pb_mrq: 0.5, ps_ttm: 2 } : { date: d, pe_ttm: 10, pb_mrq: 5.5, ps_ttm: 2 });
    }
    const text = await t.execute(
      { data: { code: '600585.SH', name: '海螺水泥', source: 'e2e', trade_date: '2026-11-09', history } },
      {},
    );
    if (/环境错误|输入或计算错误/.test(text)) throw new Error(text);
    if (!text.includes('HTML 报告')) throw new Error('没出报告');
  });

  await test('CSV 通道按文件名推断标的代码（这是真实语义，钉住它）', async () => {
    // 产品 ingest.load_csv 没有 code 列可取，只能从文件名推。
    // 这条测试的价值不是「应该这样」，而是「确实这样」——
    // 用户把文件命名成 600585_SH.csv 就得到 600585,SH；
    // 命名成 my_valuation.csv 就会在报告标题里看到 "my_valuation"。
    // 不知道这条语义的人会以为报告认错了股票。
    const t = byName('a_share_valuation_checkup');
    const plain = join(work, 'my_valuation.csv');
    await writeFile(plain, csvLines.join('\n'), 'utf8');
    const text = await t.execute({ data_path: plain }, {});
    if (/环境错误|输入或计算错误/.test(text)) throw new Error(text);
    const m = text.match(/HTML 报告: (.+)/);
    const html = await readFile(m[1].trim(), 'utf8');
    if (!/my_valuation/.test(html)) {
      throw new Error('文件名不像代码时，代码没有回退成文件名 —— 行为变了，需同步文档');
    }
  });

  await test('财务工具同样接受 data_path（JSON）', async () => {
    const t = byName('a_share_financial_checkup');
    const p = join(work, 'fin.json');
    await writeFile(
      p,
      JSON.stringify({
        code: '600519.SH',
        name: '贵州茅台',
        industry: '白酒',
        source: 'e2e',
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
      }),
      'utf8',
    );
    const text = await t.execute({ data_path: p }, {});
    if (/环境错误|输入或计算错误/.test(text)) throw new Error(`工具报错：\n${text}`);
    if (!text.includes('HTML 报告')) throw new Error('没出报告');
  });
} finally {
  await rm(work, { recursive: true, force: true }).catch(() => {});
}

console.log(`\n${pass} 通过，${failures.length} 失败`);
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`);
  process.exitCode = 1;
}
