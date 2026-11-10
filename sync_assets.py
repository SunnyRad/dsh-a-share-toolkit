# -*- coding: utf-8 -*-
"""把 products/ 里的产品运行时同步进插件 assets/，并校验没有漂移。

**为什么需要这个脚本，而不是手工拷一次。**

本目录是多会话共享工作区。2026-11-10 01:51 实测：另一个会话正在改
`products/a-share-valuation-percentile/`（视觉核查修重复文案，动了
analysis.py 与 report.py）。任何一次手工 `Copy-Item` 都会把插件钉在一个
当时恰好读到的版本上，之后没人知道它落后了——插件出的报告和产品出的报告
悄悄不一致，而这正是最难查的那类缺陷：两边各自「跑得通」。

    python -X utf8 sync_assets.py            # 同步 + 校验
    python -X utf8 sync_assets.py --check    # 只校验（CI / 交付前）

--check 的判据是**内容哈希**，不是时间戳：时间戳会被 checkout 改掉，
内容才是「插件里跑的东西等于产品里验证过的东西」这件事本身。

退出码：0 = 一致；1 = 有漂移。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS = os.path.join(HERE, "..", "..", "products")
ASSETS = os.path.join(HERE, "assets")

# 产品目录 → 插件内的名字。只同步「运行时」，不拉 tests / tools / dist / reports。
LAYOUT = {
    "a-share-valuation-percentile": "valuation",
    "a-share-financial-checkup": "financial",
}

# 只收这些子目录下的 .py：产品 CLI 与它 import 的同级模块。
RUNTIME_GLOB_DIRS = ("scripts",)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _runtime_files(product_dir: str) -> dict[str, str]:
    """产品里应进插件的文件：{相对路径: 绝对路径}。"""
    out: dict[str, str] = {}
    for sub in RUNTIME_GLOB_DIRS:
        src = os.path.join(product_dir, sub)
        if not os.path.isdir(src):
            continue
        for name in sorted(os.listdir(src)):
            if not name.endswith(".py"):
                continue
            if name.startswith("_"):  # 开发期一次性脚本不进包
                continue
            out[f"{sub}/{name}"] = os.path.join(src, name)
    return out


def _skill_files(product_dir: str) -> dict[str, str]:
    """SKILL.md 也要同步：技能面靠它的 description 立技能目录。"""
    p = os.path.join(product_dir, "SKILL.md")
    return {"SKILL.md": p} if os.path.isfile(p) else {}


def sync(write: bool) -> list[str]:
    """返回漂移清单；write=True 时顺带把差异写过去。"""
    drift: list[str] = []

    for product_name, asset_name in LAYOUT.items():
        product_dir = os.path.normpath(os.path.join(PRODUCTS, product_name))
        if not os.path.isdir(product_dir):
            drift.append(f"缺失产品目录: {product_dir}")
            continue

        expected = _runtime_files(product_dir)
        expected.update(_skill_files(product_dir))

        # 目标位置：scripts 进 assets/<name>/scripts/，SKILL.md 进 skills/<原名>/
        for rel, src in expected.items():
            if rel == "SKILL.md":
                dst = os.path.join(HERE, "skills", product_name, "SKILL.md")
            else:
                dst = os.path.join(ASSETS, asset_name, rel)

            src_hash = _sha256(src)
            if os.path.isfile(dst):
                dst_hash = _sha256(dst)
                if src_hash == dst_hash:
                    continue
                reason = "内容不一致"
            else:
                reason = "目标缺失"

            drift.append(f"{product_name}/{rel}  {reason}")
            if write:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

        # 反向：插件里有、产品里没有的 .py，是删不掉的旧货
        asset_scripts = os.path.join(ASSETS, asset_name, "scripts")
        if os.path.isdir(asset_scripts):
            for name in sorted(os.listdir(asset_scripts)):
                if not name.endswith(".py"):
                    continue
                if f"scripts/{name}" in expected:
                    continue
                drift.append(f"{product_name}/scripts/{name}  产品侧已不存在（残留）")
                if write:
                    os.remove(os.path.join(asset_scripts, name))

    return drift


def main() -> int:
    ap = argparse.ArgumentParser(description="同步/校验插件 assets 与产品源的一致性")
    ap.add_argument("--check", action="store_true", help="只校验，不写任何文件")
    args = ap.parse_args()

    drift = sync(write=not args.check)

    if not drift:
        print("assets 与产品源一致（按内容哈希逐文件比对）✔")
        return 0

    verb = "已同步" if not args.check else "漂移"
    print(f"{verb} {len(drift)} 项：")
    for line in drift:
        print(f"  - {line}")

    if args.check:
        print("\n插件内的运行时落后于产品源。跑 `python -X utf8 sync_assets.py` 同步后重试。")
        return 1

    # 写完自证：再校验一遍必须干净，否则同步本身有问题
    recheck = sync(write=False)
    if recheck:
        print(f"\n同步后仍有 {len(recheck)} 项不一致 —— 同步失效：")
        for line in recheck:
            print(f"  - {line}")
        return 1
    print("同步后复验一致 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
