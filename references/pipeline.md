# 流水线映射：9 步手工 → 5 阶段编排

## 一、阶段总表

| 阶段 | 名称 | 谁执行 | 输入 | 产物 | 失败退出码 |
|---|---|---|---|---|---|
| — | 体检 | `scripts/preflight.py` | — | 体检报告 | 2 |
| A | 生产 | 技能 `toutiao-wealth-mindset-writer` + `article-theme-cover` | 选题编号 / record_id | `article_<slug>.md`、`covers/公众号头图_<主题>_<hash>.jpg` | 技能自带 |
| B | 规整 | `scripts/render_platforms.py` | 阶段 A 的 md | `dist/toutiao_body.txt`、`dist/yanxu_payload.json`、`dist/toutiao_meta.json`、`dist/render_report.json` | 2 / 3 / 4 |
| C | 归档 | 技能 `article-feishu-archive`（内部 `scripts/archive_article.py`） | md + record_id | 飞书文档链接 + 表格回填 | 2 / 3 / 4 / **5** |
| D | 分发 | 技能（MCP / bsk 交互，无法脚本化） | `dist/` 产物 | 言序 `info_id`/`publish_id`、头条文章 URL | 单平台失败不回滚 |
| E | 收口 | `scripts/backfill_links.py` | 各平台链接 | 表格「文章链接」+「状态」 | 2 / 3 / 5 |

## 二、原 9 步 → 新阶段

| 原步骤 | 归入 | 变化 |
|---|---|---|
| ① 选题 | 入口 + 阶段 A | 变成一句话入口，自动查 record_id |
| ② 定角度 / 标题 | 阶段 A | 不变（写作技能职责） |
| ③ 写正文 | 阶段 A | 不变，产物统一为 md |
| ④ 出配图 | 阶段 A | 不变；压缩挪到阶段 B/D |
| ⑤ 自查 | 阶段 A 尾部 + 阶段 B 校验 | 字数/敏感词留在写作技能；**格式合规移交脚本** |
| ⑥ 飞书存档 | 阶段 C | 不变（复用 `article-feishu-archive`），环境约束统一 |
| ⑦ 言序推送 | 阶段 D-1 | 封面直链流程固化；payload 来自 `dist/yanxu_payload.json` |
| ⑧ 头条发布 | 阶段 D-2 | 正文改用 `dist/toutiao_body.txt`（已规整，不再现转） |
| ⑨ 回填链接 | 阶段 E | 手工拼 JSON → 脚本 + 自动回读校验 |

## 三、关键决策点

### 决策 1：分发前停不停
**默认停。** 阶段 A→C 跑完即停，向用户报一段摘要（标题、字数、存档链接、表格回填状态），
然后问一句「要分发到哪些平台」。

- `--publish yanxu,toutiao` → 用户已在本条消息里明确要求分发时可一路跑完
- `--yes` → 已确认过的重跑

### 决策 2：言序导语取哪一段
`--intro-mode`：

| 值 | 行为 | 适用 |
|---|---|---|
| `first-chapter`（默认） | 首章当导语（无小标题渲染），chapter 从 02 起 | 本账号五段式（01 是开场钩子） |
| `all-chapters` | 不设导语，01–05 全是带标题章节 | 章节本身就是并列子标题的稿子 |
| `none` | 不设导语区 | 特殊排版需求 |

### 决策 3：多链接写哪个
`文章链接` 是单值字段，多平台会打架。默认**只写第一条**（按 `--link` 给定顺序 = 优先级），
要全写加 `--all-links`（换行分隔）。推荐顺序：**公众号 → 头条 → 其他**。

## 四、断点续跑

产物全部落盘，因此不需要状态机 —— **看 `dist/` 里有什么，就知道跑到哪了**：

| `dist/` 内容 | 说明 | 从哪继续 |
|---|---|---|
| 目录不存在或为空 | 还没跑 | 阶段 B（若 md 与封面已存在）或阶段 A |
| 有 `toutiao_body.txt` + `yanxu_payload.json` | 阶段 B 完成 | 阶段 C |
| 有 `render_report.json` 且表格「存档文档」已回填 | 阶段 C 完成 | 阶段 D |
| `.pipeline/backfill_<rid>_result.json` 里 `verified: true` | 阶段 E 完成 | 结束 |

**重跑约定**：

- 阶段 B / E 幂等，直接重跑
- 阶段 C **不幂等**（会新建文档）。若上次失败在退出码 5（文档已建、回填失败），
  **严禁重跑**，改用 `backfill_links.py --archive-doc <已有链接>` 手工补
- 阶段 D **不幂等**（言序无 update 接口，头条会重复发）。重跑前先确认上次是否真的没成功

## 五、每次运行的固定动作（不可省略）

1. `preflight.py` 体检（有 error 就先修）
2. 阶段 A 出稿 → 出图，**两个产物都要存在**
3. 阶段 B 渲染，读 `render_report.json` 的 `warnings` 数组，逐条处理
4. 阶段 C 归档，拿到文档链接
5. **停在决策 1**，向用户报告并询问分发范围
6. 阶段 D 分发（每个平台独立报告成功/失败）
7. 阶段 E 回填 + 运行小结

## 六、阶段 D 的平台子流程

### D-1 公众号（言序）
```
get_upload_channel --scene cover          → 一次性 upload_url（5 分钟有效）
curl -X POST -F "file=@covers/xxx.jpg" "<upload_url>"  → data.url 公网直链
render_platforms.py --article <md> --only yanxu --cover-url "<直链>"   # 回填 cover_img
create_article(payload=dist/yanxu_payload.json)        → info_id
publish_article(info_id, account_id, theme_id)         → 进草稿箱
```

### D-2 头条（bsk）
```
export BSK_AUTO_START=0
session start → 逐个实例 navigate，看 url 是否被重定向到 /auth/page/login 来判断登录态
navigate https://mp.toutiao.com/profile_v4/graphic/publish
fill   标题（textarea[placeholder*="请输入文章标题"]）
evaluate 注入 ClipboardEvent paste 正文（读 dist/toutiao_body.txt）
evaluate base64 自建 File 注入封面（先 sips 压到 <100KB）
click  勾选「个人观点，仅供参考」
预览并发布 → 确认二层弹窗
session stop
```

详见 `references/env-constraints.md`。
