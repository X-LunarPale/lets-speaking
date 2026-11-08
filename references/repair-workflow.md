# 局部说话音高修复流程

## 确认目标与范围

先离线读用户指定文件，按可见范围检查结构和受损位置。已有明确问题区间时不做全工程改写。范围尚不明确时，用音频/用户描述及局部数值找候选；原始偏移大负值、快速升降、AI 尚未合成、正常清音都不能单独证明损坏。

修复定位必须包含工程、片段 UUID、局部 tick 区间。片段的 `pos+局部tick` 是工程时间；原始数组索引与 CLI 时间排序索引可能不同。原始 note UUID 可重复，用片段身份区分。

## 确认表达语境

先看受影响字词所在的完整分句，必要时扩至前后句。结合原说话音频、用户说明和完整词句，确认需要保留的语气、重音落点、句中或句尾作用；记录依据是音频、用户说明、文本推测还是未知。文本和标点只能提供线索，不单凭曲线形状断言惊讶、委屈、反问等情绪。未知不等于中性。

整字前后24 tick、几个邻字以及插值边界的外侧锚点，用于数值或定位检查，不能替代完整表达语境；停顿阈值也不能自动划定语义句子。原参考没有确定的情绪/语气标签，30例的数值选材不代表覆盖了用户全部表达风格。

把快速升降、大跨度、多次折返视为待解释的现象。参考或用户意图能支持这些起伏时保留它们；只有形状可疑而缺少损坏依据时，先保留原曲线并继续处理已确认的问题。表达尚不明确，也可以对有独立损坏依据的局部做最小恢复，但不要据此重塑整句语气。

## 读取应用数据

```powershell
$aceCli = 'C:\Program Files\ACE Studio\acestudio-cli.exe'
& $aceCli --json project dirty
& $aceCli --json project info
```

若当前是无关的未保存工程，不能强制丢弃。先完成可独立做的离线工作；确实需要应用操作时再请用户保存或切换。若当前已是待修复工程，保留其中的现有修改。

没有未保存冲突时，可以原生打开指定文件：

```powershell
& $aceCli --json project open '目标.acep'
& $aceCli --json track list
& $aceCli --json clip list --track-uuid '{轨道UUID}'
& $aceCli --json clip get --clip-uuid '{片段UUID}' --preferred-time-unit tick
```

先读取，核对字音和位置后再修改：

```powershell
& $aceCli --json clip note-content --track-index 0 --clip-index 0 --range-scope clip-local
& $aceCli --json vocalpitch read --clip-uuid '{片段UUID}' --level user
& $aceCli --json vocalpitch read --clip-uuid '{片段UUID}' --level combined --range-begin 1000 --range-end 1401
& $aceCli --json vocalpitch voicing --clip-uuid '{片段UUID}'
& $aceCli --json vocalparam layers --clip-uuid '{片段UUID}'
```

保持读取结果原样保存为 UTF-8 JSON；例如 PowerShell：

```powershell
& $aceCli --json vocalpitch read --clip-uuid '{片段UUID}' --level combined --range-begin 1000 --range-end 1401 |
    Set-Content -LiteralPath 'target-combined.json' -Encoding utf8
```

这里的文件写入是工具使用示例；在要求 apply_patch 的开发环境中，通过可用结构化写入或脚本导出来保存。

`combined` 的 JSON 应有 `level/posBegin/count/points/voicing.unvoiced/fingerprint`。输出的 `range-end` 不包含在数据内。需要右端点1400，就至少读到1401。

保存区间前后健康上下文的 `user` 数据、显式 `voicing.spans`、有效 `voicing.unvoiced` 和dense `combined`。上下文至少覆盖健康端点与其最近的外侧原始锚点之间的完整区间，以检查可能的边界切线影响；只比较外侧锚点值不充分。小范围修复无需导出整个音频或整个缓存。

## 选择修复方法

优先顺序：

