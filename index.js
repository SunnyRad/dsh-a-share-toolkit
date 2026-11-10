/**
 * dsh-a-share-toolkit —— A 股估值分位体检 + 个股财务体检。
 *
 * 这个插件把两个已经独立验证过的「能力型」产品包进 DSH：
 *   - a-share-valuation-percentile  估值贵不贵、处于历史什么位置
 *   - a-share-financial-checkup     三张表勾稽、盈利质量、财务红旗
 *
 * 两个产品共用同一条边界：**只算数，不取数**。
 * 数据由调用方按 JSON 契约传入（用户自己的 tushare / 问财 / CSV），
 * 插件全程零网络，脚本只用 Python 标准库，可离线审计。
 *
 * 这不是口味问题，是合规边界：平台「股票信息服务平台」类目要求
 * 增值电信业务经营许可证 + 沪深交易所证券信息经营许可，个人主体拿不到。
 * 自行抓取证券数据并交付给用户，等于以个人身份从事需要许可的证券信息服务。
 *
 * ⚠️ 本包**零 import 依赖**（只用 node: 内建），工具定义按 ToolDefinition
 * 接口直接构造，不经 `@deepseek-ai/dsh-tools` 的 defineTool。
 * 理由是实测出来的：插件以 junction 形式装进 profile 时，模块解析按
 * **链接的真实路径**（插件的实际安装目录）向上找，而 dsh-tools 装在
 * profile 的 node_modules 里，于是 import 必然失败、工具面静默不注册。
 * 2026-11-10 首次装完后 listTools 里一个工具都没有，才查到这里。
 * defineTool 的作用本是参数类型推断与校验，对运行时不是必需，
 * 构造纯对象即可，且省掉一个 peer 依赖。
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';

import { findPython, runCheckup, printSpec } from './lib/python.js';

const here = dirname(fileURLToPath(import.meta.url));
const skillsDir = join(here, 'skills');
const assetsDir = join(here, 'assets');

/** 两个产品的运行时参数；其余部分同构，走同一套执行层。 */
const PRODUCTS = {
  valuation: {
    tool: 'a_share_valuation_checkup',
    contractTool: 'a_share_valuation_contract',
    script: join(assetsDir, 'valuation', 'scripts', 'checkup.py'),
    cwd: join(assetsDir, 'valuation'),
    title: 'A 股估值分位体检',
    skill: 'a-share-valuation-percentile',
    // 报告子目录名。两个产品的产物文件名可能是同一个
    // （同一只股票 + 同一天），必须分开落盘，详见 makeCheckupTool。
    slug: 'valuation',
  },
  financial: {
    tool: 'a_share_financial_checkup',
    contractTool: 'a_share_financial_contract',
    script: join(assetsDir, 'financial', 'scripts', 'checkup.py'),
    cwd: join(assetsDir, 'financial'),
    title: 'A 股个股财务体检',
    skill: 'a-share-financial-checkup',
    slug: 'financial',
  },
};

/** 报告落盘位置：DSH 数据根下，用户可预期、可清理、不污染工作区。 */
function defaultOutputDir() {
  const home = process.env.DSH_HOME || join(homedir(), '.dsh');
  return join(home, 'a-share-toolkit', 'reports');
}

/**
 * 从 SKILL.md 的 frontmatter 取一个标量。
 *
 * 只取 `key: value` 单行形式——两个产品的 description 都是单行，
 * 不需要引入 YAML 依赖。
 */
function frontmatterValue(raw, key) {
  const block = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!block) return undefined;
  const line = block[1].match(new RegExp(`^${key}:\\s*(.+)$`, 'm'));
  return line?.[1].trim();
}

/** 去掉 frontmatter，只留正文给技能注册表（元数据走显式字段）。 */
function stripFrontmatter(raw) {
  return raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '').trimStart();
}

/** 工具统一返回纯文本：结论 + 报告路径 + 数据责任声明。 */
const textOutput = {
  schema: { type: 'string' },
  render: (_args, value) => [{ type: 'text', text: String(value) }],
};

export const name = 'a-share-toolkit';

