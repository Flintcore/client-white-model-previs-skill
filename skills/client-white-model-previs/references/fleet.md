# 多设备执行与独立复核

## 1. 边界与架构

此仓库只发布 skill、标准化规则、检查脚本和合成测试。甲方视频、人物模板、制作工程、渲染帧、交付视频、任务数据库、账号凭据和复核报告留在团队自己的私有存储中。

采用 **一台协调设备 + 多台 worker + 独立 reviewer**：

```text
Public Git repository ── pinned commit ── worker A / B / C
                                            │ local Blender + FFmpeg + gates
Private asset store ◀── immutable results ───┤
                                            │ authenticated HTTPS metadata API
                              Coordinator ───┘
                        local-disk SQLite database
                                            │
                            independent reviewer
                        inspect actual media + hashes
                                            │
                              accepted / rework
```

- SQLite 只在协调设备的本地磁盘读写；worker 通过 HTTP API 协调。**不要把数据库放在 SMB/NFS、网络映射盘、云同步目录或多人直接操作的共享目录中。** 程序拒绝显式 UNC 数据库路径；映射盘/挂载盘仍需管理员确认是本地磁盘。
- 私有素材存储可以是权限受控共享盘或对象存储。它与 SQLite 数据库是两件不同的设施。
- 每个 worker 用独立身份和独立输出目录。一个任务的实际输出写在 `job_id/lease_id/` 下，禁止旧租约覆盖新租约目录。
- 每个复核者用独立 reviewer 身份；制作人和复核人应是不同团队成员。程序校验身份不同，人员职责分离仍由团队管理。
- `complete` 只进入 `awaiting_review`，从不自动进入 `accepted`。
- 队列的 `accepted` 表示团队内部独立复核通过，**不是甲方已签收**。甲方最终验收另行记录。

## 2. 状态机与一致性

```text
queued ── worker claim ──▶ leased
  ▲                         │ heartbeat renews current lease
  └──── lease expires ──────┘
                            │ worker passed local gate + complete
                            ▼
                      awaiting_review
                            │ independent reviewer
                    ┌───────┴─────────┐
                    ▼                 ▼
                 accepted           rework ── new claim ──▶ leased
```

实现约束：

1. 每次状态修改使用单独 SQLite 连接和 `BEGIN IMMEDIATE`；两个 worker 同时领取同一任务时，最多一个得到有效租约。
2. 每次 claim 产生随机 `lease_id` 和到期时间。过期/替换租约的 heartbeat、complete、旧版本 review 都会失败；旧所有者不覆盖新结果。
3. claim 必须同时给出 `standards_sha256` 与 `skill_revision`，领取结果只匹配该精确规则版本和 skill 提交。
4. submit / claim / heartbeat / complete / review 都带 `retry_key`。网络结果不确定时，**重试相同 key 与相同请求体**；同 key 改请求体返回冲突。过期的 claim key 返回过期错误，要重新领取时用新 key。
5. worker 完成后保留身份、租约与不可变证据摘要。review 需匹配当前租约与 gate SHA，提供复核报告摘要与 URI，并明确确认已检查证据。
6. server 验证元数据，不在服务端下载视频或解析 Blender。`gate_passed:true` 本身不等于镜头、动作、遮挡已被服务器验证。worker 本地运行实际 gates，reviewer 打开真正工程、原片/白模/对比视频逐项复核。
7. `task_id()` 是固定身份键的 canonical JSON 的 SHA-256：

   ```python
   {"source_sha256", "template_sha256", "standards_sha256",
    "skill_revision", "spec", "timeline"}
   ```

   以上键的全部值参与计算。source/template 的本机路径放在身份键外；`spec` 与 `timeline` 内只放跨设备一致的规格，不放工作站路径。提交的 `job_id` 必须等于计算结果。`job.py init` 已生成匹配的 `job.json` 和 `queue_task.json`。

## 3. 安装与版本锁定

