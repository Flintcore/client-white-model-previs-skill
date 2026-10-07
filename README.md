# 甲方白模视频复刻标准执行 Skill

一个面向团队多设备的 Codex skill：把甲方当前有效条款变成**完整规则目录、实际检查与不达标不交付的门禁**，而不是仅提供提示词。

## 已覆盖

- 133条溯源记录：123条有效阻断项、5条建议、5条失效/图例记录；每条都有来源、范围、验证方法和脚本/视觉执行入口。
- 统一L3、球头/分段几何角色、完整肢体、朝向标识、身份色稳定；真实落地/支撑/步态、空间/遮挡、相机/节奏、基础光照。
- 实际Blender全帧检查、按锁定交付规格真实渲染PNG与render manifest、64采样/当前规格关闭光追、H.264/FPS/尺寸、原片声音、片尾和四文件ZIP。
- 中央任务队列：版本锁定、确定性job_id、原子领单、租约/心跳、到期重领、幂等提交、独立reviewer与返工。
- 固定阶段缓存和实测计时；实际工程投影、全帧低清预览、源片数值匹配预检与4K入口双检查。灯光变更只失效下游，缺观测/旧证据/损坏缓存保持返修。
- 标准、技术证据、独立视觉审查与客户签收分层；旧删除线和当前样片个案不被错误泛化。

**100%指有效要求覆盖和严格放行条件，不表示任意视频会自动100%复刻或甲方已验收。**相机、轮廓、步态、遮挡、节奏和光影必须以实际源片与成片独立复核。失败样片的解算轨迹没有作为批量生产算法发布。

## 每台设备安装同一版本

依赖：Python3.10+、Git；制作机器还需项目选定的Blender和FFmpeg/FFprobe。队列/门禁/安装脚本使用标准库，不依赖当前机器的私人软件路径。真实Blender检查测试基线为5.2，其他版本先跑本机验证。

```powershell
git clone --branch v1.2.0 --depth 1 https://github.com/Flintcore/client-white-model-previs-skill.git
cd client-white-model-previs-skill
python -X utf8 tools/install_skill.py
python -X utf8 tools/install_skill.py --verify
```

Linux/macOS 使用同样命令。默认安装到 `$CODEX_HOME/skills/client-white-model-previs` 或 `~/.codex/skills/client-white-model-previs`。更新先切到明确新tag/commit，再用 `python -X utf8 tools/install_skill.py --update`；旧安装自动移至skills外备份，所有新文件hash回读。安装目录不是Git checkout，不对它执行git pull。

仓库通过 `.gitattributes` 保留提交字节，避免跨系统换行转换改变规范SHA；所有命令显式启用UTF-8。安装和运行均核对真实提交/完整文件hash，不接受随意填入的版本字符串。

新对话调用：

```text
使用 $client-white-model-previs 按锁定甲方规范处理这条任务。
先读取当前job/源片/模板/最近报告，只修未过项；完整证据通过后再四文件交付。
```

Mac完整安装、已有环境测试和SOP起步命令见[Mac安装](docs/mac-install.md)。当前生产profile为`client-4k-project-1080p`，原视频保留原件，工程4K、白模1920×1080、上下对比1920×2160，64采样，光追关闭。

## 操作入口

- [技能主入口](skills/client-white-model-previs/SKILL.md)
- [完整标准](skills/client-white-model-previs/references/standards.md) / [机器规则目录](skills/client-white-model-previs/references/standards.json)
- [来源、删除线与冲突](skills/client-white-model-previs/references/source-decisions.md)
- [制作、返修与原生渲染命令](skills/client-white-model-previs/references/production.md)
- [固定 SOP、缓存和匹配预检](skills/client-white-model-previs/references/optimization.md)
- [证据契约与严格门禁](skills/client-white-model-previs/references/evidence-contract.md)
- [多设备协调器与队列](skills/client-white-model-previs/references/fleet.md)
- [防止旧问题复发](skills/client-white-model-previs/references/known-failures.md)
- [甲方模型、原始规范与案例取用入口](client-materials/README.md) / [素材manifest](client-materials/manifest.json)

