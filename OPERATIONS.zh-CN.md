# TODO Flow 运维

[English](OPERATIONS.md) · [한국어](OPERATIONS.ko.md) · [日本語](OPERATIONS.ja.md) · [简体中文](OPERATIONS.zh-CN.md)

[从这里开始](README.zh-CN.md#quick-start) · [代理安装](AGENT_INSTALL.zh-CN.md) · [更新](UPDATES.zh-CN.md) · [演示](DEMO.zh-CN.md)

<!-- translation-source: OPERATIONS.md; source-sha256: dc80dbfa42b0f3ac15e014939c1ffe60ef2c86c74080c9e2c76be1213271fdbe; status: translated -->

<a id="project-state-and-language"></a>

## 项目状态与语言

引擎只需安装一次。为每个目标仓库单独初始化，分别使用自己的状态、技能安装、工作树以及仪表盘和驱动进程。同时打开多个项目时，请使用不同的仪表盘端口。

```sh
# 在目标仓库中运行，默认使用 ./todo。
trackrun TRACK_ID_1 TRACK_ID_2
todo-flow serve --port 8765

# 在任意工作目录中显式指定状态路径。
trackrun --state /absolute/project/todo TRACK_ID_1
```

`init --language en|ko|ja|zh-CN` 将主要语言保存到 `config/1.json`。省略该参数时，交互式安装会询问语言，无人值守安装默认使用英语。代理辅助安装在没有偏好或现有配置时会询问用户。缺少该字段的旧配置默认使用英语。

技能指令以英语编写，新文档和面向用户的说明使用主要语言。已安装的 `project.json` 标识项目状态路径和语言。内置工作器适配器收到相同的语言指令；自定义适配器的输入中包含 `language` 和 `output_language_instruction`。模式键、ID、命令、代码约定和原文引用保持不变。

仪表盘的语言切换按项目在浏览器本地保存偏好，并保留选择、搜索、决策回复草稿和当前视图。它不会改写项目配置，也不会翻译已编写的文档、日志或历史结果。现有执行配置不可变；请勿在运行期间编辑。

<a id="documents-and-files"></a>

## 文档与文件

[HTML 模板](templates/track.html) · [Markdown 模板](templates/track.md) · [JSON 模板](templates/track.json)

```sh
todo-flow --state STATE register /absolute/scratch/track.html \
  --assets /absolute/scratch/track-assets

# 阅读当前修订版后再修订文档。
todo-flow --state STATE register /absolute/scratch/revised.html --expected-revision 2
```

HTML 保留完整正文以及 SVG、图片、CSS、JavaScript 和 Three.js 资源。Markdown 保留完整源文并渲染为 HTML。请使用 `assets/...` 路径；每个修订版都会保存自己的资源快照。HTML 执行契约是 ID 为 `todo-flow-track` 的 `application/json` 块。

请将契约的 `language` 和 HTML 的 `lang` 设置为选定的编写语言。JSON 注册省略语言时使用项目语言。Markdown 渲染使用元数据中的语言，默认英语；已编写的 HTML 会直接提供，不会改写。必需的 goal/scope/evidence/condition 字段必须与可见文档一致。

```text
todo/
  config/1.json                    项目执行契约与语言
  tracks/<id>/track.html            可供人工审查的文档
  tracks/<id>/source.md             提供时保留的 Markdown 原文
  tracks/<id>/assets/               当前文档资源
  tracks/<id>/state.json            状态、请求与执行引用
  tracks/<id>/revisions/            历史文档与资源
  tasks/ · attempt-records/         工作与工作器尝试
  results/ · decisions/ · events/   结果、问题与事件历史
  effects/                         外部操作意图与确认回执
  findings/ · triages/ · watches/   发现事项及其处置
  attempts/                        工作器原始输入、输出与诊断
  .cache/query.sqlite              可丢弃的查询缓存
```

文件系统是权威来源。可以使用 `rg`，无需 SQL。删除缓存不会删除权威工作记录。不要直接覆盖已注册文档来绕过修订管理或复用过时证据。

仪表盘打开 `/documents/ID/REVISION/index.html`。本地 JavaScript 模块和模拟应使用 HTTP，而不是 `file://`。文档与仪表盘分别置于沙箱中。在声称完成视觉审查之前，请检查实际渲染和交互。

<a id="selection-execution-and-concurrency"></a>

## 选择、执行与并发

- 通过 todo 技能或 CLI 注册和修订。仪表盘没有编写文档的控件。
- 通过仪表盘或 track-picks 选择。仪表盘复制 `trackrun` 命令，不会发送执行请求。
- 使用 trackrun 执行具体 ID。不同轨道使用独立工作树。工作器根据持久化上下文选择范围有限且有用的下一项工作。
- 活跃 TODO 使用没有分页按钮的连续列表。已完成轨道有独立、可搜索并支持增量加载的归档。

`--jobs N` 限制一个驱动进程内的并发任务数（默认 2），而不是所选轨道数。`--max-tasks N` 限制一次运行中的任务分配数（默认 100）。达到上限后，待办工作仍保留在文件中。终端没有单独的数量上限：旧的 `terminal_concurrency`、`terminal_idle_limit` 和容量台账不再参与启动准入。清理只检查当前执行；旧的或未确认的 UI 标签页不会阻塞其他工作器。

```sh
# 可选：提交给已经运行的驱动进程。
trackrun --state STATE TRACK_ID --request-only

# 可选：等待新工作。任务分配上限仍然适用。
todo-flow --state STATE run --daemon
```

多个驱动进程不共享全局并发额度。驱动进程死亡或 claim 过期不代表完成。判断当前活动前，请检查所有者、租约、尝试和结果。

<a id="worker-context-and-terminal-launchers-unreleased"></a>
<a id="worker-context-and-terminal-launchers"></a>

## 工作器上下文与终端启动器

本节描述 `0.0.2`；已发布的 `0.0.1` wheel 仍使用快照工作器。现在，工作器会在分配的实现或审查检出目录中启动，triage 则使用已获取的精确基线检出目录。输入包含任务、workspace、head、language、探索提示、写入边界和 `paths` 映射。目标与条件、富内容轨道文档、完整差异、验证、决策、历史结果和 triage 证据都通过路径读取。项目源码不会被收集到标准输入中，也没有源码总量 150 KB 的限制。模型上下文限制仍适用于所选择的读取内容。

Codex 使用包括 `rg` 在内的只读 shell 工具；Claude 提供 Read、Glob 和 Grep。`context_patterns` / `--context` 是导航提示，不是读取权限控制。请以适合项目的访问权限运行。工作器返回 JSON 提案；引擎仍负责应用获准的写入、运行验证、提交和处理远程操作。这些是带实时日志的自动工作器，不是交互式代理聊天。

`init --launcher auto` 是默认值，也适用于没有 `worker_launcher` 的现有配置。每个驱动进程都可以在不修改项目配置的情况下覆盖它：

```sh
trackrun TRACK_ID --launcher auto
trackrun TRACK_ID --launcher orca
todo-flow --state STATE run --launcher headless
```

`auto` 首先使用能够识别项目仓库的、正在运行的 Orca 运行时。它会在该项目下打开带标题的终端，并在分配的检出目录中启动工作器。否则依次使用已配置的 `terminal_command`、从现有 tmux 会话中调用时可用的 tmux，最后使用 headless。显式指定的 `orca`、`tmux` 或 `terminal` 模式在不可用时会失败。远程 Orca PTY 需要驱动进程在该主机上运行，不会由本地驱动进程启动。轨道完成后，如果仍能确认终端身份和非活动状态，已退出的工作器终端会被关闭；日志保留在尝试目录中。如果需要保留已完成运行的资源以供检查，请使用 `--no-auto-cleanup`。

对于其他终端应用，请将 `terminal_command` 配置为受信任启动器的 argv 数组，该启动器应在打开终端后返回。`{command}` 是经过 shell 引用的工作器桥接命令；`{cwd}` 和 `{title}` 是可选占位符。启动器必须原样启动该命令并在十秒内返回。认证从终端环境继承；凭据不会复制到启动记录中。如果所需认证只存在于调用方 shell 中，请使用 headless。

每次尝试都会保存 `launch.json`（后端及 Orca 终端句柄或启动器回执）、`terminal-process.json`（实际工作器 PID 和退出码）、`input.json`、`output.json` 和 `stderr.log`。打开终端不能证明工作器已经启动或完成。完成需要有退出记录和有效结果。创建或启动失败的状态不明确时，不会再启动第二个 headless 工作器；尝试记录会保存针对延迟启动的取消操作。超时会停止工作器进程组。重试前请检查该尝试。

基于路径的输入使用工作器协议 **2**；结果提案保持现有模式。现有内置 Claude/Codex 配置无需编辑项目状态即可适配。旧版自定义命令适配器必须读取 `workspace` 和 `paths`，然后在项目停止期间显式设置 `worker_protocol: 2`。迁移完成前，它们会在启动前失败，而不会悄悄接收不同的契约。

<a id="questions-interruption-and-recovery"></a>

## 问题、中断与恢复

```sh
todo-flow --state STATE status
todo-flow --state STATE pause TRACK_ID
todo-flow --state STATE resume TRACK_ID
todo-flow --state STATE cancel TRACK_ID
todo-flow --state STATE answer DECISION_ID --text 'The decision and its reasoning'
todo-flow --state STATE run
```

中断后请使用同一状态重新启动。来自已被替代 claim 的迟到响应会被拒绝。远程操作会与持久化回执核对，以避免响应丢失后的意外重复。如果没有驱动进程运行，`resume` 或 `answer` 后可能需要重新启动驱动进程。

请根据尝试记录诊断工具故障，不要用编造的决策替代故障。如果端口被占用，请使用其他端口。如果找不到已安装的命令，请检查 `uv tool dir --bin` 和 PATH。如果当前会话未发现技能，请直接读取其安装文件或启动新会话。

<a id="review-landing-and-completion"></a>

## 审查、合入与完成

**`0.0.4` 中的执行边界：** 普通提案提交只包含提案中的路径。无关的已暂存和未暂存文件保持不变；提案路径已有编辑时，会停止应用。检出目录中的剩余改动会阻塞验证和审查，不会被静默纳入。合并修复会记录检出目录和索引的检查点；准备后的编辑，或没有检查点的恢复合并，都需要检查。只有父提交和记录的树匹配时，恢复才接受已提交的修复。

验证在使用缓存前检查检出目录是否干净，并在命令执行后检查 HEAD 和干净状态。审查在工作器读取前后检查预期 HEAD 和干净的检出目录，并在记录结论前再次检查。发布和合入也会拒绝有未提交改动或不匹配的候选。请保留手动恢复编辑，并在恢复运行前解决决策；不要重置或自动暂存它们。

验证命令在独立进程组中运行。超时清理会向整个进程组发送信号，必要时升级为强制终止，回收直接子进程，并确认组内没有存活进程后才返回验证失败。即使父进程成功退出，遗留的子进程也会被停止并导致失败。如果无法确认终止，执行会停止并请求处理。

`verify_timeout` 接受正的有限秒数，或表示不限制执行时间的 JSON `null`。省略时保持默认的 180 秒。null 超时会保留在验证身份中，并且不同于任何数字超时，因此在两者之间切换会使验证缓存失效。即使没有执行时间限制，claim 取消、驱动进程断连清理、有时限的清理等待以及持久化终止确认仍然有效。`worker_timeout` 单独控制工作器执行。

审查使用独立于实现的全新代理上下文，检查精确的候选和验证。当 PR 由同一个 GitHub 账号拥有时，评估会以 COMMENT 审查发布，而不是另一人的 APPROVE。

默认端点为 `review`。明确授权的 `land` 端点要求在初始化时同时指定 `--endpoint land` 和 `--allow-land`。宿主在隔离的检出目录中合并当前基线和候选，验证组合后的树，并发布经过验证的精确合并。基线前进会触发再次比较；不会绕过分支保护。

**`0.0.3` 中的集成修复：** 实际合并冲突或组合验证失败会记录持久化修复意图，并使旧审查和验证失效。在工作会话开始前，宿主获取并固定当前基线，然后将其合并到自己拥有的候选检出目录中。`paths.integration_repair` 描述固定的提交、失败、基线差异路径和冲突路径；每个冲突都包含可读取的祖先、候选和基线版本，workspace 文件中包含合并标记。内容保留在文件中，不会注入提示词。没有未合并路径的 Git 失败会作为执行错误报告。

工作器返回已解决冲突的 UTF-8 文件提案或具体问题。有未提交改动的检出目录会被保留；缺失的解决方案和残留标记会阻止提交，不支持的二进制或删除解决方案需要处理。宿主记录两个合并父提交，验证并发布修复后的候选，然后请求新的独立审查，再重试合入。决策回答和中断的尝试会继续已记录的合并，不会中止它；之后基线再次前进时，会在合入阶段重新检查。

合入确认后会安排 triage。关闭 Issue 和完成前必须有针对当前状态的、已清理完待处理事项的回执。不得为了让原轨道通过，而将原始义务转移到后续 TODO 或 Watch。修复使用新分支、验证和独立审查。其他新 TODO 会被注册，但须等待人工审查和选择。

<a id="watch-and-optional-jev"></a>

## Watch 与可选的 Jev

```sh
todo-flow --state STATE watches
todo-flow --state STATE signal TRIGGER --version VERSION
todo-flow --state STATE watch-dispose WATCH_ID --status resolved --evidence 'Current evidence'
# 提升还需要 --target TRACK_ID。
```

非活跃轨道的信号不会启动新的代码执行。已确认的缺陷需要实际工作或已选定的轨道；Watch 表示带有延期理由、触发条件和下一步行动的条件性观察。关闭需要证据，而不是等待一段时间。

todo 和 watchlist 技能推荐将 **Jev 作为可选的筛查辅助工具**：注册时检查调查充分性和重叠，审查 Watch 时判断变更源码的相关性和发现事项的优先级。请先使用现有文件和工具。Jev 集成缺失或失败不会阻塞当前请求。使用已配置集成的文档化接口，保留判断依据，不要将分数视为问题已解决的证据。TODO Flow 不会安装 Jev 客户端、凭据或远程调用。

<a id="updates-and-compatibility"></a>

## 更新与兼容性

有关带保护措施的引擎替换、基于清单的技能更新、回滚和恢复，请参阅 [UPDATES.zh-CN.md](UPDATES.zh-CN.md)。运行时会在使用前拒绝不支持的状态或配置格式。包版本变化不会自动改写项目数据。引擎更新要求所有已知项目中遵循协调机制的进程均为空闲；技能更新只要求相关项目空闲。现有请求和文档保持原位。

<a id="cleanup-migration-and-hooks"></a>

## 清理、迁移与钩子

```sh
todo-flow --state STATE cleanup TRACK_ID --dry-run
todo-flow --state STATE cleanup TRACK_ID
todo-flow migrate-files --source OLD_SQL_STATE --target NEW_FILE_STATE
todo-flow --state STATE hooks
```

在 `0.0.2` 中，完成会请求自动清理轨道的实现工作树（包括早期修复尝试）、集成检出目录、triage 检出目录和已退出的工作器终端。已发布的 `0.0.1` 版本仅支持显式清理当前实现工作树。`init --no-auto-cleanup` 为新项目禁用自动清理；`trackrun ... --no-auto-cleanup` 或 `run --no-auto-cleanup` 为单个驱动进程禁用自动清理。仍可手动清理。

清理保留主检出目录、本地分支、轨道文档、修订版、结果、验证/审查/triage 证据以及原始尝试日志。它将实际检出 HEAD 与已获取的远程基线比较，而不只检查记录的候选 SHA。尚未合入的候选、未完成工作、已修改或未跟踪文件、未知的 ignored 文件、身份变化的终端和有更新活动的终端都会保留并记录原因。ignored 的 Python `__pycache__/*.pyc` 文件可以丢弃，其他 ignored 文件需要检查。不会强制删除工作树或删除分支。

关闭 Orca 终端使用记录的 PTY/incarnation 和新获取的清单。已复用的终端会保留。tmux 窗口仅在指定的单个 pane 已退出时关闭；没有受支持关闭接口的自定义终端启动器需要手动关闭。未解决的终端也会使其关联的检出目录被保留。

清理意图和每项资源的结果保存在 `cleanup/TRACK/EXECUTION.json` 以及 `cleanup.requested`、`cleanup.complete` 或 `cleanup.deferred` 事件中。中断后可以通过 `cleanup TRACK_ID` 重试；后续驱动进程也会重试待处理的清理请求。清理问题不会重新打开已交付工作或重新运行代理。删除后，历史 workspace 路径仍保留在证据中；保留的 Git 分支和提交保存源码。

迁移将旧 SQL 存储复制到新的文件状态中；它不是其他工具台账的导入器。

内部钩子是持久化事件，包括文档注册、执行受理、工作器 claim、工作结果、验证、外部操作确认、决策回答、控制、claim 恢复、Watch 变更、triage 和完成。文件系统重做日志和持久化任务日程保留交接。目前不提供外部回调钩子。

<a id="native-orca-worker-sessions"></a>

### Native Orca 工作器会话

工作器默认没有实际经过时间的限制，包括进程监督器和 native 提案传输。项目配置中显式指定的 `worker_timeout` 仍然生效；使用 JSON `null` 表示不限制。启动握手和单次 CLI 调用仍有时限。native 工作器运行期间，运行时持续检查 claim，因此即使没有时间限制，取消仍会停止所拥有的进程组，并清理身份未变的查看器。驱动进程断连仍保留现有监督器的清理屏障。

可见客户端在受管理工作树中的专用终端里运行。其宿主侧桥接使用 Orca 已安装的 Codex 状态钩子和终端自身的路由环境，将同一会话注册到侧边栏。模型的钩子保持禁用。只有 task/generation、候选 HEAD、workspace、thread 和 turn 都匹配的日志才能驱动该显示。`native-sidebar.json` 保留针对该精确 pane 的新一次公开 `worktree ps` 观察；仅有钩子进程成功或终端创建响应不能视为确认。`launch-status` 区分已确认的侧边栏会话和未确认的客户端。桥接随客户端一起退出，不会留下 shell。侧边栏状态只描述工作器活动，不代表验证、审查、合入或轨道完成。

Native 失败会将异常类型和消息记录到 `native-session.json`，并在决策中报告这些路径。已完成的提案和失败或停止的工作器，会先向显示投递 turn 结束事件，再清理身份未变的查看器。侧边栏投递状态未知时，会保留明确的未确认回执；这不能削弱进程清理或提案验证。

流式片段不会触发文件系统扫描或 Git 子进程。完整提案可用时，宿主检查当前 claim 和精确 HEAD，同时所属驱动进程继续检查取消。收集器可以每五秒在同一连接上读取完整 turn 历史，不会启动或恢复另一个 turn。完成通知缺失时，可以用精确绑定的 thread/turn 中唯一的已完成最终回答确认完成；部分、歧义、失败或不匹配的历史不能用于确认。一秒一次的接收轮询会保留不完整的 WebSocket 帧，不会为工作器施加截止时间。现有 Python 工作器进程在源码更新后仍保留已加载的代码；将工作交给更新后的进程前，请保留其结果，并核对和解决其拥有的清理事项。

新 Codex 轨道可以使用 Orca 管理的 workspace，并在注册前保存持久化创建意图、独立检查 Git/Orca 所有权。现有候选保留其注册路径。native 适配器使用现有的 `CODEX_HOME`，由 Codex 处理其已配置的认证，包括文件、钥匙串和环境凭据。它不会检查或复制凭据，也不会根据 Codex 版本字符串决定是否允许运行。按进程覆盖的配置使 workspace 保持不受信任、外部集成保持禁用、沙箱保持只读且无网络、审批策略保持 `never`。启动 thread 前，适配器在内存中读取生效配置，并禁用每个继承的 MCP 注册；配置响应不会写入尝试证据。实际的启动、认证和协议失败仍是保留证据的失败，不会成为启动第二个工作器的许可。登录存储和令牌刷新仍由 Codex 管理。

宿主对每个 initialize/thread/turn 请求只发送一次，并在传输前记录。启动响应不确定时，会阻止该任务的替代尝试，绝不使用 exec fallback 掩盖。如果已知 turn 失去连接，但原服务器仍运行且 socket 身份未变，适配器会重连一次，并读取该精确 thread/turn 的完整历史。它绝不会重复 thread/start 或 turn/start；未知、额外或不完整的历史仍然阻塞。可见客户端只接收精确的远程 thread/socket，完整最终提案则来自有序的 App Server 通知。终端受理不代表工作器完成。宿主验证 claim 和候选 HEAD，现有监督器在执行外部操作前确认进程组退出。清理查看器前，会检查身份、活动、退出状态和完整清单。检查到关闭之间的输入竞争使用现有已授权策略。终端清理状态未知时，会保留标签页及其证据，不会使已完成提案失效。实际进程组终止以及精确的会话/HEAD 检查仍然必需。

审查使用新的服务器/thread 和已知的实现会话来源信息；旧实现记录保持现有的新审查路径。`launch-status` 和任务检查会显示实际模式、兼容性原因和资源关联，不会打开执行端点。真实模型及外部环境的现场验收尚未执行；本地合成服务器/CLI/进程 fixture 和已记录的本地协议探测是独立证据。
