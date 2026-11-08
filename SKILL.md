---
name: lets-speaking
description: "辅助检查和微调 ACE Studio 中文（普通话）说话工程（.acep）的局部音高，尽量保留情绪、语气和节奏。仅作辅助优化，不直接产出完整的语音调教作品；不用于常规唱歌调教。"
---

# Let‘s speaking

目前仅针对中文（普通话）做过调整。这个 skill 只是辅助检查和局部优化，不会直接产出完整的语音调教作品，其他语言尚未适配。

把 ACE 当作对白和说话调教工具处理。目标是恢复局部损坏的连续音高，同时保留字音、词调、句调、情绪、语气、重读和停顿。不要把说话修成符合调式或音阶的歌声。

这份 skill 已对用户提供的《第二章》完成多轮离线研究和独立审查，并从用户注明有部分差错的《1500语调》中，经整字、邻域筛选与曲线图复核选入30例。两份来源分别保留，不把不同声源的音域或时序合成一套规则。它保存参考数据、定位索引、判断方法和修复工具，不是训练过的语音模型；两份均未完成原生合成和听感验证。显示名为 `Let‘s speaking`，调用名为 `$lets-speaking`。

## 首先读取

- 第一次处理此类任务，读 [参考研究](references/reference-study.md)，了解该用户的说话调教和真实异常边界。
- 细查语流、延续音、清浊、同源变体或选修复参照时，读 [第二轮细查](references/deep-study.md)。其中有插值遮挡实验和可定位的不同版本。
- 解析工程或解释数值，读 [数据格式](references/ace-format.md)。
- 实际修复时，读 [修复流程](references/repair-workflow.md)。
- 使用轮廓候选、解释验证结果或判断修复可靠性时，读 [审查结论与边界](references/repair-validation.md)；其中记录了已修正的反例和仍待原生验证的插值行为。
- 使用第二来源、学习较长时序或审查参考自身局部差错时，读 [《1500语调》精选研究](references/1500-study.md)。核心平顺但字尾异常的反例在此；30例只作为经过筛选的数值参考。

数据资源：

- `assets/reference-profile.json`：可见音符、音高存储、参数和发音统计。
- `assets/reference-contours.json`：40 个原始相对音高示例，附轨道/片段/字音定位。它们不能直接作为 CLI 的绝对音高写入。
- `assets/reference-deep-profile.json`：实际 tempo 积分、内部窗口统计及短段直线插值损失。
- `assets/reference-note-index.json`：4,601 个可见音符出现记录及 2,757 个去重数值轮廓候选，带延续音头部和邻音。文件较大，用脚本查询，勿整体读入上下文；候选未经原生清浊或听感验证。
- `assets/reference-rhythm.json`、`reference-voicing.json`、`reference-variant-cases.json`：节奏/发音、原始 VUV 对齐和三处不同版本/疑点的详细测量；按需查询。
- `assets/reference-review-evidence.json`：大跨度核心的敏感度分层、原始Unicode字音和同源组的独立核对。
- `assets/reference-1500-profile.json`：第二来源的整体存储与参数统计，不是合格样本清单。
- `assets/reference-1500-selected-index.json`：仅30个入选出现及核心，兼容现有search；`raw_note_contexts`另存整字与前后24 tick的原始偏移，不会随检索展开。声源配置与短文本上下文同时保留。
- `assets/reference-1500-selection.json`：逐层筛选条件、实际数量、入选位置及6个暂缓样本；数值阈值只用于这次选材，不是损坏判据。
- `assets/reference-1500-context-cases.json`：6个暂缓样本的原始核心、整字邻域和邻字摘录。用于结合语境研究复杂起伏，情绪与语气尚未确定；不混入30例索引，也不自动作为修复供体。

## 工作原则

