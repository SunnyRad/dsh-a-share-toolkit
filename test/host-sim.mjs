/**
 * 最小宿主替身：验证 apply() 真的往 ctx.skills / ctx.tools 里注册了东西。
 *
 * 为什么要它：插件装进 DSH 之后，「工具没出现」有两种完全不同的成因——
 *   (a) 代码没写对（注册逻辑本身不工作）
 *   (b) 宿主还跑着旧模块（junction 链接下改磁盘对已加载模块无效）
 * 这两者的处置完全不同：前者改代码，后者重启宿主。只看 listTools 分不出来。
 *
 * 这个脚本用真实的 cordis Context 和真实的 ctx.effect 语义，把 apply() 跑一遍，
 * 从而把 (a) 单独证伪。它不连 DSH、不读 profile 状态。
 *
 * ⚠️ 注意它验证的是「注册动作发生」，不是「工具在 DSH 里可被模型调用」。
 * 后者只有真宿主能给答案。
 *
 *   node test/host-sim.mjs
 */
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

/**
 * 按绝对路径加载 cordis。
 *
 * 不能用裸说明符 `@deepseek-ai/cordis`：ESM 按**导入文件所在位置**向上找
 * node_modules，而不是按 cwd。本包以 junction 装进 profile 时，cordis 装在
 * ~/.dsh/profiles/node_modules，从前者向上找不到——这正是插件里
 * `import('@deepseek-ai/dsh-tools')` 当初失败的原因（也是它后来被去掉的原因）。
 * 这里显式给出路径，好让本脚本专注验证「注册逻辑」而不是重复同一个路径问题。
 */
async function loadCordis() {
  const candidates = [
    join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'profiles', 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js'),
    join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'profiles', 'desktop', 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js'),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return import(pathToFileURL(c).href);
  }
  throw new Error(`找不到 cordis，尝试过：\n  ${candidates.join('\n  ')}`);
}

const { Context } = await loadCordis();

const skills = [];
const tools = [];
const logs = [];

const ctx = new Context();

// 模拟 skills 服务
ctx.provide('skills', {
  register(skill) {
    skills.push(skill);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      const i = skills.indexOf(skill);
      if (i >= 0) skills.splice(i, 1);
    };
  },
});

// 模拟 tools 服务
ctx.provide('tools', {
  register(definition) {
    tools.push(definition);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      const i = tools.indexOf(definition);
      if (i >= 0) tools.splice(i, 1);
    };
  },
});

const logger = {
  info: (m) => logs.push(`info ${m}`),
  warn: (m) => logs.push(`warn ${m}`),
  error: (m) => logs.push(`error ${m}`),
  debug: (m) => logs.push(`debug ${m}`),
};
// 插件调用 ctx.logger(name) 取命名 logger
ctx.provide('logger', Object.assign(() => logger, logger));

// Windows 绝对路径不能直接 import，必须转成 file:// URL
const mod = await import(pathToFileURL(join(root, 'index.js')).href);

let pass = 0;
const failures = [];
function check(name, fn) {
  try {
    fn();
    pass++;
    console.log(`  ok  ${name}`);
  } catch (error) {
    failures.push(`${name}: ${error.message}`);
    console.log(`  FAIL ${name}\n       ${error.message}`);
  }
}

console.log(`\n插件 name = ${mod.name}`);
console.log(`inject   = ${JSON.stringify(mod.inject)}\n`);

const fiber = ctx.plugin(mod, {});
await fiber.start?.();
// 给 effect 一点时间落位
await new Promise((r) => setTimeout(r, 50));

check('apply 可执行且导出 name/inject', () => {
  if (typeof mod.apply !== 'function') throw new Error('apply 不是函数');
  if (!mod.name) throw new Error('缺 name');
});

check('注册了 2 个技能', () => {
  if (skills.length !== 2) {
    throw new Error(`预期 2 个，实际 ${skills.length}：${skills.map((s) => s.name).join(', ')}`);
  }
});

check('技能名正确且带 resourceBase', () => {
  const names = skills.map((s) => s.name).sort();
  const want = ['a-share-financial-checkup', 'a-share-valuation-percentile'];
  if (JSON.stringify(names) !== JSON.stringify(want)) {
    throw new Error(`技能名不符：${names.join(', ')}`);
  }
  for (const s of skills) {
    if (!s.description || s.description.length < 20) {
      throw new Error(`${s.name} 的 description 太短或缺失`);
    }
    if (s.resourceBase?.kind !== 'directory') {
      throw new Error(`${s.name} 缺 resourceBase.directory`);
    }
    if (!existsSync(s.resourceBase.path)) {
      throw new Error(`${s.name} 的 resourceBase 指向不存在的目录`);
    }
    if (!s.content || s.content.startsWith('---')) {
      throw new Error(`${s.name} 的正文没有剥掉 frontmatter`);
    }
  }
});

