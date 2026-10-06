# 更新 TODO Flow

[English](UPDATES.md) · [한국어](UPDATES.ko.md) · [日本語](UPDATES.ja.md) · [简体中文](UPDATES.zh-CN.md)

[README](README.zh-CN.md) · [代理安装](AGENT_INSTALL.zh-CN.md) · [运行指南](OPERATIONS.zh-CN.md) · [演示](DEMO.zh-CN.md)

<!-- translation-source: UPDATES.md; source-sha256: b8f4847e9e26e9a8cb7225813e3f800ed9c4b86951f4ba9fc94420f645477723; status: translated -->

先更新一次共享引擎，再更新每个项目中安装的技能。项目文档和执行记录保留在原有状态目录中。更新不会重新初始化项目，也不会启动待办工作。

<a id="update-capabilities"></a>

## 更新功能

| 功能 | 行为 |
|---|---|
| 版本显示 | `todo-flow --version` 和 `trackrun --version` 显示已安装的软件包版本。 |
| 版本诊断 | `diagnose-versions` 将运行中的引擎、PATH 中的 `todo-flow`/`trackrun`、已安装的技能清单和回环地址仪表盘作为独立来源报告，并在不做任何更改的情况下说明所提供 wheel 的更新计划。 |
| 兼容性检查 | `compatibility` 报告引擎、状态/配置格式、工作器协议，以及可选的已安装技能差异。 |
| 引擎更新 | `upgrade --wheel` 使用明确提供的、更新的本地发行版 wheel 替换 uv tool 安装。 |
| 运行时互斥 | 遵循协调机制的 CLI 操作、驱动进程和仪表盘持有进程锁。引擎更新要求它们全部停止；技能更新要求对应项目空闲。 |
| 技能更新 | 安装清单记录版本、协议和文件的 SHA-256 基线。更新比较基线、当前文件和新技能包。 |
| 本地编辑 | 保留自定义文件、项目语言/状态绑定，以及对新技能包中未更改文件的编辑。若修改冲突，则在任何修改之前停止整个更新。 |
| 备份与回滚 | 替换前备份引擎环境/入口点及受影响的技能目录。回滚须明确执行；更新失败时恢复修改前副本。 |
| 中断的更新 | 持久化 pending 标记阻止正常使用未完成更新的引擎。恢复运行器保留在被替换环境之外。技能更新有自己的恢复标记。 |
| 对未来格式的兼容性 | 拒绝未知的状态/配置格式及工作器/技能协议。旧版读取器绝不会应用未来格式的待处理状态日志。 |

`0.0.1` 发行版的契约为 **状态格式 1、配置格式 1、工作器协议 1 和技能协议 1**。软件包版本与数据格式相互独立。没有新增可选配置元数据的旧文件式项目使用格式/协议 1。此发行版不需要数据迁移。现有的显式 SQL 到文件迁移仍是独立命令。

