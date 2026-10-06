# 分镜工坊 · storyboard-studio

把小说文本翻译成**可执行的镜头语言**：先做节拍分解（叙事层），再做镜头设计（执行层），
然后让分镜师在界面上把这两层都改到能用为止。

不是「输入一段话，吐一堆好看的图」——它输出的是能交给画师和声音设计师开工的分镜表：
镜号、景别、运镜、可拍的画面描述、逐字核验过的台词、音效、时长、以及每个切点为什么这么切。

## 线上演示

**<https://story-pi-weld.vercel.app>**

> ⚠️ **需要开代理 / VPN 才能访问。**
>
> 该地址托管在 Vercel，而 `*.vercel.app` 这个默认域名在境内被 **DNS 污染 +
> SNI 阻断**：DNS 会被解析到无关的海外地址（实测返回 Facebook 的地址段），
> TLS 握手被直接重置——**裸连连首页都加载不出来**，不是只有生成会失败。
>
> 这与本项目无关，是域名层面的网络限制，Vercel 也**无法为默认的 `vercel.app`
> 域名做国内优化**（官方的中国优化 CNAME 只对自定义域名生效）。
>
> 开代理后打开即是**实时模式**，顶部显示 `实时 · qwen3.7-flash-2026-07-15`
> ——背后是阿里百炼的 qwen，走 Anthropic 兼容网关，不是回放的 mock 数据。

## 快速开始

```bash
npm install
npm run dev        # http://localhost:3000
```

**不配任何东西也能打开。** 没有凭据时自动进入演示模式，回放
`lib/demo-cache.ts` 里人工撰写的样例分镜（内置文本《雨夜归人》），
界面顶部标注「演示数据」。演示模式下编辑、重排、删除、导出全部可用，
只有「重跑镜头」和「忠实度自查」会禁用并说明原因——它们需要真的调用模型。

### 接真模型

```bash
ANTHROPIC_API_KEY=sk-...            # 或者 ANTHROPIC_AUTH_TOKEN
```