check('注册了 4 个工具', () => {
  if (tools.length !== 4) {
    throw new Error(`预期 4 个，实际 ${tools.length}：${tools.map((t) => t.name).join(', ')}`);
  }
});

/**
 * 描述「退化成技能名」必须判红。
 *
 * index.js 用 `frontmatterValue(raw,'description') ?? child.name` 兜底：
 * frontmatter 解析失败时技能照常注册、描述变成名字本身。上一版判据只有
 * `description.length < 20`，而 'a-share-financial-checkup' 就有 26 个字符——
 * 于是解析失败时测试全绿，技能目录里的描述却已经退化成名字。
 * 2026-11-10 实测：把 frontmatterValue 改成恒返回 undefined，
 * host-sim 仍报 10 通过 0 失败。那条判据是假绿。
 *
 * 这里直接钉住那个失败形态，不再靠长度间接推断。
 */
check('描述未退化成技能名（frontmatter 解析失败会静默兜底成 name）', () => {
  for (const s of skills) {
    if (s.description.trim() === s.name) {
      throw new Error(
        `${s.name} 的 description 等于技能名 —— frontmatter 解析失败，` +
          `已静默兜底成 ?? child.name`,
      );
    }
    if (!s.description.startsWith('当')) {
      throw new Error(
        `${s.name} 的 description 不是从触发条件写起：${JSON.stringify(s.description.slice(0, 40))}`,
      );
    }
  }
});

check('工具名与契约完整（name/description/parameters/output/execute）', () => {
  const want = [
    'a_share_valuation_checkup',
    'a_share_financial_checkup',
    'a_share_valuation_contract',
    'a_share_financial_contract',
  ];
  const got = tools.map((t) => t.name).sort();
  if (JSON.stringify(got) !== JSON.stringify(want.sort())) {
    throw new Error(`工具名不符：${got.join(', ')}`);
  }
  for (const t of tools) {
    if (!t.description || t.description.length < 30) throw new Error(`${t.name} 描述过短`);
    if (typeof t.parameters !== 'object' || t.parameters === null) {
      throw new Error(`${t.name} 缺 parameters`);
    }
    if (!t.output || typeof t.output.render !== 'function' || !t.output.schema) {
      throw new Error(`${t.name} 的 output 契约不完整`);
    }
    if (typeof t.execute !== 'function') throw new Error(`${t.name} 缺 execute`);
  }
});

check('体检工具接受两种入口：data 内联 或 data_path 文件（且都不标 required）', () => {
  for (const n of ['a_share_valuation_checkup', 'a_share_financial_checkup']) {
    const t = tools.find((x) => x.name === n);
    if (!t.parameters.data) throw new Error(`${n} 缺 data 参数`);
    if (!t.parameters.data_path) throw new Error(`${n} 缺 data_path 参数（文件形态）`);
    if (t.parameters.data.required === true) {
      throw new Error(`${n} 把 data 标成必填 —— 用 data_path 的用户会被 schema 直接拒掉`);
    }
    const d = t.parameters.data_path.description ?? '';
    if (!/二选一/.test(d)) throw new Error(`${n} 的 data_path 描述没说明与 data 二选一`);
  }
});

check('财务工具额外暴露 peers_path', () => {
  const t = tools.find((x) => x.name === 'a_share_financial_checkup');
  if (!t.parameters.peers_path) throw new Error('缺 peers_path');
});

check('output.render 对字符串产出 text block', () => {
  const t = tools[0];
  const blocks = t.output.render({}, 'hello');
  if (!Array.isArray(blocks) || blocks[0]?.type !== 'text' || blocks[0]?.text !== 'hello') {
    throw new Error(`render 输出不对：${JSON.stringify(blocks)}`);
  }
});

// 真跑一次契约工具：证明 execute 端到端可用（这是能脱离宿主验证的那一半）
console.log('\n  调用 a_share_valuation_contract.execute() …');
const contractTool = tools.find((t) => t.name === 'a_share_valuation_contract');
const spec = await contractTool.execute({}, {});
check('契约工具真的跑通 Python 并返回契约文本', () => {
  if (typeof spec !== 'string') throw new Error(`返回值不是字符串：${typeof spec}`);
  if (!/history/.test(spec)) throw new Error('契约里没有 history 字段说明');
  if (!/pe_ttm/.test(spec)) throw new Error('契约里没有 pe_ttm 字段说明');
});

// 卸载：两个 effect 的清理都该跑干净
await fiber.dispose?.();
await new Promise((r) => setTimeout(r, 20));

check('卸载后技能与工具都被清理（effect 清理生效）', () => {
  if (skills.length !== 0) throw new Error(`技能残留 ${skills.length} 个`);
  if (tools.length !== 0) throw new Error(`工具残留 ${tools.length} 个`);
});

console.log(`\n日志：`);
for (const l of logs) console.log(`  ${l}`);

console.log(`\n${pass} 通过，${failures.length} 失败`);
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`);
  process.exitCode = 1;
}
