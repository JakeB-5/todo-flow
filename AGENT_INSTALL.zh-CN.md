# TODO Flow — 智能体安装与首次运行

[English](AGENT_INSTALL.md) · [한국어](AGENT_INSTALL.ko.md) · [日本語](AGENT_INSTALL.ja.md) · [简体中文](AGENT_INSTALL.zh-CN.md)

[README](README.zh-CN.md) · [运维](OPERATIONS.zh-CN.md) · [更新](UPDATES.zh-CN.md) · [演示](DEMO.zh-CN.md)

<!-- translation-source: AGENT_INSTALL.md; source-sha256: 91a1c6b7bd92fffce08b72274b66cfd8aa79147fa24d24cf049ce5c283855ed8; status: translated -->

遵守用户请求的范围。复用已有的安装、配置和授权。本文档本身不构成执行工作或合入变更的许可。完成已获授权、可撤销的准备工作；只询问缺失的决策，或要求用户完成必须由其本人进行的认证。

## 1. 确认目标与语言

区分 TODO Flow 的检出目录与用户的目标项目。阅读目标项目的 `AGENTS.md`、开发说明、验证命令、Git 远程、基准分支、已有技能和状态配置。

**主要语言由用户选择：英语（`en`）、韩语（`ko`）、日语（`ja`）或简体中文（`zh-CN`）。** 复用已有的项目语言，或本次请求中明确表达的偏好。否则，询问用户要使用哪种语言。等待时可以继续独立的环境调查，但不要根据本 README 的语言擅自决定。技能指令仍使用英语；报告、问题和新编写的文档使用选定语言，除非用户明确另有要求。

```sh
FLOW_SOURCE='/absolute/todo-flow'
FLOW_PROJECT='/absolute/my-project'
FLOW_STATE="$FLOW_PROJECT/todo"
FLOW_SKILLS="$FLOW_PROJECT/.agents/skills"

git -C "$FLOW_PROJECT" rev-parse --show-toplevel
git -C "$FLOW_PROJECT" status --short
git -C "$FLOW_PROJECT" rev-parse --verify HEAD
```

Claude 会话使用 `.claude/skills`。变量可能不会在不同的 shell 调用之间保留；请重新声明或使用绝对路径。如果 `todo/` 属于其他工具，请选择单独的状态目录，不要覆盖它。保留已有变更、Git 历史和技能安装。

## 2. 准备工具与认证

检查 Python 3.11+、uv、Git，以及选定且已认证的 Claude/Codex CLI。GitHub 集成还需要已认证的 `gh` 和实际目标远程的访问权限。没有用户请求中的依据，不要替换模型。不要在文档、配置或输出中包含凭据。

全新安装发行版时，无需检出源码：

```sh
uv tool install https://github.com/JakeB-5/todo-flow/releases/download/v0.0.9/todo_flow-0.0.9-py3-none-any.whl
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --version
trackrun --version
```

