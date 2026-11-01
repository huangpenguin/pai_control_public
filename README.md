# PAI AI inspection control

自然语言 → Vertex Gemini → 严格校验的任务计划 → ROS 2/Nav2 → Gazebo TurtleBot3 Waffle Pi。默认仅仿真；P1 已取得“真实 Gemini 生成计划 → 留存后真实 Gazebo 执行”的两阶段通过证据。当前 Pro 在固定24题语义验收中24/24通过，产品同进程在线 P2 完整任务为1/3：第三次由真实新计划驱动B→A→base，三站均实际到站停稳；此前速度命令过期保护停与返航进展失败继续保留。RotationShim包裹原DWB后本轮通过，不代表长期稳定性或此前所有根因已解决。真实在线 P3 的未知拒绝与隔离不可达处理两项通过，视觉三项正式场景均通过（3/3任务、5/5实际新鲜图像），包含同站换色与灰板unknown后返航；仅固定仿真条件少量样本，不保证任意灯具与环境。进展与证据见 [docs/STATUS.md](docs/STATUS.md)。

基于 porizou1 的 [Qiita 主样例](https://qiita.com/porizou1/items/a085d20e936946330540) 的“自然语言请求、模型、ROS 2、Gazebo TurtleBot3”链路设计。主样例生成并运行 Python 节点；本项目重新编写固定执行器，由模型只产生四种声明式操作：`navigate_to(place_id)`、`inspect_indicator(place_id)`、`return_to_base()`、`stop()`。代码、shell、函数调用不会进入执行接口。增量限于命名站点与顺序、Nav2 真实反馈、到站稳定后的指示灯识别。

## 环境与入口

容器环境：Ubuntu 24.04、ROS 2 Jazzy、Gazebo Harmonic，headless CPU 仿真，无公网端口、无 GPU；Nav2 使用人工配置的地图与站点。宿主需要 Docker Engine 和 Compose v2。ROS 构建入口固定在 `/opt/pai`，ROS 相关源码提交固定在 `dependencies.repos`，容器基镜像 digest 固定于 Dockerfile，安装后的 apt 清单在容器 `/opt/pai/debian-packages.lock`。相机默认 5 Hz 仿真更新率、640×480，激光 10 Hz；可用 `PAI_CAMERA_UPDATE_RATE` 调整相机负载。CPU 仿真不保证实时速度，停止新鲜度判据使用墙钟时间。

```bash
scripts/build.sh
scripts/start.sh                 # 默认选择真实 Vertex；无 ADC 时只启动静止诊断
scripts/diagnose.sh 15
# 诊断通过后，显式解锁并发送P1单点任务（真实Vertex需先认证）
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli resume
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli send '去设备 A'
# 顺序任务同一接口，真实Vertex需先认证；显式stub仅测试
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli send '先去 B，再去 A，最后回起点'
# 独立安全停机，不依赖LLM
scripts/ros.sh ros2 run pai_inspection_ros instruction_cli stop
scripts/stop.sh
```

需要只验证无收费模型的仿真管道时显式运行 `scripts/start.sh stub`。其输出必须标注 `stub`/`stub-test-double`，不计入真实 AI 验收。ROS 执行器默认最多 8 步，接收命名站点导航、返航或停止；历史 P2/P3 验证镜像不含视觉；当前 P4 已接入 `inspect_indicator`，49项镜像回归与独立真实图像校准完成，三项正式场景3/3任务、5/5图像通过。通过 ROS 接口返回的结果以 Nav2 状态为依据，不用等待固定时长声明成功。已知站点不可达时最多重试一次，再继续其余步骤；安全异常立即中止。普通停止后需要显式 `resume` 并提交新任务；若目标或取消终态未确认，系统进入 `navigation_restart_required` 故障锁，普通 `stop`/`resume` 都不能清除，必须运行 `scripts/stop.sh` 后 `scripts/start.sh` 重启整个仿真、Nav2 与 guard 链。

纯 Python 核心无需 ROS。`google-genai==2.29.0` 与传递依赖在 `requirements.lock` 锁定：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pytest tests/core -q
.venv/bin/pai-plan --mode stub --text '去设备 A'
.venv/bin/pai-evaluate --mode stub --output docs/evidence/stub-semantic.json
```

`pai-plan` 和 `pai-evaluate` 只规划/评测，不会移动机器人。退出码：规划接受 0、拒绝 2、认证/预算/无效响应等错误 1。

## 真实 Vertex 与认证

只使用用户现有、有权访问的 Google Cloud 项目；本项目不创建云项目、不开启收费资源、不变更云预算。SDK 明确使用 `vertexai=True` 与 Application Default Credentials（ADC）。导入模块不读取凭据，默认后端是 Vertex，认证失败不回退到 stub。用户本人交互认证：

```bash
export GOOGLE_CLOUD_PROJECT='现有项目ID'
scripts/auth_adc.sh
export PAI_ADC_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/pai_ai_control/gcloud/application_default_credentials.json"
export GOOGLE_CLOUD_LOCATION=global
export PAI_VERTEX_MODEL=gemini-3.1-pro-preview
scripts/start.sh vertex
```

ADC 默认保存于用户配置目录 `~/.config/pai_ai_control/gcloud/`（可用 `PAI_ADC_DIR` 覆盖），以认证脚本实际打印的路径为准；容器只在运行时只读挂载该凭据文件。项目内 `.local/runtime.env` 只保留未跟踪的项目/路径配置。宿主纯 Python 调用可设置 `GOOGLE_APPLICATION_CREDENTIALS="$PAI_ADC_PATH"`，然后运行 `pai-plan --text '去设备 A'`。真实评测采用先 4 条 sentinel 再 20 条的冻结用例；结果与人工改进方法见 [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md)。此命令会真实收费，使用下述项目累计额度；保留每条失败和原始响应，不能把 stub 结果改名当真实结果。容器中输出到可写的 `/opt/pai/logs/vertex-semantic.json`，宿主可将该真实文件复制到 `docs/evidence/vertex-semantic.json` 保留。

当前 ADC 已成功刷新。用户直接授权的正确配置请求已由 `gemini-2.5-flash` 返回 `navigate_to(station_a)` 并通过语义校验，实际 token 为输入 453/输出 42/合计 495。同一份留存计划已由生产执行器在真实 Gazebo 中导航到设备 A：第二轮 Action/Nav2 均 SUCCEEDED、位置误差 0.19446 m/朝向误差 0.06103 rad，连续停稳。quota project 仍未设置，Service Usage 使用/读取权限仍缺少；这次事实证明该缺项没有阻止此既有配置的推理，但 quota 设置与 API 状态读取仍受限。管理员补齐 `roles/serviceusage.serviceUsageConsumer` 后可运行 `scripts/auth_adc.sh --quota-only`，无需重复 OAuth 或额外 Vertex 角色。此两阶段历史检查点累计 2 次尝试、1 次未知；US$0.166604 台账金额包含首失败保守预留 US$0.166363 和本次按真实 token 估算 US$0.000241，均不等同已确认云账单。完整诊断及真实原始计划见 [docs/ADC_SETUP.md](docs/ADC_SETUP.md)。

本次真实请求成功 **1/2**：首轮临时诊断配置错误失败保留。随后**同一份真实模型输出执行成功 1/2**：首执行外部测试辅助漏设仿真时钟，导致到站新鲜度检查失败；仅修正 test-only 时钟配置后，第二执行通过，生产导航/安全/到站判据没有修改。每轮只发一个 ROS Action、使用独立消费凭据；两轮执行没有新增模型请求，台账字节完全相同。该历史检查点是“在线生成、留存、再真实执行”的两阶段证据，不能写成在线同进程验收，也不能与此前 stub 导航统计混合。后续产品在线任务/真实模型比较单独见STATUS与评测说明。原始模型 JSON/用量见 [真实请求](docs/evidence/vertex-second-authorized-probe.json)，两轮事件及失败保留见 [执行证据](docs/evidence/recorded-real-plan-execution.json)，来源哈希/台账见 [完整性核验](docs/evidence/recorded-plan-integrity.json)。已消费凭据不删除、不自动重放；辅助仅用于这份指定响应的测试验收，产品默认仍为真实 Vertex。辅助纯验证复现命令：`.venv/bin/python -m unittest discover -s src/pai_inspection_ros/test -p test_recorded_plan_provider.py -v`，不调用云模型或运动。

官方 [模型生命周期](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions)（2026-10-11 查证）将 `gemini-2.5-flash` 的 Vertex 退役日期列为 **2026-10-20**。原经济迁移候选 `gemini-3.1-flash-lite` 在实际硬门槛用例未通过，3.8 Flash 得23/24但8步边界硬门槛失败；同提示词/同24用例的 `gemini-3.1-pro-preview` LOW真实24/24、hard100%后被选为当前默认。它是preview，可用性可能变化，不宣称永久最优；可显式覆盖模型并复测，详见 [评测与人工改进说明](docs/AI_EVALUATION.md)。代码在退役日期起拒绝 2.5 配置，不会悄悄切换模型。交付截止为 **2026-11-02 10:00 Asia/Tokyo**。

## 支持的任务范围

白名单地点只有 `base`、`station_a`、`station_b`，坐标人工维护于 `simulation/config/stations.yaml`；模型不能设置坐标。中文别名包含基地/起点/设备 A/设备 B/A站/B站/站点A/站点B，日文包含ホーム/拠点/出発地点/設備A/設備B/地点A/地点B/ステーションA/ステーションB；也支持 canonical ID 与 `station a`/`station b`。

支持显式顺序短句，例如“去设备 A”、“先去 B，再去 A，最后回起点”、`まず設備Bへ行き、次に設備Aへ行って、最後に出発地点へ戻ってください。`。普通“去B站”只导航；“检查B站指示灯”生成导航后检查。

完整示例“先去设备 A 检查指示灯，再去设备 B，最后回起点。红灯记为异常，绿灯记为正常，看不清就记为未确认。”采用巡检语境：A 和 B 都检查，再返航。末尾固定记录规则只影响巡检记录，不产生条件导航；视觉验收完成前不声明颜色识别成功。日文固定规则也可用 `赤は異常、緑は正常、不明は未確認として記録`。

本地语义校验器保守地限制动作词、地点别名与顺序连接词；未识别词、未知地点、含已知与未知地点、歧义、否定、条件导航或不支持的改写整单拒绝，不静默映射站点。需要扩展改写时先补人工标注回归。输入最多 512 字符、最多 8 步；模型步骤的顺序与动作须匹配原文，且每步提供原文地点/动作依据。校验通过后仍保留模型响应中的步骤与依据，不以本地 parser 生成路线替代真实模型。

## 累计额度和结果证据

用户已明确确认当前既有台账应用累计上限为 **US$5 或 300 次真实尝试**，任一达到则拒绝新调用，80% 发出预警。新台账默认仍 US$1/100，只有显式 `.venv/bin/pai-budget set --max-usd 5 --max-attempts 300` 修改，`.venv/bin/pai-budget show` 查看；单行 policy 与 reserve 使用同一 SQLite 原子锁，所有模型/进程共享，不通过环境变量自动增额。[迁移证据](docs/evidence/budget-policy-update.json)确认旧请求和未知预留保留。`state/vertex-usage.sqlite3` 跨进程持久化，Compose 绑定宿主 `state/`；保留这个目录可保持所有运行的累计计数。每次请求发送前，SQLite 原子预留输入/输出费用上界，SDK HTTP 总尝试数为 1，超时 30 秒，不自动重新规划。响应保存实际 `usage_metadata`，包含 thinking 等字段；无用量或超时保留预留、标为 `unknown`，不会伪造 0。

这只是项目应用内限制，**不是 Google 账单硬上限**，不覆盖其他应用或人工调用。标准在线文本价格按 [官方价格页](https://cloud.google.com/vertex-ai/generative-ai/pricing) 于 2026-10-11 记录，USD/百万 token：2.5 Flash 输入 0.30/输出及推理 2.50；3.1 Flash-Lite global 输入 0.25/输出及推理 1.50，non-global 分别 0.275/1.65。费用结算优先保存原始计数并保守计入所有生成 token；预留较真实短任务保守。

[16 条原始人工标注语义任务](docs/semantic_cases.json) 按正常/同义改写/顺序/未知各 4 条组织；[24 条冻结验收任务](docs/semantic_acceptance_cases.json)加入关键安全用例，先4条 sentinel，全部通过后才继续20条。硬语义必须100%、总至少23/24；[判断效果及技术改进位置](docs/AI_EVALUATION.md)明确模型、校验、HTTP与ROS故障的区分和高级模型使用条件。另有完整用户例、注入、假地点映射、遗漏检查、输入/步数上限、跨进程预算和单次 HTTP 超时测试。当前 stub 与真实输出严格分开，见 [证据目录](docs/evidence)。

[日本語の報告書原稿](docs/REPORT_JA.md)包含已验证结果、复现入口、费用与人工改进位置；视觉已完成固定三场景评测。当前课程评审版报告源为受版本控制的 `docs/REPORT_JA.md` 与 `docs/REPORT_JA.html`；对应6页PDF与日文讲解178.1秒/2倍速视频保存在本地可选 `upload/`，不是运行源码依赖。原127.8秒/3倍速媒体仅为历史记录。

## 许可与交付状态

本项目原创文件采用 [Apache-2.0](LICENSE)。第三方组件保留原 LICENSE/版权声明，来源见 [docs/THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md) 和 `src/pai_sim_safety/UPSTREAM.md`。Qiita 文章作为设计参考，没有宣称取得文章代码的 Apache 授权。

[本地提交包清单与最小复现](docs/DELIVERY.md)说明源码、许可证、脱敏证据及正式媒体的包含/排除范围。

按10/19修订版课程要求，完整源码将通过 public GitHub 仓库交付。当前等待用户指定仓库名，再按已审核的安全发布历史直接Git推送；不以源码ZIP交付，不预先配置远端或推送。原 `submission/` 是保留的历史材料，当前推荐PDF/日文视频及私用提交指南在可选本地 `upload/` 中。

## 人工目视资料入口

本地可选目录 `human_review/` 集中旧版127.8秒视频、7张校准/正式截图及中文阅读说明；该目录被忽略，**不随公开源码追踪**。公开源码的主要阅读/复现入口是本README、[tracked日文评审稿](docs/REPORT_JA.md)、[效果与调参说明](docs/AI_EVALUATION.md)和[精简证据](docs/evidence)，不依赖本地媒体文件。实验来自2026-10-11，资料整理不增加模型或机器人实验。

文档中的旧实现commit和image SHA仅记录运行时内部来源，不是公开仓库必须checkout的版本。直接使用当前公开源码及其受控脚本/配置复现，不要求取得内部Git历史。
