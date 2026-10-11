#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
article-pipeline / 阶段 D-2 辅助 —— 一次生成头条发布要用的 3 条 evaluate 表达式

头条编辑器有三处**必须**用页面内 JS 注入，常规浏览器自动化命令都会失败：

  1. 标题 —— textarea 是 React 受控组件，直接赋值不触发状态更新，
             必须用 HTMLTextAreaElement.prototype.value 的 native setter + 派发 input/change
  2. 正文 —— ProseMirror 场景下 `bsk fill` 必报 "the fill target changed during the action"
             （聚焦触发 React 重渲染，元素句柄失效），只能注入 ClipboardEvent paste
  3. 封面 —— `bsk upload --mode input` 报 Not allowed（扩展缺文件网址权限）、
             `--mode drop` 报 No node found，只能 base64 在页面内自建 File

本脚本把这三段 JS 一次生成到文件，省得每次现场拼 shell 转义。

用法：
  python3 mk_toutiao_js.py --title "<文章标题>" --cover <封面图路径> \
      [--body dist/toutiao_body.txt] [--outdir /tmp/tt_js]

生成：
  <outdir>/title.js   <outdir>/body.js   <outdir>/cover.js

随后在**同一个 session 上串行**执行：

  bsk evaluate --session <sid> "$(cat <outdir>/title.js)"
  bsk evaluate --session <sid> "$(cat <outdir>/body.js)"
  bsk click    --session <sid> --selector ".article-cover-add"     # 必须先点开，input 才会出现
  bsk evaluate --session <sid> "$(cat <outdir>/cover.js)"
  # 再依次：点面板里的「确定」关掉 → 勾选「个人观点，仅供参考」
  #        → 「预览并发布」→ 弹窗里点「确认发布」（两层都要点）

判据提醒（都是踩过的坑）：
  · 封面注入会返回 `assigned: 0` —— 头条处理完会清空 input.files，这是**假阴性**。
    以页面文案「已上传 N 张图片，支持拖拽调整图片顺序」+ 缩略图为准。
  · 正文注入返回的 `paras` 通常比源文件非空行**多 1**（末尾空段），属正常。
  · 标题注入返回 `len` 应用来选择，且 ≤ 30 字（头条上限）。
  · 封面体积建议先压到 100KB 以内：`sips -Z 1200 -s formatOptions 72 <in> --out <out>`

退出码：0 成功；2 输入文件缺失。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_INPUT = 2

COVER_MAX_KB = 100
TITLE_MAX = 30


def build_title_js(title: str) -> str:
    return """(function(){
  var el = document.querySelector('textarea[placeholder*="请输入文章标题"]');
  if (!el) return JSON.stringify({ok:false, err:'title textarea not found'});
  var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return JSON.stringify({ok:true, len: el.value.length, val: el.value});
})()""" % json.dumps(title, ensure_ascii=False)


def build_body_js(body: str) -> str:
    return """(function(){
  var text = %s;
  var el = document.querySelector('div.ProseMirror');
  if (!el) return JSON.stringify({ok:false, err:'ProseMirror not found'});
  el.focus();
  var dt = new DataTransfer();
  dt.setData('text/plain', text);
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  var ps = el.querySelectorAll('p');
  return JSON.stringify({ok:true, paras: ps.length, chars: el.innerText.replace(/\\s/g,'').length});
})()""" % json.dumps(body, ensure_ascii=False)


def build_cover_js(b64: str) -> str:
    return """(function(){
  var b64 = "%s";
  var bin = atob(b64);
  var arr = new Uint8Array(bin.length);
  for (var i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
  var f = new File([arr], "cover.jpg", {type:"image/jpeg"});
  var input = document.querySelector(".btn-upload-handle input[type=file]");
  if(!input) return JSON.stringify({ok:false,err:"no .btn-upload-handle input[type=file] (先点 .article-cover-add 打开面板)"});
  var dt = new DataTransfer();
  dt.items.add(f);
  input.files = dt.files;
  input.dispatchEvent(new Event("change", {bubbles:true}));
  return JSON.stringify({ok:true, assigned: input.files.length, size: f.size, note: "assigned=0 是假阴性，以页面文案为准"});
})()""" % b64


def main() -> int:
    ap = argparse.ArgumentParser(description="生成头条发布的 3 条 evaluate 表达式")
    ap.add_argument("--title", required=True, help="文章标题（≤30 字）")
    ap.add_argument("--cover", required=True, help="封面图路径（建议 <100KB）")
    ap.add_argument("--body", default="dist/toutiao_body.txt", help="头条正文纯文本，默认 dist/toutiao_body.txt")
    ap.add_argument("--outdir", default="/tmp/tt_js", help="输出目录，默认 /tmp/tt_js")
    args = ap.parse_args()

    body_path = Path(args.body)
    cover_path = Path(args.cover)
    missing = [str(p) for p in (body_path, cover_path) if not p.exists()]
    if missing:
        print(json.dumps({"ok": False, "error": "文件不存在", "missing": missing}, ensure_ascii=False))
        return EXIT_INPUT

    body = body_path.read_text(encoding="utf-8")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    (outdir / "title.js").write_text(build_title_js(args.title), encoding="utf-8")
    (outdir / "body.js").write_text(build_body_js(body), encoding="utf-8")
    (outdir / "cover.js").write_text(
        build_cover_js(base64.b64encode(cover_path.read_bytes()).decode()), encoding="utf-8"
    )

    cover_kb = cover_path.stat().st_size / 1024
    warnings = []
    if len(args.title) > TITLE_MAX:
        warnings.append(f"标题 {len(args.title)} 字，超过头条上限 {TITLE_MAX}")
    if cover_kb > COVER_MAX_KB:
        warnings.append(
            f"封面 {cover_kb:.0f}KB 超过建议的 {COVER_MAX_KB}KB，"
            f"先跑 sips -Z 1200 -s formatOptions 72 压缩"
        )

    print(json.dumps({
        "ok": True,
        "outdir": str(outdir),
        "files": {n: str(outdir / f"{n}.js") for n in ("title", "body", "cover")},
        "title_len": len(args.title),
        "body_chars": len(body),
        "body_lines": len([l for l in body.split("\n") if l.strip()]),
        "cover_kb": round(cover_kb, 1),
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": repr(exc)}, ensure_ascii=False))
        sys.exit(2)
