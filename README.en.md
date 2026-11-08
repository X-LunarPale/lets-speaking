# Let‘s speaking

[中文](README.md)

Let‘s speaking is a skill that helps an AI work with existing speech projects in ACE Studio. It uses suitable speech passages as references to check pitch problems and make small, local improvements, while aiming to preserve the original emotion, tone, emphasis, and rhythm.

**It has only been adapted for Mandarin Chinese so far. Other languages have not been adapted.**

It is an aid for checking and making small improvements. **It does not create a finished voice performance or generate speech in one click.** You still need an existing project and should check and listen to the result in ACE Studio.

This repository contains instructions, helper scripts, and reference data, not a trained voice model. The data and scripts have been checked, but the results have not yet been verified through listening.

## Installation

These instructions use **Windows + Codex**. The scripts have been checked with Python 3.14; other operating systems and Python versions have not been individually verified.

### 1. Prepare Your Environment

- Codex desktop or CLI with access to local files and permission to run commands.
- Python 3.14 and an existing Mandarin Chinese `.acep` project.
- To apply changes to a project, run ACE Studio with agent connectivity on the same computer. A connection is not needed for offline file analysis.

### 2. Install the Skill

**Option A: Ask Codex to install it (recommended)**

Send this message to Codex using its built-in `skill-installer`:

```text
$skill-installer
Install the skill from https://github.com/X-LunarPale/lets-speaking.
SKILL.md is at the repository root. Use --path . --name lets-speaking.
```

After installation, use `$lets-speaking` in your next message. If it still does not appear, reopen Codex and try again.

**Option B: Install manually**

1. On this repository's page, select **Code → Download ZIP** and extract the archive.
2. Rename the extracted folder containing `SKILL.md` to `lets-speaking` and place it in your Codex user skills directory.
3. The built-in installer on the tested setup uses the location below by default. If you have set `CODEX_HOME`, use `%CODEX_HOME%\skills\lets-speaking` instead. For other versions, follow the directory reported by the installer or the [official Codex documentation](https://developers.openai.com/codex/skills/).

```text
%USERPROFILE%\.codex\skills\lets-speaking\
├── SKILL.md
├── agents\
├── scripts\
├── references\
└── assets\
```

`SKILL.md` must be directly inside `lets-speaking`, without an extra `lets-speaking-main` folder. Keep the entire folder's contents rather than copying only `SKILL.md`. Use the skill in your next message; reopen Codex if it is not detected.

### 3. Install the Script Dependencies

Run this in PowerShell:

```powershell
python -m pip install cbor2 numpy matplotlib
```

These packages support project reading, data analysis, and plotting. Python versions earlier than 3.14 also need `zstandard`; compatibility with those versions has not been fully verified. Ask Codex to run the scripts with the same Python interpreter used to install the dependencies.

### 4. Connect ACE Studio

1. Open ACE Studio. Click the agent icon in the lower-right corner, follow **Connect to Agents**, choose the appropriate assistant entry, and send the copied connection prompt to Codex.
2. Follow that prompt to install the [official ACE Studio plugin](https://github.com/BeatMagic/acestudio_agent_plugin). It provides the basic ACE Studio operation guidance and is installed separately from this skill.
3. Ask Codex to read the current ACE Studio project information. Once it can report the current tracks and other details, continue with project adjustments.

Prefer ACE Studio's bundled CLI. If it cannot be used, follow the official instructions to set up an MCP connection. Default Windows locations:

```text
C:\Program Files\ACE Studio\acestudio-cli.exe
C:\Program Files\ACE Studio\ace-mcp-server.exe
```

For a custom installation location, copy the actual paths from **Preferences → General → External Agent Access** in ACE Studio. See the [official ACE Studio connection guide](https://docs.acestudio.ai/ai-agent/external-agent-access) for complete instructions.

## First Use

After installation, send a request to Codex, for example:

```text
$lets-speaking
Check the pitch of the sentence "[insert the Mandarin sentence]"
in D:\Projects\example.acep.
Preserve its emotion, tone, emphasis, and rhythm. Only repair clearly damaged parts.
Explain the proposed changes first. Save any actual edits as a new project,
keeping the original file.
```

Replace the sample path and sentence with your own. For an open project, you can instead identify the track, clip, and position.

Installing this skill does not automatically change a project. The bundled scripts only produce analysis or proposed pitch data. Applying adjustments in ACE Studio, checking them, and listening to the result are separate steps.

## Troubleshooting

- **Skill not found:** Check that `SKILL.md` is directly inside `lets-speaking` and that the folder is in the skills directory used by your Codex installation. Then reopen Codex.
- **Missing Python module:** Run the dependency installation command with the same Python interpreter that runs the scripts.
- **ACE Studio will not connect:** Keep ACE Studio running on the same computer and copy its connection paths again from its settings. Installing this skill does not establish the connection.

## More Details

The detailed instructions below are currently in Chinese:

- [Instructions for the AI](SKILL.md)
- [Editing workflow](references/repair-workflow.md)
- [Reference passages](references/1500-study.md)