1. **完整上下文/同源重复。** 优先比较可确认同一表达的未坏版本。同一句、同字音仍可能是不同语气或重音版本；核对完整音符、冻结默认及显式发音、声源、时序、裁切和表达意图。先比较实际曲线内容，不以相同UUID推断相同数据；隐藏版本可能有意修改或使用不同字音，不能自动优先。不要误把同一份坏源的另一裁切当健康 donor。
2. **极短毛刺。** 在同一原生有效浊音段内，端点健康、没有必要转折时，用 `bridge`。默认24 tick在120 BPM约25ms只是工具上限。第二轮对原有内部曲线做直线遮挡重建，24 tick窗口约20.16%最大误差超过0.5半音，P95约1.286半音；8 tick仍有约1.17%超过0.5半音。实验不是损坏检测准确率，也未验证原生Hermite插值，不能仅凭长度放行。
3. **中等长度的内部轮廓损坏。** 选择字音、词调、语气、声源、分句位置、重音落点及尾音作用相近的健康 donor，用 `donor` 保留峰谷并匹配目标端点。同字和相近端点不足以证明供体合适；内部峰顶落在另一个字或不同时间，也可能改变表达。
4. **整字/整句丢失或端点也坏。** 先重建音节和句调控制点，依据上下句、发音和完整参考。两个工具不能自动恢复词调、意图或整句 F0。不要为了让工具通过而把清音 mask 删除、把 null 填成0，或任意提高 bridge 上限。

参考曲线库是原始相对偏移，不是目标绝对音高；需要作为 donor 时，根据其轨道/片段/位置从应用读取该健康片段的 **combined** 音高。修复工具不接受 raw data、`ai`、`final` 或 sparse `user`，以避免偏移误用、颤音重复和插值猜测。

用 `deep_speech_analysis.py search` 查询参考索引时，`-` 会追溯到实际头部字音；检查 `pronunciation_head/previous_note/next_note` 后再匹配。2,757个数值候选仅通过内部窗口、数值连续性和无显式原始VUV0筛选；缺失VUV仍未知，不能直接当成健康浊音模板。相同拼音也没有声调或语义保证。完整例子和已发现的同源不同曲线见 [deep-study.md](deep-study.md)。

检索只匹配字音，按索引顺序返回前 `limit` 条，不评估情绪、句调、重音或健康程度。第一条结果没有供体优先权，`rise/peak/valley`也不代表某种情绪。两来源分别查询；若命中缺少完整语境，回到原位置补读整句，再决定是否采用。

辅音原始时长可能超过音符时长；不要为修音高而截短。energy的多数边缘不在音符边界，保留原有跨音符曲线；未建立可用的音高/energy耦合公式，不从能量值反推丢失音高。

如果 `combined` 为空，先分清未合成与真实缺失。完整手绘 `user` 仍可人工分析；若确需合成才能判断，在任务范围内做必要的局部渲染。没有渲染的空缺不能直接判坏。

遇到异常平坦的 `combined`，同时核对 `user` 和对应音符的 `vocalpitch vibrato --note-uuid '{音符UUID}'`。Verse 2.6在未绘制区设置颤音时，可让combined显示音符自身的平坦音高而不改变手绘存储；不能单凭平坦外观判断损坏。

## 生成可检查的修改数据

```powershell
$speechSkill = 'C:\path\to\lets-speaking'
python -B "$speechSkill\scripts\pitch_patch.py" bridge --target 'target-combined.json' --left 1100 --right 1110 --output 'patch.json'
python -B "$speechSkill\scripts\pitch_patch.py" donor --target 'target-combined.json' --left 1100 --right 1300 --donor 'donor-combined.json' --donor-left 2200 --donor-right 2380 --output 'patch.json'
```

`left/right` 均为健康端点，包含在输出中。工具拒绝跨 null、非有限值或 `unvoiced` 区间；有限的坏内部数值可以被替换。MIDI0在明确 voicing 下是合法值，不能单看0判清音。

