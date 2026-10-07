#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
article-pipeline / 阶段 E —— 链接回收与表格回填

发布完成（或部分完成）后，把各平台拿到的链接一次性写回选题多维表格，
并把「状态」推进到目标值。写完后自动回读校验（record-batch-update 不校验
record_id 是否存在，回读是唯一可靠的确认方式）。

用法：
  python3 backfill_links.py --record <record_id> \
      --link "https://www.toutiao.com/item/1234567890123456789/" \
      --link "https://mp.weixin.qq.com/s/xxxx" --status 已发布

  python3 backfill_links.py --record <record_id> --status 已写待发 --dry-run

常用可选参数：
  --archive-doc <url>    同时回填「存档文档」列
  --all-links            多链接时全部写入（换行分隔）；默认只写第一条
  --no-verify            跳过回读校验（不推荐）
  --account "<账号名>"     飞书账号选择器（须为 larkswitch 托管版可用）
  --dry-run              只打印将要提交的 payload

退出码：
  0  写入且回读一致
  2  环境问题（找不到 lark-cli）
  3  授权 / 账号问题
  5  写入失败（可安全重试，写操作是幂等的）
  6  其他异常
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()

LARK_CLI_CANDIDATES = [
    HOME / "Library/Application Support/dev.LarkProfileConsole.Lark-Profile-Console/bin/lark-cli",
    HOME / ".workbuddy/binaries/node/cli-connector-packages/bin/lark-cli",
]

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "references" / "pipeline-config.json"

EXIT_OK = 0
EXIT_ENV = 2
EXIT_AUTH = 3
EXIT_BASE = 5
EXIT_OTHER = 6

AUTH_HINTS = [
    "not authenticated", "unauthorized", "invalid access token", "refresh_token",
    "no permission", "99991663", "99991664", "20005", "login required",
]
RETRY_HINTS = ["LPC_IO", "Operation not permitted"]

# 写入后读副本有秒级延迟，回读校验需要轮询
VERIFY_TRIES = 4
VERIFY_DELAY = 4


# --------------------------------------------------------------------------
# 基础工具（与 article-feishu-archive/scripts/archive_article.py 保持同款姿势）
# --------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def resolve_cli(explicit: str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    for cand in LARK_CLI_CANDIDATES:
        if cand.exists():
            return cand
    return None


def supports_account(cli: Path) -> bool:
    return "Lark-Profile-Console" in str(cli)


def build_cmd(cli: Path, account: str | None, args: list[str]) -> list[str]:
    cmd = [str(cli)]
    if account and supports_account(cli):
        cmd += ["--account", account]
    return cmd + args


def run_cli(cmd: list[str], cwd: Path, tries: int = 5, timeout: int = 180) -> tuple[int, str]:
    last = ""
    for attempt in range(1, tries + 1):
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True,
                env=os.environ.copy(), timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            last = f"[timeout] 命令超过 {timeout}s 未返回"
            out, rc = last, 1
        if not any(h in out for h in RETRY_HINTS):
            return rc, out
        if attempt < tries:
            time.sleep(3)
    return 1, last


