# -*- coding: utf-8 -*-
"""一道闸跑完本插件的全部交付前检查。

    python -X utf8 check.py            # 全跑，失败即停
    python -X utf8 check.py --fast     # 跳过变异（开发时提速）

检查项与「它到底在防什么」：

  1. sync_assets --check   插件里的 Python 运行时 == 产品源（按内容哈希）
                           → 防：插件出的报告与产品验证过的版本悄悄不一致
  2. node --check          语法
  3. smoke.mjs             执行层端到端（真跑 Python、真读产物）
                           → 防：装进宿主才发现 Python 找不到 / 产物读不回来
  4. host-sim.mjs          注册逻辑（真 cordis Context）
                           → 防：apply() 写了但没注册 / effect 清理漏掉
  5. e2e-tool.mjs          端到端（按工具 schema 调用）
                           → 防：注册了但模型按描述用不起来
                              （本轮真缺陷：工具只收内联 data，
                               而 SKILL.md 教用户传文件路径）
  6. mutate*.mjs           反向验证上面几条判据真的会红
                           → 防：判据写成恒真，测试全绿却挡不住任何东西
  7. pack-gate.mjs         发布包闸（npm pack 产出到底有什么）
                           → 防：`files` 写对了但目录展开把 __pycache__/*.pyc
                             一起收进 tarball（真踩过，130 KB 里 7 个字节码），
                             以及反向缺陷：files 少写一个目录，包变小、
                             装上去技能面或执行层直接空
  8. collision.mjs         产物不串号（两个产品同日同标的会撞同名文件）
                           → 防：后跑的报告覆盖先跑的（实测 7737→7174 字节），
                             两份都能打开、都不报错，只是内容被换掉
  9. sample-thresholds.mjs 样本量门槛三档（<250 拒绝 / 250–749 仅供参考 / >=750 正常）
                           → 防：样本太短也照常出分位，用户拿没有统计意义的
                             百分位当结论。判据锚「分位有没有被算出来」，
                             不锚「有没有报错」—— 短样本本来就是正常返回
 10. mutate-pack.mjs       反向验证第 7 条（5 条变异，含正向+反向两类）
 11. mutate-collision.mjs  反向验证第 8 条（2 条变异，均还原缺陷原始形态）
 12. mutate-sample.mjs     反向验证第 9 条（2 条变异：门槛归零 / 降级阈值下调）

为什么 4 与 5 必须同时存在：3 和 4 全绿只证明「实现能过用例」，
不证明「用例真在检查那条判据」。第 5 步是唯一能证伪这件事的东西。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(label: str, argv: list[str]) -> bool:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(argv, cwd=HERE, env=env)
    if proc.returncode != 0:
        print(f"\n✘ {label} 失败（exit {proc.returncode}）")
        return False
    print(f"✔ {label}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过变异验证")
    args = ap.parse_args()

    steps: list[tuple[str, list[str]]] = [
        ("1/12 资产同步闸（插件运行时 vs 产品源）", [sys.executable, "-X", "utf8", "sync_assets.py", "--check"]),
        ("2/12 JS 语法", ["node", "--check", "index.js"]),
        ("3/12 执行层烧烟（真跑 Python）", ["node", "test/smoke.mjs"]),
        ("4/12 注册逻辑（真 cordis Context）", ["node", "test/host-sim.mjs"]),
        ("5/12 端到端（按工具 schema 调用，含 data_path 文件入口）", ["node", "test/e2e-tool.mjs"]),
        ("6/12 发布包闸（tarball 里到底有什么）", ["node", "test/pack-gate.mjs"]),
        ("7/12 产物不串号（两产品同日同标的不互覆盖）", ["node", "test/collision.mjs"]),
        ("8/12 样本量门槛三档（拒绝 / 仅供参考 / 正常）", ["node", "test/sample-thresholds.mjs"]),
    ]
    if not args.fast:
        steps += [
            ("9/12 变异：执行层", ["node", "test/mutate.mjs"]),
            ("9b/12 变异：注册逻辑", ["node", "test/mutate-index.mjs"]),
            ("10/12 变异：发布包闸", ["node", "test/mutate-pack.mjs"]),
            ("11/12 变异：产物不串号", ["node", "test/mutate-collision.mjs"]),
            ("12/12 变异：样本量门槛", ["node", "test/mutate-sample.mjs"]),
        ]

    for label, argv in steps:
        if not run(label, argv):
            return 1

    print(f"\n{'=' * 60}")
    print("全部通过 ✔" if not args.fast else "快速模式通过 ✔（变异未跑）")
    if args.fast:
        print("交付前请跑完整的 `python -X utf8 check.py`。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
