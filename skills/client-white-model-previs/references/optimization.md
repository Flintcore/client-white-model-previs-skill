# 固定 SOP、阶段缓存与匹配预检

此版本落实可验证的工作流提效，不宣称通用自动解算或任意视频1:1验收。低精度几何不等于低精度时空要求；模型细节让步，动作幅度/节奏、相机、站位、遮挡、接触和光照结构保持原标准。

## 一次准备，反复调用

团队固定 Skill 完整提交、标准SHA、Blender版本、源/模板SHA、FPS分数和范围。旧job使用其原提交；在独立目录测新工具，禁止替旧证据手改版本。每设备复用人物骨架、投影导出器、预览/编码/门禁脚本；每任务只填源观测、场景尺度/支撑/锚点、身份映射和动作目标。自动化脚本需实际执行，缓存不是自动生成算法。

可见主体十件：球头、躯干、左右上臂/前臂、左右大腿/小腿。隐藏骨架/控制器数量不限，不额外渲染球关节、手掌或脚。比例一次适配，全片固定。对实际求值网格做落地/穿模检查，不把控制器位置当网格位置。

## 固定计算顺序与局部修订

| stage | 产物和约束 | 当前失败时的局部承接 |
|---|---|---|
| observations | 原片可见2D点/轮廓/事件、稳定ID、静态背景fit/holdout分组；保留置信度和observed/inferred | 只补遮挡、低置信或ID错误的帧；不重复抓取已锁素材 |
| camera | 静态背景、几何/消失点/视差约束的相机轨迹 | 锁主体，先修背景重投影；不靠移动主体掩盖错误镜头 |
| pose | 固定长度骨架、可见头肩躯干/肢体投影、幅度和开始/峰值/结束 | 原快速运动保留；只去重建新增噪声，不全局缩小幅度或滤到峰值错帧 |
| contact | 真实支撑网格、显式支撑/摆动/换步、固定端面接触点 | 不让地面跟人物跑；隐藏脚相位为推断，不宣称实测；别用错误脚点拖低头身 |
| scene | 角色/环境/道具的工程合成；真实前后关系和出入画 | 用源片可见面积/边界检查，禁止隐藏角色或伪挡板凑像 |
| light | 主光方向、亮暗比、阴影方向/长度、明显变化 | 区别曝光/世界光和主光；仅调灯，不重算前五stage |
| preview | 同FPS、全任务帧低清渲染；数值匹配+实际看对应失败帧 | `match_check`给帧段，物理检查给接触/穿模；先整改再最终1080p导出 |
| render | 工程保存4K；全帧直接1080p/100%/64采样/Eevee，光追关闭 | 入口双预检，旧预览/旧工程SHA不放行 |
| media | 全片编码/解码、原片声音、末秒、上原片下白模 | 保留既有四文件门禁；诊断图不放大交付 |
| review | 独立完整播放/逐帧和123条规则 | 不自动填写人工观看；内部通过与甲方签收分别记录 |

场景**几何定义**可事先作为多stage显式input：相机和接触都需要它。scene stage指最终工程合成，不要求先解动作再凭空设地面。几何改变须把其文件同时列入依赖该几何的stage.inputs。光照文件只列到light，不能把可变的整个job.json或整个工程列到所有stage造成全量失效。

固定DAG是 `observations → camera → pose → contact → scene → light → preview → render → media → review`，其中pose/contact/scene还直接依赖camera。需要重新标注源观测时下游自然失效；只改light时前五stage命中。多设备队列负责租约，每个worker的pipeline/job目录独立，不把同一缓存目录并发写到SMB。

## 阶段工作表和真实缓存

```powershell
# 只有缺少工作表时init；不会执行解算或重建已有任务。
python -X utf8 "$S/pipeline.py" init --job WORK_JOB/job.json
python -X utf8 "$S/pipeline.py" plan --job WORK_JOB/job.json
python -X utf8 "$S/pipeline.py" start --job WORK_JOB/job.json --stage camera
# 实际完成对应程序后使用start返回的TOKEN；不要填写想象的耗时。
python -X utf8 "$S/pipeline.py" record --job WORK_JOB/job.json --token TOKEN --status passed --output analysis/camera.json
python -X utf8 "$S/pipeline.py" timing-summary --job WORK_JOB/job.json
```

`pipeline.json` init锁实际job binding，算法字段初始null（待配置）。每stage示例：

```json
{
  "algorithm": {"name": "pinned-camera-fit", "path": "code/camera_fit.py", "sha256": "ACTUAL_64_HEX_SHA", "revision": "OPTIONAL_FULL_40_HEX_GIT_SHA"},
  "parameters": {"holdout_policy": "fixed-ids-no-training"},
  "inputs": ["analysis/source_observations.json", "config/static_geometry.json"]
}
```

