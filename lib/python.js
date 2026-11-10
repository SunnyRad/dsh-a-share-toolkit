/**
 * Python 执行层：定位解释器、把 JSON 契约落成临时文件、跑产品脚本、读回产物。
 *
 * 设计约束（来自两个产品的定位）：
 *   1. 能力型 —— 本插件只做计算，不取数，不联网。数据由调用方经 JSON 契约传入。
 *   2. 零依赖 —— 产品脚本只用 Python 标准库，不需要装任何第三方包。
 *   3. 失败要响 —— 任何环节缺数据都明确报错，不降级、不编造。
 */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, readFile, readdir, rm, mkdir, copyFile } from 'node:fs/promises';
import { existsSync, statSync } from 'node:fs';
import { tmpdir, homedir } from 'node:os';
import { join, isAbsolute, resolve } from 'node:path';

const run = promisify(execFile);

/** 单个脚本的执行预算。产品脚本是纯本地计算，正常在秒级完成。 */
const TIMEOUT_MS = 120_000;
const MAX_BUFFER = 32 * 1024 * 1024;

const isWin = process.platform === 'win32';

/**
 * 定位可用的 Python 解释器。
 *
 * 不写死路径：Windows 上 `python` 最常见，其余平台按 PEP 394 是 `python3`。
 * 逐个试跑 `--version`，第一个能跑的就是它——比读 PATH 更可靠，
 * 因为 PATH 里可能有 WindowsApps 的占位存根（存在但一跑就弹商店）。
 */
export async function findPython(preferred) {
  const candidates = preferred
    ? [preferred]
    : isWin
      ? ['python', 'python3', 'py']
      : ['python3', 'python'];

  const tried = [];
  for (const bin of candidates) {
    try {
      const { stdout } = await run(bin, ['--version'], { timeout: 15_000 });
      return { bin, version: String(stdout).trim(), tried };
    } catch (error) {
      tried.push(`${bin}: ${error.code ?? error.message}`);
    }
  }
  throw new Error(
    `找不到可用的 Python 解释器。已尝试：${tried.join('；')}。` +
      '本插件需要 Python 3.8+（产品脚本只用标准库，不需要安装任何第三方包）。',
  );
}

/**
 * 在给定目录里跑一个产品脚本。
 *
 * 统一做三件事，缺一个中文 Windows 上就会炸：
 *   - `-X utf8`：不靠 locale，源码与输出都按 UTF-8 走
 *   - `PYTHONIOENCODING/PYTHONUTF8`：子进程 stdout 不按 cp936 写
 *   - `PYTHONDONTWRITEBYTECODE`：不在只读资源目录里留 __pycache__
 */
async function execScript(python, scriptPath, args, cwd) {
  const env = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    PYTHONDONTWRITEBYTECODE: '1',
  };
  try {
    const { stdout, stderr } = await run(python, ['-X', 'utf8', scriptPath, ...args], {
      cwd,
      env,
      timeout: TIMEOUT_MS,
      maxBuffer: MAX_BUFFER,
      windowsHide: true,
    });
    return { ok: true, stdout: String(stdout), stderr: String(stderr) };
  } catch (error) {
    // 产品脚本用退出码 2 表达「输入不合法」，并把原因写在 stderr。
    // 这属于可诊断的用户错误，原样带回，不能吞成「Python 挂了」。
    const stderr = String(error.stderr ?? '');
    const stdout = String(error.stdout ?? '');
    if (error.code === 2 || stderr.trim()) {
      return { ok: false, stdout, stderr, exitCode: error.code };
    }
    return {
      ok: false,
      stdout,
      stderr:
        error.killed
          ? `脚本执行超时（${TIMEOUT_MS / 1000}s）`
          : `脚本启动失败：${error.code ?? error.message}`,
      exitCode: error.code,
    };
  }
}

/**
 * 解析调用方给的**文件路径**。
 *
 * 为什么要有这条路：契约要求 750 个交易日以上的估值历史，实测 1800 条
 * 的 JSON 是 155 KB / 约 4.5 万 token。让模型把整段历史内联进 tool call
 * 是能跑，但一个正常问题就要吃掉小半个上下文窗。而 SKILL.md 里教用户的
 * 本来就是 `--data my_valuation.csv`（文件形态）——两边口径必须一致。
 *
 * 规则：
 *   - 绝对路径直接用；`~` 展开；相对路径按进程 cwd 解析
 *     （DSH 里即会话工作区，也就是用户放数据文件的地方）
 *   - 不存在就明确报错，不猜、不降级成空数据
 *   - 扩展名由产品脚本自己判（它已有准确的错误信息），这里只挡掉目录
 */
export function resolveInputPath(inputPath) {
  const raw = String(inputPath).trim();
  if (!raw) throw new Error('data_path 为空字符串。');

  let p = raw;
  if (p === '~' || p.startsWith('~/') || p.startsWith('~\\')) {
    p = join(homedir(), p.slice(1));
  }
  if (!isAbsolute(p)) p = resolve(process.cwd(), p);

  let stat;
  try {
    stat = statSync(p);
  } catch {
    throw new Error(
      `找不到数据文件：${p}\n` +
        `  （传 data_path 时按当前工作目录解析相对路径；` +
        `若文件在工作区里，用相对路径或绝对路径都可以）`,
    );
  }
  if (stat.isDirectory()) {
    throw new Error(`data_path 指向的是目录而不是文件：${p}`);
  }
  return p;
}

