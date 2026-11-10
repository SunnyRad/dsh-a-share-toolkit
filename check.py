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
        ("1/6 资产同步闸（插件运行时 vs 产品源）", [sys.executable, "-X", "utf8", "sync_assets.py", "--check"]),
        ("2/6 JS 语法", ["node", "--check", "index.js"]),
        ("3/6 执行层烧烟（真跑 Python）", ["node", "test/smoke.mjs"]),
        ("4/6 注册逻辑（真 cordis Context）", ["node", "test/host-sim.mjs"]),
        ("5/6 端到端（按工具 schema 调用，含 data_path 文件入口）", ["node", "test/e2e-tool.mjs"]),
    ]
    if not args.fast:
        steps += [
            ("6a/6 变异：执行层", ["node", "test/mutate.mjs"]),
            ("6b/6 变异：注册逻辑", ["node", "test/mutate-index.mjs"]),
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
