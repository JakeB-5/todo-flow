# TODO Flow

**选好工作，让智能体接力推进。**

把选中的 TODO 转化为并行工作、独立评审和经过验证的交付。文件在会话结束后依然保留，仪表盘让你了解谁正在做什么。

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md)

[安装](AGENT_INSTALL.zh-CN.md) · [运维](OPERATIONS.zh-CN.md) · [更新](UPDATES.zh-CN.md) · [演示](DEMO.zh-CN.md)

[快速开始](#quick-start) · [查看实际操作](#see-it-in-action) · [智能体指南](#for-agents) · [运维](OPERATIONS.zh-CN.md)

[![许可证：MIT](https://img.shields.io/badge/license-MIT-257854)](LICENSE) [![状态：开发中](https://img.shields.io/badge/status-development-d4a34b)](#current-scope) [![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab)](pyproject.toml)

<!-- translation-source: README.md; source-sha256: 02e2117958fbe35650f9b24fbd5893d58caafaa9f57eebe4f196d4f9cb32bcdc; status: translated -->

## 37 秒了解 TODO Flow

https://github.com/user-attachments/assets/8b5acf19-292a-4205-aef1-f48b75305327

*地铁线路图风格的动画概览：选择轨道、并行智能体、会话交接、验证、评审和合入。*

## 开发活动，最高增长至 33 倍

![从 TODO 到交付，每日提交活动达到 33 倍。](assets/metrics/workflow-impact.png)

**登记、选择、运行。** 你决定方向；可替换的智能体负责调查、实现、验证和评审，并在获得授权后完成合入与分诊。

<sub>匿名的前身工作流记录：每日提交数从 2026 年 1 月的 9.3 次增加到 9 月的 316.3 次（9 月数据为 1–23 日）。这衡量的是提交活动，不是劳动生产率的实测倍数，也不是本软件包的基准测试。</sub>

<details>
<summary>1–9 月增长情况与测量说明</summary>

![1 月至 9 月的每日开发活动](assets/metrics/workflow-growth.png)

调整后的每日源代码变更量从 **6 月到 9 月增长至 4.52 倍**；记录的每日完成状态转换次数从 **7 月到 9 月增长至 2.30 倍**。不同指标反映不同结果：1–9 月的源代码变更量增长至 1.09 倍，而 9 月的完成状态转换次数低于 8 月。

![每月源代码新增、删除和排除量](assets/metrics/source-changes.png)

[方法与月度数据](assets/metrics/README.md) · [汇总 JSON](assets/metrics/measurements.json) · [CSV](assets/metrics/monthly.csv)。这些是前身工作流的运行观察，不是受控因果实验。可识别身份的原始资料不公开。

</details>

## 工作流循环

![TODO Flow 循环：todo 登记计划，trackpicks 推荐轨道供你选择，trackrun 执行选中的 ID，watchlist 重新评估条件性观察。可执行的后续工作返回 todo。](assets/workflow/cycle-en.svg)

**`todo` → `trackpicks` → `trackrun` → `watchlist` → `todo`。** 登记可供评审的计划，选择工作，执行选中的轨道，然后重新评估需要关注的事项。也可以直接在仪表盘中选择轨道。只有能接收新执行请求的轨道才启用复选框。标为 **Requested（已请求）**、**Paused（已暂停）** 或 **Pause requested（已请求暂停）** 的轨道不能再次选择。点击轨道标题查看详情；在详情中使用 **Resume（恢复）** 继续暂停的工作。`trackpicks` 只推荐工作，不启动工作进程。

`trackrun` 默认在评审后停止。授权合入后，它会继续完成合入和分诊，包括重新评估该轨道尚未关闭的观察项。使用 `watchlist` 显式请求重新评估；可执行的发现会归入现有或新的 TODO。新的后续轨道等待你选择。这些是共享项目状态的入口，不是每条轨道都必须经历的固定阶段。

<a id="see-it-in-action"></a>

## 查看实际操作

**我们已开始在本仓库中使用 TODO Flow。** 请查看[实际配置、仪表盘截图和可复现的设置](examples/self-hosting/README.md)。初始快照中还没有登记轨道；后续会随实际工作运行记录执行结果。

![运行中的仪表盘：TODO 选择、活动、文档评审和归档](assets/demo/dashboard-tour.gif)

*使用明确标注的合成数据，在真实仪表盘中录制。此界面导览不代表真实模型运行。*

[仪表盘截图](assets/demo/dashboard-en.png) · [韩语仪表盘](assets/demo/dashboard-ko.png) · [复现演示](DEMO.zh-CN.md) · [计划预览](assets/demo/track-example.png) · [HTML 示例](examples/retry-backoff.html) · [真实 Issue → 已合并 PR](DEMO.zh-CN.md#查看此前的公开验收运行)

导览展示了紧凑而连续的 TODO 列表、为 `trackrun` 选择轨道、当前工作进程与决策等待、内容丰富的轨道文档以及独立的已完成归档。要了解完整执行路径，请按照[双轨道操作步骤](DEMO.zh-CN.md#运行完整工作流)操作，或在一次性项目中运行文档所述的真实验收测试。

## 为跨越会话的工作而设计

| 你的需要 | TODO Flow 提供的能力 |
|---|---|
| 同时推进多项任务 | 选中的轨道在独立 Git 工作树中运行；工作进程负责有界的工作单元。 |
| 会话结束后恢复 | 文档、认领记录、结果、问题和后续工作意图保存在可搜索的文件中。 |
| 执行前评审计划 | HTML 文档保留图表、图片、脚本和模拟。Markdown 必须渲染为 HTML。 |
| 了解谁在做什么 | 紧凑的仪表盘展示当前工作、负责人、等待事项和证据。已完成工作有独立归档。 |
| 根据证据交付 | 针对准确候选版本的验证、独立智能体评审、经授权的合入，以及合入后分诊。 |

适用于由独立变更组成的待办列表、跨会话工作，以及集成前评审多个候选变更。在开发分支上，工作进程接收工作空间和证据路径，然后自行搜索并读取相关文件。各发行版本的可用功能和剩余限制见[当前范围](#current-scope)。

<a id="quick-start"></a>

## 快速开始

在项目设置时选择 **英语（`en`）、韩语（`ko`）、日语（`ja`）或简体中文（`zh-CN`）**。这会设置项目的默认仪表盘语言，以及请求智能体用于报告和新轨道文档的语言。技能指令仍使用英语。仪表盘还提供 English / 한국어 / 日本語 / 简体中文切换，用于个人显示偏好。

### 让智能体完成设置

填写项目路径和首项任务，将以下内容粘贴到你的编程智能体会话中：

```text
请按照 https://github.com/JakeB-5/todo-flow/blob/main/AGENT_INSTALL.zh-CN.md
在 /absolute/my-project 中安装 TODO Flow。如果我尚未指定语言，
请让我在 en、ko、ja、zh-CN 中选择。
我的首项任务是：[所需变更和预期结果]。
登记一个可供评审的 HTML TODO，并向我展示链接。选择并运行涵盖此请求的
轨道，然后报告实际结果和下一步。
```

[智能体指南](#for-agents)说明了安装约定。仅请求设置时，完成设置后停止；上面的提示还请求了首次运行。

### 手动安装

前提条件：**Python 3.11+、uv、Git，以及已认证的 Claude 或 Codex CLI**。使用 GitHub Issue 和 PR 还需要已认证的 `gh`。安装已发布版本：

```sh
uv tool install https://github.com/JakeB-5/todo-flow/releases/download/v0.0.9/todo_flow-0.0.9-py3-none-any.whl
todo-flow --version
```

[发行文件与校验和](https://github.com/JakeB-5/todo-flow/releases/tag/v0.0.9)。这会安装 CLI 和随包提供的仪表盘、技能，无需检出仓库。进行源码开发时，克隆本仓库，然后使用 `uv sync --frozen` 和 `uv tool install .`。

在**目标项目**中，使用该项目实际的验证命令、基准分支和相关文件模式。下面假设已有一个 Python 项目，包含测试套件、初始 Git 提交和 `origin` 远程：

```sh
cd /absolute/my-project
todo-flow init --repo . --base main --worker codex \
  --language zh-CN \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md

todo-flow install-skills --target .agents/skills
todo-flow serve --port 8765
```

其他语言使用 `--language en`、`--language ko` 或 `--language ja`。省略时，在交互式终端中会询问；没有终端时默认使用英语。Claude 会话请安装到 `.claude/skills`。技能继承配置的语言。要使用 GitHub Issue 和 PR，请为 `init` 添加 `--github OWNER/REPOSITORY`。

打开 **http://127.0.0.1:8765**。让智能体使用已安装的 todo 技能，评审生成的文档，然后选择并运行其实际 ID。当仪表盘可打开、登记的文档正常渲染、请求的首次运行到达配置的终点时，设置才算完成。参见[详细设置与恢复指南](AGENT_INSTALL.zh-CN.md)。

### 与其他工作流共存

没有名称冲突时，全部九个技能名称和现有调用方式保持不变。Codex 使用 `.agents/skills`，Claude 使用 `.claude/skills`。通过 `todo-flow install-skills --help` 检查是否支持 `--alias`；较早的发行版可能需要经过授权的更新，或包含此功能的源码构建。

只为确实被其他工作流占用的名称设置别名。下例假设 `todo`、`track-picks`、`track-run` 和 `watchlist` 已被占用，而四个别名均未使用：

```sh
FLOW_STATE=/absolute/my-project/todo-flow-state
FLOW_SKILLS=/absolute/my-project/.claude/skills
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS" \
  --alias todo=flow-todo --alias track-picks=flow-track-picks \
  --alias track-run=flow-track-run --alias watchlist=flow-watchlist
```

已经初始化的 TODO Flow 项目应保留原有 STATE。如果其他工具拥有 `todo/`，请在初始化前选择独立 STATE，并向前面的 `init` 命令传入 `--state "$FLOW_STATE"`。不要在其他工具的状态上初始化，也不要用 `--adopt` 接管其技能。别名被占用时，整个技能安装都会中止；请选择未使用的别名或另一个受支持的安装目标。原有文件和链接会保留，未冲突的角色保持原名。

检查安装报告中的 `entrypoints`（规范角色 → 安装名称）、`state`、`language`，以及各技能的 `project.json`。在此示例中，应要求智能体使用 `flow-todo`、`flow-track-picks`、`flow-track-run` 或 `flow-watchlist`；被占用的原始名称仍调用另一个工作流。`trackrun` 和内部角色保留原名，每个已安装的角色表都链接到实际 TODO Flow 入口。Shell 命令仍为 `todo-flow` 和 `trackrun`；独立 STATE 使用 `trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID`。

更新会复用已保存的映射，无需重复指定别名：

```sh
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --dry-run
# 审查计划并解决冲突后再应用：
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS"
```

上游文件未改变时会保留本地修改；两边同时修改会使整个更新停止。请保留安装清单。`--recover` 和 `--rollback BACKUP_ID` 的用法见[共存、恢复与回滚](AGENT_INSTALL.zh-CN.md#coexist-with-occupied-skill-names)。一次性技能包测试检查文件布局、元数据和链接。真实 Claude 会话中的技能发现与模型调用是单独的检查，这些测试并不执行它们。若当前会话未刷新，请打开新会话，或显式读取已安装的 `SKILL.md`。

### 指定工作进程路由

在轨道的 `workerPlan` 中记录各角色的 `provider`、`model`、推理 `effort` 和选择依据 `basis`。使用 `trackrun TRACK_ID --worker-mode auto`、`--worker-mode codex-only` 或 `--worker-mode claude-only` 运行。`--worker-roles FILE.json` 提供显式的角色覆盖配置，参见[选择指南与完整示例](skills/todo/worker-routing.md)。限定单一供应商的模式需要允许的角色选择或项目默认值、配置档案；不兼容的推荐绝不会授权使用另一供应商。

每个请求都会持久保存路由快照，包括 `--request-only`、驱动进程重启、暂停与恢复、修复与重试。重复启动时若没有路由选项，会保留原选择；显式重新选择只影响之后的尝试，不改变项目默认值、旧回执或尚未确认的启动。没有计划或路由选项的旧轨道继续使用原有单一或自定义适配器。`worker-selection.json` 记录所选值；原生执行回执单独记录供应商确认的值，这些值可能为 null。登记只推荐角色，不执行它们。轨道顶层的 `effort` 仍表示工作量估计。

### 限制请求的工作进程尝试次数

如需明确限定请求，请使用 `trackrun TRACK_ID --worker-attempt-limit 10` 或 `todo-flow --state STATE start TRACK_ID --worker-attempt-limit 10`。正整数上限分别应用于每个选中轨道的请求。省略时保留现有的请求无限制策略。`--max-tasks` 仍默认为 100，只限制一次驱动进程调用处理的任务数；重启该进程不会重置请求的工作进程尝试上限。

评估、实现、独立评审、分诊和观察尝试会在认领时原子地预占一次额度。失败或中断的尝试仍计入使用量，包括工作进程启动前的失败。宿主验证、合入和完成任务不消耗工作进程尝试额度。`budgets/` 中的文件保留上限和累计使用量；`status`、驱动进程输出、决策和预算事件展示使用量、候选 HEAD 和剩余任务。预算耗尽后的停止会保留结果与未完成义务，不表示目标已完成。

使用 `todo-flow --state STATE answer DECISION_ID --text '批准额外尝试' --additional-worker-attempts 5` 恢复因预算停止的请求；必要时再启动 `todo-flow --state STATE run`。正整数增量会扩展现有总上限，不清除使用量，也不替换待处理工作。普通回答、`pause`/`resume` 或重复执行 `start`/`trackrun` 均不能授予额外尝试。预算批准请使用 CLI；其他决策仍可通过仪表盘正常回答。

### 运行可选的验证预检

对于新状态，在 `init` 命令中添加以下参数，可在原有 `--verify` 命令之前运行明确选择的低成本检查：

```sh
--verify-preflight '[["uv","run","ruff","check","src","tests"],["uv","run","ruff","format","--check","src","tests"]]'
```

这是参数，不是可独立运行的命令。请先安装示例工具，或把这些 argv 数组替换为项目中可用的检查。命令在候选工作空间中按顺序执行，不隐式调用 shell；使用验证器环境，并为每条命令应用 `--verify-timeout`。首次失败或超时会停止验证；取消会停止受监督的进程并阻止后续命令运行。全部预检通过后才运行原有完整验证器。仅预检成功绝不代表验证成功，也不赋予合入资格。

省略该选项或使用 `[]` 会保留原有执行路径。验证记录包括每项已尝试检查的阶段、argv 和日志引用；明确的需求失败进入修复工作，无法判定的执行进入诊断。有序列表参与验证身份计算，因此改变它会使先前的成功失效。外部检查脚本和其他相关输入应按下文所述使用 `--verify-identity` 声明。不能通过 `init` 重新配置已有状态；下文的配置迁移限制同样适用。

### 选择中间验证检查

对于新状态，为 `init` 添加 `--verify-related '[["uv","run","python","-m","unittest","tests.test_calc"]]'`，并将示例替换为项目支持的检查。有序 argv 数组保存为 `verify_related`；不会自动进行影响分析。与预检一样，已有状态不能通过 `init` 重新配置。

中间变更提案会在已配置的预检后运行这些相关检查。每条命令使用候选工作空间、捕获的验证器环境和验证超时，采用相同的取消与失败处理方式。部分验证成功仅提供反馈。工作进程的 `verify:true`、发布请求，或 review/land/complete 后续请求都需要运行未改变的完整 `verify` 命令。显式验证任务、恢复的提案和集成验证也需要完整验证。两种路径都先运行预检；相关检查不会替代已配置完整验证器的任何部分。

省略 `verify_related` 或设为 `[]` 会保留每次变更后进行完整验证的行为。记录将验证范围和有序的相关检查策略绑定到验证身份；部分结果不能满足完整验证缓存查询或外部效果门禁。策略变化会使先前证据失效。未配置相关检查策略时，身份格式受支持且匹配的历史完整记录仍然有效；没有身份信息的记录仍需重新验证。外部相关检查脚本也应通过 `--verify-identity` 声明。不承诺实际耗时会加速：本地回归夹具比较两次中间变更和一个最终候选版本，将完整调用从三次减少为一次，同时仍能检测到最终缺陷。

### 验证结果

验证记录保留 `ok`，并增加 `outcome`（`passed`、`failed` 或 `inconclusive`）和 `reason`。只有 `passed` 与 `ok:true` 同时成立才可作为成功依据，且仍需满足原有范围、干净 HEAD、输入身份、进程和独立评审门禁。明确的未通过、取消、不完整证据或记录中未通过的检查，不能被 `ok:true` 覆盖。

验证器可以在 **stdout 最后一行**报告结果，格式为 JSON 对象加上字面后缀 ` TODO_FLOW_RESULT_V1`：

```text
{"outcome":"failed","reason":"add(2, 3) returned 6; expected 5"} TODO_FLOW_RESULT_V1
```

整行必须不超过 4096 个 UTF-8 字节；`reason` 必须是非空字符串。此前可以输出普通日志。带标记的行格式错误时，结果为无法判定。验证器负责区分已检查的需求违反（`failed`）与环境或检查执行失败（`inconclusive`）；宿主不会从 `AssertionError`、安装诊断或其他异常文本推断这一区别。声明失败可伴随非零退出码，但不会因此让日志回执变得完整。声明通过仍需要零退出码、完整输出和确认的正常进程终止。超时和残留进程的优先级高于结果声明。

兼容规则是明确的：未标记且退出码为零的命令，在所有现有门禁下保留原有成功行为。未标记的非零退出仍为 `ok:false`，现在归类为 `inconclusive`，因为仅凭退出码不能证明需求违反。历史布尔记录不会被改写：`ok:true` 只有在受支持的身份匹配且满足其他现有门禁时才有资格使用；`ok:false` 保持未通过，除非显式结果证明需求失败，否则显示并路由为无法判定。缺失或未知的结构化结果不能授予成功。

显式 verify 任务与提案后验证都会把 `failed` 路由到需求修复，把 `inconclusive` 路由到对记录阶段、日志和环境的评估。诊断保留候选版本，在经过授权的恢复后请求验证相同 HEAD；恢复方式不明确时请求具体决策。这不授权自动安装依赖或凭推测修改产品。无法判定的集成验证会保留候选证据并请求恢复决策，而不是启动产品修复。未通过的结果绝不缓存为成功，因此环境恢复后无需产品提交也可重新验证。

### 声明验证输入

`0.0.5` 支持 `init --verify-identity`。对于**新状态**，向实际的 `init` 命令添加类似参数，并将示例路径替换为验证器使用的已有输入文件：

```sh
--verify-identity '{"version":1,"files":["/absolute/verification/verify.py","/absolute/python/bin/python3","uv.lock"],"environment":["PATH","VERIFY_MODE"],"nonce":"baseline-1"}'
```

这是参数，不是独立命令。`version` 必须为 `1`；`files` 包含单个普通文件，不能是目录或 glob 模式。绝对路径标识外部输入；`uv.lock` 等相对路径按候选验证工作空间解析。请显式声明外部执行脚本及相关输入、配置文件。缺失或不可读取的输入会阻止接受缓存的成功。将执行脚本放在候选写入权限之外，固定其解释器和工具，并按锁定依赖的流程安装。仅有稳定路径或锁文件并不能证明已安装环境未改变。

`environment` 列出变量名，不能写成 `NAME=value`。身份依据保存所选名称及摘要，不保存明文值；未设置与空值不同。验证器使用捕获的环境，并设置 `GIT_TERMINAL_PROMPT=0`。摘要不是密码保护措施，单独保存的执行脚本输出可能暴露它打印的值。不要把秘密放进 argv、输出或 nonce 标签中。

不同的 `nonce` 会使先前身份依据失效。请使用不含秘密的标签：配置将其保存为文本，而身份依据保存其摘要。**现有配置不能通过 `Store.configure` 修改，也没有配置更新 CLI。** 请在初始化新状态时选择声明和 nonce。重复 `init` 不能轮换已有 nonce；不要通过编辑或删除权威状态绕过此限制。已有状态需要另行支持的配置迁移才能更改声明。有意改变已声明外部输入的内容，也会使其先前身份失效。

缓存复用还要求相同的 HEAD、tree、命令、超时和干净的检出目录。没有身份信息的历史成功需要重新验证；未知身份版本会被拒绝。执行前后会观察文件内容、解析后的路径和元数据。这能检测一般变更，包括元数据改变的一般修改后恢复操作，但不会执行不可变快照，也不防御特权主体对元数据的操纵。未声明文件、传递依赖、环境变量和远程服务不会被推断。外部执行脚本示例见[本仓库自身的验证声明与运行限制](examples/self-hosting/README.md#declare-verification-inputs-with-a-supporting-engine)。

## 从首个 TODO 到结果

在智能体会话中：

```text
todo 为临时网络故障添加有次数上限的重试，并编写测试。
todo 在请求无法重试时显示有帮助的错误信息。
trackpicks
```

智能体会搜索现有文件中的重叠需求并登记 HTML 计划。在仪表盘中评审它们。可以在仪表盘中选择轨道并复制命令，也可以采用 `trackpicks` 的推荐：

```sh
trackrun retry-backoff request-error-message
```

*这些只是示例 ID，请使用你实际登记后返回的 ID。* `todo` 和 `trackpicks` 是智能体技能请求；`trackrun` 同时也是安装后的终端命令。

| 步骤 | 可检查的内容 |
|---|---|
| 登记与评审 | HTML 计划、范围、证据和验收条件。[示例](examples/retry-backoff.html) |
| 选择与执行 | 选中的 ID、独立工作树、当前工作进程和问题。 |
| 验证与评审 | 绑定到准确候选版本的验证输出与独立评审。 |
| 合入与分诊 | 已授权合入终点时：集成 SHA、发现事项的处置、Issue 关闭和完成。 |

默认终点为 **`review`**，会保留已评审的候选版本。初始化时添加 **`--endpoint land --allow-land`** 可授权自动合入。合入后，分诊处理原范围内修复、独立后续工作和条件性观察。新的后续 TODO 等待你选择。

<a id="for-agents"></a>

## 智能体指南

阅读 **[AGENT_INSTALL.zh-CN.md](AGENT_INSTALL.zh-CN.md)**，执行请求范围内的安装和首次运行。复用现有配置与授权。**若未指定主要语言，请询问，并通过 `init --language en|ko|ja|zh-CN` 持久保存，用于新文档和报告。** 不要仅凭英文 README 推断语言。

验收条件应能追溯到选定结果，或此变更影响的现有不变量。将可选功能和无关缺陷单独处理，并在工作与评审中遵守已记录的用户取舍。发现改进机会不代表获准将其加入当前轨道。

[智能体设置指南](AGENT_INSTALL.zh-CN.md)涵盖项目调查、认证、语言选择、技能安装、文档评审、执行和基于证据的交接。[技能目录](skills/)包含各任务的专用指令。

## 各部分如何协作

```text
你的选择 ── trackrun ── 有界、可替换的工作进程
                              ↕
                    项目文件：目标、工作、证据
                              ↓
                    验证 → 评审 → 经授权合入
                              ↓
                         分诊 → 完成

仪表盘全程读取同一份项目状态。
```

一个安装好的引擎服务多个项目。每个项目都有自己的配置、文件、工作树、已安装技能，以及运行中的仪表盘和驱动进程。工作进程提出有用的后续工作；宿主验证所有权、证据和外部效果。没有固定的全局阶段顺序，也没有常驻的监督智能体。

| 组件 | 支持情况 |
|---|---|
| 工作进程适配器 | Claude CLI 和 Codex CLI；用于集成、测试的可信命令适配器 |
| 远程交付 | Git 远程；可选择通过 `gh` 使用 GitHub Issue / PR |
| 项目状态 | HTML / Markdown / JSON 文件；SQLite 仅为可重建的查询缓存 |
| 语言 | 英语、韩语、日语、简体中文的项目偏好、仪表盘界面和五份核心指南（README、安装、运维、更新、演示） |
| 环境 | 本地 macOS 验证；CI 配置了 Linux 检查。当前 POSIX 进程与锁实现不支持 Windows。 |

## 更新

```sh
todo-flow --version
todo-flow --state /absolute/project/todo compatibility --target /absolute/project/.agents/skills
```

通过 uv tool 安装时，`todo-flow upgrade --wheel /absolute/new-release.whl --dry-run` 用于规划引擎更新；在所有驱动进程和仪表盘停止后，省略 `--dry-run` 应用更新。更新器检查已知项目格式、备份环境，并在安装或验证失败时恢复环境。请提供可信的更新版本 wheel；尚不提供自动查找发行版的功能。

然后对每个项目使用 `todo-flow --state STATE update-skills --target PATH --dry-run`，去掉 `--dry-run` 后应用。保留本地修改及语言、状态绑定；有冲突的变更会在替换任何文件前停止。引擎更新和技能更新返回各自独立的回滚 ID。

[更新、回滚与恢复指南](UPDATES.zh-CN.md)包括中断更新的恢复、旧技能接管、已测试边界和未来发行检查清单。

## 常见问题

**它会替代我的编程智能体吗？** 它使用你已认证的 Claude 或 Codex CLI 协调选定的工作。你保留自己的模型和项目配置。

**数据在哪里？有共享数据库吗？** 每个项目拥有自己的 `todo/` 目录，或显式指定的 `--state` 目录。可以用 `rg` 检查。无需共享服务器或数据库服务。

**会话或驱动进程停止后会怎样？** 对同一组文件重新运行 `todo-flow --state STATE run`。运行时会协调认领记录和已记录的外部效果。进程停止不会被报告为完成。

**会自动合并吗？** 默认只到评审。初始化的 `land` 终点配合 `allow_land` 才允许合入，之后还需分诊和完成检查。已有分支保护规则仍然适用。

**可以选择多少条轨道？** 向 `trackrun` 传入多个 ID。`--jobs` 限制该驱动进程的并发任务数，默认为 2；它不是选中轨道数，也不保证每条轨道有专属工作进程。没有单独按终端数量进行的准入限制，历史终端记录不会阻止新的工作进程。

**费用如何？** TODO Flow 使用 MIT 许可证。模型使用量和外部服务按你现有供应商账号及计费规则收费。并行工作可能增加模型使用量。

**可以只切换显示语言而不改变项目吗？** 可以。仪表盘会在当前浏览器中记住此项目的显示偏好。它不会翻译已有的人工或智能体撰写文档，也不会改变智能体配置的主要语言。

**需要 Jev 吗？** 不需要。todo 和 watchlist 技能推荐 Jev 作为调查、重叠需求和变更来源筛查的可选工具。没有它也会继续工作；TODO Flow 不捆绑或自动安装 Jev 集成。

<a id="current-scope"></a>

## 当前范围

**0.0.9 新增：** 配置可选预检与中间验证检查，同时在交付边界保留完整验证和独立评审。将提供的机器证据绑定到其条件、候选版本和原始产物；将工作进程终止情况与提案有效性分开检查。显式请求上限保留尝试使用量，过期集成检出目录的清理仍保护所有权和用户修改。

**0.0.8 新增：** 活动视图按轨道汇总当前工作，将长指令保留在任务详情中，并在语言切换时保留导航。相同的待处理义务共享一次执行，同时保留各父请求。工作进程可提出有界文本替换；验证输出保存为独立日志产物，支持按范围读取。

**0.0.6 新增：** 原生工作进程复用现有 Codex 登录，不使用版本允许列表或凭据文件限制。终端数量和历史启动记录不再阻止新工作进程，延迟的查看器清理会保留已完成提案。仅发行变更的 CI 避免重复完整运行时测试套件。

**0.0.5 新增：** 显式声明验证输入身份、持久化进程清理，以及原生 Orca/Codex 工作进程会话。参见[执行边界](OPERATIONS.zh-CN.md#review-landing-and-completion)。

**0.0.3 新增：** 集成修复会把当前基准分支合并到候选检出目录，按路径提供冲突证据，并要求合入前重新验证和独立评审。修复中断和决策回答会保留已记录的合并。参见[修复行为](OPERATIONS.zh-CN.md#review-landing-and-completion)。

**0.0.0.2 新增：** 工作进程按需读取项目文件，`trackrun` 优先通过 Orca、配置的终端启动器或 tmux 显示终端日志。没有可用终端时使用无界面执行；`--launcher headless` 可显式选择该模式。已发布的 `0.0.1` wheel 仍使用早期的快照、无界面实现。参见[工作进程执行](OPERATIONS.zh-CN.md#worker-context-and-terminal-launchers)。

已完成轨道会自动清理一次性检出目录和未改变的工作进程终端，同时保留文档、日志、结果和 Git 分支。存在用户修改或所有权未确认的资源会保留，并记录原因。若需保留资源以供检查，使用 `--no-auto-cleanup`；参见[清理与重试](OPERATIONS.zh-CN.md#cleanup-migration-and-hooks)。

最新发行版：**0.0.9**。已演练小项目完整循环、恢复和两到三条独立轨道并发；大型列表有独立的合成界面测试覆盖。

- 每份项目状态对应一个仓库。尚未实现 Forgejo、子模块或协调多个仓库的合入。
- 开发分支的工作进程使用只读工具探索检出目录并返回 JSON 提案。运行时应用变更、验证并发布。尚未实现浏览器工作流。
- 不会把源码内容和完整证据注入提示；`main` 没有源码合计 150,000 字节的限制。工作进程自行选择读取的内容仍受供应商上下文限制。不支持文件删除和二进制编辑。
- 工作进程默认没有时限。`init --worker-timeout SECONDS` 可选择设置时限；已有项目保留配置的限制（`worker_timeout: null` 表示禁用时限）。取消和驱动进程丢失后的清理仍然有效。驱动进程默认任务分配上限为 100；剩余请求保留到下一次运行。
- 没有跨驱动进程的共享槽位预算、独立的高开销验证队列，也没有经过验证的分布式文件系统运行支持。

## 文档与贡献

[更新与回滚](UPDATES.zh-CN.md) · [运维与恢复](OPERATIONS.zh-CN.md) · [演示与验收测试](DEMO.zh-CN.md) · [贡献指南](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) · [智能体仓库规则](AGENTS.md) · [CI 配置](.github/workflows/ci.yml)

请通过仓库 Issues 报告缺陷或提出改进，并附上可安全公开的最小复现。贡献和本地验证命令见 [CONTRIBUTING.md](CONTRIBUTING.md)。`docs/` 中的本地设计笔记被 Git 忽略，使用或构建项目不依赖它们。

如需复用已确认的反馈，请参阅[已确认反馈的复用指南](AGENTS.md#reusing-confirmed-feedback)：在现有规则旁保留来源、适用范围、可公开的反例和检查方法。实施者的提议不等于用户批准，记录规则也不能替代未满足的条件或 finding 的处置。

**[MIT 许可证](LICENSE)** · Copyright © 2026 TODO Flow contributors.

原生 Orca 执行使用有明确所有权的托管检出目录和专用 Codex App Server 会话。可见的 Codex 客户端在轮次被接受后连接到准确的服务器、线程；只读提案通过服务器协议返回。显式选择的无界面执行仍保持无界面。Codex 版本和凭据存储方式不决定执行路线：适配器复用现有 Codex 登录并验证实际协议响应。现有 Git 检出目录以及缺少评审来源记录的情况，会保留明确报告的兼容路线。CLI 启动证据和仪表盘展示已记录的工作空间、会话、轮次和终端关联。包含合成协议、进程测试；不声称已进行外部模型验收测试。
