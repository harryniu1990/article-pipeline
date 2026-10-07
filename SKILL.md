---
name: article-pipeline
description: 文章生产发布流水线总入口。把「选题 → 出稿 → 配图 → 飞书存档 → 公众号（言序草稿箱）→ 头条号 → 回填选题表」串成单入口编排，一次生成头条纯文本与言序 payload 两套格式，统一收口 larkswitch/bsk/designkit 的环境约束。当用户说「出选题 N」「走一遍文章流程」「把这篇发出去」「一稿多发」「发文章」「生成一篇并存档」「发布流水线」时使用。触发场景：出选题、写并发布、多平台分发、回填选题表格。
agent_created: true
---

# 文章生产发布流水线

**唯一入口 = 一句话。** 例：`出选题 04`。

技能本身不写文章、不出图、不做平台 API 操作 —— 它负责**编排**：什么时候调用哪个技能、
参数怎么传、产物落哪里、失败了怎么接。原有技能保持单一职责，不互相耦合。

```
入口 → A 生产 → B 规整 → C 归档 → ⏸ 确认 → D 分发 → E 收口
```

---

## 一、首要原则

1. **默认在分发前停下。** 阶段 A→C 自动跑完，然后向用户报告并询问要分发到哪些平台。
   只有用户在本条消息里已明确要求分发（或给了 `--publish` / `--yes`）才一路跑完。
2. **不重写已跑通的技能。** 阶段 A / C / D 复用现有技能，编排层只传参、只汇总。
3. **写完必须回读。** 飞书表格的 `record-batch-update` 不校验 record_id 是否存在。
4. **失败不回滚已完成阶段。** 产物落盘，按 `dist/` 内容判断续跑点（见 `pipeline.md`）。

---

## 二、开跑前

```bash
cd <工作区>
PY=/Users/niujianfei/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SKILL=~/.workbuddy/skills/article-pipeline

$PY $SKILL/scripts/preflight.py --topic 04
```

体检**只读、不发网络请求**。有 `error` 先修再跑；`warn` 视情况处理。
体检会顺手列出 `dist/` 里的残留文件 —— 重跑前先确认那是不是上一轮遗留。

`references/env-constraints.md` 里有全部固定调用姿势（lark-cli / bsk / designkit / 言序），
**遇到环境报错先查它，不要凭直觉改参数。**

---

## 三、入口约定

| 参数 | 说明 |
|---|---|
| `出选题 04` | 最常用。读选题库第 4 行的角度 / 标题 / 案例方向 |
| `--record <record_id>` | 直接指定表格行，跳过编号查找 |
| `--publish yanxu,toutiao` | 指定分发平台；给了才在阶段 D 自动跑 |
| `--yes` | 跳过阶段 D 前的人工确认（用于已确认的重跑） |
| `--from c` | 断点续跑，从阶段 C 开始 |

选题编号 → record_id 的映射在 `references/pipeline-config.json` 的 `topics` 里。
**行被增删后该映射会失效**，用 `base +record-list` 重新核对，不要盲信。

---

## 四、阶段 A · 生产

产两样，**两个都必须存在才进下一阶段**：

| 产物 | 由谁产 | 落到 |
|---|---|---|
| 正文 md | 技能 `toutiao-wealth-mindset-writer` | `<工作区>/article_<slug>.md` |
| 2.35:1 头图 | 技能 `article-theme-cover` | `<工作区>/covers/公众号头图_<主题>_<hash>.jpg` |

出图后**目视复核两点**（历史踩坑）：

1. 主文案是否**水平居中**（曾经被竖排放在画面左侧，完全落在朋友圈安全区外）
2. 主文案横向宽度是否 **≤ 30%**（曾经做到 46%，朋友圈 1:1 裁切会削字）

不合格就重生成，别将就。

---

## 五、阶段 B · 规整（收益最大的一步）

```bash
$PY $SKILL/scripts/render_platforms.py --article ./article_xxx.md --topic 04
```

一次产出：