`0.0.2` 使用 **工作器协议 2** 初始化，以支持基于路径的输入。新初始化的项目配置要求引擎 `0.0.2` 或更高版本。状态/配置/技能格式仍为 1。内置 Claude/Codex 适配器接受现有项目配置，并在不改写状态的情况下生成新的基于路径的输入。自定义命令适配器必须更新为读取 `workspace` 和 `paths`，并在停止期间明确选择协议 2；协议 1 的自定义工作器会在启动前失败。旧发行版引擎无法运行新初始化的协议 2 项目。终端选择是独立的可选设置（`worker_launcher`，默认 `auto`）；`--launcher` 仅改变当前驱动进程。请参阅[工作器执行](OPERATIONS.zh-CN.md#worker-context-and-terminal-launchers)。

`0.0.3` 修复集成修复的交接和恢复，不改变这些格式或协议，也不需要状态迁移。修复指令随引擎提供；请按常规更新流程检查每个项目已安装的技能。

`0.0.4` 增加提交、审查和验证进程的边界检查，不改变格式或协议，也不需要状态迁移。之前中断且没有新检出目录/索引检查点的合并会保留供检查，不会接纳未知的已暂存修改。切换引擎前，请完成或检查现有修复；参阅[执行边界](OPERATIONS.zh-CN.md#review-landing-and-completion)。

`0.0.5` 增加声明式验证输入身份、持久化进程清理、有明确边界的终端生命周期，以及受支持的 native Orca/Codex 会话。状态/配置格式及工作器/技能协议不变。没有身份信息的旧验证成功记录需要重新验证。`init --verify-identity` 适用于新状态；升级不会改写现有配置。请更新项目技能，以获得基于范围的规划、工作和审查指引。此版本的 native 会话支持 Codex CLI 0.157.1；兼容路径和仅本地验证的限制见[运行指南](OPERATIONS.zh-CN.md#native-orca-worker-sessions)。

`0.0.6` 移除精确 Codex 版本门槛、凭据文件限制和终端数量准入检查。Native 工作器复用现有 Codex 登录；旧终端记录和容量台账保留为历史证据，启动下一个工作器前无需迁移。即使查看器清理延期，已完成的提案仍会保留。状态/配置及工作器/技能协议版本不变；请更新项目技能以获得修订后的执行指引。

`0.0.7` 移除默认工作器期限，增加宿主观测的 native 侧边栏状态和完成历史核对，并提供运行中取消与经过验证的所属工作树清理。现有显式工作器限制仍有效；`worker_timeout: null` 选择无限时执行。状态/配置格式及工作器/技能协议不变。请更新项目技能，以获得请求范围、适度验证和自动清理指令。

`0.0.8` 增加轨道级活动摘要、带父级来源的精确待处理义务去重、限制大小的替换提案，以及保留的验证日志产物。状态/配置格式及工作器/技能协议不变。请更新项目技能，以获得限定变更提案的指引。

`0.0.9` 增加可选的验证预检和中间检查、与条件绑定的机器证据、工作器停止回执、显式请求尝试次数上限，以及过时集成检出目录的清理。状态/配置格式及工作器/技能协议不变。现有项目配置不会被改写；预检和相关检查是新状态的可选设置。请更新项目技能，以获得分阶段验证指引，以及在报告轨道运行完成前必须清理完所属资源的要求。

`0.1.0` 新增四语言指南与 UI、主题选择、工作进程模型/推理强度证据、按角色路由、按验证结果分配后续工作、独立评审来源区分、技能共存和离线质量案例。前一次执行仍持有轨道锁时，将阻止后续工作取得该轨道。状态/配置格式及工作进程/技能协议保持不变，无需数据迁移。请在空闲时升级引擎并更新项目技能。现有项目绑定、本地修改和配置仍受保护。

`0.1.1` 修复在新合并中复用已完成的无修改提案的问题，在保留提交引用的前提下安全回收被替代的干净集成检出，并将测试运行时注册与实际安装隔离。状态/配置格式及工作进程/技能协议保持不变，无需数据迁移。请在空闲时升级引擎。现有项目状态、用户修改及未经确认的资源仍受保护。

<a id="1-inspect-and-stop-relevant-processes"></a>

## 1. 检查并停止相关进程

```sh
todo-flow --version
trackrun --version
todo-flow --state /absolute/project/todo compatibility \
  --target /absolute/project/.agents/skills
```

要在停止任何进程之前查看实际使用的版本，请运行只读诊断。传入每个运行中仪表盘的 URL，并可选地传入计划安装的本地发行版 wheel：

```sh
todo-flow --state /absolute/project/todo diagnose-versions \
  --target /absolute/project/.agents/skills \
  --dashboard http://127.0.0.1:8765 \
  --wheel /absolute/releases/todo_flow-0.1.1-py3-none-any.whl
```

`diagnose-versions` 不获取运行时锁，不创建 `TODO_FLOW_HOME`，从不运行 PATH 中的可执行文件（而是读取其解释器环境的软件包元数据），并且只通过无需令牌的 `/api/version` 查询 `127.0.0.1`/`localhost` 仪表盘。每个条目报告 `source`、`path`、`version` 和 `status`（`observed`、`unknown` 或 `unreachable`）；没有 `/api/version` 的仪表盘报告为 unknown，可能是旧版发行版。使用 `--wheel` 时，计划为 `applicable`、`blocked`（仪表盘有响应、有运行中的工作、有待处理事务或中断的更新）、`conflict`（已安装技能的编辑与 wheel 内置技能冲突）或 `unknown`（发行版清单不明确，或引擎不是 uv tool 安装），随后列出要运行的命令。它复用 wheel 检查、项目兼容性和技能 dry-run 检查；绝不会仅凭更高的版本号就认定可以安装。添加 `--json` 可获得结构化输出。

让工作完成或暂停，然后正常停止驱动进程和仪表盘。更新器不会替你终止工作器。未解决的运行中任务和记录中仍存活的工作器 PID 也会阻止更新；请检查它们，并在已停止驱动进程的 claim 过期后使用 `reconcile`。排队的请求会保留，不会由更新执行。

正常使用会将项目状态路径注册到 `TODO_FLOW_HOME` 下，默认是 `$XDG_STATE_HOME/todo-flow` 或 `~/.local/state/todo-flow`。引擎更新器会根据候选的兼容性清单检查这些已知项目。从未通过此运行时使用的项目无法自动发现：切换版本前请明确检查它们。

所有遵循协调机制的进程必须使用同一个 `TODO_FLOW_HOME`。进程锁是协调边界，不是沙箱。没有防护的旧进程、直接的包管理器命令、源码编辑和自定义集成都可能绕过锁；更新前请停止这些进程。Windows 和分布式文件系统不在当前支持范围内。

<a id="2-update-the-shared-engine"></a>

## 2. 更新共享引擎

此路径要求已有 **`uv tool install` 安装**，且 PATH 中可找到 `uv`。源码检出目录、editable 环境、普通虚拟环境安装，以及具有自定义额外要求/选项或入口点的 uv tool 安装会收到诊断信息，不会被覆盖。请在空闲时按原有工作流程更新这些环境，再检查项目兼容性和技能。

从 [v0.1.1 发行版](https://github.com/JakeB-5/todo-flow/releases/tag/v0.1.1)下载 wheel 和 `SHA256SUMS`，验证校验和后传入本地 wheel 路径：

```sh
todo-flow upgrade --wheel /absolute/releases/todo_flow-0.1.1-py3-none-any.whl --dry-run
todo-flow upgrade --wheel /absolute/releases/todo_flow-0.1.1-py3-none-any.whl
```

计划会显示版本、产物摘要、兼容性契约和已知项目。执行时会在独占运行时锁下重新检查这些信息，保存产物快照，备份已安装环境和两个入口点，调用 uv，并检查安装后的版本、入口点及内置技能。普通安装/验证失败会恢复原环境。升级不改变项目配置、文档、claim 或远程状态。

返回的 `backup` 标识引擎回执。之后如需恢复：

```sh
todo-flow upgrade --rollback ENGINE_BACKUP_ID
```

回滚要求当前发行版符合预期，并检查旧引擎是否仍能读取已知项目数据。它绝不会撤销代码合入或远程操作，也不会回滚项目状态。引擎回滚和项目技能回滚是独立操作。

<a id="3-update-each-projects-installed-skills"></a>

## 3. 更新各项目已安装的技能

Claude 使用 `.claude/skills`，相应项目安装使用 `.agents/skills`：

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --dry-run

todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills
```

省略 `--state` 时，已安装的 `project.json` 可以提供状态路径。现有绑定及其精确内容会保留。管理技能包之外的额外项目技能不受影响。已从技能包移除的资源只有在仍匹配原始基线时才会删除；已废弃技能目录中的自定义文件会保留。

如果你和发行版都修改了同一个管理文件，命令会列出冲突且不应用任何修改。重试前请将你的编辑与新技能包协调一致。没有一概强制覆盖的选项。必要时启动新的代理会话，以便发现更新后的技能指令。

要恢复已完成的更新：

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --rollback SKILL_BACKUP_ID
```

如果更新后文件又发生变化，回滚会拒绝执行，因此不会静默抹掉之后的用户编辑。请先保留并协调这些修改。

<a id="installations-created-before-manifests"></a>

### 引入清单前创建的安装

不会猜测现有未跟踪的技能目录是未经修改的原始版本。请使用 **匹配的原始技能包** 执行：

```sh
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --adopt --dry-run

todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --adopt
```

接纳要求旧管理文件与该技能包完全匹配。如果不同，请确定原始版本并协调文件，而不是删除安装。接纳后，后续更新便有可靠基线。

<a id="4-recover-an-interrupted-update"></a>

## 4. 恢复中断的更新

```sh
# 已安装的 CLI 仍能启动时：
todo-flow upgrade --recover

# 对于项目技能：
todo-flow --state /absolute/project/todo update-skills \
  --target /absolute/project/.agents/skills --recover
```

替换引擎前，命令会打印一条使用绝对路径的 **基础 Python + 恢复运行器命令**。请保存这一行。运行器位于被替换环境之外的 `TODO_FLOW_HOME/engine-updates/ID/engine_updates.py`。如果更新途中 CLI 入口点消失，请在打印出的命令后加上 `--recover` 执行。它通过持久化回执恢复原安装，不需要运行中的仪表盘或模型。

回执、不可变的修改前副本和被替换后移出的目录会保留供检查。它们可能包含本地路径和自定义技能内容，请勿放入 Git 或公开报告。尚未实现按保留期限自动清理。不要手动移除 pending 标记来绕过恢复。

<a id="verification"></a>

## 验证

单元测试覆盖冲突、用户编辑保留、废弃资源、精确回滚、部分失败、恢复、拒绝符号链接、运行时互斥和不支持的格式。隔离的验收脚本构建合成的未来发行版，并在不触碰正常安装的情况下测试真实 uv tool 替换：

```sh
uv build --out-dir dist/update-check
uv run python scripts/update_smoke.py --artifacts dist/update-check --root /absolute/new-update-test-directory
```

它检查运行中的仪表盘阻止升级、成功更新引擎、技能更新/回滚、引擎回滚、从故意损坏的发行版自动恢复，以及 CLI 缺失时的恢复。它还逐一比较所有权威状态文件的更新前后内容，确认排队工作被保留而未执行。不需要模型调用、Issue、PR 或远程修改。合成的未来 wheel 是测试产物，不是可发布的发行版。

<a id="further-preparation-for-future-releases"></a>

## 为未来发行版进一步准备

这些是后续事项，不是当前实现已经提供的能力。

| 优先级 | 准备事项 | 原因 / 完成标准 |
|---|---|---|
| 公开发布前 | 选择权威分发渠道和软件包/仓库名称 | 公布一个规范安装 URL 和一个升级来源；宣传包索引安装前先确认名称所有权。 |
| 公开发布前 | 不可变的发行版本、校验和、可复现的标签/构建关系 | 让用户能验证下载的 wheel。更新器摘要可检测产物变化，但不是发布者签名。 |
| 公开发布前 | 在受支持的托管运行器上执行更新验收任务 | 本地成功不能替代配置的 Linux/macOS 矩阵。 |
| 任何数据格式变更前 | 明确的迁移注册表，包含预检、状态备份、可恢复检查点和降级规则 | 定义实际的旧→新转换，并在交付前测试中断。绝不能仅从软件包版本推断迁移。 |
| 下一步 | 版本发现及 stable/preview 渠道 | 显示可用发行版及变更，在获取更新锁前确定精确产物。 |
| 下一步 | 签名的发行版来源和可信发布 | 除了匹配所提供文件的哈希，还要确定产物由谁生成。 |
| 下一步 | 依赖/Python 兼容性及回滚覆盖 | 测试真实依赖变更和解释器切换，而不只是契约相同的软件包版本。 |
| 下一步 | 项目清单管理和批量更新 | 列出、移除登记或迁移已知项目；提供各项目的计划与回执，不靠目录猜测。 |
| 下一步 | 项目/技能协议迁移与混合版本支持政策 | 定义旧版已安装技能和工作器与每个引擎保持兼容的期限。 |
| 下一步 | 平稳排空工作与更强的工作器进程身份 | 改善长时间更新调度，并通过进程启动身份区分记录的 PID 与 PID 重用。 |
| 下一步 | 备份保留期限、磁盘空间检查和中断备份清理 | 保持可恢复性，同时避免本地存储无限增长。 |
| 以后 | 离线发行包、代理/索引配置和组织级部署控制 | 在无法自由获取依赖的环境中实现可复现安装。 |
| 以后 | 更多操作系统支持 | 宣称支持 Windows 前，替换 POSIX 专有的锁和进程假设。 |

此发行版尚未实现自动下载最新版本、状态格式迁移、定时自我更新、插件市场更新，以及跨项目全成或全败的升级。