2026-10-07用户明确要求把甲方模型和案例公开方便团队Agent取用：v1.1.0直接包含7份小原件（人物模板、结构图、反馈图、3份规范PDF与问题DOCX），视频整包和3条快速起步视频放在同版本GitHub Release。全部12项有来源/角色/尺寸/SHA；原件字节保留。原文档元数据也随原件保留。旧失败样片、账号路径索引、凭据和实际生产审查报告仍不发布。

```powershell
# 列出素材，不会自动下载全部视频。
python -X utf8 tools/client_assets.py list
# 当前第1条完整源片；模板已随Skill安装，另需样例时显式选择。
python -X utf8 tools/client_assets.py fetch --id 0927-01 --output TEAM_ASSET_ROOT
python -X utf8 tools/client_assets.py fetch --id practice-original-01 --id practice-comparison-01 --output TEAM_ASSET_ROOT
```

安装后相同入口在 `scripts/client_assets.py`，模型为 `assets/client/models/person.blend`（甲方人.blend原件）。无需知道任何制作成员的盘符；具体承接命令见[素材指南](skills/client-white-model-previs/references/client-materials.md)。

## 30,000分钟规模

30,000分钟=500小时素材；若全部24fps，约43,200,000原始帧。不同源FPS按实际job统计。渲染吞吐、4K临时帧存储、传输、返工率与独立复核时间要用代表任务实测，不套一个单帧速度估全部设备。

先通过一个10秒pilot，再做两设备真实共享素材/版本/领单/重领/复核联测，再小批次扩容。协调器只存元数据，本地SQLite不放SMB；每个租约写独立工作目录。公开Release只分发明确列出的起步参考，不替代大规模生产资产库或渲染农场。本发布没有跑完整30,000分钟负载，也没有把现有样片声明为达标。

## 测试

```powershell
python -X utf8 -m unittest discover -s tests -v
$env:BLENDER_EXE = '本机已确认的Blender可执行路径'
python -X utf8 -m unittest discover -s tests -p test_blender_check.py -v
```

Blender缺席时其测试明确skip；队列单测是本机真实HTTP/SQLite并发和失败情形，不等于跨设备压力验证。脚本通过与客户成片验收仍分开。仓库CI执行可移植测试，生产GPU验证在制作节点上执行。

2026-10-06本地完整实跑 **100/100通过，无skip**：35项真实Blender、26项HTTP/SQLite队列、3项实际媒体时序、36项任务/门禁/安装契约测试。测试边界及原生4K隔离链路见[发布验证记录](docs/validation.md)。

2026-10-07 v1.2.0本地最终回归 **300项：299通过、1个Windows符号链接环境跳过、0失败**；38项Blender检查及26项真实预览/投影/原生渲染链路全部执行。含4K工程不改存盘、1080p实际渲染、源音频包/PTS保留、队列导出规格复验的正负例，见[规格修订验证](docs/render-profile-validation.md)。Mac本机GPU与客户视觉验收另行实测。

推荐团队固定版本为 **v1.2.0**。新增固定SOP、匹配预检和2026-10-07甲方规格变更：**4K可编辑工程、1080p视频、64采样、关闭光追**。标准版本1.1.0保留133条来源映射与123条检查，仅CW071/CW074按直接用户说明修订，原文和旧1.0.0在Git历史保留；新批次锁定新提交，不修改旧job的pin/通过位。

v1.2.0包含工作分支 `codex/previs-pipeline-optimization` 的固定SOP/提效模块及修订渲染profile。新原生渲染CLI增加必填 `--match-report`，先物理检查与全帧匹配预检，旧job/报告继续使用其原固定提交。验证范围见[提效模块验证](docs/optimization-validation.md)，不是任意客户视频自动复刻算法。
