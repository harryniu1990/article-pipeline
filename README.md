# article-pipeline

> 把「选题 → 出稿 → 配图 → 飞书存档 → 公众号草稿箱 → 头条号 → 回填选题表」串成**一句话入口**的 Agent Skill。

一个内容账号的日常是：从选题库里挑一条 → 写 → 配图 → 存档 → 发公众号 → 发头条 → 把链接填回表格。
每步单独做都不难，难的是**每一步的参数和坑都要重新回忆一遍**：CLI 走哪个入口、沙箱要不要关、
payload 的字段叫 `content` 还是 `blocks`、头条编辑器为什么 `fill` 不进去……

这个技能把这些踩过的坑固化成了编排层：**你只说「出选题 04」，其余交给它。**

---

## 它做了什么

| 原来（手工 9 步） | 现在 |
|---|---|
| ① 翻表格找选题行 | 入口一句话，自动查 `record_id` |
| ② 定角度 / 选标题 | 交给写作技能（不变） |
| ③ 写正文 | 交给写作技能（不变） |
| ④ 出配图 | 交给配图技能（不变） |
| ⑤ 自查格式 | **脚本硬校验**，格式问题不再漏到发布环节 |
| ⑥ 飞书存档 | 复用归档技能，环境约束统一收口 |
| ⑦ 言序推公众号 | payload 由脚本一次生成，封面直链流程固化 |
| ⑧ 头条发布 | 正文直接用规整好的纯文本，不再现场转换 |
| ⑨ 手工拼 JSON 回填 | **脚本 + 自动回读校验** |

核心收益在第 ⑤ 和第 ⑨ 步：**格式合规从"靠人记得"变成"脚本拦得住"，表格回填从"写完就完事"变成"回读确认"。**

---

## 五阶段编排

```
入口(出选题 04)
   │
   ├─ A 生产   写作技能出稿 + 配图技能出 2.35:1 头图
   ├─ B 规整   render_platforms.py → 头条纯文本 + 言序 payload（带硬校验）
   ├─ C 归档   飞书云文档 + 表格回填「存档文档」
   │
   ⏸  决策点：停下问「要分发到哪里」
   │
   ├─ D 分发   言序 → 公众号草稿箱 ／ bsk → 头条号
   └─ E 收口   backfill_links.py 回填「文章链接」+「状态」并回读校验
```

**默认在分发前停下。** 只有你明确说了要发（或给了 `--publish`）才会一路跑完 ——
发布是不可逆动作，值得一次确认。

---

## 目录结构

```
article-pipeline/
├── SKILL.md                          # 编排总纲（Agent 读这个）
├── references/
│   ├── pipeline.md                   # 9 步 → 5 阶段映射、决策点、断点续跑
│   ├── platform-specs.md             # 稿件格式约定 + 三平台硬约束 + 字数口径
│   ├── env-constraints.md            # lark-cli / bsk / 配图 / 言序 的固定调用姿势
│   └── pipeline-config.example.json  # 配置模板（复制成 pipeline-config.json 再填）
└── scripts/
    ├── preflight.py                  # 开跑前体检（只读、不发网络请求）
    ├── render_platforms.py           # 阶段 B：一次产出两套平台格式
    └── backfill_links.py             # 阶段 E：回填表格 + 回读校验
```

---

## 安装

这个技能面向 **WorkBuddy / Claude Code 这类支持 Agent Skills 的运行环境**。

```bash
git clone https://github.com/harryniu1990/article-pipeline.git
cp -r article-pipeline ~/.workbuddy/skills/       # 或你所在环境的 skills 目录

# 生成自己的配置（不要直接改 example）
cp ~/.workbuddy/skills/article-pipeline/references/pipeline-config.example.json \
   ~/.workbuddy/skills/article-pipeline/references/pipeline-config.json
```

然后编辑 `pipeline-config.json`。文件顶部有一块 `_how_to`，逐项说明了每个值去哪拿：

| 字段 | 怎么拿 |
|---|---|
| `account` | larkswitch 里的飞书账号名（`lark-cli --account` 的值） |
| `base.base_token` / `base.table_id` | 多维表格 URL 里 `base/` 和 `table=` 后面的那两段 |
| `topics.01` … `topics.10` | `base +record-list --table-id <tid>` 拉出来的 record_id |
| `yanxu.account_id` | `mcp__yanxu__list_wechat_accounts` |
| `yanxu.theme_id` | `mcp__yanxu__list_theme_styles` |
| `skills.*` | 你实际要编排的三个技能名 |

