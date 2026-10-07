#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
article-pipeline / 阶段 B —— 一稿转多份平台格式

输入：本技能家族写作技能的固定输出 Markdown（见 references/platform-specs.md）
输出（默认落在 ./dist/）：
  toutiao_body.txt     头条号编辑器可直接粘贴的纯文本（逐行一段）
  toutiao_meta.json    头条发布元信息（标题、字数、话题、声明）
  yanxu_payload.json   言序（公众号草稿箱）create_article 的 payload
  render_report.json   本次渲染统计与告警（供阶段 C/D/E 与人工复核使用）

设计要点：
  * 格式转换集中在这里一处，头条 / 言序不再各自转一次。
  * 言序 payload 做强校验：content 里出现 "<" 直接报错退出（历史上曾误塞 <b> 进正文）。
  * 引号统一 ASCII " → 中文 “”。中文排版下直引号观感差，且两平台都要处理。

用法：
  python3 render_platforms.py --article ./article_xxx.md
  python3 render_platforms.py --article ./article_xxx.md --topic 04
  python3 render_platforms.py --article ./article_xxx.md --only yanxu --cover-url https://.../c.jpg
  python3 render_platforms.py --article ./article_xxx.md --dry-run

退出码：
  0  成功
  2  输入文件问题（不存在 / 编码错）
  3  解析失败（缺【正文】章节，或正文为空）
  4  平台校验失败（标题超长、content 含 HTML 标签等）
  6  其他异常
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_PARSE = 3
EXIT_VALIDATE = 4
EXIT_OTHER = 6

YANXU_TITLE_MAX = 30
YANXU_ABSTRACT_MAX = 100

RE_SECTION = re.compile(r"^##\s*[【\[](.*?)[】\]]\s*$")
RE_SEQ = re.compile(r"^\*\*\s*(\d{1,2}|[一二三四五六七八九十]+)\s*\*\*$")
RE_HEADING = re.compile(r"^#{3,4}\s+(.+?)\s*$")
RE_NUM_ITEM = re.compile(r"^(\d{1,2})[.、]\s+(.*)$")
RE_SUBNOTE = re.compile(r"^(?:→|->)\s*(.*)$")
RE_BULLET = re.compile(r"^(?:[·•]|[-*+])\s*(.*)$")
RE_TRAIL_PAREN = re.compile(r"[（(][^（()）]*[）)]\s*$")
RE_TAG = re.compile(r"#([^\s#]{2,12})")
RE_HTMLISH = re.compile(r"<[A-Za-z/!][^>]*>")


# --------------------------------------------------------------------------
# 文本规整
# --------------------------------------------------------------------------

def cn_quotes(text: str) -> str:
    """把成对的 ASCII 直引号转成中文弯引号；逐行独立配对。"""
    out: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            out.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


def strip_inline(text: str) -> str:
    """去掉行内 Markdown 标记，保留纯文本。"""
    t = text.strip()
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)   # 图片
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)     # 链接
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)             # 粗体
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", t)  # 斜体
    t = re.sub(r"`([^`]*)`", r"\1", t)                 # 行内代码
    t = t.lstrip(">").strip()
    return cn_quotes(t)


def _next_nonempty(lines: list[str], i: int) -> tuple[int, str]:
    j, n = i, len(lines)
    while j < n and not lines[j].strip():
        j += 1
    return (j, lines[j].strip()) if j < n else (n, "")


