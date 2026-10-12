# 环境约束：固定调用姿势

这些约束原先散落在 2–3 个技能的踩坑清单里，每次都要重新回忆。集中放这里，
出问题时先对照本文件，不要凭直觉改参数。

---

## 1. 飞书 lark-cli

### 1.1 两个入口，别走错

| 入口 | 路径 | 身份 | 支持 `--account` |
|---|---|---|---|
| **larkswitch 托管版（用这个）** | `~/Library/Application Support/dev.LarkProfileConsole.Lark-Profile-Console/bin/lark-cli` | 可切 | ✅ |
| WorkBuddy 连接器版 | `~/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli` | 固定为连接器绑定的账号（不可切） | ❌ |

本流水线**必须**用 larkswitch 托管版，并用 `--account` 显式指定目标账号名
（与 `pipeline-config.json` 的 `account` 字段保持一致）。`--account` 必须放在
lark-cli 的**第一个参数位**：

```bash
"$CLI" --account "<账号名>" base +record-get --base-token ... --table-id ... --record-id rec...
```

PATH 里的 `lark-cli` 会落到连接器绑定的另一个账号，**不要用裸命令**。

### 1.2 必须关沙箱 + 前台执行

larkswitch 通过跨进程 IPC 管理授权租约，沙箱会拦掉它的锁文件操作。

- 脚本由**前台** Bash 调用，且需要 `dangerouslyDisableSandbox: true`
- **后台任务不继承沙箱豁免** —— 后台跑必然失败，stderr 里会出现沙箱拦截的
  `file-delete`
- 症状：`raw` 字段为空，只有一行沙箱拒绝信息

### 1.3 `LPC_IO` 是间歇性的，重试即可

```
LPC_IO: Operation not permitted (os error 1)
```

这不是配置错误，是授权租约竞争。`run_cli()` 里的处理：命中
`["LPC_IO", "Operation not permitted"]` 就 `sleep 3` 重试，最多 5 次，单次超时 180s。
不要把这类失败当成真失败去改代码。

### 1.4 常用命令的正确参数

| 目的 | 命令 | 坑 |
|---|---|---|
| 建文档 | `docs +create --doc-format xml --content "@./x.xml" --as user --parent-position my_library` | `--content @路径` **只接受 cwd 相对路径** |
| 语法校验 | `docs +script --command parse` | 看 `data.assessment.status == "passed"` |
| 更新记录 | `base +record-batch-update --json '@./x.json'` | **没有 `--yes`**（risk 等级是 write，不是 high-risk-write） |
| 读记录 | `base +record-get --record-id <rid> --format json` | 用于回读校验，`+record-batch-update` 不校验 rid 是否存在 |
| 搜文档 | `drive +search --query "<关键词>" --created-by-me` | **`--query` 不是位置参数**，写位置参数会报 `positional arguments are not supported` |

### 1.5 写入成功判据

- `record-batch-update` 成功 = 返回 `ok == true`
- API **不校验 record_id 是否存在**，写完**必须回读**（`backfill_links.py` 已内置）
- 字段名要么用 field_id 要么用列显示名，别混

### 1.6 电子表格是另一个话题

`sheets +*` 系列对多数飞书自建应用**默认没有授权**，会报
`missing_scope: sheets:spreadsheet:read`。本流水线只用 `base`（多维表格），不碰 `sheets`。

---

## 2. bsk（BrowserSkill，头条发布）

### 2.1 每次调用都要 `export BSK_AUTO_START=0`

WorkBuddy 会收割子进程，不设这个环境变量时 `session start` 之后会话立刻消失，
后续命令报 `session not registered or already stopped`。

```bash
export BSK_AUTO_START=0
~/ .local/bin/bsk session start ...
```

`session start` 与后续操作**分两次工具调用**（不要写在同一条命令里）。收尾
`bsk session stop <id>`，让 daemon 常驻复用。

### 2.2 命令参数易错点

| 命令 | 正确写法 | 错误写法 |
|---|---|---|
| `evaluate` | `bsk evaluate --session <id> '<EXPRESSION>'` | `--expression` 会报 `unexpected argument` |
| `screenshot` | `bsk screenshot --session <id> --out /tmp/x.png` | `--output` 会报 `unexpected argument` |
| `upload` | `bsk upload --file <path> --mode input\|drop` | 见 2.4 |

### 2.3 多实例：用"是否被重定向"判断登录态

`bsk browsers` 列出的实例**没有 label**，需要逐个试：

```
bsk session start --browser <id>
bsk navigate --session <sid> https://mp.toutiao.com/profile_v4/index
```

- 返回 `url` 仍是传入的首页 → 已登录 ✅
- 返回 `url` 变成 `.../auth/page/login` → 这个实例未登录 ❌，换下一个

### 2.4 头条编辑器的三个绕法

**① 正文：`fill` 必失败 → 用 ClipboardEvent 注入**

`bsk fill` 对 `div.ProseMirror` 一律报
`the fill target changed during the action` —— 聚焦触发 React 重渲染，元素句柄失效。
加 `focus` 先聚焦、换成 CSS 选择器或 @ref 都一样。