def parse_json_blob(out: str) -> dict | None:
    out = (out or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except Exception:
        pass
    start, end = out.find("{"), out.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(out[start:end + 1])
        except Exception:
            return None
    return None


def is_auth_failure(out: str) -> bool:
    low = (out or "").lower()
    return any(h.lower() in low for h in AUTH_HINTS)


def cell_to_text(v) -> str:
    """把回读到的 CellValue 压成可比对的字符串。

    lark-cli 的 +record-get --format json 会把含 URL 的文本字段渲染成
    Markdown 链接 `[url](url)`，这里要把外层语法剥掉，否则比对必然失败。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        m = re.fullmatch(r"\[([^\]]*)\]\(([^)]*)\)", v.strip())
        if m:
            return (m.group(2) or m.group(1)).strip()
        return v
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, list):
        parts = []
        for it in v:
            if isinstance(it, dict):
                parts.append(str(it.get("text") or it.get("name") or it.get("id") or it))
            else:
                parts.append(str(it))
        return " ".join(parts)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v.get("link") or v)
    return str(v)


def flatten_fields(obj: dict) -> dict:
    """把 +record-get / +record-list 的各种返回外形统一成 {字段名: 值}。

    实际观察到的主外形（lark-cli 1.0.86，--format json）：
        {"ok":true,"data":{"data":[[v1,v2,v3]],"fields":["选题","状态",...],
                           "field_id_list":[...],"record_id_list":[...]}}
    也可能出现 {record:{fields:{...}}} / {fields:{...}} 等变体。
    """
    node = obj
    if isinstance(node, dict) and isinstance(node.get("data"), dict):
        node = node["data"]

    # 主外形：二维值数组 + 字段名列表
    if isinstance(node, dict):
        names = node.get("fields")
        rows = node.get("data")
        if isinstance(names, list) and isinstance(rows, list) and rows:
            row = rows[0]
            if isinstance(row, list):
                return {names[i]: row[i] for i in range(min(len(names), len(row)))}
            if isinstance(row, dict):
                return row

    for key in ("record", "records"):
        if isinstance(node, dict) and key in node:
            nxt = node[key]
            if isinstance(nxt, list) and nxt:
                node = nxt[0]
            elif isinstance(nxt, dict):
                node = nxt
    if isinstance(node, dict) and isinstance(node.get("fields"), dict):
        return node["fields"]
    if isinstance(node, dict) and isinstance(node.get("record"), dict):
        return flatten_fields(node["record"])
    return node if isinstance(node, dict) else {}


def read_back(cli: Path, account: str | None, base_token: str, table_id: str,
              record_id: str, fields: list[str]) -> tuple[dict | None, str]:
    args = ["base", "+record-get",
            "--base-token", base_token, "--table-id", table_id,
            "--record-id", record_id, "--as", "user", "--format", "json"]
    for f in fields:
        args += ["--field-id", f]
    rc, out = run_cli(build_cmd(cli, account, args), Path.cwd())
    return parse_json_blob(out), out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    cfg = load_config()
    base_cfg = cfg.get("base", {})
    field_cfg = cfg.get("fields", {})
    status_cfg = cfg.get("status", {})

    ap = argparse.ArgumentParser(description="把发布链接与状态回填到选题多维表格")
    ap.add_argument("--record", required=True, help="选题表的 record_id")
    ap.add_argument("--link", action="append", default=[],
                    help="发布链接，可重复；默认只写第一条（按给定顺序即优先级）")
    ap.add_argument("--all-links", action="store_true", help="多链接全部写入（换行分隔）")
    ap.add_argument("--status", default=status_cfg.get("published", "已发布"),
                    help="要写入的状态值（select 选项名）")
    ap.add_argument("--archive-doc", default=None, help="顺带回填「存档文档」列")
    ap.add_argument("--account", default=os.environ.get("ARTICLE_PIPELINE_ACCOUNT")
                    or cfg.get("account"))
    ap.add_argument("--base-token", default=base_cfg.get("base_token"))
    ap.add_argument("--table-id", default=base_cfg.get("table_id"))
    ap.add_argument("--cli", default=None)
    ap.add_argument("--log-dir", default=".pipeline", help="回填日志目录，默认 ./.pipeline")
    ap.add_argument("--no-verify", action="store_true", help="跳过回读校验")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cwd = Path.cwd()
    f_status = field_cfg.get("status", "状态")
    f_link = field_cfg.get("article_link", "文章链接")
    f_doc = field_cfg.get("archive_doc", "存档文档")

    links = [x.strip() for x in args.link if x and x.strip()]
    if not links:
        print(json.dumps({"ok": False, "stage": "args",
                          "error": "至少给一个 --link（发布链接）"},
                         ensure_ascii=False, indent=2))
        return EXIT_OTHER

    link_value = "\n".join(links) if args.all_links else links[0]

    record_fields: dict[str, object] = {
        f_link: link_value,
        f_status: [args.status],
    }
    if args.archive_doc:
        record_fields[f_doc] = args.archive_doc

    payload = {"update_records": {args.record: record_fields}}

    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "record": args.record,
                          "payload": payload, "links_received": links,
                          "note": "多链接时默认只写第一条；要全写加 --all-links"},
                         ensure_ascii=False, indent=2))
        return EXIT_OK

    if not (args.base_token and args.table_id):
        print(json.dumps({"ok": False, "stage": "config",
                          "error": "缺少 base_token / table_id，检查 references/pipeline-config.json"},
                         ensure_ascii=False, indent=2))
        return EXIT_ENV

    cli = resolve_cli(args.cli)
    if not cli:
        print(json.dumps({"ok": False, "stage": "env",
                          "error": "找不到 lark-cli（larkswitch 或 WorkBuddy 飞书连接器）"},
                         ensure_ascii=False, indent=2))
        return EXIT_ENV
    account = args.account if supports_account(cli) else None

    logdir = Path(args.log_dir)
    if not logdir.is_absolute():
        logdir = cwd / logdir
    logdir.mkdir(parents=True, exist_ok=True)

    json_path = logdir / f"backfill_{args.record}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rel_json = os.path.relpath(json_path, cwd)

    result: dict = {
        "ok": False,
        "record": args.record,
        "wrote": record_fields,
        "links_received": links,
        "link_written": link_value,
    }

    args_list = ["base", "+record-batch-update",
                 "--base-token", args.base_token,
                 "--table-id", args.table_id,
                 "--as", "user",
                 "--json", f"@./{rel_json}"]
    rc, out = run_cli(build_cmd(cli, account, args_list), cwd)
    blob = parse_json_blob(out)
    if not (blob and blob.get("ok")):
        result["stage"] = "update"
        result["error"] = "回填失败（写操作幂等，可直接重试）"
        result["raw"] = (out or "").strip()[:1200]
        if is_auth_failure(out):
            result["error"] = "飞书授权失败：请在 larkswitch 中对目标账号完成登录/授权"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return EXIT_AUTH
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_BASE

    result["ok"] = True
    result["updated"] = list((blob.get("data") or {}).get("records") or []) or "accepted"

    if not args.no_verify:
        want = [f_link, f_status] + ([f_doc] if args.archive_doc else [])
        link_back = status_back = ""
        got = None
        raw = ""
        # 写入后读副本存在秒级延迟（实测踩过：紧接着回读会读到旧值 → 误报不一致），
        # 因此这里轮询重试，只有连续失败才判定为真不一致。
        for attempt in range(1, VERIFY_TRIES + 1):
            got, raw = read_back(cli, account, args.base_token, args.table_id, args.record, want)
            if got is not None:
                fields = flatten_fields(got)
                link_back = cell_to_text(fields.get(f_link))
                status_back = cell_to_text(fields.get(f_status))
                if (link_back and link_back in link_value or link_value in link_back) \
                        and args.status in status_back:
                    break
            if attempt < VERIFY_TRIES:
                time.sleep(VERIFY_DELAY)

        if got is None:
            result["verified"] = False
            result["verify_note"] = "回读未拿到 JSON，请人工核对一次"
            result["verify_raw"] = (raw or "").strip()[:600]
        else:
            ok_link = bool(link_back) and (link_value in link_back or link_back in link_value)
            ok_status = args.status in status_back
            result["verified"] = bool(ok_link and ok_status)
            result["read_back"] = {f_link: link_back, f_status: status_back}
            if not result["verified"]:
                result["ok"] = False
                result["error"] = (f"写入已提交，但 {VERIFY_TRIES} 次回读值仍不一致，"
                                   "请人工核对表格")

    (logdir / f"backfill_{args.record}_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK if result["ok"] else EXIT_BASE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_OTHER)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "stage": "unexpected", "error": repr(exc)},
                         ensure_ascii=False))
        sys.exit(EXIT_OTHER)