官方仓库为 [https://github.com/JakeB-5/todo-flow](https://github.com/JakeB-5/todo-flow)；发行文件与校验和位于 [GitHub Releases](https://github.com/JakeB-5/todo-flow/releases)。如果用户请求源码开发，则从 TODO Flow 的检出目录安装：

```sh
cd "$FLOW_SOURCE"
uv sync --frozen
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --help
trackrun --help
```

复用兼容的已有安装。重新安装前先诊断 PATH。替换共享引擎前，考虑其他正在运行的项目。使用文档指定的 GitHub 发行版 wheel；不要假定其他软件包索引中的同名包就是本项目。

`0.0.2` 增加了按需读取文件和优先使用终端的工作进程。已有 `0.0.1` 安装需要更新引擎与项目技能才能使用这些功能；请遵循[更新指南](UPDATES.zh-CN.md)。新启动器默认使用 `auto`：先使用 Orca、配置的终端或已有 tmux，然后才使用 headless。不要仅因为工作进程是自动运行的就强制使用 headless。参见[工作进程执行](OPERATIONS.zh-CN.md#worker-context-and-terminal-launchers)。

`0.0.3` 还将集成修复连接到当前基准分支，并要求合入前重新验证和独立评审。升级共享引擎以获得此修复；参见[修复行为](OPERATIONS.zh-CN.md#review-landing-and-completion)。

`0.0.9` 包含提案提交隔离、准确候选版本的检出检查、已声明验证输入的身份确认、持久化进程清理、不受数量限制的逐次执行终端清理，以及原生 Orca/Codex 会话支持。工作进程默认没有时限；原生侧栏状态、运行中取消，以及经过验证的自有工作树清理均保留其所有权边界。检出目录中已有的手动修改会被保留，并可能需要恢复决策；不要自动重置或暂存这些修改。

## 3. 配置项目

如果已初始化，请读取已有状态配置，并复用其工作进程、语言、验证、范围和终点。不要在运行期间重新执行 `init` 或编辑执行配置。

对于新项目，从实际项目中获取以下值：

| 设置 | 来源 |
|---|---|
| `--repo`, `--state` | 目标 Git 根目录和独立的项目权威状态目录 |
| `--language en`, `--language ko`, `--language ja`, `--language zh-CN` | 用户选择的主要语言 |
| `--base`, 可选的 `--github` | 实际远程/基准分支和 GitHub 所有者/仓库 |
| `--worker` | 用户选定或可用的、已认证的 Claude/Codex CLI |
| `--verify` | 已有且可正常运行的验证命令，以 JSON argv 表示 |
| `--context`, `--write` | `0.0.2` 中的探索提示（`0.0.1` 中用于选择快照）和获准写入的路径模式 |
| 终点 | 默认为 `review`；已获合入授权时使用 `--endpoint land --allow-land` |

配置验证命令前先运行它。报告已有失败，不要隐瞒。排除包含秘密的文件。执行需要初始提交、Git 作者身份和 `origin`。除非用户请求涵盖这些操作，否则不要创建缺失的远程、提交无关工作或重置历史。

以下为 Python 项目的示例，请替换实际值和语言：

```sh
todo-flow --state "$FLOW_STATE" init \
  --repo "$FLOW_PROJECT" --base main --worker codex --language en \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md
```

只有使用受支持的 GitHub 适配器时才添加 `--github OWNER/REPOSITORY`。尚未实现 Forgejo 和多个仓库的联合交付。使用项目已有策略或本地 Git 排除设置，防止意外提交运行时记录。不要取消对已有版本化轨道台账的跟踪。

## 4. 安装技能并打开仪表盘

```sh
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS"
todo-flow --state "$FLOW_STATE" serve --port 8765
```

安装器保留已有目录，并在每个已安装技能的 `project.json` 中记录语言与状态。它继承已初始化项目的语言。独立安装支持 `--language en|ko|ja|zh-CN`；与已初始化项目冲突的显式值会被拒绝。不要为了处理一个冲突而替换整个技能目录。对于已有安装，先使用 `update-skills --target PATH --dry-run` 检查冲突，然后只在请求的更新范围内应用。清单接管与回滚见 [UPDATES.zh-CN.md](UPDATES.zh-CN.md)。

<a id="coexist-with-occupied-skill-names"></a>

### 与已被占用的技能名称共存

没有冲突时，保留全部九个已有名称：`todo`、`track-picks`、`trackrun`、`track-run`、`watchlist`、`track-work`、`track-review`、`track-land` 和 `track-triage`。只有当规范名称已被占用，且不属于当前 TODO Flow 安装时，才可使用别名。确认 `todo-flow install-skills --help` 列出了 `--alias`；较旧发行版可能需要通过已获授权的安装/更新流程，安装包含此功能的源码构建版本。

先检查目标。以下示例假定另一个工作流恰好占用了 `todo`、`track-picks`、`track-run` 和 `watchlist`，而建议的四个别名都未被使用。只为实际冲突指定别名。Codex 使用 `.agents/skills`，Claude 使用 `.claude/skills`：

```sh
FLOW_SKILLS="$FLOW_PROJECT/.claude/skills"
# Choose this separate STATE before initialization if another tool owns todo/.
FLOW_STATE="$FLOW_PROJECT/todo-flow-state"
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS" \
  --alias todo=flow-todo \
  --alias track-picks=flow-track-picks \
  --alias track-run=flow-track-run \
  --alias watchlist=flow-watchlist
```

对于已初始化的 TODO Flow 项目，应保留原有 STATE。对于新项目，在安装前用选定的独立 STATE 执行第 3 步。不要在其他工具的状态上进行初始化。安装器不会将那个工具的 `project.json` 导入为 TODO Flow 上下文。

如果别名已被占用，整个安装会在修改技能文件之前失败。错误会指出角色和入口；请选择未使用的别名，或智能体支持的其他安装目标。保留已有文件和符号链接。不要用 `--adopt` 接管其他工作流；接管只适用于匹配的旧版 TODO Flow 技能包。不要重命名没有冲突的角色。

检查返回的 `entrypoints` 映射（规范角色 → 安装名称）、`state` 和 `language`，然后阅读每个安装名称下的 `SKILL.md` 及相邻的 `project.json`。在此示例中，请让智能体使用 `flow-todo`、`flow-track-picks` 或 `flow-watchlist`；原名称仍属于另一个工作流。`flow-track-run` 链接到未改名的 `trackrun` 技能。安装后的角色表将全部九个 TODO Flow 角色链接到其实际入口。对于 Claude，请在目标项目中打开的会话里确认这些名称；如果发现结果尚未刷新，请打开新会话，或显式读取 `.claude/skills/flow-todo/SKILL.md`。

别名不会改变 shell 命令。继续使用 `todo-flow --state "$FLOW_STATE" ...` 和 `trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID`。保留安装清单：更新时会复用已保存的映射，无需重复 `--alias`。

```sh
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --dry-run
# After reviewing the plan and resolving any conflicts:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS"
# Only when an interrupted update is reported:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --recover
# To undo a completed update, use its actual returned backup ID:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --rollback BACKUP_ID
```

以上命令先检查计划并解决冲突，再应用更新。仅当报告更新中断时使用 `--recover`。撤销已完成的更新时，使用实际返回的备份 ID。

技能包中的文件未变化时，本地修改会被保留；本地与技能包同时发生变化时，整个更新会中止以等待协调。回滚会拒绝覆盖后续修改，而不会丢弃它们。恢复和回滚从备份中还原本安装拥有的文件、映射和上下文，不管理另一个工作流。未知清单格式会被拒绝；不要删除清单来绕过此检查。

`tests/test_skill_coexistence_bundle.py` 中的七个回归测试，在 `.agents/skills` 和 `.claude/skills` 两种目录下使用实际九技能包的一次性副本。它们覆盖默认名称保持不变、四个同时发生的名称冲突、已占用别名的拒绝、已有文件和符号链接、已安装的 frontmatter、角色链接、模板字节、STATE 和 CLI 示例的保留。别名更新测试覆盖本地修改、基线与映射保留、冲突时拒绝整个更新、中断恢复、回滚和未知清单格式。通过 `uv run python -m unittest discover -s tests -p 'test_skill_coexistence_bundle.py' -v` 运行，并报告所评估候选版本的实际结果。测试通过提供的是文件系统布局和元数据证据。它们不会启动 Claude、检查其会话中的技能发现，或调用模型。实际 Claude 发现与模型调用必须单独报告；此 fixture 既不执行，也不暗示完成了这两项操作。

在持续运行的终端/进程中启动仪表盘。如果端口已被占用，使用空闲端口，不要终止无关服务器。验证实际 URL、项目和默认语言。显示语言切换仅保存在当前浏览器中，按项目隔离；它不会改变工作进程语言，也不会翻译历史文档。

如果当前智能体会话没有发现新安装的技能，请直接阅读已安装的 `SKILL.md`，并说明是否需要新会话才能发现它们。

## 5. 登记第一个实际需求

如果请求仅限于设置，请提供仪表盘 URL 和 `todo [requirement]` 请求示例。不要在实际工作项目中创建任意示例轨道。如果用户要求首次执行却没有提供需求，请询问希望进行的变更。

阅读已安装的 todo 技能。调查实际需求，搜索已有轨道和 Watch，然后使用已安装的模板，在权威状态目录之外编写可评审的 HTML 文档。可见文档与结构化说明使用选定语言；将 `language` 和 HTML `lang` 设为 `en`、`ko`、`ja` 或 `zh-CN`。保持 ID 和模式键不变。

```sh
todo-flow --state "$FLOW_STATE" register /absolute/scratch/first-track.html
```

需要时添加 `--assets`。在 `http://127.0.0.1:PORT/documents/ID/REVISION/index.html` 中使用实际返回的 ID 和修订号。检查已登记文档的浏览器渲染与代表性交互，并提供链接。没有实际执行视觉验证，就不要声称已验证。

todo 和 watchlist 技能推荐可选的 Jev 辅助。使用已有工具开始并完成请求的工作；缺少 Jev 不会阻碍设置。不要将安装 Jev 或索取凭据变成用户未请求的前置要求。

## 6. 选择、运行与交接

使用 track-picks 检查当前状态、依赖和可执行范围。如果用户已经请求选择并执行这个首个需求，请在该范围内继续。仅登记或推荐并不构成执行授权。

阅读 trackrun，并执行实际登记的 ID：

```sh
trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID
```

`--jobs` 是可选参数。如果使用 `--request-only`，请确认另有驱动进程正在运行；只持久保存请求不等于完成首次运行。

检查 `state.json`、`tasks`、`attempt-records`、`results`、`decisions` 和 `effects`。对于 GitHub，将实际 Issue/PR 状态与回执对照。`review` 终点生成已评审的候选版本。`land` 终点要求已合入的 SHA、当前有效且已清理完待处理项的分诊记录、已完成轨道，以及存在 Issue 时将其关闭。

发生中断时，检查证据并基于同一状态恢复；不要重新初始化。用 `answer` 记录真实用户回答，然后按需运行驱动进程。不要把技术错误编造成用户决策。保留失败记录，并区分经过干预的恢复与顺利完成的运行。新的后续 TODO 等待选择。

使用选定语言交接：**安装与状态路径、语言、仪表盘/文档链接、实际结果和 Issue/PR 链接、剩余决策及下一条命令**。指出仍在运行的终端/进程。

## 更新已有安装

阅读 [UPDATES.zh-CN.md](UPDATES.zh-CN.md)。先检查已安装版本和项目兼容性。保留当前主要语言、状态绑定、用户修改和已有执行授权。单纯更新不授权新轨道或合入。

对于 uv tool 安装，显式选择可信发行版，并使用受保护的 `upgrade --wheel` 路径。替换前，让用户/驱动进程完成或暂停正在执行的工作，并停止仪表盘。不要通过包管理器直接替换来绕过维护冲突。保留打印出的恢复命令和返回的备份 ID。更新已安装项目技能前先执行 dry run；冲突需要协调解决，不能通过删除目录或强制替换处理。验证新引擎、技能和已有文档，然后只重启用户请求涵盖的进程。

当前不提供自动联网发现版本的功能。源码检出目录应在没有运行中任务时使用已有更新/安装流程，然后执行相同的兼容性和技能检查。如果更新中断后 CLI 消失，请使用保存在被替换环境之外、基于原始 Python 的恢复执行器。