def count_cjk(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def truncate_soft(text: str, limit: int) -> str:
    """按标点就近截断，避免把句子拦腰砍断。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for punct in "。！？；，、":
        idx = window.rfind(punct)
        if idx >= limit * 0.6:
            return window[: idx + 1]
    return window


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def split_sections(md: str) -> tuple[str, list[str], dict[str, list[str]]]:
    """返回 (标题, 元信息行, {章节名: 原始行列表})。"""
    title, meta, sections, order = "", [], {}, []
    current: str | None = None
    in_meta = False
    for raw in md.splitlines():
        s = raw.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            in_meta = True
            continue
        m = RE_SECTION.match(s)
        if m:
            current = m.group(1).strip()
            in_meta = False
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if in_meta and s.startswith(">"):
            meta.append(s.lstrip(">").strip())
            continue
        if s and not s.startswith(">"):
            in_meta = False
        if current:
            sections[current].append(raw)
    _ = order
    return title, meta, sections


def parse_body(body: list[str]) -> list[dict]:
    """把【正文】解析成 [{seq, heading, paras}]。"""
    chapters: list[dict] = []
    for raw in body:
        s = raw.strip()
        if not s or s == "---":
            continue
        ms = RE_SEQ.match(s)
        if ms:
            chapters.append({"seq": ms.group(1), "heading": None, "paras": []})
            continue
        mh = RE_HEADING.match(s)
        if mh:
            if chapters and chapters[-1]["heading"] is None and not chapters[-1]["paras"]:
                chapters[-1]["heading"] = strip_inline(mh.group(1))
            else:
                chapters.append({"seq": "", "heading": strip_inline(mh.group(1)), "paras": []})
            continue
        if not chapters:
            chapters.append({"seq": "", "heading": None, "paras": []})
        chapters[-1]["paras"].append(strip_inline(s))
    return [c for c in chapters if c["paras"] or c["heading"]]


def parse_title_candidates(lines: list[str]) -> list[dict]:
    """解析【备选标题】，返回 [{text, recommended, note}]。"""
    items: list[dict] = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        m = RE_NUM_ITEM.match(s)
        if not m:
            i += 1
            continue
        text = strip_inline(m.group(2))
        text = RE_TRAIL_PAREN.sub("", text).strip()
        rec = "\u2605" in s
        notes: list[str] = []
        i += 1
        while i < n:
            cur = lines[i].strip()
            if RE_NUM_ITEM.match(cur):
                break
            mn = RE_SUBNOTE.match(cur)
            if mn:
                note = strip_inline(mn.group(1))
                notes.append(note)
                if "\u2605" in note or "推荐" in note:
                    rec = True
                i += 1
                continue
            if cur and not mn:
                # 续行（无前缀的说明行）
                if notes and not RE_SUBNOTE.match(cur):
                    notes[-1] += cur
                i += 1
                continue
            i += 1
        items.append({"text": text, "recommended": rec, "note": " ".join(notes).strip()})
    return items


def parse_bullets(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s or s == "---":
            continue
        m = RE_BULLET.match(s)
        if m and m.group(1):
            out.append(strip_inline(m.group(1)))
        elif out and not RE_SECTION.match(s):
            out[-1] += s.strip()
    return out


def pick_title(candidates: list[dict], fallback: str) -> tuple[str, str]:
    for c in candidates:
        if c["recommended"] and c["text"]:
            return c["text"], "recommended"
    for c in candidates:
        if c["text"]:
            return c["text"], "first-candidate"
    return fallback, "doc-title"


# --------------------------------------------------------------------------
# 构建各平台产物
# --------------------------------------------------------------------------

def build_toutiao_body(chapters: list[dict]) -> str:
    lines: list[str] = []
    for ch in chapters:
        marker = ch["heading"] or ch["seq"]
        if marker:
            lines.append(marker)
        lines.extend(ch["paras"])
    return "\n".join(lines) + "\n"


def build_yanxu_payload(title: str, chapters: list[dict], intro_mode: str,
                        abstract: str, cover_url: str) -> tuple[dict, list[str]]:
    warnings: list[str] = []

    if intro_mode == "first-chapter":
        intro_paras = chapters[0]["paras"] if chapters else []
        rest = chapters[1:]
    elif intro_mode == "none":
        intro_paras = []
        rest = chapters
    else:  # all-chapters
        intro_paras = []
        rest = chapters

    intro = "\n\n".join(intro_paras)

    if not abstract:
        base = intro_paras[0] if intro_paras else (chapters[0]["paras"][0] if chapters else "")
        abstract = truncate_soft(base, YANXU_ABSTRACT_MAX)

    if len(title) > YANXU_TITLE_MAX:
        warnings.append(f"标题 {len(title)} 字超出言序上限 {YANXU_TITLE_MAX}，已截断")
        title = title[:YANXU_TITLE_MAX]
    if len(abstract) > YANXU_ABSTRACT_MAX:
        warnings.append(f"摘要 {len(abstract)} 字超出言序上限 {YANXU_ABSTRACT_MAX}，已截断")
        abstract = truncate_soft(abstract, YANXU_ABSTRACT_MAX)

    out_chapters = []
    for ch in rest:
        ctitle = ch["heading"] or ch["seq"]
        if not ctitle:
            warnings.append("存在无序号也无小标题的章节，已用「正文」占位")
            ctitle = "正文"
        blocks = [{"type": "paragraph", "content": p} for p in ch["paras"] if p.strip()]
        out_chapters.append({"type": "chapter", "title": ctitle, "blocks": blocks})

    payload = {
        "title": title,
        "abstract": abstract,
        "cover_img": cover_url or "",
        "intro": intro,
        "chapters": out_chapters,
    }
    return payload, warnings


def validate_yanxu(payload: dict) -> list[str]:
    errors: list[str] = []
    if not payload.get("title"):
        errors.append("标题为空")
    if not payload.get("chapters"):
        errors.append("chapters 不能为空（言序会拒绝）")

    flat: list[tuple[str, str]] = [("intro", payload.get("intro", ""))]
    for ci, ch in enumerate(payload.get("chapters", [])):
        flat.append((f"chapter[{ci}].title", ch.get("title", "")))
        for bi, b in enumerate(ch.get("blocks", [])):
            content = b.get("content") or ""
            flat.append((f"chapter[{ci}].blocks[{bi}].content", content))

    for where, text in flat:
        if not isinstance(text, str):
            continue
        if "<" in text or ">" in text:
            m = RE_HTMLISH.search(text)
            snippet = (m.group(0) if m else text[:60])
            errors.append(f"{where} 含 HTML 标签或尖括号：{snippet!r}（言序 content 必须是纯文本）")
        if "**" in text:
            errors.append(f"{where} 残留 Markdown 粗体标记 **")
    return errors


def build_toutiao_meta(chapters: list[dict], title: str, tags: list[str],
                       declare: str, platform_max_title: int) -> dict:
    body_paras = [p for ch in chapters for p in ch["paras"]]
    full = "\n".join([ch["heading"] or ch["seq"] for ch in chapters if (ch["heading"] or ch["seq"])] + body_paras)
    return {
        "title": title,
        "title_len": len(title),
        "title_over_limit": len(title) > platform_max_title,
        "paragraph_count": len(body_paras) + sum(1 for ch in chapters if (ch["heading"] or ch["seq"])),
        "total_chars": len(full.replace("\n", "")),
        "cjk_chars": count_cjk(full),
        "chapter_count": len(chapters),
        "tags": tags,
        "declare": declare,
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="一稿转多份平台格式（头条 / 言序 / 元信息）")
    ap.add_argument("--article", required=True, help="文章 Markdown 路径")
    ap.add_argument("--outdir", default="dist", help="产物目录，默认 ./dist")
    ap.add_argument("--only", default="toutiao,yanxu,meta",
                    help="只产指定产物，逗号分隔：toutiao,yanxu,meta")
    ap.add_argument("--topic", default=None, help="选题编号，写入 report 便于追溯")
    ap.add_argument("--title", default=None, help="覆盖标题（默认取【备选标题】里带 ★ 的那条）")
    ap.add_argument("--abstract", default=None, help="覆盖言序摘要（默认取导语首段，截 100 字）")
    ap.add_argument("--cover-url", default=None, help="言序封面公网直链（阶段 D 上传后回填）")
    ap.add_argument("--intro-mode", default="first-chapter",
                    choices=["first-chapter", "all-chapters", "none"],
                    help="言序导语来源：首章当导语（默认）/ 不设导语 / 另配")
    ap.add_argument("--declare", default="个人观点，仅供参考", help="头条声明文案")
    ap.add_argument("--toutiao-title-max", type=int, default=30, help="头条标题长度上限（仅告警）")
    ap.add_argument("--dry-run", action="store_true", help="只解析与校验，不写文件")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}

    src = Path(args.article).expanduser()
    if not src.exists():
        print(json.dumps({"ok": False, "stage": "read", "error": f"文件不存在: {src}"},
                         ensure_ascii=False, indent=2))
        return EXIT_INPUT
    try:
        md = src.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "stage": "read", "error": f"读取失败: {exc}"},
                         ensure_ascii=False, indent=2))
        return EXIT_INPUT

    doc_title, meta_lines, sections = split_sections(md)
    body_raw = sections.get("正文") or sections.get("正文 ") or []
    if not body_raw:
        print(json.dumps({
            "ok": False, "stage": "parse",
            "error": "没找到【正文】章节，请确认稿件是该技能家族的固定输出格式",
            "found_sections": list(sections.keys()),
        }, ensure_ascii=False, indent=2))
        return EXIT_PARSE

    chapters = parse_body(body_raw)
    if not chapters:
        print(json.dumps({"ok": False, "stage": "parse", "error": "【正文】章节为空"},
                         ensure_ascii=False, indent=2))
        return EXIT_PARSE

    candidates = parse_title_candidates(sections.get("备选标题", []))
    quotes = parse_bullets(sections.get("可截图金句", []))
    advice = parse_bullets(sections.get("发布建议", []))
    tags = [t for line in advice for t in RE_TAG.findall(line)]

    picked, pick_source = pick_title(candidates, args.title or doc_title)
    title = args.title or picked

    warnings: list[str] = []
    if not candidates:
        warnings.append("未解析到【备选标题】，标题回退到文档一级标题")
    if pick_source != "recommended" and candidates:
        warnings.append("【备选标题】里没有带 ★ 的推荐项，已取第一条")

    outdir = Path(args.outdir).expanduser()
    if not outdir.is_absolute():
        outdir = Path.cwd() / outdir

    written: dict[str, str] = {}

    toutiao_body = build_toutiao_body(chapters)
    toutiao_meta = build_toutiao_meta(chapters, title, tags, args.declare, args.toutiao_title_max)
    if toutiao_meta["title_over_limit"]:
        warnings.append(
            f"头条标题 {toutiao_meta['title_len']} 字，超过上限 {args.toutiao_title_max}，发布前需缩短")

    payload, ywarn = build_yanxu_payload(
        title, chapters, args.intro_mode, args.abstract or "", args.cover_url or "")
    warnings.extend(ywarn)

    errors = validate_yanxu(payload) if "yanxu" in only else []
    if errors:
        print(json.dumps({
            "ok": False, "stage": "validate", "platform": "yanxu",
            "error": "言序 payload 校验未通过，已中止（避免重演「HTML 标签塞进 content」的废稿）",
            "errors": errors,
        }, ensure_ascii=False, indent=2))
        return EXIT_VALIDATE

    report = {
        "ok": True,
        "dry_run": args.dry_run,
        "topic": args.topic,
        "source": str(src),
        "doc_title": doc_title,
        "title": title,
        "title_source": pick_source,
        "title_candidates": len(candidates),
        "quotes": len(quotes),
        "tags": tags,
        "chapters": [{"seq": c["seq"], "heading": c["heading"], "paras": len(c["paras"])}
                     for c in chapters],
        "toutiao": toutiao_meta,
        "yanxu": {
            "title_len": len(payload["title"]),
            "abstract_len": len(payload["abstract"]),
            "intro_chars": len(payload["intro"]),
            "chapters": len(payload["chapters"]),
            "cover_img": bool(payload["cover_img"]),
        },
        "warnings": warnings,
        "written": {},
    }

    if args.dry_run:
        report["preview"] = {
            "toutiao_first_lines": toutiao_body.splitlines()[:6],
            "yanxu_chapter_titles": [c["title"] for c in payload["chapters"]],
            "yanxu_abstract": payload["abstract"],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK

    try:
        outdir.mkdir(parents=True, exist_ok=True)
        if "toutiao" in only:
            p = outdir / "toutiao_body.txt"
            p.write_text(toutiao_body, encoding="utf-8")
            written["toutiao_body"] = str(p)
        if "meta" in only:
            p = outdir / "toutiao_meta.json"
            p.write_text(json.dumps(toutiao_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            written["toutiao_meta"] = str(p)
        if "yanxu" in only:
            p = outdir / "yanxu_payload.json"
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written["yanxu_payload"] = str(p)
        report["written"] = written
        p = outdir / "render_report.json"
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        written["render_report"] = str(p)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "stage": "write", "error": repr(exc)},
                         ensure_ascii=False, indent=2))
        return EXIT_OTHER

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_OTHER)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "stage": "unexpected", "error": repr(exc)},
                         ensure_ascii=False))
        sys.exit(EXIT_OTHER)