/**
 * `skills` 是硬依赖（技能面是主交付物）。
 * `tools` 也声明为硬依赖：本插件的作用就是把能力做成可调用的工具，
 * 没有 tools 服务时没有可降级的形态。
 */
export const inject = ['skills', 'tools'];

export function apply(ctx, config = {}) {
  const outputDir = (config && config.outputDir) || defaultOutputDir();
  const pythonOverride = (config && config.python) || '';
  const logger = ctx.logger('a-share-toolkit');

  // ---- 技能面 ----
  ctx.effect(() => {
    if (!existsSync(skillsDir)) {
      logger.warn(`技能目录不存在，技能面为空：${skillsDir}`);
      return () => {};
    }

    const disposers = [];
    for (const child of readdirSync(skillsDir, { withFileTypes: true })) {
      if (!child.isDirectory()) continue;
      const skillFile = join(skillsDir, child.name, 'SKILL.md');
      if (!existsSync(skillFile)) continue;

      const raw = readFileSync(skillFile, 'utf-8');
      disposers.push(
        ctx.skills.register({
          name: child.name,
          description: frontmatterValue(raw, 'description') ?? child.name,
          content: stripFrontmatter(raw),
          source: 'bundled',
          path: skillFile,
          resourceBase: { kind: 'directory', path: join(skillsDir, child.name) },
        }),
      );
      logger.info(`已注册技能 ${child.name}`);
    }
    return () => {
      for (const dispose of disposers.reverse()) {
        try {
          dispose();
        } catch {
          /* 单个技能失败不阻断其余清理 */
        }
      }
    };
  }, 'a-share-toolkit skills');

  // ---- 工具面 ----
  const resolvePython = async () => {
    try {
      return { ok: true, bin: (await findPython(pythonOverride)).bin };
    } catch (error) {
      return { ok: false, message: error.message };
    }
  };

  /** 体检工具：跑产品脚本，返回摘要与报告路径。 */
  const makeCheckupTool = (product, description, parameters, buildArgs) => ({
    name: product.tool,
    description,
    parameters,
    output: textOutput,
    async execute(args) {
      const py = await resolvePython();
      if (!py.ok) return `[环境错误] ${py.message}`;

      const outcome = await runCheckup({
        python: py.bin,
        scriptPath: product.script,
        cwd: product.cwd,
        // 每个产品落自己的子目录。两个产品对同一只股票、同一天会产出
        // **同名文件**（600519_SH_20250930.html），共用一个目录时后写的
        // 直接覆盖先写的——两份都是真报告、都能打开，只是内容被换掉了。
        // 2026-11-10 实测确证：估值 7737 字节被财务 7174 字节顶掉。
        outDir: join(outputDir, product.slug),
        ...buildArgs(args),
      });

      if (!outcome.ok) return `[输入或计算错误] ${outcome.message}`;

      const lines = [outcome.summary];
      if (outcome.paths.html) lines.push(`  HTML 报告: ${outcome.paths.html}`);
      if (outcome.paths.json) lines.push(`  JSON 结果: ${outcome.paths.json}`);
      if (outcome.input && !args.data) lines.push(`  输入文件: ${outcome.input}`);
      lines.push('', '⚠️ 本插件不提供数据：以上结论完全取决于调用方传入的数据源。');
      return lines.join('\n');
    },
  });

  /** 契约工具：只讲该准备什么数据，不读用户数据。 */
  const makeContractTool = (product, description) => ({
    name: product.contractTool,
    description,
    parameters: {},
    output: textOutput,
    async execute() {
      const py = await resolvePython();
      if (!py.ok) return `[环境错误] ${py.message}`;
      return printSpec({ python: py.bin, scriptPath: product.script, cwd: product.cwd });
    },
  });

  const dataParam = (description) => ({
    data: { type: 'object', additionalProperties: true, required: false, description },
    data_path: {
      type: 'string',
      description:
        '数据文件的路径（.json 或 .csv），与 data 二选一。' +
        '**推荐这条**：750 个交易日以上的估值历史内联进 tool call 要几万 token，' +
        '而用户本来就有导出好的文件。相对路径按当前工作目录解析。',
    },
  });

  const DEFINITIONS = [
    makeCheckupTool(
      PRODUCTS.valuation,
      'A 股估值分位体检：把一段 PE/PB/PS 历史序列算成历史分位并出 HTML 报告。' +
        '正确处理三类会让结论反转的陷阱——亏损股负 PE 判为不适用并引导改看 PB；' +
        '样本过短不出分位（<250 交易日拒绝，<750 标注仅供参考）；' +
        'PE 与 PB 分位差 >40 个百分点判为周期特征并自动切换主判断指标。' +
        '本工具不取数：数据用 data_path 指向用户自带的 .csv/.json 文件（推荐），' +
        '或少量数据时用 data 内联传入。契约见 a_share_valuation_contract。',
      dataParam(
        '估值数据对象（与 data_path 二选一，少量数据时用）：' +
          '{code, name, industry, source, trade_date, ' +
          'history:[{date, pe_ttm, pb_mrq, ps_ttm}], industry_rows:[...]}。' +
          'history 建议 ≥750 个交易日；亏损公司的负 PE 照原样抄，不要改 0 或删掉。',
      ),
      (args) => ({ data: args.data, dataPath: args.data_path }),
    ),
    makeCheckupTool(
      PRODUCTS.financial,
      'A 股个股财务体检：读三张报表（利润表/资产负债表/现金流量表）出财务体检 HTML 报告，' +
        '覆盖三表勾稽、盈利质量（应计与现金流）、杜邦拆解、十余项财务造假红旗与同业分位。' +
        '已修复六类会误导用户的缺陷并留回归测试：绝对阈值评分（亏损公司反被评高分）、' +
        '亏损零红旗、小基数误报、银行毛利率 None 崩溃、勾稽口径混淆、过度转义。' +
        '本工具不取数：数据用 data_path 指向用户自带的 .json 文件（推荐），' +
        '或少量期数时用 data 内联传入。契约见 a_share_financial_contract。',
      {
        data: {
          type: 'object',
          additionalProperties: true,
          required: false,
          description:
            '财务数据对象（与 data_path 二选一）：{code, name, industry, source, unit, ' +
            'income:[{report_date, revenue, net_profit, deduct_net_profit, operate_cost}], ' +
            'balance:[{report_date, total_assets, total_liab, total_equity, monetary, accounts_rece, inventory}], ' +
            'cashflow:[{report_date, cfo, net_invest, net_finance}]}。' +
            '建议 8 期以上（含去年同期）。unit 取 yuan/wan/yi。',
        },
        data_path: {
          type: 'string',
          description:
            '财务数据文件的路径（.json），与 data 二选一。',
        },
        peers: {
          type: 'object',
          additionalProperties: true,
          description:
            '同业样本（可选）：{rows:[{code, name, ...指标}]} 或直接数组。少于 3 家不出横截面分位。',
        },
        peers_path: {
          type: 'string',
          description: '同业样本文件的路径（.json，可选），与 peers 二选一。',
        },
      },
      (args) => ({
        data: args.data,
        dataPath: args.data_path,
        peers: args.peers,
        peersPath: args.peers_path,
      }),
    ),
    makeContractTool(
      PRODUCTS.valuation,
      '打印 A 股估值分位体检要求的数据契约：字段名与别名、输入形态、样本量门槛、' +
        '空值与负 PE 的正确写法、常见坑。拿到用户数据之前先调它。',
    ),
    makeContractTool(
      PRODUCTS.financial,
      '打印 A 股个股财务体检要求的数据契约：三张表各自的字段与别名、unit 单位、' +
        '建议期数、同业样本格式。拿到用户数据之前先调它。',
    ),
  ];

  ctx.effect(() => {
    const disposers = DEFINITIONS.map((definition) => ctx.tools.register(definition));
    logger.info(
      `已注册 ${disposers.length} 个工具，报告输出目录 ${outputDir}（按产品分 valuation/ 与 financial/ 两子目录）`,
    );
    return () => {
      for (const dispose of disposers.reverse()) {
        try {
          dispose();
        } catch {
          /* 单个工具失败不阻断其余清理 */
        }
      }
    };
  }, 'a-share-toolkit tools');
}
