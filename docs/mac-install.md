# Mac 安装与本机验证

固定团队版本v1.2.1，生产任务还会锁定完整Git SHA。当前客户口径：4K存盘工程、1080p视频、64采样、关闭光追。安装、输入校验和开始SOP不等于自动完成视频或客户验收。

## 1. 已有环境检查

```bash
set -euo pipefail
export PYTHONUTF8=1
export BLENDER_EXE="/Applications/Blender.app/Contents/MacOS/Blender"
python3 -c 'import sys; assert sys.version_info >= (3,10), "Select Python 3.10 or newer"; print(sys.version)'
git --version
ffmpeg -version
ffprobe -version
test -x "$BLENDER_EXE"
"$BLENDER_EXE" --background --disable-autoexec --version
```

Blender装在其他目录时只修改`BLENDER_EXE`。本发布Blender真实测试基线5.2；Mac现有版本和渲染设备须在该机验证，不要求CUDA，不修改全局GPU配置。

## 2. 克隆固定版本、安装或有备份更新

```bash
ROOT="$HOME/Projects/client-white-model-previs-v1.2.1"
test ! -e "$ROOT"
mkdir -p "$(dirname "$ROOT")"
git clone --branch v1.2.1 --depth 1 https://github.com/Flintcore/client-white-model-previs-skill.git "$ROOT"
cd "$ROOT"
PIN="$(git rev-parse 'v1.2.1^{commit}')"
test "$(git rev-parse HEAD)" = "$PIN"
SKILL="${CODEX_HOME:-$HOME/.codex}/skills/client-white-model-previs"
if [ -e "$SKILL" ]; then
  python3 -X utf8 tools/install_skill.py --revision "$PIN" --update
else
  python3 -X utf8 tools/install_skill.py --revision "$PIN"
fi
python3 -X utf8 tools/install_skill.py --verify
# 必须看到verified:true，revision与PIN相同。
python3 -X utf8 -m unittest discover -s tests -p test_render_contract.py -v
python3 -X utf8 -m unittest discover -s tests -p test_blender_check.py -v
python3 -X utf8 -m unittest discover -s tests -p test_render_pipeline.py -v
```

后两组需要真实Blender/FFmpeg；日志skip不是该机制作环境已验证。更新自动保留旧Skill到skills目录之外；安装目录不是Git checkout，不在它里面git pull。已跑过的job继续用其原pin。安装成功后在新一轮Codex对话显式调用`$client-white-model-previs`。

## 3. 完整视频进入SOP

把视频拷到Mac本地，设置**真实绝对路径**，不要把Windows路径当成可读取文件。

```bash
SOURCE="$HOME/Movies/white-model-inputs/3.mp4"
JOB="$HOME/Movies/white-model-work/3-full-v1.2.1"
test -f "$SOURCE"
test ! -e "$JOB"
S="$SKILL/scripts"
python3 -X utf8 "$S/job.py" init \
  --source "$SOURCE" --template "$SKILL/assets/client/models/person.blend" \
  --output "$JOB" --name 3 --profile client-4k-project-1080p \
  --worker-id "$(scutil --get ComputerName)" --palette-decision '保留蓝/橙角色区分'
python3 -X utf8 "$S/job.py" validate --job "$JOB/job.json"
python3 -X utf8 "$S/media.py" stage --job "$JOB/job.json"
python3 -X utf8 "$S/pipeline.py" init --job "$JOB/job.json"
python3 -X utf8 "$S/pipeline.py" plan --job "$JOB/job.json"
```

整片省略`--frames`。当前3.mp4应实读450帧/24fps/18.75秒；其他源按实际探测。初始化只锁输入、版本和规格，场景映射/算法初始为空并继续显示待制作。

给Mac上的Codex：

```text
使用 $client-white-model-previs，读取本机刚生成的job.json并按pipeline next_action继续完整视频制作。
4K可编辑工程，1080p视频，64采样，关闭光追；甲方十部件人物，蓝/橙身份色。
源片观测与推断分开，固定比例；优先校准相机/静态锚点、真实动作、足底支撑和遮挡。
不生成随机手持或默认循环步态。实际物理和全帧匹配预检通过后渲染，保留源音轨。
交付原片、白模视频、上下对比、.blend和同名四文件ZIP；缺证据继续返修，不把安装测试说成客户通过。
```

渲染和编码入口见Skill的`references/production.md`；先通过观测、相机、动作、接触、场景、灯光和全帧预览，不把`init`误认为动画自动生成器。