/**
 * 跑一次体检：落输入 → 跑脚本 → 读回 JSON 结果与 HTML 报告路径。
 *
 * ⚠️ 产物先用**本次调用独占的暂存目录**接住，再搬进 outDir。
 *
 * 早先的写法是「脚本直接写 outDir，然后列目录、按文件名排序取最后一个」。
 * 那是错的：估值报告（600585_…）和财务报告（600519_…）落在同一目录时，
 * 后者按字典序排在前面，于是财务工具读回的是**上一份估值结果**，
 * 而两份都是真实报告、都能解析——不报错，只是答案串了。
 * 2026-11-10 由 test/smoke.mjs 的双产品连跑抓到。
 *
 * 现在每次调用有自己的 staging 目录，只认自己产出的文件，
 * 与 outDir 里已有多少历史报告完全无关。
 *
 * @param {object} opts
 * @param {string} opts.python     解释器
 * @param {string} opts.scriptPath 产品 CLI 脚本
 * @param {object} [opts.data]     调用方传入的数据（JSON 契约）；与 dataPath 二选一
 * @param {string} [opts.dataPath] 调用方自带的数据文件路径（.json / .csv）
 * @param {object} [opts.peers]    同业样本（仅财务体检用，内联形态）
 * @param {string} [opts.peersPath] 同业样本文件路径（仅财务体检用）
 * @param {string} opts.outDir     报告最终落盘目录
 * @param {string} opts.cwd        脚本工作目录（产品包根）
 */
export async function runCheckup({
  python,
  scriptPath,
  data,
  dataPath,
  peers,
  peersPath,
  outDir,
  cwd,
}) {
  await mkdir(outDir, { recursive: true });

  // 本次调用独占的产物目录，脚本只往这里写。
  // 放在外面是因为文件路径形态不再建临时输入目录。
  const work = await mkdtemp(join(tmpdir(), 'a-share-toolkit-'));
  const staging = join(work, 'out');
  await mkdir(staging, { recursive: true });

  try {
    let inputPath;
    if (dataPath) {
      try {
        inputPath = resolveInputPath(dataPath);
      } catch (error) {
        // 路径问题属于「用户输入不合法」，必须走 ok:false 这条诊断通道，
        // 不能让异常冒到工具层变成「插件崩了」。
        return { ok: false, message: error.message };
      }
    } else if (data) {
      inputPath = join(work, 'input.json');
      await writeFile(inputPath, JSON.stringify(data, null, 2), {
        encoding: 'utf8',
        mode: 0o600,
      });
    } else {
      return {
        ok: false,
        message: '缺少数据：请传 data（内联对象）或 data_path（数据文件路径）。',
      };
    }

    const args = ['--data', inputPath, '--out', staging];

    if (peersPath) {
      try {
        args.push('--peers', resolveInputPath(peersPath));
      } catch (error) {
        return { ok: false, message: error.message };
      }
    } else if (peers) {
      const p = join(work, 'peers.json');
      await writeFile(p, JSON.stringify(peers, null, 2), { encoding: 'utf8', mode: 0o600 });
      args.push('--peers', p);
    }

    const result = await execScript(python, scriptPath, args, cwd);
    if (!result.ok) {
      return { ok: false, message: (result.stderr || result.stdout || '脚本执行失败').trim() };
    }

    // 只认这次 staging 里的文件；有且各应有一个。
    const produced = await readdir(staging).catch(() => []);
    const jsonName = produced.filter((f) => f.endsWith('.json'))[0];
    const htmlName = produced.filter((f) => f.endsWith('.html'))[0];

    if (!jsonName) {
      return { ok: false, message: '脚本成功退出但没有产出 JSON 结果，无法确认计算完成。' };
    }

    let payload;
    try {
      payload = JSON.parse(await readFile(join(staging, jsonName), 'utf8'));
    } catch {
      return { ok: false, message: `产出的 JSON 无法解析：${jsonName}` };
    }

    // 搬进最终目录，并把真实路径告诉调用方。
    const finalJson = join(outDir, jsonName);
    await copyFile(join(staging, jsonName), finalJson);
    let finalHtml = null;
    if (htmlName) {
      finalHtml = join(outDir, htmlName);
      await copyFile(join(staging, htmlName), finalHtml);
    }

    return {
      ok: true,
      summary: result.stdout.trim(),
      result: payload,
      input: inputPath,
      paths: { json: finalJson, html: finalHtml },
    };
  } finally {
    await rm(work, { recursive: true, force: true }).catch(() => {});
  }
}

/** 打印输入契约（产品脚本的 --spec），给模型和用户看该准备什么数据。 */
export async function printSpec({ python, scriptPath, cwd }) {
  const result = await execScript(python, scriptPath, ['--spec'], cwd);
  return (result.stdout || result.stderr).trim();
}

export { existsSync };
