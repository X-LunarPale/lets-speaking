# ACE 工程和音高的实际语义

本页的容器字节数和原始采样统计指《第二章》；第二来源《1500语调》的版本22与精选范围见 [1500-study.md](1500-study.md)。不能把某份工程的数量或参数覆盖直接套到另一份。

## 离线文件

本次参考是 `ACEP2` 容器，工程内容版本 14，外层不是 JSON，也不是 ZIP。实测：

| 字段 | 字节/数值 |
|---|---:|
| 文件总长度 | 19,521,876 |
| ZSTD 内容偏移 | 1,280 |
| 压缩内容长度 | 19,520,596 |
| 解压后 CBOR 长度 | 125,915,709 |

开头字段为 little endian：magic `[0:5]`、flags `[5]`、header_size `[6:8]`、content_offset `[8:16]`、compressed_content_size `[16:24]`、content_size `[24:32]`。按偏移解压 ZSTD 后用 CBOR 解析即可读取主内容。元数据和内容 hash 的语义未验证；不要为了修改音高而重新制造或清空这些字段。

`ace_project.py` 支持：

- 此种 ACEP2/ZSTD/CBOR。
- 已解码的工程 JSON。
- 非加密旧格式 JSON/base64/ZSTD。

加密旧版本交给原生应用读取；脚本没有应用程序破解或许可绕过用途。

保留原始 CBOR 字典。公开转换库的模型会补默认值、迁移字段、插值或采样；不适合保全现有工程的局部修改。

## 坐标和裁切

每四分音符 480 tick。120 BPM 时 480 tick 为 0.5 秒。工程具有实际 tempo 列表，不能假设全程固定 BPM；参考含同 tick 的连续 tempo 事件。

对 tick 片段：

- 片段完整起点为 `pos`，其内部坐标都是局部 tick。
- 可见内部范围为 `[clipPos, clipPos + clipDur)`。
- 工程上的位置为 `pos + 局部 tick`。
- 可见音符条件为 `note.pos < 可见结束 && note.pos + note.dur > 可见开始`。

多个裁切片段保存同一整段源数据，所以全量存储样本会重复。统计风格时按可见范围；修改时保留裁切范围外的数据。UUID 也会复用，离线使用 `(pattern UUID, note UUID/索引)` 或轨道/片段/位置定位，不能按 note UUID 全局替换。

## 两种音高表示不能混用

`parameters.pitchDelta` 名称不能决定其值的语义：

| 曲线 | 数据 | 音高值含义 |
|---|---|---|
| `type:"data"` | `offset` + 每 tick 一个 `values` | 相对引擎底层音高的半音偏移 |
| `type:"anchor"` | 扁平 `points:[tick,value,...]` | 绝对 MIDI 半音 |

本次参考的 2,376 条音高曲线全部为 `data`。数据里的 `-53` 不是绝对 MIDI -53，不能直接给 CLI，也不能对所有偏移加同一个常数。

底层音高在音符交接处会平滑，可能包含其他模型处理。`note.pitch + delta` 只能在稳定音符内部作为粗略诊断，不能代替引擎 `combined/final`，尤其不能用来精确写回。

原始空缺、低值、无声编码不能统一为零。参考里184,759个采样精确抵消最近音符支架，但它们全部没有序列化VUV。用公开119点平滑底层算法近似重建时，181,018个采样达到 `|base+delta|<=0.0001`，同样全无VUV；这是不同定义的统计，不等于实际静音样本数。

原始VUV的时序强烈支持0清/1浊，但未经当前引擎逐段验证。10,291个VUV与pitchDelta重叠采样均未抵消支架，517个VUV采样甚至没有手绘pitchDelta。因此正音高、抵消平台、稀疏原始VUV与原生有效清浊必须分别处理；VUV缺失不表示浊音，也不能把原始VUV直接当成CLI `support/spans/unvoiced`。原值保留，修复依赖当前应用的有效清浊。

## CLI 的音高管线

使用运行时 `help 'vocalpitch read'` 等文档；本次可用 CLI 0.18.7，面向 ACE Studio 2.1.9。其内置帮助也描述了较新版本行为，所以运行时返回形状优先。

`ai -> user 覆盖 -> combined -> vibrato -> final`