输出：

- `patch.json`：可写入 `user` 的绝对 MIDI 锚点。
- `patch.meta.json`：源文件、区间、fingerprint、方法和限制。

`donor` 从 donor 中减去其端点直线，把剩余内部起伏映射到目标健康端点的直线上。`--amplitude` 默认1，可在0..2内调整。它会保留目标的总升降趋势，**不会从平坦目标端点自动恢复整个上扬音节的端点音程**。

工具不会判断情绪或对齐语义重音。供体的转折位置需要与目标表达相符，不能为套入数值选材阈值而缩小幅度、压平峰谷或统一句尾。只有明确的表达依据才调整 `--amplitude`；默认1也不证明表达匹配。若损坏涉及承载语气的整体端点趋势，应先依据整句重建控制点，不能指望内部残差映射自动恢复。

默认 `--max-error 0` 不简化，保留每个供体采样及映射后可能为小数的tick；小数tick是合法锚点。健康首末点直接使用目标原值，避免浮点消减让合法MIDI0端点被误拒绝；内部真实越界仍报错，不裁平越界结果。

确需较疏锚点时可显式使用 `--max-error 0.05`，但该5 cents只约束折线。三角形供体简化成首/峰/末三点后，标准单调Hermite在肩部可偏离1半音；这是插值反例，不是ACE原生测量。因此不能把折线误差称为写回精度。密集写入会影响以后Pitch Anchor工具的选取，需要在形状保留与编辑便利之间按实际任务选择，并检查原生dense读回。

所有生成文件默认不覆盖已有文件。修改建议由代理检查；用户已经要求修复时无需重复询问普通可逆的局部编辑许可。

## 原生写入和保存

默认先原生另存为修复副本，读取结果中的 `savedPath`；ACE可能创建工程目录，所以最终路径不一定等于请求路径。

```powershell
& $aceCli --json project save-as '目标-音高修复.acep'
```

另存后重新确认片段 UUID，并在写前新读修复窗口及边界上下文的combined、有效清浊和相关音符内容，确认它们仍与生成建议时相符；健康端点也需一致。若供体继续编辑或合成，重新确认所用供体仍适用。输入变化时重新判断并重建建议。

同时取得当前曲线fingerprint；若与建议记录不符，重新读数、确认差异并重建建议，不能移除 `--if-match` 强行写。该token只保护整个手绘音高存储，不覆盖AI输出、音符或清浊，因此不能用token相同代替上面的局部新读，也不能把独立清浊token传给音高写入。

```powershell
& $aceCli --json vocalpitch write --clip-uuid '{片段UUID}' --points '@patch.json' --if-match '刚读取的TOKEN'
```

省略 `--voicing`，保留显式清浊；仍可能生成未显式设置部分的派生清浊，写后必须检查有效mask。不联动重置音符、独立颤音、energy、发音或其他层。修改多个区间时逐个取得新的fingerprint，不能批量复用旧token。

一次局部写入后读回相同小窗口：

- 锚点与写入建议一致，端点仍连续；对必要转折检查 `combined`。
- 修改区外原有手绘锚点，以及两侧相邻锚点区间的dense `combined` 均未意外改变；只比较锚点不充分。
- 显式清浊与有效 `unvoiced` 没有意外变化。若有变化，先撤销这次原生写入，再调查边界、派生mask与实际插值，不能扩大修复区或强制浊音来掩盖差异。
- 其他音符、音素和参数保持原状。

需要时在完整分句中试听修复前后，核对词调、重音落点、句尾语气、情绪强度及相邻句的衔接，同时检查爆音或意外滑音。单字听着平顺、端点连续都不足以证明原表达保留。最后 `project save` 保存副本，并报告真实 `savedPath`。

如果当前环境无法试听，只报告已完成的数据读回和未验证的听感。单份参考不能支持“全部工程自动无损修复”的承诺。
