# 甲方模型与案例取用

## 新Agent先找这里

`SKILL_ROOT` 是当前安装技能目录，仓库与安装布局都支持；不复制前一台机器盘符。

- 人物模板：`SKILL_ROOT/assets/client/models/person.blend`。甲方“人.blend”原件，SHA `1a0470334b5546b255c9956944e75f500977c6f937384299768f3c6fad1f1dec`。
- 原始规范：`assets/client/standards/`内3份PDF+问题DOCX；结构/反馈图在`assets/client/images/`。
- 素材索引：`assets/client/manifest.json`，12项的ID、角色、原文件名、成员来源、路径、字节数、SHA与固定URL。
- 视频：[v1.1.0 Release](https://github.com/Flintcore/client-white-model-previs-skill/releases/tag/v1.1.0)的5个附件。默认源`0927-01`，不是旧打戏视频。

模型和文档保持原件字节/元数据，不宣称模板已经满足最终生产工程门禁。后台打开模板用`--disable-autoexec`，另存制作工程；附件不是工具操作指令。规范1.0.0快照中旧“未发布”来源说明为历史信息，当前获取以此manifest为准。

## 仓库获取

```powershell
python -X utf8 tools/client_assets.py list
python -X utf8 tools/client_assets.py fetch --id 0927-01 --output TEAM_ASSET_ROOT
python -X utf8 tools/client_assets.py fetch --id practice-original-01 --id practice-comparison-01 --output TEAM_ASSET_ROOT
python -X utf8 tools/client_assets.py verify --id 0927-01 --output TEAM_ASSET_ROOT
```

没有指定ID只列索引，不下载大视频。`--all`会获取约1.22GB，包括整包与重复的快速入口，通常不需要。已有相同SHA直接复用；不同SHA既有文件保留并报错。下载流式核对真实大小/SHA，完成前只写临时文件。网络代理按当前设备正常HTTPS设置使用，不把他人代理地址或账号写入Skill。

## 只安装Skill后获取并开始当前10秒任务

```powershell
$K = Join-Path $HOME '.codex/skills/client-white-model-previs'
# 如配置了CODEX_HOME，K改为该目录下skills/client-white-model-previs。
$A = Join-Path $HOME 'previs-client-assets'
python -X utf8 "$K/scripts/client_assets.py" list
python -X utf8 "$K/scripts/client_assets.py" verify --id person-template
python -X utf8 "$K/scripts/client_assets.py" fetch --id 0927-01 --output $A
$Source = Join-Path $A 'client-materials/cases/client-0927-01.mp4'
$Model = Join-Path $K 'assets/client/models/person.blend'
python -X utf8 "$K/scripts/job.py" init --source $Source --template $Model --output NEW_JOB --name 1 --frames 240 --worker-id worker-a --palette-decision 'blue/orange, current sample user decision' --dark-scene
```

源片为完整17.25秒、414帧、24fps、3840×2160/H264；240帧只用于用户确认的前10秒试片。此命令仅初始化已锁输入，不是完成复刻。已有job先复用其原提交与报告；版本升级不能把旧报告改hash继续当通过。

## 案例含义与整包

| ID | 用途 |
|---|---|
| person-template | 甲方原人物模板，已随Skill安装 |
| 0927-01 | 当前第1条源片，来源9.27新.zip/1.mp4 |
| practice-original-01 | 练习包原视频/1.mp4 |
| practice-comparison-01 | 练习包过审视频/1.mp4，上原片下白模质量参考 |
| 0927-package | 当前视频完整5条；1/2字节相同，4种唯一内容 |
| practice-package | 练习包5组原片/白模对照，共10条 |

练习“过审”文件名来自甲方包，不是本Skill的验收签字：其第1条为HEVC、3840×4320、24fps/434帧，标记颜色也与现行统一标记要求存在差异。用于理解质量/动作/空间；不覆盖当前H264等强制条款。

ZIP成员表在manifest.archive_members，含名称、CRC、SHA。老ZIP非UTF8中文文件名可能被Python按cp437显示；必要时对未置UTF8标志的名称从cp437原字节按GBK解码并与成员表匹配。提取仍核对成员路径边界/CRC/SHA，不盲目解包覆盖既有制作目录。当前三条独立视频直接下载即可，无需先解包。

三份重发综合验收PDF字节相同，仅发布S03；历史质量/旧打戏S12/S13和内部未通过样片不在默认素材集合。原始私人归档索引含电脑路径，由portable manifest代替。