path与inputs是job内可移植相对文件。配置权重文件也列入inputs；代码SHA每次读取验证，不接受仅声明latest/main。key包含运行时脚本SHA、job版本/源/模板/规格、当前算法/参数/inputs及依赖阶段的真实输出SHA。输出非空、完整文件hash相同才命中；文件存在、旧passed或半成品不够。

`record --output/--report`支持job内相对路径（例如`analysis/camera.json`）或job内文件的绝对路径，不再额外加`WORK_JOB/`前缀。preview/render/media/review必须提供`--report`对应当前实际报告；preview会重算match报告，render还需`--output`记录实际渲染工程`.blend`且自动纳入全帧PNG，review只承接现有独立gate，不生成通过结论。

失败用 `record --status failed --failed-ranges qa/failed-ranges.json --message '实际问题'`；JSON列表为`[{"start": 37, "end": 44, "reason": "源片需要补测/当前腿提前露出"}]`。plan只返回最多12段的短承接摘要，原始完整失败列表保留。更改输入后旧结果失效；首次新输入返回run，失败/损坏返回repair，依赖未完成返回blocked。

`start/record`来自同主机monotonic计时，包含这次尝试的实际外部程序及间隔，不分离GPU、Agent思考或Token成本。跨重启/时钟中断须另起实测尝试；不把历史统计换算为任意设备吞吐。

## 投影、全帧预览与数值证据

`export_projection.py`只读当前保存工程，经depsgraph求值骨端/网格中心/背景锚点和当前camera投影。使用配置确定对象/骨名，不把旧动作输入JSON当成最终工程姿态。输出不是像素可见性/轮廓遮挡的真值；遮挡从实际ID mask或观察提取，灯光另测。具体配置/命令见脚本`--help`和配置校验；diagnostic-lock模式不接生产门禁。

`render_preview.py`实际渲染全部任务帧，默认25%/16采样，同源FPS/比例，不改存盘工程。保存的工程仍需原生100%规格；指定新的job内输出目录，不混修订。输出`preview_manifest.json`，只有COMPLETE、正确帧/PTS、所有实际PNG尺寸/CRC/解码结构和SHA通过才作为证据。

`match_check.py check --job JOB --evidence EVIDENCE --output REPORT`的四份证据：
- observations：`client-white-model-match-observations.v1`，源片实际可见观测；隐藏点为inferred/unknown，不能充当真值。
- candidate：`client-white-model-match-candidate.v1`，当前工程投影和实际预览的可见性/遮挡测量，绑定blend SHA。
- calibration：`client-white-model-match-calibration.v1`，明确当前源SHA、actors、阈值、标定人/依据和motion/handheld窗口。
- preview：`client-white-model-match-preview.v1`，全帧实际PNG/PTS/hash清单。

evidence为`client-white-model-match-evidence.v1`；顶层bindings锁job/source/template/standards/revision/blend，scene_path及四artifact `{path,sha256}`均job内相对。每帧`frame=1..N`、`source_frame=0..N-1`、`pts_seconds=(frame-1)*fps_den/fps_num`。空间声明宽高和`to_source=[a,b,c,d,e,f]`仿射换算；native源像素阈值与低清像素绝不混用。完整字段说明在`match_check.py`模块文档及独立数值测试里。

数值预检覆盖：固定背景fit/holdout重投影、可见关节bbox归一化误差、显式源动作窗口的幅度/峰值差、身份与可见性事件、声明的前后顺序、静态背景去线性趋势后的残差幅度/相位/相关性。残差是屏幕轨迹指标，不是真正完整3D相机分解；box重叠不是轮廓重叠，缺语义顺序观测须补测。

阈值必须完整且有诊断/客户标定状态和依据；诊断状态不意味着甲方认可数值。未测手持/幅度、缺可见帧、缺holdout等均失败；缺证据同时仍保留已测的数值和帧段。`validate-report`会重新读取全部hash、数值与PNG并重算结果，手填passed和旧SHA不放行。

## 仍需独立验证的范围

数值预检不验证灯光匹配、像素轮廓面积、隐藏源肢体真值、完整3D接触/碰撞、人工观看和甲方签收。实际网格接触/穿模继续由blender_check执行；灯光/部分露出和原片快速动作/手持真实性继续逐镜独立审查。最终gate与四文件格式保留；2026-10-07新口径为4K工程/直接1080p视频，关闭光追，64采样。缓存/短摘要减少重复工作，只有代表任务实际测到完整制作时间、返工率和通过率后再判断总体提速。