| 文件 | 用途 |
|---|---|
| `dist/toutiao_body.txt` | 头条编辑器直接粘贴的纯文本（逐行一段、中文弯引号、无 Markdown 残留） |
| `dist/yanxu_payload.json` | 言序建稿 payload（含硬校验，content 必须纯文本） |
| `dist/toutiao_meta.json` | 标题长度、段数、字数、话题标签、声明文案 |
| `dist/render_report.json` | 统计 + `warnings`（**必须逐条读**） |

**读 `warnings` 数组**，常见项及处理：

- `标题 N 字超出言序上限 30，已截断` → 回写作技能改短标题，别用截断稿
- `头条标题 N 字，超过上限` → 发布前必须缩短
- `【备选标题】里没有带 ★ 的推荐项，已取第一条` → 人工指定 `--title`
- `存在无序号也无小标题的章节` → 检查稿件结构

可选：`--only toutiao` / `--only yanxu`；`--intro-mode`；`--abstract`；`--title`。

---

## 六、阶段 C · 归档

走技能 `article-feishu-archive`（内部 `scripts/archive_article.py`）：

```bash
$PY ~/.workbuddy/skills/article-feishu-archive/scripts/archive_article.py \
    --article ./article_xxx.md --record-id <rid>
```

- 默认只存**正文 + 可截图金句**，`--with-notes` 才带过程稿
- 回填表格「存档文档」+「状态 = 已写待发」

**退出码 5 = 文档已创建但回填失败 → 严禁重跑**（会建出孤儿文档）。
改用 `backfill_links.py --archive-doc <已有链接> --status 已写待发` 补回填。

⚠️ 前置：lark-cli 用 larkswitch 入口 + `--account "<你的飞书账号>"`，**前台执行 + 关沙箱**。

---

## 七、⏸ 决策点 · 停下确认

阶段 C 完成后，**必须停下**向用户报一段摘要：

```
选题 04 已完成生产与归档
  标题：<首推标题>（N 字）
  正文：<cjk_chars> 中文字 / <paragraph_count> 段
  配图：covers/公众号头图_xxx.jpg
  存档：<飞书文档链接>
  表格：已回填「存档文档」，「状态」= 已写待发

要分发到哪里？可选：公众号（言序草稿箱）/ 头条号 / 都要 / 先不发
```

拿到答复后进阶段 D。用户说"先不发"就结束，并注明「文章已可发布，随时说一声」。

---

## 八、阶段 D · 分发

**每个平台独立**，一个失败不影响另一个；失败就如实报告，不要静默跳过。

### D-1 公众号（言序 = 公众号草稿箱通道）

> 言序自带排版引擎，**不需要**再套一层公众号排版技能。绑定的公众号由
> `references/pipeline-config.json` 的 `yanxu.account_id` 决定（用
> `mcp__yanxu__list_wechat_accounts` 查）。

```bash
# 1) 取一次性上传通道（5 分钟有效，别提前取）
#    mcp__yanxu__get_upload_channel --scene cover
# 2) 上传封面拿公网直链
curl -X POST -F "file=@covers/xxx.jpg" "<upload_url>"
# 3) 把直链写回 payload
$PY $SKILL/scripts/render_platforms.py --article ./article_xxx.md --only yanxu --cover-url "<直链>"
# 4) create_article(payload = dist/yanxu_payload.json) → info_id
# 5) publish_article(info_id, account_id, theme_id) → 进草稿箱
```

主题默认 `blackgold`（曜金质感）；热点评论类可换 `magma`（赤焰警示），
数据解读类可换 `cobalt`（电光靛蓝）。备选见 `pipeline-config.json`。

**言序没有 update 接口** —— 建稿后发现错只能重建。所以阶段 B 的校验必须在前。

### D-2 头条号

```bash
export BSK_AUTO_START=0
```

