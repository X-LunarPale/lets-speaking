# Let‘s speaking

[English](README.en.md)

Let‘s speaking 是一个辅助 AI 使用 ACE Studio 的 skill。它会参考已有的说话片段，帮助检查音高问题，并对局部做一点优化，尽量保留原来的情绪、语气、重音和节奏。

**目前只针对中文（普通话）做过调整，其他语言尚未适配。**

它只是辅助检查和小范围优化，**不会直接产出完整的语音调教作品，也不是一键生成语音的工具**。你仍然需要已有的工程，并在 ACE Studio 中检查和试听最终效果。

仓库包含操作说明、辅助脚本和参考数据，不是训练好的语音模型。目前已做过数据和脚本检查，还没有完成实际试听验证。

## 安装教程

以下以 **Windows + Codex** 为例。本项目已在 Python 3.14 环境下检查过脚本；其他系统和 Python 版本尚未逐一验证。

### 1. 准备环境

- 能读取本地文件、执行命令的 Codex 桌面端或终端端。
- Python 3.14，以及一份已有的中文（普通话）`.acep` 工程。
- 要把调整应用到工程时，需要在同一台电脑上运行支持智能体连接的 ACE Studio。只做离线文件分析时，可以先不连接。

### 2. 安装 skill

**方式一：让 Codex 安装（推荐）**

把下面这段话发送给 Codex，使用它自带的 `skill-installer`：

```text
$skill-installer
请从 https://github.com/X-LunarPale/lets-speaking 安装这个 skill。
SKILL.md 位于仓库根目录，请使用 --path . --name lets-speaking。
```

安装完成后，在下一轮对话中输入 `$lets-speaking` 使用它。如果仍未出现，重新打开 Codex 后再试。

**方式二：手动安装**

1. 在本仓库页面选择 **Code → Download ZIP**，然后解压。
2. 将解压后包含 `SKILL.md` 的整个文件夹改名为 `lets-speaking`，放入 Codex 的用户级 skills 目录。
3. 本机 Codex 自带安装器默认使用下面的位置；如果设置过 `CODEX_HOME`，则使用 `%CODEX_HOME%\skills\lets-speaking`。不同版本请以安装器报告的目录或 [Codex 官方说明](https://developers.openai.com/codex/skills/) 为准。

```text
%USERPROFILE%\.codex\skills\lets-speaking\
├── SKILL.md
├── agents\
├── scripts\
├── references\
└── assets\
```

`SKILL.md` 必须直接位于 `lets-speaking` 文件夹内，不能多套一层 `lets-speaking-main`。保留整个文件夹的内容，不要只复制 `SKILL.md`。完成后，在下一轮对话中使用；如果未被识别，重新打开 Codex。

### 3. 安装脚本依赖

在 PowerShell 中运行：

```powershell
python -m pip install cbor2 numpy matplotlib
```

这些依赖用于读取工程、分析数据和绘图。如果使用早于 Python 3.14 的版本，还需要安装 `zstandard`；这些版本的兼容性尚未完整验证。让 Codex 运行脚本时使用与安装依赖相同的 Python。

### 4. 连接 ACE Studio

1. 打开 ACE Studio。点击窗口右下角的智能体图标，按 **Connect to Agents** 的提示选择适用的助手入口，将复制的连接提示发送给 Codex。
2. 按连接提示安装 [ACE Studio 官方插件](https://github.com/BeatMagic/acestudio_agent_plugin)。它提供操作 ACE Studio 的基础说明，与本 skill 分开安装。
3. 连接后，让 Codex“读取当前 ACE Studio 的工程信息”。能读到当前轨道等信息后，再进行工程调整。

优先使用 ACE Studio 自带的 CLI；不能使用时，再按官方说明配置 MCP 连接。Windows 默认位置为：

```text
C:\Program Files\ACE Studio\acestudio-cli.exe
C:\Program Files\ACE Studio\ace-mcp-server.exe
```

如果安装位置不同，在 ACE Studio 的 **Preferences → General → External Agent Access** 中复制实际路径。完整步骤见 [ACE Studio 官方连接说明](https://docs.acestudio.ai/ai-agent/external-agent-access)。

## 开始使用

安装后，在 Codex 中发送，例如：

```text
$lets-speaking
请检查 D:\Projects\example.acep 中“这里填需要处理的句子”的音高。
保留原本的情绪、语气、重音和节奏，只处理明显损坏的部分。
先说明建议；实际修改时另存为新工程，保留原文件。
```

把示例路径和句子换成自己的。如果要处理已经打开的工程，也可以直接说明轨道、片段和位置。

安装本 skill 不会自动修改工程。附带脚本只输出分析结果或候选音高数据；把调整应用到 ACE Studio、检查和试听，仍是后续步骤。

## 常见问题

- **找不到 skill：** 检查 `lets-speaking` 文件夹内是否直接包含 `SKILL.md`，确认放入当前 Codex 使用的 skills 目录，再重新打开 Codex。
- **提示缺少 Python 模块：** 用运行脚本的同一个 Python 重新执行依赖安装命令，避免把依赖装到另一个环境。
- **连接不到 ACE Studio：** 确认 ACE Studio 正在同一台电脑上运行，并从其设置中重新复制连接路径。安装 skill 本身不会建立连接。

## 进一步了解

- [给 AI 的使用说明](SKILL.md)
- [具体处理步骤](references/repair-workflow.md)
- [参考片段整理](references/1500-study.md)