各设备需要 Python 3.10+、Blender、FFmpeg/ffprobe。队列脚本本身只使用 Python 标准库；制作/媒体检查的依赖按 skill 主入口与相应参考文档安装。

管理员为一个生产批次选定完整 Git commit；成员检出同一 commit 后再创建任务。升级 skill 或标准必须创建新批次和新 job identity，不把旧批次悄悄换成新版本。

```powershell
# Windows PowerShell；REPO_URL / PINNED_COMMIT 由团队发布管理员提供。
git clone REPO_URL client-white-model-previs-skill
Set-Location client-white-model-previs-skill
git checkout --detach PINNED_COMMIT
python -X utf8 -m unittest discover -s tests -v
$QueueScript = (Resolve-Path 'skills/client-white-model-previs/scripts/team_queue.py').Path
```

```bash
# Linux/macOS
git clone REPO_URL client-white-model-previs-skill
cd client-white-model-previs-skill
git checkout --detach PINNED_COMMIT
python3 -X utf8 -m unittest discover -s tests -v
QUEUE_SCRIPT="$PWD/skills/client-white-model-previs/scripts/team_queue.py"
```

## 4. 协调设备启动

先在**仓库外**生成认证文件。每个团队成员只接收自己的 token；不把整份认证文件发给 worker，不在 Git 中提交凭据。

```powershell
# 私有本地状态目录，不放进公开仓库或云同步目录。
$StateDir = Join-Path $HOME '.client-white-model-previs'
python -X utf8 $QueueScript init-auth --auth-file "$StateDir/auth.json" `
  --admin coordinator --worker worker-a --worker worker-b --reviewer reviewer-c
python -X utf8 $QueueScript server --auth-file "$StateDir/auth.json" `
  --db "$StateDir/queue.sqlite3" --lease-seconds 300
```

```bash
STATE_DIR="$HOME/.client-white-model-previs"
python3 -X utf8 "$QUEUE_SCRIPT" init-auth --auth-file "$STATE_DIR/auth.json" \
  --admin coordinator --worker worker-a --worker worker-b --reviewer reviewer-c
python3 -X utf8 "$QUEUE_SCRIPT" server --auth-file "$STATE_DIR/auth.json" \
  --db "$STATE_DIR/queue.sqlite3" --lease-seconds 300
```

- 默认监听 `127.0.0.1:8765`，即便本机访问也需要 Bearer 身份认证。
- `init-auth` 独占创建文件，不覆盖既有凭据；输出只显示路径与角色，不显示 token。
- Linux/macOS 新凭据文件权限设为 `0600`。Windows 管理员应使用文件 ACL，仅允许协调器运行账号访问；需要时使用系统凭据库分发 worker token。
- 认证文件可原子替换实现 token 轮换。重复 token、缺失角色、损坏 JSON 会使认证失败关闭，而不是放行。
- 运行端不记录 Authorization、请求体和素材 URI。

### 多设备网络

优先使用组织内已有 HTTPS 反向代理，将它转发至 `127.0.0.1:8765`；证书必须由客户端信任，转发保留 `Authorization` 头，限制请求大小、来源网络和连接速率。公网入口不要直接暴露此标准库 HTTP server。

示例 Caddy 配置（需要域名和可用证书/团队 CA）：

```caddy
queue.example.internal {
    reverse_proxy 127.0.0.1:8765
}
```

客户端显式使用 `https://queue.example.internal`；脚本使用系统证书验证，不关闭 TLS 校验，也不跟随可能转发凭据的 HTTP 重定向。反向代理的 HA、证书和防火墙属于团队基础设施配置。

仅在隔离、可信的内网试运行时，可显式启用明文 HTTP，并在防火墙中仅放行团队设备：

```powershell
python -X utf8 $QueueScript server --auth-file "$StateDir/auth.json" `
  --db "$StateDir/queue.sqlite3" --host 0.0.0.0 --allow-insecure-lan