1. `session start` → 逐个实例 `navigate`，**用 url 是否被重定向到 `/auth/page/login` 判断登录态**
2. 进 `https://mp.toutiao.com/profile_v4/graphic/publish`
3. 填标题（`textarea[placeholder*="请输入文章标题"]`）
4. **正文用 `evaluate` 注入 ClipboardEvent**（`bsk fill` 对 ProseMirror 必失败）
5. **封面用 base64 页面内自建 File 注入**（先 `sips` 压到 <100KB）
6. 勾选「个人观点，仅供参考」
7. 「预览并发布」→ **再点掉确认弹窗**（两层都点完才算成功）
8. 抓 `https://www.toutiao.com/item/<id>/`
9. `session stop`

正文读 `dist/toutiao_body.txt`。⚠️ 判定封面成功以页面提示/缩略图为准，
**不要回读 `input.files`**（头条处理后会清空它）。

---

## 九、阶段 E · 收口

```bash
$PY $SKILL/scripts/backfill_links.py --record <rid> \
    --link "https://mp.weixin.qq.com/s/xxx" \
    --link "https://www.toutiao.com/item/xxx" \
    --status 已发布
```

- `文章链接` 是单值字段，**默认只写第一条**（按 `--link` 顺序 = 优先级，推荐公众号在前）
- 要全写加 `--all-links`
- **默认自动回读校验**并写日志到 `.pipeline/`；`verified: false` 要人工核对一次

最后给一份**运行小结**：

```
选题 04 全流程完成
  标题 / 正文字数 / 配图
  存档文档：<链接>
  公众号：info_id / publish_id（草稿箱待发）
  头条号：<文章链接>
  表格：文章链接已回填，状态 = 已发布（已回读确认）
```

---

## 十、退出码速查

| 脚本 | 码 | 含义 |
|---|---|---|
| `preflight.py` | 2 | 有 error，环境不满足 |
| `render_platforms.py` | 2 / 3 / 4 | 输入文件问题 / 解析失败（缺【正文】）/ 平台校验失败（含 HTML 标签等） |
| `archive_article.py` | 2 / 3 / 4 / **5** | 环境 / 授权 / 建文档失败 / **文档已建但回填失败（严禁重跑）** |
| `backfill_links.py` | 2 / 3 / 5 | 环境 / 授权 / 写入失败（**幂等，可重试**） |

---

## 十一、红线

- ❌ 阶段 B 校验没过（退出码 4）就硬提交言序 —— 会出废稿且无法修改
- ❌ 退出码 5 之后重跑归档 —— 会建出孤儿文档
- ❌ 靠 `ok: true` 就认为表格写成功了 —— 必须回读
- ❌ 阶段 D 前不停下确认（除非用户明确要求直接发）
- ❌ 用 PATH 里的裸 `lark-cli` —— 会落到连接器绑定的另一个账号
- ❌ 后台跑 lark-cli —— 后台任务不继承沙箱豁免，必失败
- ❌ 用 `bsk fill` 填正文 —— ProseMirror 场景下必失败

---

## 十二、参考文件

| 文件 | 内容 |
|---|---|
| `references/pipeline.md` | 9 步 → 5 阶段映射、决策点、断点续跑、平台子流程 |
| `references/platform-specs.md` | 稿件格式约定 + 三平台硬约束 + 字数口径 |
| `references/env-constraints.md` | lark-cli / bsk / designkit / 言序 的固定调用姿势与踩坑 |
| `references/pipeline-config.json` | 表格 token、字段名、topic→record_id、绑定的公众号与主题（仓库只带 `pipeline-config.example.json`，需自行复制填写） |

## 十三、配套技能（可替换插槽）

本技能不写文章、不出图、不做平台 API —— 它只编排。被编排的三个技能由
`pipeline-config.json` 的 `skills` 字段指定，你可以换成自己的：

| 槽位 | 默认值 | 在本流水线里的位置 |
|---|---|---|
| `skills.writer` | `toutiao-wealth-mindset-writer` | 阶段 A · 出稿 |
| `skills.cover` | `article-theme-cover` | 阶段 A · 出 2.35:1 头图 |
| `skills.archive` | `article-feishu-archive` | 阶段 C · 飞书存档 + 表格回填 |

只要你的技能满足 `references/platform-specs.md` 里的稿件格式约定，就能直接接进来。
