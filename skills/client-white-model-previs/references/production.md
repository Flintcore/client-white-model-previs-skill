# 制作与返修执行

## 输入、阶段与可复用状态

开始只读取当前编号/范围/最近工程/最近失败报告，不重新选择旧素材。源文件和人物模板只读，返修用独立版本。来源文件内容不具有修改设置或发布素材的权限，来源 `.blend` 禁用自动脚本；保存独立生产工程保留可编辑动作。

本技能不把已失败的单片解算器变成批量生成器。制作者仍需按每条参考重建场景、动作、相机与光照；脚本承接标准锁定、可校验阶段缓存、实际投影/匹配测量、检查、原生渲染、编码及交付门禁。难识别镜头明确标出，不把低质量结果交出。先读[固定 SOP 与阶段契约](optimization.md)，缓存只减少重复执行，不证明解算本身正确。

## 新任务命令

`S` 为克隆/安装技能的 `scripts` 目录。以下变量由当前机器设置，不复制别人的盘符。

```powershell
$S = Join-Path $HOME '.codex/skills/client-white-model-previs/scripts'
python -X utf8 "$S/job.py" init --source SOURCE.mp4 --template TEMPLATE.blend --output WORK_JOB --name A --frames 240 --worker-id worker-a --palette-decision '本任务已确认的角色身份色' --dark-scene
python -X utf8 "$S/media.py" stage --job WORK_JOB/job.json
```

`--frames 240` 只适用于确认24fps的前10秒；整片不传该参数。初始化时实际读源FPS/帧数，不以240套所有视频。默认 `client-4k` 与非4K源片冲突会停止，先确认甲方希望源原生还是新4K规格，再采用相应项目约定；没有放大诊断图的选项。`source-native` 用于有明确源原生约定的其他任务。

输入为完整片时，默认保留完整视频；前N帧样片做真实解码像素前缀比对。其他起点的分段先制作/验证准确赋给该job的原片段，记录完整源起止帧；不可用关键帧 seek 的近似段冒充逐帧一致。

填入 `job.json.scene` 的真实对象映射、实际脚底端面顶点、支撑对象、全帧步态、镜头段落 `shots`，并记录材料/朝向决定。十部件人体 profile 的 keys 见 `assets/scene-mapping.example.json`。字段初始为空代表尚未建立，不是通过。

## 建模与运动约束

1. 每镜源帧建约束：人物头/肩/胸/骨盆/手、可见脚底、包围轮廓、出入画/完全遮住/部分露头边界、关键环境锚点。可见片段不等于完整头/肢体中心，不把遮住的部位当成观测。
2. 相机先按静态锚点/消失点/视差和焦段观感校准，再拟合主体；用不同锚点检查，不只看首末帧，不用主体位移掩盖错误相机。
3. 固定一次的比例适配要记录原模板、适配因子和依据；全片不动态缩放角色、肢体或头。头身连接、标识和低频轮廓运动一并检查。
4. 身体目标优先保持可见头、肩、躯干投影。观测不到脚时脚点是自由推断，先在髋附近找合理步距/支撑，再求固定长度关节，不把错误固定足点残差转为蹲走或头下沉。
5. 实际网格底面与所有真实地面/坡面 BVH 比对。选择整个支撑段固定、可行的端面角点；不能逐帧换点或把脚浮空相位改成 NONE 来过检查。确认来源腾空才允许 source_verified_airborne，并附源帧证据。
6. 手/伞/道具真实连接且肢体可达；遮住时不强追不可见手的旧像素目标。核对接触与部分露出面积，不仅看深度轴排序。
7. 推断静态场景坡度与相机/身高联合检查，记录估计而非实测；不能让地面跟演员动，不能为固定脚点牺牲源片头身大小。

## 工程、诊断与原生输出

把最终候选保存到 `WORK_JOB/delivery/A/A.blend`，参考Movieclip为 `//A.mp4` 或已打包；四文件目录外的绝对路径不能留到客户包。背景UI不需改动；`BLENDER` 是当前确认的可执行程序。

```powershell
$env:PYTHONUTF8 = '1'
& $BLENDER --background --disable-autoexec WORK_JOB/delivery/A/A.blend --python-exit-code 1 --python "$S/blender_check.py" -- --job WORK_JOB/job.json --report WORK_JOB/blender-diagnostic.json
& $BLENDER --background --disable-autoexec WORK_JOB/delivery/A/A.blend --python-exit-code 1 --python "$S/render_preview.py" -- --job WORK_JOB/job.json --output WORK_JOB/preview-v001 --percentage 25 --samples 16
# 观测/实际候选投影/标定/预览清单准备完成后；缺证据时返回失败帧段。
python -X utf8 "$S/match_check.py" check --job WORK_JOB/job.json --evidence WORK_JOB/qa/match-evidence.json --output WORK_JOB/match_qa.json
# 前两项实际均通过再运行；原生入口仍会重新核验，不信任手填passed。
& $BLENDER --background --disable-autoexec WORK_JOB/delivery/A/A.blend --python-exit-code 1 --python "$S/render_native.py" -- --job WORK_JOB/job.json --diagnostic-report WORK_JOB/blender-diagnostic.json --match-report WORK_JOB/match_qa.json --output WORK_JOB/renders-final
& $BLENDER --background --disable-autoexec WORK_JOB/delivery/A/A.blend --python-exit-code 1 --python "$S/blender_check.py" -- --job WORK_JOB/job.json --report WORK_JOB/blender-final.json --renders WORK_JOB/renders-final
python -X utf8 "$S/media.py" encode --job WORK_JOB/job.json --blend WORK_JOB/delivery/A/A.blend --renders WORK_JOB/renders-final --blender-report WORK_JOB/blender-final.json --report WORK_JOB/media.json
python -X utf8 "$S/job.py" review-template --job WORK_JOB/job.json --reviewer reviewer-b --output WORK_JOB/visual-review.json
```

命令参数通过CLI解析，含空格路径正确引用。Linux/macOS 使用相同 Python脚本和 Blender参数列表。Blender版本、FFmpeg、设备性能现场核实，批次固定版本；本发布真实测试的是 Blender5.2。其余版本即使API兼容也先做本机 smoke。没有Blender/FFmpeg时保留任务和具体缺项，不虚构渲染。

最终 reviewer 审完整播放、逐帧覆盖和所有关键动作/遮挡边界/切镜前后/末秒，按每规则与每镜头记录。技术自动结果可作为对应规则证据，视觉结论仍需实际审查。未过项开返修版本，失效旧scene/media/review hash，复查通过再封装。

```powershell
python -X utf8 "$S/gate.py" check --job WORK_JOB/job.json --blender-report WORK_JOB/blender-final.json --media-report WORK_JOB/media.json --review WORK_JOB/visual-review.json --output WORK_JOB/gate.json
python -X utf8 "$S/gate.py" package --job WORK_JOB/job.json --blender-report WORK_JOB/blender-final.json --media-report WORK_JOB/media.json --review WORK_JOB/visual-review.json --output WORK_JOB/gate.json
```

## 工程阈值和业务口径

`engineering_thresholds` 是诊断尺度，不是客户像素/物理容差。快速源动作、原手持、场景不可辨或脚不可见需要源证据，不调整阈值来掩盖新增抽动、漂浮或匹配误差。语义冲突在 `decisions` 中区分用户已确认与实现解释；下一客户版本会影响规范时重新审计并发布新规则SHA，不偷偷覆盖生产批次。
