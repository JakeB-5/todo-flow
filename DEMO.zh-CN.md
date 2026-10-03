# 查看 TODO Flow 的实际操作

[English](DEMO.md) · [한국어](DEMO.ko.md) · [日本語](DEMO.ja.md) · [简体中文](DEMO.zh-CN.md)

[README](README.zh-CN.md) · [智能体安装](AGENT_INSTALL.zh-CN.md) · [运维](OPERATIONS.zh-CN.md) · [更新](UPDATES.zh-CN.md)

<!-- translation-source: DEMO.md; source-sha256: 181597c84a147f8b0a8c41d739588656e153da14097006b5d541e294a88fe283; status: translated -->

## 本仓库自身的配置

[自托管操作指南](examples/self-hosting/README.md)记录了本仓库在2026年9月26日的实际配置：单独安装的 `0.0.4` 引擎、韩语项目语言、Codex 工作进程以及 review 终点。其中包含实际的空白仪表盘和可在其他环境使用的验证步骤。在该时间点，本项目尚未注册或执行任何轨道。今后的实际任务结果应与这份配置记录一并保存。

## 仪表盘导览

![仪表盘导览](assets/demo/dashboard-tour.gif)

这些截图来自使用**合成的只读数据**的实际应用，并非模型执行的证据。测试数据包含48个活跃轨道和2,500个已完成轨道，涵盖当前工作、等待决策和条件性 Watch。

[英语截图](assets/demo/dashboard-en.png) · [韩语截图](assets/demo/dashboard-ko.png) · [活动](assets/demo/activity-en.png) · [丰富的计划示例](assets/demo/track-example.png)

在源码检出目录中操作，并指定**仓库外的新目录**：

```sh
uv sync --frozen
uv run python scripts/dashboard_fixture.py --state /absolute/new-dashboard-demo --language en
uv run todo-flow --state /absolute/new-dashboard-demo serve --port 8766
```

打开 `http://127.0.0.1:8766`：

1. 滚动连续显示的活跃列表，搜索并选择两个符合执行条件的轨道。
2. 复制 `trackrun` 命令；复制本身不会启动工作。
3. 打开活动页面，查看负责人、当前任务和等待决策的事项。
4. 打开轨道，阅读文档、条件和证据。
5. 搜索独立的已完成归档，并在需要时加载更早的记录。
6. 切换 English / 한국어 / 日本語 / 简体中文。选择和决策草稿会保留，已编写的内容仍使用原来的语言。

该测试环境会拒绝仪表盘的修改请求。请在另一个已初始化的项目中执行实际工作。

从运行中的浏览器截取画面后，可用以下命令合成导览：

```sh
uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py \
  --output assets/demo/dashboard-tour.gif \
  assets/demo/dashboard-en.png assets/demo/selection-en.png \
  assets/demo/activity-en.png assets/demo/track-en.png assets/demo/completed-en.png
```

该脚本只组合提供的截图，不会虚构执行状态。

## 运行完整工作流

使用一个具有初始提交、origin 远程仓库、身份验证和正常测试的一次性项目。按照[安装指南](AGENT_INSTALL.zh-CN.md)操作，选择 `en`、`ko`、`ja` 或 `zh-CN`。如果此次练习需要包含实际集成和 triage，请使用 `--endpoint land --allow-land`。没有这些选项时，流程会在候选变更通过评审后结束。

例如，向智能体提供两个范围明确的需求：

```text
todo 为临时网络故障添加次数有限的重试。保留对永久性故障的处理，并添加测试。
todo 解释永久性请求失败，并提供有用的后续操作建议。添加消息测试。
trackpicks
```

[重试计划示例](examples/retry-backoff.html)和[错误消息计划示例](examples/request-error-message.html)展示了可供评审的 HTML 文档。注册前，请根据实际测试项目调整范围和证据；这些文件是需求示例，并非已完成的工作。

检查生成的文档，然后使用实际返回的 ID 请求执行：

```sh
trackrun retry-backoff request-error-message
```

在仪表盘中观察独立的 worktree 和任务。检查每个候选变更的实际验证和独立评审。如果已授权集成，请核对集成 SHA、集成后的 triage、议题关闭情况和完成状态。如果工作正在等待决策，请回答，并在需要时重启驱动进程。后续 TODO 仍保持未选中状态。

## 查看此前的公开验收运行

2026年9月24日的一次性公开测试产生了以下成果：

| 需求 | 议题 | 已合并变更 |
|---|---|---|
| 文本 slug 化 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/1) | [PR #3](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/3) |
| 序列分块 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/2) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/4) |
| 数值范围限制 | [Issue #5](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/5) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/6) |

前两个轨道在修复 triage 重复搜索缺陷后需要恢复。新建的第三个轨道无需额外干预便完成了。这些成果展示的是当时的执行工作流，不能证明后续所有 UI、本地化变更或大规模项目场景均已通过验证。

## 复现远程验收

最近一次开发运行于2026年9月24日在可见的 Orca 终端中使用了基于路径的工作进程：

| 需求 | 议题 | 已合并变更 |
|---|---|---|
| 压缩空白 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/1) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/4) |
| 保留不同值的首次出现 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/2) | [PR #5](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/5) |
| 带明确回退行为的除法 | [Issue #3](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/3) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/6) |

三个实现工作进程曾并行运行。包括基线推进后重新进行的 triage 在内，共有14个实际 Codex 工作进程在 Orca 终端中运行。选中的三个轨道全部完成；新建的使用文档 TODO 和已有的许可证 TODO 保持未选中状态。本次运行没有等待决策或运行时错误，交付的测试项目通过了19项测试。测试项目包含一个超过150 KB的源文件。这些是范围有限的验收任务，不是大规模项目基准测试，也不是实际 Claude 验证。

运行完成后，生成的11个 worktree 和14个工作进程终端被清理，原有的466个证据文件以及所有本地分支的顶端提交均被保留。随后，另一项[清理生命周期任务（PR #8）](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/8)由实际 Codex 工作进程完成：在集成和 triage 后，该任务生成的3个 worktree 和4个工作进程终端被自动移除，只留下主检出目录和保留的证据。工作流关闭了 [Issue #7](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/7)。

以下命令**会创建公开仓库，并产生真实的议题、PR、模型调用和合并**。只有在确实打算进行这项外部实验时，才使用自己的账号和新的测试目录运行：

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --launcher orca --register-orca --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

此方式还会在本地运行中的 Orca 应用中注册一次性测试项目，并要求使用真实终端。无界面运行时，请使用 `--launcher headless`，并省略 `--register-orca`。脚本会注册可供评审的 HTML 计划。生成的报告会记录实际轨道、远程成果、工作进程运行时间的重叠、终端凭据记录以及输入大小。请分别保留首次失败、恢复和全新运行的结果。有关实验边界，请参阅 [CONTRIBUTING](CONTRIBUTING.md#demos-and-external-acceptance)。