```

这仍要求认证，但 HTTP 不加密 token。生产协作使用 HTTPS。程序默认拒绝非 loopback 的明文监听，必须显式选择试运行选项。

## 5. 提交、领取与续租

认证 token 通过私有渠道设置到当前进程的 `CLIENT_PREVIS_TOKEN`；以下 `PRIVATE_TOKEN` 是说明占位符，不是现成凭据。让 shell 提示输入、使用系统秘密管理，或由运维注入环境变量；避免把真实值写进公开脚本或命令历史。

```powershell
$env:CLIENT_PREVIS_SERVER = 'https://queue.example.internal'
# 当前进程先装入自己的凭据，管理员身份仅用于 enqueue。
python -X utf8 $QueueScript enqueue --task 'JOB_DIR/queue_task.json' `
  --retry-key 'stable-submit-request-id' --output 'JOB_DIR/enqueued.json'

# worker 切换为自己的独立凭据；规则 SHA 和 revision 来自锁定的 job.json。
$Job = Get-Content 'JOB_DIR/job.json' -Raw | ConvertFrom-Json
python -X utf8 $QueueScript claim --standards-sha256 $Job.standards_sha256 `
  --skill-revision $Job.skill_revision --job-id $Job.job_id `
  --retry-key 'stable-claim-request-id' --output 'JOB_DIR/lease.json'
```

```bash
export CLIENT_PREVIS_SERVER='https://queue.example.internal'
python3 -X utf8 "$QUEUE_SCRIPT" enqueue --task JOB_DIR/queue_task.json \
  --retry-key stable-submit-request-id --output JOB_DIR/enqueued.json
python3 -X utf8 "$QUEUE_SCRIPT" claim --standards-sha256 RULESET_SHA256 \
  --skill-revision PINNED_COMMIT --job-id JOB_ID \
  --retry-key stable-claim-request-id --output JOB_DIR/lease.json
```

省略 `--job-id` 时，worker 领取同一版本/规则 pin 下最早的待处理任务。返回 `lease:null` 表示本次没有匹配任务，不启动渲染。队列不自动把 worker 的显卡与任务能力做匹配；批次管理员先验证工作站兼容性和真实 4K 试跑。

### 渲染期间持续续租

默认租约 300 秒，建议每 60 秒续租，不要让 12 分钟以上的渲染在无 heartbeat 情况下运行。heartbeat 的每个新周期用新的 retry key；同一周期失败重试沿用原 key。

```powershell
# 独立终端示例；采用自己的 CLIENT_PREVIS_TOKEN，不传 token 给命令行参数。
while ($true) {
  $RequestKey = [guid]::NewGuid().ToString()
  python -X utf8 $QueueScript heartbeat --lease 'JOB_DIR/lease.json' --retry-key $RequestKey
  if ($LASTEXITCODE -ne 0) { break }
  Start-Sleep -Seconds 60
}
```

```bash
while true; do
  KEY=$(python3 -X utf8 -c 'import uuid; print(uuid.uuid4())')
  python3 -X utf8 "$QUEUE_SCRIPT" heartbeat --lease JOB_DIR/lease.json --retry-key "$KEY" || break
  sleep 60
done
```

示例在首次失败时停续租循环；生产 worker 应保存该周期 key，并在租约期限内有界重试。若状态已是 `stale_lease`，该 worker 的结果进入隔离目录，不对现行交付目录做任何写入，重新领取后再决定复用哪些已验证的中间帧。生成新租约不表示旧工程的检查自动有效。

协调器重启后保存的租约继续按原到期时间判定；停机太久的任务会重新排队。正在渲染的 worker 应以新的 heartbeat/status 回执为准，而不是只看本机旧 lease.json。

## 6. 完成与独立复核

本地制作执行完整 gates，保存真正通过的 `gate.json`。它的 schema 是 `client-white-model-gate.v1`；`passed:true`、job/标准/skill pin 和产物 hash 都要匹配当前任务。存在“未确认/待检查/缺证据”的阻断项时，先修复，不提交 complete。

