#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
article-pipeline / 开跑前体检

把散落在各处的环境约束一次性检查完，避免跑到一半才发现 CLI 没有 / 账号不对 /
沙箱没关 / 产物会覆盖。体检只读、不写任何东西、不发网络请求（默认）。

用法：
  python3 preflight.py                    # 基础体检
  python3 preflight.py --topic 04         # 加上选题 04 的产物冲突检查
  python3 preflight.py --article ./article_xxx.md

退出码：
  0  全部通过（可能有 warn）
  2  有 error（环境不满足，先修再跑）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "references" / "pipeline-config.json"

LARK_CLI_CANDIDATES = [
    HOME / "Library/Application Support/dev.LarkProfileConsole.Lark-Profile-Console/bin/lark-cli",
    HOME / ".workbuddy/binaries/node/cli-connector-packages/bin/lark-cli",
]
BSK = HOME / ".local/bin/bsk"
DESIGNKIT = HOME / ".workbuddy/binaries/node/cli-connector-packages/bin/designkit"

REQUIRED_CONFIG = [
    ("account", "飞书账号选择器"),
    ("base.base_token", "选题表 base token"),
    ("base.table_id", "选题表 table id"),
    ("fields.status", "状态列名"),
    ("fields.archive_doc", "存档文档列名"),
    ("fields.article_link", "文章链接列名"),
    ("yanxu.account_id", "言序绑定的公众号 id"),
    ("toutiao.publish_url", "头条图文发布页"),
]


def dig(cfg: dict, dotted: str):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def probe_version(cmd: list[str], timeout: int = 20) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[0][:80]
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="文章流水线开跑前体检")
    ap.add_argument("--topic", default=None, help="选题编号，用于产物冲突检查")
    ap.add_argument("--article", default=None, help="稿件路径，用于存在性与格式检查")
    ap.add_argument("--dist", default="dist", help="产物目录，默认 ./dist")
    args = ap.parse_args()

    cwd = Path.cwd()
    checks: list[dict] = []

    def add(name: str, level: str, detail: str, fix: str = "") -> None:
        checks.append({"check": name, "level": level, "detail": detail, "fix": fix})

    # --- 1. Python ------------------------------------------------------
    v = sys.version_info
    add("python", "ok" if v >= (3, 9) else "error",
        f"{v.major}.{v.minor}.{v.micro}", "需要 Python 3.9+")

    # --- 2. lark-cli ----------------------------------------------------
    cli = next((c for c in LARK_CLI_CANDIDATES if c.exists()), None)
    if not cli:
        add("lark-cli", "error", "两个候选路径都不存在",
            "安装 larkswitch，或启用 WorkBuddy 飞书连接器")
    else:
        has_account = "Lark-Profile-Console" in str(cli)
        add("lark-cli", "ok",
            f"{cli}（{'支持 --account' if has_account else '不支持 --account，身份固定'}）")
        if not has_account:
            add("lark-cli 账号", "warn",
                "当前落在 WorkBuddy 连接器版，身份固定为连接器绑定的账号，无法用 --account 切换",
                "若与 pipeline-config.json 的 account 不一致，请改用 larkswitch 托管版")

    # --- 3. bsk（头条发布）----------------------------------------------
    if BSK.exists():
        ver = (probe_version([str(BSK), "--version"]) or "").strip()
        if ver.startswith("bsk"):
            ver = ver[3:].strip()
        add("bsk", "ok", f"{BSK.name} {ver}".strip())
    else:
        add("bsk", "warn", f"未找到 {BSK}",
            "头条自动发布将无法执行；可在浏览器里手工发布")

    # --- 4. designkit（配图）---------------------------------------------
    if DESIGNKIT.exists():
        add("designkit", "ok", str(DESIGNKIT))
    else:
        add("designkit", "warn", f"未找到 {DESIGNKIT}",
            "配图技能可能无法调用；可换用内置图片生成")

    # --- 5. 配置 --------------------------------------------------------
    if not CONFIG_PATH.exists():
        add("pipeline-config.json", "error", f"不存在: {CONFIG_PATH}",
            "复制 references/pipeline-config.example.json 为 pipeline-config.json 并填写")
        cfg: dict = {}
    else:
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            cfg = {}
            add("pipeline-config.json", "error", f"解析失败: {exc}")
        missing = [f"{k}（{desc}）" for k, desc in REQUIRED_CONFIG if not dig(cfg, k)]
        unfilled = [
            k for k, _ in REQUIRED_CONFIG
            if isinstance(dig(cfg, k), str)
            and ("YOUR_" in dig(cfg, k) or "REPLACE_ME" in dig(cfg, k))
        ]
        if missing:
            add("pipeline-config.json", "error", "缺少字段: " + "、".join(missing),
                "复制 references/pipeline-config.example.json 后填写")
        elif unfilled:
            add("pipeline-config.json", "error",
                "仍是模板占位符: " + "、".join(unfilled),
                "参照 pipeline-config.example.json 顶部的 _how_to 填真实值")
        else:
            add("pipeline-config.json", "ok", "字段齐全")

    # --- 6. 言序 MCP = 公众号草稿箱通道 -----------------------------------
    if cfg.get("yanxu", {}).get("account_id"):
        add("言序 / 公众号通道", "ok",
            f"公众号「{cfg['yanxu'].get('account_name', '?')}」"
            f"，主题 {cfg['yanxu'].get('theme_name', cfg['yanxu'].get('theme_id', '默认'))}")
    add("言序 MCP", "info", "脚本无法探测 MCP；调用前请确认 `mcp__yanxu__*` 四工具可用")

    # --- 7. 工作目录 ----------------------------------------------------
    dist = Path(args.dist)
    if not dist.is_absolute():
        dist = cwd / dist
    if dist.exists():
        stale = sorted(p.name for p in dist.iterdir())
        add("产物目录", "warn" if stale else "ok",
            f"{dist} 已存在，内含 {len(stale)} 个文件: {', '.join(stale[:6])}",
            "确认是否是上一轮的残留；要重跑先清理该目录")
    else:
        add("产物目录", "ok", f"{dist} 不存在，将自动创建")

    # --- 8. 稿件 --------------------------------------------------------
    if args.article:
        src = Path(args.article).expanduser()
        if not src.exists():
            add("稿件", "error", f"不存在: {src}")
        else:
            text = src.read_text(encoding="utf-8")
            has_body = "【正文】" in text
            ascii_q = text.count('"')
            add("稿件", "ok" if has_body else "error",
                f"{src.name}｜{len(text)} 字符｜【正文】{'有' if has_body else '缺失'}"
                f"｜ASCII 直引号 {ascii_q} 个（阶段 B 会自动转中文弯引号）",
                "" if has_body else "稿件缺少【正文】章节，阶段 B 会退出码 3")

    if args.topic:
        add("选题", "info", f"选题 {args.topic}（表格行需自行确认 record_id）")

    # --- 汇总 ----------------------------------------------------------
    errors = [c for c in checks if c["level"] == "error"]
    warns = [c for c in checks if c["level"] == "warn"]
    report = {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warns),
        "checks": checks,
        "cwd": str(cwd),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "stage": "unexpected", "error": repr(exc)},
                         ensure_ascii=False))
        sys.exit(2)