`scripts/preflight.py` 会检查配置是否还是模板占位符，没填就报 error，不会闷头往下跑。

> `pipeline-config.json` 已写进 `.gitignore` —— **它装着你的表格 token，不要提交。**

---

## 依赖

### 被编排的技能（可替换）

技能本身不写文章、不出图、不做平台 API，它只负责**什么时候调用谁、参数怎么传、产物落哪里**。
`skills.*` 三个槽位默认指向：

| 槽位 | 默认值 | 职责 |
|---|---|---|
| `skills.writer` | `toutiao-wealth-mindset-writer` | 出稿（需产出 `platform-specs.md` 约定的稿件结构） |
| `skills.cover` | `article-theme-cover` | 出 2.35:1 头图（内置出图 + 该技能的 `scripts/compose_cover.py` 本地叠中文） |
| `skills.archive` | `article-feishu-archive` | 飞书存档 + 表格回填 |

这三个是**插槽**，换成你自己的即可 —— 只要稿件结构符合 `references/platform-specs.md`。

### 命令行工具

| 工具 | 用途 | 缺失时 |
|---|---|---|
| `lark-cli`（larkswitch 托管版） | 飞书多维表格 / 云文档 | 阶段 C、E 不可用 |
| `bsk`（BrowserSkill） | 浏览器自动化发头条 | 阶段 D-2 需手工发 |
| 内置出图（`ImageGen`）+ `compose_cover.py` | 配图：生成背景 + 本地叠字 | 可退回 `designkit` 备选通道 |

### MCP

- `mcp__yanxu__*` —— 言序，公众号草稿箱通道（`list_wechat_accounts` / `list_theme_styles` / `create_article` / `publish_article` / `get_upload_channel`）

体检脚本无法探测 MCP，需要你自己确认这四个工具可用。

---

## 快速开始

```bash
cd <你的工作区>
SKILL=~/.workbuddy/skills/article-pipeline

# 0) 体检（只读，不发网络请求）
python3 $SKILL/scripts/preflight.py --topic 04

# 之后交给 Agent 即可，说一句：
#   出选题 04
```

阶段 B、E 可以单独重跑（幂等）：

```bash
# 阶段 B：从稿件一次产出两套平台格式
python3 $SKILL/scripts/render_platforms.py --article ./article_xxx.md --topic 04
# 产物：dist/toutiao_body.txt / dist/yanxu_payload.json
#       dist/toutiao_meta.json / dist/render_report.json（记得读 warnings）

# 阶段 E：回填链接 + 回读校验
python3 $SKILL/scripts/backfill_links.py --record <record_id> \
    --link "https://mp.weixin.qq.com/s/xxx" \
    --link "https://www.toutiao.com/item/xxx" \
    --status 已发布
```

---

## 设计原则

1. **默认在分发前停下。** 发布不可逆，值得一次人工确认。
2. **不重写已跑通的技能。** 编排层只传参、只汇总，原技能保持单一职责。
3. **写完必须回读。** 飞书 `record-batch-update` 不校验 `record_id` 是否存在，
   `ok: true` 不等于写对了 —— 回读是唯一可靠的确认方式。
4. **失败不回滚已完成阶段。** 产物落盘，看 `dist/` 里有什么就知道续跑点在哪。

## 红线

- ❌ 阶段 B 校验没过（退出码 4）就硬提交言序 —— 会出废稿**且言序无 update 接口，改不了**
- ❌ 归档退出码 5 之后重跑 —— 会建出孤儿文档
- ❌ 靠 `ok: true` 就认为表格写成功了 —— 必须回读
- ❌ 用 PATH 里的裸 `lark-cli` —— 会落到连接器绑定的另一个账号
- ❌ 后台跑 `lark-cli` —— 后台任务不继承沙箱豁免，必失败
- ❌ 用浏览器自动化的 `fill` 填头条正文 —— ProseMirror 场景下必失败，得注入 paste 事件

## 退出码

| 脚本 | 码 | 含义 |
|---|---|---|
| `preflight.py` | 2 | 有 error，环境不满足 |
| `render_platforms.py` | 2 / 3 / 4 | 输入文件问题 / 解析失败 / 平台校验失败 |
| `backfill_links.py` | 2 / 3 / 5 | 环境 / 授权 / 写入失败（**幂等，可重试**） |

---

## License

MIT