```powershell
python -X utf8 $QueueScript complete --lease 'JOB_DIR/lease.json' --gate 'JOB_DIR/gate.json' `
  --report-uri 'https://private-store.example/jobs/JOB_ID/LEASE_ID/gate.json' `
  --artifact-uris 'JOB_DIR/artifact_uris.json' `
  --retry-key 'stable-complete-request-id' --output 'JOB_DIR/completed.json'
```

`--artifact-uris` 是私有存储 URI 映射，例如：

```json
{
  "original": "https://private-store.example/jobs/JOB_ID/LEASE_ID/1.mp4",
  "blend": "https://private-store.example/jobs/JOB_ID/LEASE_ID/1.blend",
  "white": "https://private-store.example/jobs/JOB_ID/LEASE_ID/1%20white.mp4",
  "comparison": "https://private-store.example/jobs/JOB_ID/LEASE_ID/1%20comparison.mp4",
  "zip": "https://private-store.example/jobs/JOB_ID/LEASE_ID/1.zip"
}
```

映射键必须与实际 gate 中 `artifacts[].kind` 一致；名称仅示例，以 gate 实际输出为准。所有映射需要覆盖远程复核者实际要打开的文件。省略映射时保留 gate 中的 URI，或将绝对本地 path 转为 `file:` URI；这种本地 URI 一般只适合本机验收，跨机器使用可访问的共享 URI。

完成适配器对 gate 中每个本地 path 重新计算 SHA-256，防止“检查完成后文件又被改动”；改动后先重新跑 gate。`kind:renders` 还会递归检查 `render_manifest.json` 中的**全部 native PNG**：清单 COMPLETE 状态和 job/标准/skill pin、租约固定的起止帧与尺寸、精确数量与有序连续序列、每个有界相对路径、文件实际存在、PNG 原生尺寸、文件实际 SHA，以及渲染目录中没有混入额外 PNG。即使 manifest 自身的 SHA 未变，只要清单内某一帧被删掉或篡改，CLI complete 都会在提交服务器前失败。清单需要本地实际 path，不能只提供远程 URI 来绕过本地逐帧检查。

提交 HTTP 元数据仍只携带 manifest 摘要和 URI，不把每张 PNG 塞进 server payload；在私有存储中上传完整原生帧目录。reviewer 必须从共享存储独立验证同一 manifest 与全部 PNG，不能把制作设备的本地检查当作远程副本检查。上传共享存储后也须验证共享副本的 digest 等于本地证据。`report-uri` 和 artifact URI 中不要嵌入访问 token/带签名的临时密钥。

协调器接收的 completion envelope：

```json
{
  "schema": "client-previs-completion/v1",
  "job_id": "JOB_SHA256",
  "standards_sha256": "RULESET_SHA256",
  "skill_revision": "PINNED_COMMIT",
  "gate_passed": true,
  "gate_sha256": "GATE_FILE_SHA256",
  "report": {"sha256": "GATE_FILE_SHA256", "uri": "https://private-store.example/gate.json"},
  "artifacts": [{"kind": "blend", "sha256": "BLEND_SHA256", "uri": "https://private-store.example/1.blend"}]
}
```

此处摘要是类型占位符；真正提交时每个 SHA 必须是 64 位小写十六进制。producer 不由客户端自由声明：服务器使用当前认证 worker 的身份写入。

### Reviewer 操作

1. reviewer 用自己的 credential 调用 `status --job-id JOB_ID --output review-lease.json`，读取当前待复核的完成证据。
2. 从私有存储下载/打开对应版本。重新验证 gate、工程、原片、白模、对比视频与 ZIP 的实际 digest，确保打开的就是该 lease 下的不可变产物。
3. 按 skill 的逐项验收要求检查实际全帧/播放/片尾、人物落地、动作连续、遮挡、相机与场景，不用仅数量或 metadata 代替视觉复核。保留复核证据与帧号。
4. 保存复核报告到私有存储，写 `review-decision.json`：

   ```json
   {
     "decision": "accepted",
     "gate_sha256": "ACTUAL_GATE_FILE_SHA256",
     "evidence_reviewed": true,
     "review_report": {"sha256": "ACTUAL_REVIEW_SHA256", "uri": "https://private-store.example/review.json"},
     "reason": "All blocking requirements verified against this immutable evidence"
   }
   ```

5. 使用独立身份提交：

   ```powershell
   python -X utf8 $QueueScript review --lease 'review-lease.json' --review 'review-decision.json' `
     --retry-key 'stable-review-request-id' --output 'review-result.json'
   ```