可选：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ANTHROPIC_MODEL` | `claude-opus-5` | |
| `ANTHROPIC_BASE_URL` | 官方端点 | 走兼容网关时设这个 |
| `ANTHROPIC_EFFORT` | `medium` | `low`/`medium`/`high`/`xhigh`/`max` |
| `ANTHROPIC_MAX_TOKENS` | `32000` | 被截断时报错会提示调大它 |
| `ANTHROPIC_DEBUG` | 关 | 置 `1` 时把模型原始输出打到服务端日志 |

**换成第三方兼容网关（DeepSeek 等）时，把 `ANTHROPIC_MODEL` 设成不带 `claude-` 前缀的名字。**
`thinking` 与 `output_config` 是 Anthropic 专有参数，网关会直接 400，
所以代码只在 `MODEL.startsWith("claude-")` 时才发送它们。同一份代码因此既能跑官方 API，
也能跑兼容网关。

## 两层都可以改

产品里没有「只读层」。节拍是叙事骨架，镜头是执行单元，两层都能就地改。

| | 节拍层 | 镜头层 |
| --- | --- | --- |
| 编辑 | 摘要 / 情绪 / 强度（1-5 色块点选） | 景别 / 运镜 / 时长 / 画面 / 台词 / 音效 / 依据 |
| 重排 | ↑↓ 交换 | ↑↓ 上下移动 |
| 删除 | 连带删除其下全部镜头，**二次确认并显示镜头数** | 直接删除 |
| 重做 | 按新节拍照跑这一段戏的全部镜头 | 拆分 / 合并 / 改写描述 / 换个设计 |

改强度时**节拍卡顶边、节奏条、镜头左轨三处颜色同步变化**——它们共用
`lib/types.ts` 的 `INTENSITY_COLORS`，强度在这个产品里就是「颜色」这一个语义。

## 几个关键设计

**同一套色阶只有一份。** 强度色表曾经在三个组件里各存了一份，现在统一取
`INTENSITY_COLORS[intensity - 1]`。这类重复不会立刻出错，只会在某次调色时漏掉一处。

**`Beat.id` 是 `Shot.beatId` 的外键。** 所以节拍重排/删除之后必须**同时**做三件事：
重编节拍号、按新节拍顺序重排镜头数组、改写每个镜头的 `beatId`。漏掉第三件，
镜头会挂到语义错误的节拍下，界面冒出假的「空节拍」警告。这三件事只有一处实现
（`normalizeBeats`），因为它的不变量必须只有一个出口。

**镜头数组的顺序是有意义的。** 界面按节拍分组渲染，而 CSV 按数组顺序逐行导出。
只换节拍顺序而不动镜头数组，交出去的分镜表镜号顺序就和读者看到的对不上。

**清洗层能修的修，不能修的标出来，绝不静默放行。** 模型会吐「全景镜头」这类
不在枚举里的景别、把时长写成字符串、引用不存在的节拍 id、**把台词润色得更通顺**。
最后一条最危险，因为读起来毫无破绽——所以每句台词都回原文逐字核验，
不一致就在卡片上打 flag。`lib/schema.ts` 是这一层。

**自查是独立的一次调用。** 让同一段上下文给自己打分，几乎必然得到「没问题」。
所以 `/api/audit` 把分镜和原文一起发给模型逐镜对质，专找「无中生有」和「台词被改写」。

**自查结论会过期。** 改过任何参与自查的字段（节拍表 + 镜头表）之后，
界面会提示「结论可能已经过期」。判定靠 `auditSignature` 的指纹，
它和 `buildAuditPrompt` 的输入严格对齐——改了运镜、音效、时长不会误报过期，
因为它们本来就不参与自查。

**流式 NDJSON 而不是等一整块 JSON**，是为了让节拍和镜头立刻上屏。
推理型模型在出字之前会先花一大段预算在思考上，这段时间界面是全黑的，
所以思考增量也被翻译成 `{t:"thinking"}` 事件，做成「导演正在权衡」的进度提示。

## 接口

| 端点 | 请求 | 返回 |
| --- | --- | --- |
| `POST /api/generate` | `{text, medium, targetDurationSec}` | NDJSON 流（`StreamEvent`） |
| `POST /api/refine` | `{action, medium, beat?, shot, next?, sourceText}` | `{shots}` |
| `POST /api/beat` | `{medium, beat, shots, targetDurationSec, sourceText}` | `{shots}` |
| `POST /api/audit` | `{sourceText, shots, beats}` | `AuditResult` |
| `GET /api/status` | — | `{live, model}` |

`/api/beat` 与 `/api/refine` 分开是有意的：那边是镜头级动作，请求体围绕
「一个镜头 + 可选的下一个镜头」；这里是节拍级动作，请求体是「一个节拍 + 它下面的全部镜头」，
返回的镜头数也不固定。共用一个端点只会让两边的校验互相迁就。

## 目录

```
app/api/{generate,refine,beat,audit,status}/route.ts   一个端点一个职责
components/Studio.tsx        状态层：生成、编辑、重排、删除、导出
components/Storyboard.tsx    节奏条 + 节拍轴 + 统计 + 景别分布 + 体检 + 镜头表
components/{BeatCard,ShotCard,AuditPanel,ui}.tsx
lib/prompt.ts                提示词。这个产品真正的核心资产
lib/schema.ts                清洗层：字段别名、景别归一、时长钳制、台词核验
lib/llm.ts                   模型调用层。保持薄
lib/storyboard.ts            统计、整理（重编号/重排）、导出
lib/types.ts                 领域模型与 StreamEvent 协议
lib/demo-cache.ts            无凭据时回放的样例分镜
scripts/{smoke,probe}.mjs    诊断脚本
```

## 诊断脚本

```bash
node scripts/smoke.mjs    # 凭据是否可用、网关是否接受参数、模型是否真按 NDJSON 输出
node scripts/probe.mjs    # 这个端点到底吐哪些事件、预算花在哪
```

换网关或换模型时先跑这两个，比在应用里猜快得多。

## 已经验证过的

- `npx tsc --noEmit`、`npm run build` 通过；`npm run lint` 无报错。
- 无头浏览器驱动真实点击，逐项核对：改强度三处颜色同步、重排后
  **界面顺序 / 镜号 / CSV 行序三者一致**、删除带镜头的节拍后无假「空节拍」告警。
- 假网关（只讲 Anthropic 协议、不做推理）跑通 `/api/generate` 流式解析、
  `/api/beat`、`/api/refine`、`/api/audit` 全链路，验证清洗层的归一化与台词 flag、
  以及改动节拍后自查提示过期。**这条路子不花真钱，改提示词之后应当重跑。**

上述脚本是开发期的临时脚手架，没有入库。

## 完成边界

**真实可用**：节拍分解与镜头设计（流式逐镜返回）、单镜拆分/合并/改写/重生成、
节拍与镜头的就地编辑/重排/删除、按节拍重跑镜头、忠实度自查、
景别分布与节奏统计、CSV / Markdown 导出。

**尚未完成**：分镜图 / 关键帧生成、多人协作与版本历史、节拍的拆分与手工新增、
导出为专业软件（Storyboarder / Premiere）的工程格式、撤销/重做
（所以删除节拍要二次确认）。

## 部署

线上跑在 Vercel（Hobby 套餐），三个环境变量：

| 变量 | 值 |
| --- | --- |
| `ANTHROPIC_BASE_URL` | `https://dashscope.aliyuncs.com/apps/anthropic` |
| `ANTHROPIC_API_KEY` | 阿里百炼的 Key（`sk-` 开头） |
| `ANTHROPIC_MODEL` | `qwen3.7-flash-2026-07-15` |

⚠️ 百炼的 **Key 格式必须和端点配套**：按量计费的 `sk-` Key 配
`dashscope.aliyuncs.com/apps/anthropic`，Coding Plan 的 `sk-sp-` Key 才配
`coding.dashscope.aliyuncs.com/apps/anthropic`。配错会返回 401
`invalid_api_key` / 「invalid access token or token expired」——**报错文案会把人
误导成「Key 过期」，实际是端点选错了**。

所有 API 路由都是 `runtime = "nodejs"`；`/api/generate` 的 `maxDuration` 是 300 秒，
长文生成比较慢，注意平台的上限。

**默认函数区域是美东 `iad1`，建议改成香港 `hkg1`。** 函数调用的是国内的模型 API，
跨太平洋往返会明显拖慢生成。改法：Vercel 项目 → Settings → Functions → Region。
Hobby 只允许选**一个**区域，所以**要先把默认勾选的 Washington, D.C. 取消掉**
再选香港，否则会因为选中两个区域直接部署失败。改完需要重新部署。

本地跑：`npm run build && npm start`。