1. 先判断是**真正的局部损坏**、清音/无声编码、未合成空缺，还是有意的情绪、语气起伏。大跨度、陡升陡降、多次折返或不平顺都不能单独证明损坏；曲线平顺也不证明表达正确。表达意图未知时保留“待确认”，不默认中性，也不根据形状强贴情绪标签。
2. 保持现有片段裁切、音符时序和音高支架、歌词、音素、辅音时长、换气及非音高参数。修复范围之外不做自动优化。
3. 先看完整分句，必要时扩至前后句，核对语气、重音落点和尾音作用；固定tick邻域与几个邻字不能替代这层表达语境。优先使用受损区附近仍完整的轮廓；需要替代时，再选字音、词调、表达作用和声源相近的完整片段。同一句的不同语气版本也不能自动互换。`-` 要追溯头部字音；未标调拼音、同UUID均不能证明参照合适。
4. 只有已证实为损坏、同一有效浊音段、健康端点且没有必要转折的极短毛刺才适合端点衔接。参考中端点相距24 tick的插值实验有约20%窗口丢失超过0.5半音；时间短并不保证可以拉直。较长损坏保留或重建升降、谷底、峰顶，不对整句全局平滑或量化。
5. 离线读原始字典，不经有损的全工程格式转换。脚本不负责重写 `.acep`；实际落盘使用 ACE Studio 的原生保存。
6. 默认交付新的修复工程副本。仅在用户明确要求时覆盖原文件。用户只要求学习参考时，完成学习和 skill，不据此修改参考工程。

## 本机工具

优先 `C:/Program Files/ACE Studio/acestudio-cli.exe`。CLI 与 MCP 操作同一个实时工程，不能把它当作隔离会话。每次先确认当前工程；遇到无关的未保存内容，不使用 `--discard-changes`。

离线工具不影响正在打开的工程。Python 3.14 可用标准库 ZSTD，另需 `cbor2`；较旧 Python 还需 `zstandard`。仅缺依赖时安装相应模块。

```powershell
$speechSkill = 'C:\path\to\lets-speaking'
python -B "$speechSkill\scripts\ace_project.py" profile '目标.acep' --out '目标分析.json'
python -B "$speechSkill\scripts\ace_project.py" extract '目标.acep' --track 0 --pattern 0 --begin 1000 --end 1400 --out '局部数据.json'
```

`profile` 使用可见范围统计，`extract` 输出片段局部 tick。两者只写分析文件。文件中的 `pattern` 索引不能直接假定等于 CLI 的时间排序 `clip-index`；用 UUID 核对。

细查脚本另需 `numpy`，绘图时需 `matplotlib`；普通解析和修改建议工具不依赖它们：

```powershell
python -B "$speechSkill\scripts\deep_speech_analysis.py" search --index "$speechSkill\assets\reference-note-index.json" --syllable de --track 1 --limit 6
python -B "$speechSkill\scripts\deep_speech_analysis.py" search --index "$speechSkill\assets\reference-1500-selected-index.json" --syllable 'd e' --track 1 --limit 6
python -B "$speechSkill\scripts\deep_speech_analysis.py" analyze '目标.acep' --summary '细查.json' --index '音符索引.json' --plot '细查.png'
```

查询按歌词或当前有效音素匹配；显式发音存在时，不再匹配已被取代的冻结默认。`matched_fields` 区分歌词命中与音素命中。结果按索引顺序截取，不评估情绪、语气或供体优劣；第一条命中没有优先权。延续音追溯头部，结果保留对应出现位置、语言、核心实际时长和待检查标记。这里输出的 `note.pitch+delta` 只是内部窗口近似，不是可写入 CLI 的 `combined`。

两份索引分别查询；`core-xxxx`只在本来源内有意义，不按该ID跨源合并。`peak/valley/turns`是去除端点直线后残差的几何分类，可能仍是原始曲线单向下降；不是整字峰谷或普通话声调。

`scripts/pitch_patch.py` 根据 CLI 导出的 **dense combined** 绝对音高生成修改建议：

- `bridge`：短毛刺的健康端点连接，默认最多 24 tick。此值只是工具上限，不是损坏阈值，也不保证该长度内的转折可被省略。
- `donor`：把完整参照片段的内部起伏映射到受损区，同时精确保留目标健康端点。默认保留每个供体采样；只有显式指定 `--max-error` 才进行折线简化，该误差不约束原生三次插值。

它不操作应用，不猜清浊，不修补未证明为损坏的空缺。完整命令、写回方式与限制见修复流程。

## 完成标准

报告具体修复位置、损坏依据、拟保留的表达和方法，区分音频/用户说明与文本推测、未知；读回实际改动，比较区间内部及两侧相邻锚点之间的dense音高，检查显式清浊、有效清浊及非音高设置。区外锚点不变不能单独证明区外曲线不变；省略voicing也不冻结派生清浊。需要听感判断时在完整分句中比较修复前后，检查字调、重音落点、句尾语气、连接和爆音。未实际试听就明确说明，不能把数值验证称为表达或听感通过。