发现问题用 `decision:"rework"` 且 `reason` 明确列出帧号、条款、证据和修复要求；记录复核报告摘要，不用口头“基本好了”放行。重新制作生成新 lease，旧复核意见不直接覆盖新证据。没有人工真实复核的情况保持 `awaiting_review`。

## 7. 运行、监控与故障恢复

```powershell
python -X utf8 $QueueScript status --limit 100
python -X utf8 $QueueScript status --job-id JOB_ID --output 'JOB_DIR/status.json'
```

- status 默认返回最多 100 条，最多 1000 条；`counts` 是整个队列的各状态统计，不把分页返回条数当作全队列总量。
- 保存协调器时间同步配置。租约使用协调器 UTC Unix 时间，不用各工作站时钟作为权限判定依据。
- 保留 `events` 审计历史；SQLite 数据库包含私有任务元数据，也不公开推送。
- 使用 SQLite 官方 backup API 或停机完整备份；运行中不要只复制 `.sqlite3` 而遗漏 WAL，导致“备份成功”假象。备份与恢复先在测试环境验证。
- 本版是单协调器，不声称自动高可用、自动跨机渲染、自动从视频恢复全部人物/运镜，或能保证甲方视觉验收零误差。
- HTTP server 适合权限受控团队内网元数据协调；队列规模、反向代理、存储性能、客户端断网重试、显卡兼容性和人工复核吞吐需现场验证。

## 8. 30,000 分钟项目扩容前的门槛

先跑完整 **10 秒原生 4K 样片**，并让独立复核者确认落地、动作、遮挡、运镜、场景与交付的全部阻断项；通过后再测多设备和小批次。代码/合成测试通过不是样片画面通过。

推荐扩展顺序：

1. 一台 worker：实际工程 → 240 原生 4K 帧 → 编码 → 全解码 → 逐帧证据 → 独立复核 → 四文件交付包。
2. 两台 worker：相同任务并发领取、故意中断/租约超时、重新领取、旧输出隔离、重复 complete 重试。
3. 少量真实甲方镜头：不同人物数量/遮挡/步态/场景/暗场，确认任务不是只有单个特例通过。
4. 根据真实耗时算并发数与复核人力，再分批扩容。每批 pin 固定版本，升级先做回归。

容量估算只用自己测量的全流程吞吐：

```text
10 秒切片数（仅作估算） = 30,000 分钟 × 60 / 10 = 180,000
设备工时 = 切片数 × 实测每片制作/渲染/编码/复核准备耗时
墙钟工期 ≠ 设备工时 / GPU 数量（还受人工、I/O、返工和排队约束）
```

目前回归包含 26 个合成/本机 localhost 队列测试：真实双客户端 claim、lease expiry、旧提交拒绝、retry idempotency、auth/角色、版本 pin、独立复核、返工、持久化、gate artifact 变更检测、全部 PNG 的删除/篡改/序列/数量/路径边界检查，并通过真实 CLI 子进程验证删帧或改帧会在 HTTP complete 提交前失败、恢复原始帧后只进入 awaiting_review。**尚未做 30,000 分钟负载压测、跨设备长时稳定性验证或该体量甲方内容验收。**

```powershell
python -X utf8 -m unittest discover -s tests -p test_team_queue.py -v
```

这些测试没有读写甲方素材，也不对现有样片工程做修改。