- `user` 是绝对 MIDI 锚点 `[[tick,value],...]`；`[tick,null]` 终止当前有绘制区，避免跨空缺插值。这是唯一可写音高层。
- `combined` 通常是 AI 与手绘合并、尚未叠加独立颤音的音高。修复工具用它作为 dense 输入。Verse 2.6 (SingingMamba) 在未绘制区放置颤音时，会在这里显示音符自身的平坦音高，但不写入 `user`；遇到可疑平坦段，结合 `user` 和对应音符的 `vocalpitch vibrato` 判断，不能认定是人为拉平或损坏。
- `final` 已包含独立颤音；直接当 `user` 写回可能重复叠加颤音。
- Dense 读数从 `posBegin` 开始，每 tick 一个样本，`count` 应与点数相等。
- Dense `null` 是没有曲线；`final` 中零值可能是清音编码，也可能是合法 MIDI 0，必须查看 `voicing.unvoiced`。
- `voicing.spans` 是显式覆盖，`support` 是有手绘曲线的范围，`unvoiced` 是实际无浊音的范围。不要混淆。

`vocalpitch write` 接收绝对 MIDI 0..127 的有序锚点，覆盖首末锚点之间的区域，用 MonotonicHermite 插值。省略 `--voicing` 是仅修改音高的正常操作。密集逐 tick 锚点可能影响以后使用 Pitch Anchor 工具，需要按实际修复在形状保留与编辑便利之间选择。

写入精确穿过每个锚点，不会自动抽稀。但折线简化的误差不能约束三次插值的采样结果；为了保留供体采样，新工具默认不简化，小范围修复可接受较密锚点，需要较疏控制时显式选择简化并检查原生读回。

省略 `--voicing` 保留显式pin，仍可能在unset部分烘焙派生清浊；不保证 `voicing.unvoiced` 不变。区外锚点相同也不足以证明相邻区外采样不变：本机帮助未说明跨写入边界的切线拼接细节，实际修复必须比较边界两侧dense读回。这两点不能用离线数值测试冒充原生验证。

`vocalpitch read` 的fingerprint属于 `vocalpitch-curve`，只覆盖片段的整个手绘音高存储。它不保护AI输出、音符上下文或清浊；清浊有独立的 `vocalpitch-voicing` token。因此相同曲线token不证明旧的combined和有效mask仍然适用，写前仍需针对实际修复窗口新读这些输入，确认建议成立。

## 参数和发音

参考采用旧式参数存储：`energy` 近 1 的倍率类数据，`realEnergy` 是另一套数值；不要与新引擎的 `[0,1]` 曲线混用。发音字段版本 >=7 的 `headConsonants` 按秒；更旧版本不能直接标成秒。

当前CLI的 `help 'phoneme list'` 明确：空 `syllable` 使用冻结默认发音；歌词 `-` 延续所属组的头部字音，没有独立发音。头辅音在Note Start之前，首元音以Note Start为锚点；序列化时长是原始意图，邻接约束后的有效时长可能不同。合成也会写入音素时长，不能仅凭字段存在判断为手工调教。参考的所有569个 `-` 均紧接前音符，不能把它们当作新的字音供体。

原始 `vocalControls` 的 line 可以使用平行 `ticks/values` 数组，不能一律认为是扁平 `points`。参考的 `soft/power` 存在负值。

在应用中先调用 `vocalparam layers`；参数可用性、`scale`、`valueRange` 和可写层由当前声源/引擎决定。`effective` 是只读的引擎合成结果，不手工用各层重算。修复音高时不需要重写这些参数。

## 来源

2026-11-08 读取并与用户原文件对照的公开实现与本机运行时资料：

- [LibreSVIP ACEP 容器实现](https://github.com/SoulMelody/LibreSVIP/blob/main/libresvip/plugins/acep/acep_io.py)
- [LibreSVIP ACEP 原始模型](https://github.com/SoulMelody/LibreSVIP/blob/main/libresvip/plugins/acep/model.py)
- [LibreSVIP ACEP 音高解析](https://github.com/SoulMelody/LibreSVIP/blob/main/libresvip/plugins/acep/ace_studio_parser.py)
- [LibreSVIP 底层音高实现](https://github.com/SoulMelody/LibreSVIP/blob/main/libresvip/plugins/acep/base_pitch_curve.py)
- 本机 CLI 的 `help 'clip get'`、`help 'phoneme list'`、`help 'vocalpitch read'`、`help 'vocalpitch write'`、`help 'vocalpitch voicing'`、`help 'vocalpitch vibrato'`、`help 'vocalparam layers'`、`help 'project save-as'`、`help guardrails`。

这些来源支持读取语义，不等于已验证本机工程的听感、所有私有字段或离线重写兼容性。
