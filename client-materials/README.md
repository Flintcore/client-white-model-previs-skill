# 甲方素材入口（v1.1.0）

用户于2026-10-07明确要求公开模型和案例方便团队Agent取用。本目录是可移植索引，不包含任何成员电脑路径。大视频以GitHub Release附件分发，模型/规范/图直接随Skill安装。

## 小原件：克隆/安装后就有

| ID | 资料 | 位置 |
|---|---|---|
| person-template | 人.blend原件（422,550字节） | [person.blend](../skills/client-white-model-previs/assets/client/models/person.blend) |
| character-structure | 10部件结构图 | [character-structure.jpg](../skills/client-white-model-previs/assets/client/images/character-structure.jpg) |
| review-feedback | 六模块整改截图 | [review-feedback.jpg](../skills/client-white-model-previs/assets/client/images/review-feedback.jpg) |
| acceptance-standard | 综合验收PDF，三份同字节原件取S03 | [acceptance-v1.pdf](../skills/client-white-model-previs/assets/client/standards/acceptance-v1.pdf) |
| delivery-format | 四文件交付PDF | [delivery-format.pdf](../skills/client-white-model-previs/assets/client/standards/delivery-format.pdf) |
| sync-frame-standard | 同步帧规范PDF，保留删除线视觉信息 | [sync-frame-standard.pdf](../skills/client-white-model-previs/assets/client/standards/sync-frame-standard.pdf) |
| known-issues | 既有问题DOCX | [known-issues.docx](../skills/client-white-model-previs/assets/client/standards/known-issues.docx) |

原件字节和元数据保留；模型输入不等于可直接交付的生产工程。自动脚本关闭后打开模型，修改另存版本。

## 视频：固定Release，按需下载

| ID | 固定附件 | 字节数 |
|---|---|---:|
| 0927-01 | [当前第1条](https://github.com/Flintcore/client-white-model-previs-skill/releases/download/v1.1.0/client-0927-01.mp4) | 65,439,759 |
| practice-original-01 | [练习原片1](https://github.com/Flintcore/client-white-model-previs-skill/releases/download/v1.1.0/practice-original-01.mp4) | 70,059,899 |
| practice-comparison-01 | [练习对比1](https://github.com/Flintcore/client-white-model-previs-skill/releases/download/v1.1.0/practice-comparison-01.mp4) | 79,326,300 |
| 0927-package | [9.27新完整包](https://github.com/Flintcore/client-white-model-previs-skill/releases/download/v1.1.0/client-0927-new.zip) | 334,956,395 |
| practice-package | [练习完整包](https://github.com/Flintcore/client-white-model-previs-skill/releases/download/v1.1.0/client-practice.zip) | 674,451,768 |

完整12项大小/SHA/来源与角色见[manifest.json](manifest.json)。当前源是0927-01，默认前240帧只用于10秒试片；练习对比是甲方给的质量参考，其HEVC等历史格式不覆盖现行门禁。没有上传内部失败样片或已停用旧打戏源。

快速使用：

```powershell
python -X utf8 tools/client_assets.py list
python -X utf8 tools/client_assets.py fetch --id 0927-01 --output TEAM_ASSET_ROOT
python -X utf8 tools/client_assets.py verify --id 0927-01 --output TEAM_ASSET_ROOT
```

安装后的完整取用/承接指令：[素材指南](../skills/client-white-model-previs/references/client-materials.md)。默认只取所需视频，不下载约1.22GB全部附件。素材可获取、甲方包中名字含“过审”，均不是当前123条规则的新验收结果。