改用 `evaluate` 注入 paste 事件（ProseMirror 会正常切成 75 个 `<p>`）：

```js
const text = `...逐行一段的正文...`;
const el = document.querySelector('div.ProseMirror');
el.focus();
const dt = new DataTransfer();
dt.setData('text/plain', text);
el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
```

**② 封面：`bsk upload` 两条路都走不通 → 页面内 base64 自建 File**

- `--mode input` 报 `{"code":-32000,"message":"Not allowed"}`
  （浏览器扩展缺「允许访问文件网址」权限）
- `--mode drop` 报 `No node found at given location`

绕法：先把图压到 ~100KB，base64 传进页面，**在页面内自建 File**（完全绕开文件 URL 权限）：

```js
const bin = atob(b64);
const arr = new Uint8Array(bin.length);
for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
const f = new File([arr], 'cover.jpg', {type: 'image/jpeg'});
const input = document.querySelector('.btn-upload-handle input[type=file]');
const dt = new DataTransfer();
dt.items.add(f);
input.files = dt.files;
input.dispatchEvent(new Event('change', {bubbles: true}));
```

压缩：`sips -Z 1200 -s formatOptions 78 <in> --out <out>`（325KB → 70KB → base64 93KB）。

> ⚠️ **不要靠回读 `input.files` 判断成败** —— 头条处理完会把它清空（length 回 0），
> 但上传其实已经成功。**以页面上的「已上传 1 张图片」或缩略图为准。**

**③ 关键 DOM 选择器**

| 元素 | 选择器 |
|---|---|
| 标题 | `textarea[placeholder*="请输入文章标题"]` |
| 正文编辑器 | `div.ProseMirror`（`contenteditable=true`） |
| 封面上传 input | `.btn-upload-handle input[type=file]` |
| 封面区 | `.article-cover` / `.article-cover-add` |

### 2.5 发布是两层确认

点「预览并发布」之后还有一层确认弹窗，必须点掉。**两个都点完才算发布成功**，
之后从作品列表抓 `https://www.toutiao.com/item/<id>/` 形式的文章链接。

---

## 3. 配图（内置出图 + 本地合成）

**默认路径**：内置 `ImageGen` 出**无文字背景** → `compose_cover.py` 本地渲染中文文案。
不依赖外部 CLI 或额度，中文 100% 准确，宽度可量化校验。

```bash
# 1) ImageGen（ToolSearch → DeferExecuteTool）：出无文字背景，size=2016x864，quality=high
#    提示词必须强调「中央留大片低对比度深色空白区」+「禁止任何文字/水印/logo」
# 2) 本地叠字
PY_IMG=/opt/homebrew/bin/python3   # 必须用这个（含 Pillow；managed 版没装）
$PY_IMG ~/.workbuddy/skills/article-theme-cover/scripts/compose_cover.py \
  --bg "covers/bg/<背景图>" --main "<主文案>" --outdir ./covers
```

判据：`warnings` 为空、`main_width_pct ≤ 40`、`center_pct ≈ 50`、
`left_pct/right_pct` 落在 28.7~71.3、`size_kb ≤ 2048`。

⚠️ **为什么必须两段式**：单次生成时模型把主文案排到 **54.6% 画面宽**（越出 1:1 安全区，
朋友圈裁切会削字），且右下角会带 `AI生成 / WORKBUDDY` 水印。别退回单次生成。

### 备选：designkit（美图设计室，需授权 + 按次额度）

命令行连接器，**不是 MCP**：`~/.workbuddy/binaries/node/cli-connector-packages/bin/designkit`

```
auth status --check          # 先确认已授权
create-room / chat           # 建房间、提交需求
history-detail --watch       # 前台阻塞等待（必须前台，别放后台）
download                     # 取回图片
```

`recharge_required` = 额度用尽，**充值后用 `designkit resume` 接着原任务跑**（重提会重复扣费）。
取图时对比 `before = set(os.listdir(dir))`，只回报本次新增文件，避免把历史图当成本次产物。

---

## 4. 言序 MCP

- 前缀 `mcp__yanxu__`，经 `ToolSearch` → `DeferExecuteTool` 调用
- 四工具：`list_wechat_accounts` / `list_theme_styles` / `create_article` / `publish_article`，
  另有 `get_upload_channel`
- 封面走一次性通道（**5 分钟有效**，别先取后等）：

```bash
# 1) 拿一次性 upload_url
get_upload_channel --scene cover
# 2) curl 上传本地文件
curl -X POST -F "file=@covers/xxx.jpg" "<upload_url>"
# 3) 返回的 data.url 是公网直链，填进 payload 的 cover_img
```

- **没有 update 接口**：建稿后要改只能重建，旧稿会留在后台

---

## 5. 收尾检查（每次跑完）

- [ ] 沙箱是否关掉、是否前台执行（影响 lark-cli）
- [ ] `BSK_AUTO_START=0` 是否设置、`bsk session stop` 是否执行
- [ ] 表格是否**回读**确认过（不是只看 `ok: true`）
- [ ] 言序 payload 的封面直链是否在上传后 5 分钟内用掉
- [ ] 头条是否真的点了两层确认
