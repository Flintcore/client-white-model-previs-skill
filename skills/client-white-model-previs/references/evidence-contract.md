# 证据与交付门禁契约

所有工作产物在公开仓库外。`job.json` 保存源/模板相对路径和字节 SHA、源帧率分数/分辨率/赋予片段帧数、timeline、Eevee64、原生100%、暗场光追、配色决定、标准SHA、完整skill Git revision，以及scene映射。模型映射实际由制作者填入，不预填不存在的对象或推断脚点。

## 报告链

| 报告 | 实际生成者/必要内容 |
|---|---|
| inputs_report.json | job.py 实际探测/解码/来源 SHA，切样时真实像素前缀相等 |
| blender-diagnostic.json | 独立重开当前保存工程；真实全帧生产网格、骨骼、端面/BVH/支撑期检查 |
| render_manifest.json | Blender实际渲染器在每帧完成后写入PNG/hash/尺寸，完整才 COMPLETE |
| blender-final.json | 再次独立重开，绑定同一保存sceneSHA并核完整PNG CRC/hash/尺寸/原生清单 |
| media.json | 实际编码后三MP4规格、video/audio全解码、音频包/PTS、四文件 SHA |
| visual-review.json | 另一身份真实全片/逐帧/末秒复核；每规则每镜头对应源帧/观察/证据 |
| gate.json | 全版本、源、scene、render、media、review 和全部有效阻断项重新绑定 |
| package_receipt.json | 同名四文件 ZIP 成员列表/CRC/逐成员 SHA，引用当前gateSHA |

机器检查不签视觉结论；review模板默认所有状态 unverified。`gate.py` 拒绝缺项、重复规则、非当前版本/scene、changed files、非实际帧集、制作人自审和没有完整观看的记录。条件性 not_applicable 必须带 rationale 与 source_frames；非条件条款不能随意豁免。建议/删除线记录保留溯源，不要求填硬门禁通过位。

## visual-review 中每条有效规则

```json
{
  "id": "CW001",
  "status": "unverified",
  "shot_ids": ["shot_001"],
  "observations": "真实审查后填写对应源帧、判断和已修问题；不使用空泛通过句",
  "evidence": [
    {"kind":"inputs","path":"inputs_report.json","sha256":"ACTUAL_REPORT_SHA256"},
    {"kind":"renders","path":"renders-final/render_manifest.json","sha256":"ACTUAL_MANIFEST_SHA256"},
    {"kind":"visual","path":"delivery/A/A 对比.mp4","sha256":"ACTUAL_VIDEO_SHA256"}
  ]
}
```

示例是工作表形式，不是已验收数据。每条具体 required evidence kinds 见 standards.json；输入、Blender、native manifest、media report、最终对比和job identity分别作为相应kind。允许一个真实证据支撑多条规则，但每规则/每shot的结果必须明确，不能把重复模板句当成观看。

`scene.shots=[{"id":"shot_001","start":1,"end":N}]` 应覆盖赋予片段全范围且无空洞/重叠，切点来自实际参考。`reviewer.id` 与 `job.producer` 不同，复核绑定 source/blend/white/comparison SHA；all_frames_reviewed/playback_reviewed/last_second_reviewed 只有实际完成才置true。报告中的 client_acceptance 始终false，只有甲方单独回执可说明外部签收。

## 版本失效

`job.py validate` 校验实际源片规格、输入字节与不可变队列任务身份；不签署模板可用性或视觉达标。修改帧率、配色、源/模板SHA等关键规格会让旧job_id失效。运行时必须匹配干净Git提交或逐文件hash验证的安装回执；任意手填40位字符串不构成版本锁定。

证据kind绑定实际当前报告：inputs对应inputs_report、blender对应最终Blender报告、renders对应原生manifest、media对应媒体报告、integrity对应job，visual对应当前复核记录或最终原片/白模/对比视频。不相关文本文件即使有SHA也不能冒充这些证据。最终 `.blend` 另有可移植性检查：未打包资源只接受同交付目录的 `//A.mp4`，不接受指回工作区或机器绝对路径。

更换源/模板/规范/帧范围/配色约定或重做scene，旧相关报告均失效。之后按当前阶段生成新报告，不能仅改报告中的hash。每次实际修改有新job/revision目录；旧工程、原件、反馈保留用于回退。

`team_queue.complete --gate` 会再次 hash gate.artifacts；只有元数据的服务器不自己判断像素或几何，独立reviewer必须访问真实证据副本并确认hash。producer完成只进入待审，队列accepted是内部状态，不是客户已签收。
