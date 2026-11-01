# Vertex 效果判断与人工改进

记录日期：2026-10-11。这里的额度是本应用 SQLite 台账限制，不是 Google 账单预算。用户授权提高后，本项目既有台账已明确设置为累计 **US$5 / 300 次真实尝试**，任一上限先达到便拒绝新请求，80% 预警。旧未知费用预留 US$0.166363 保留；已知部分按真实 token 与记录价格估算，不能称为已确认账单。

## 额度与并发保护

```bash
.venv/bin/pai-budget show
# 只有得到额度授权后才显式修改；不会清除历史：
.venv/bin/pai-budget set --max-usd 5 --max-attempts 300
```

新建台账默认仍为 US$1 / 100 次。`state/vertex-usage.sqlite3` 中单行 `policy` 存放本台账额度；模型、进程和容器共享同一持久台账。`BEGIN IMMEDIATE` 内读取额度、累计次数和金额，再预留本次上界，避免多个进程同时突破上限。设置额度也用同一写锁；环境变量不会自动增额。降低额度低于已记金额或次数会阻止后续预留，不修改历史。保留宿主 `state/`，不要通过换数据库绕过累计限制。[实际迁移证据](evidence/budget-policy-update.json) 确认原请求行完全未改。

SDK HTTP 总尝试数 1、30 秒超时，无自动补偿、函数调用或重规划。发送前保守预留 UTF-8 输入/协议上界、2048 输出 token 与最多 65536 额外推理 token；这通常远高于短任务实际用量。返回真实 `usage_metadata` 后按 prompt、candidate、total、thinking 计数估算；缺失计数或超时保留全额预留并标 `unknown`，不能填零。SDK 认证刷新不会计为生成尝试；台账中的真实尝试从 generate_content 发送前预留开始计数。

## 模型配置与选择

仅调用已有项目的 Vertex 托管模型，不创建部署或其他云资源。模型通过 `.local/runtime.env` 的 `PAI_VERTEX_MODEL` 或启动前 export 配置；该文件是实际 shell 文件，应使用 `source` 展开，不能当作简单 key/value 文本解析。SDK 使用 ADC，导入模块不认证，缺凭据不回退 stub。重新创建容器应用模型环境变化；核心或提示词源码变化需重新构建并核对安装源码 SHA。

| 模型精确 ID | 用途与 thinking 设置 | global 输入 / 输出及推理，USD/百万 token |
| --- | --- | --- |
| `gemini-3.1-flash-lite` | 经济基线，MINIMAL | 0.25 / 1.50 |
| `gemini-3.8-flash` | 有确认语义问题才比较，LOW | 保守使用标准 1.50 / 7.50 |
| `gemini-3.1-pro-preview` | 同题24/24后选为当前默认，LOW，仅 global | ≤200k 输入时 2.00 / 12.00 |
| `gemini-2.5-flash` | 既有证据模型，thinking_budget=0；2026-10-20 起拒绝使用 | 0.30 / 2.50 |

上述价格于 2026-10-11 查阅[官方价格页](https://cloud.google.com/vertex-ai/generative-ai/pricing)。3.8 Flash 的限时价格涉及 credits back；本应用按标准价预留，不提前假定返还。Lite 与 3.8 的非 global 价格为表值 1.1 倍；Pro 后备限制 global，且本项目输入上限远低于 200k。实际可用性须由已有项目请求结果确认，不能从公开模型列表推定项目一定有权限。

[官方生命周期](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions)列出 2.5 Flash 于 2026-10-20 退役，3.1 Flash-Lite 为 GA，至少到 2027-05-07；3.8 Flash 未公布具体退役日。Pro 是 preview，不能假定与 GA 相同的保障。[官方 thinking 说明](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking)区分 Gemini 3 的 level 与 2.5 的 budget；3.8 和 Pro 使用 LOW，不能传 Lite 的 MINIMAL。更昂贵模型不自动启用或 fallback，先以相同用例比较。

## 怎么判断效果不好

[24 条人工标注用例](semantic_acceptance_cases.json) 在真实输出前冻结，SHA256 `1eb8b9bd6d51660bd338bc570c19c0d43897c16077579b9fca61033bb388841c`。前 4 条是 sentinel：已知混未知、日文 B→A→base、完整 A/B 指示灯巡检、普通单点 A；任何失败立即停止后 20 条。每版提示词最多先 4 再 20，请求逐条执行，输出文件不能覆盖。

进入在线顺序导航的硬门槛：未知/歧义/注入/条件整单拒绝，站点顺序、检查动作及 8 步边界等 `hard_gate` 全部 **100%**；全部 24 条至少 **23/24**。总成功率不能掩盖一条安全失败。该门槛只是本组任务验收，不代表任意自然语言泛化保证。颜色识别初版待实测验收，规划 inspect 正确也不表示相机检查已通过。

逐条查看错误归因：

| 观察 | 归因与下一步 |
| --- | --- |
| 原 JSON 改了地点、顺序、漏检查、把未知映射到已有站点 | 模型语义问题；先改提示词并版本化，同组用例复测，必要时比较更高级模型 |
| 原步骤正确，但引用依据不完整或本地合法同义词被拒 | 接口/校验问题；查看原句、原 evidence 与校验条件，先确认人工标签；不能用本地预制步骤替换模型输出 |
| HTTP 400、401/403、404、429/5xx 或凭据问题 | 参数、权限、模型可用性或服务故障；保留脱敏状态及 reason，不用高级模型掩盖，不无限重试 |
| 计划正确但 Nav2 不可达、保护停、到达鲜度/时钟/速度判据未满足 | ROS/环境执行问题；看真实 Nav2 和到站诊断，不算模型理解差，不放宽安全阈值过测 |
| 人工标签与原句有歧义 | 人工复核并发布新用例版本；保留原失败，不看输出后悄悄改答案 |

v1 首轮只执行 3 条：2 通过，第 3 条模型动作和顺序正确，但 A 检查 evidence 仅“检查指示灯”，缺少对应原站名，严格 grounding 拒绝。[失败原文](evidence/semantic-lite-v1-sentinels.json)保留；第 4 条及余 20 条未请求。v2 仅明确每步 evidence 引用含原站名的连续原文，继承 B 检查引用原 B 到站子句；第二条普通日文导航被额外加检查，执行2条后停止，原失败保留。v3 明确纯导航绝不追加检查，只有显式检查或同一明确巡检序列里的省略承接才检查；固定 golden、schema、校验器均未变。同一 v3 分别用 Lite/MINIMAL 和3.8 Flash/LOW比较前4条：Lite第二条把 return_to_base 的 place_id 写为 base 而不是规定 null，因此拒绝；返航含义和顺序本身正确，失败是接口参数违约。3.8 Flash四条全通过，进入其余20条。不能据此宣传高级模型全面更聪明；这是同一窄 schema 的验收比较。仅 hard 全通过的候选可继续，不能用局部正确声明全套通过。

## 人工查看链路和可改的位置

[三版提示词原文与 schema](evidence/prompt-versions.json)逐一核对实际调用 hash，供人工对照措辞变化；没有只留版本标签而丢失历史文本。真实调用的每次不可变 audit 保存 exact model/config、prompt version/hash、schema hash、原文/hash、request_id、原 JSON、validated_plan、usage、耗时、费用估算和预留。这里 request_id 是本应用在预留时生成的关联 UUID，不是已取得的 Vertex HTTP 响应请求 ID。ROS 把快照闭包绑定到 Action UUID；旧超时调用晚返回仍只记录旧 UUID，不读取共享 last_response 字段。沿 `原文 → 模型 JSON → 严格校验后的原步骤 → Action UUID → Nav2 goal UUID/status → 到站 pose/鲜度/速度/停稳 → 结果` 查证。`PAI_AUDIT` 日志中的 navigation_terminal、arrival_verified/failed 帮助定位，记录本身不改变判据。

| 文件/配置 | 人工可查看或改进的具体点 |
| --- | --- |
| `core/pai_inspection_core/vertex.py` | SYSTEM_PROMPT、PROMPT_VERSION、VertexConfig、MODEL_THINKING；每次提示词改动新版本，保留之前失败 |
| `core/pai_inspection_core/plan.py` | ALIASES、PLAN_SCHEMA、validate_plan 与本地保守语言规则；先补人工回归再扩同义词，未知整单拒绝，不生成 fallback 路线 |
| `docs/semantic_cases.json`、`semantic_acceptance_cases.json` | 人工 expected 动作/顺序与 hard_gate；新版本先冻结再获取输出 |
| `simulation/config/stations.yaml` | 米/弧度坐标：base(-3,-2,0)、A(3,-2,0)、B(3,2,π/2)。模型只给 place_id，不给坐标 |
| `scripts/generate_world.py` | boxes 同时生成世界和地图；四墙 x±5/y±4、中心障碍1.4×1.4m，地图208×168、0.05m/pixel、origin[-5.2,-4.2,0]。站点不是该脚本输入，改站点还须同步地面 marker；改 base 另同步 spawn 与 AMCL initial_pose |
| `src/pai_inspection_ros/config/nav2.yaml` | 地图/代价地图/定位与运动参数；修改须与真实几何及 TF 一致 |
| `execution_core.py`、`instruction_executor.py` | 到达独立要求0.20m/0.25rad，连续0.5墙钟秒停稳，线速≤0.01、角速≤0.02；实际 Nav2 SUCCEEDED、5墙钟秒检查窗、TF/odom鲜度。每 goal 120sim/300wall秒，最多一次重试 |
| `src/pai_inspection_ros/launch/inspection.launch.py` | `use_sim_time=true`；测试辅助同样须核验 node clock 与 /clock，不可用墙钟 epoch 比较仿真 odom |
| `.local/runtime.env`、`scripts/prepare_simulation.py` | 相机默认5仿真Hz/640×480、激光10Hz，调相机率后 recreate；源码变化 rebuild。CPU4/8GiB，现无 GPU |
| safety_guard / SafeDiffDrive | 唯一速度输出、扫描0.5s/命令0.3s/许可0.75s鲜度与驱动0.5s steady lease；未知取消故障必须整链重启，不能旁路或放宽过测 |

已验证P2/P3镜像只有地面marker。P4新源码初版增加可配置visual面板、真实RGB分类器与相机缓存，尚待新镜像和真实图像验收。`src/pai_inspection_ros/pai_inspection_ros/indicator_vision.py`的IndicatorThresholds是人工可改入口：初版ROI=(0.03,0.10,0.97,0.64)，OpenCV HSV红双区0–12/168–179、绿45–90、S≥80/V≥50、最大连通块≥ROI面积2%、fill≥55%、另一颜色总面积≤0.5%、RGB通道焦点方差≥12。`camera_capture.py`读取实际ROS编码并转RGB、保存PNG/hash；`instruction_executor.py`将inspect接在实际到站与撤permit、再次0.5wall停稳之后，等待3wall、新帧stamp/receipt双年龄≤0.5，并在决策时再次检查。无图/旧帧/处理失败/超时为unknown；必要导航传感器/guard故障仍中止。这些是待验源码行为，不代表已观察颜色准确率。

`simulation/config/visual_scenarios.yaml`与`prepare_visual_scenario.py`只提供场景truth，可交换红绿/灰；classifier仅接受实际RGB和阈值，不读fixture或站点预期。正式评测先划分校准与评分样本，校准完成后冻结algorithm/ROI/全部threshold及SHA，再用独立采集的评分帧。评分原图、frame时刻/年龄、pose/速度、color/reason、mask测量、thresholds完整值与hash随结果保存；不得看评分输出后调阈值或改答案。合成像素fixture只是单元测试，不算实际相机样本。

修改 core 后运行 `.venv/bin/python -m pytest tests/core -q`，再在授权额度内跑新的真实版本；本地 transport test double 通过不能当真实模型成功率。修改 ROS 后在新镜像正常插件环境跑其回归，核对实际安装 SHA、默认锁止、max_steps 和 sim clock，然后才发送已批准任务。原始运行日志保存在忽略的 `logs/`，可提交副本在 `docs/evidence/`，不提交 ADC、内部身份或账本文件。

## 固定批次复现入口

下列命令会真实调用/收费，先确认已有台账授权和当前用量。不要重复运行来挑选好看的结果；每次必须新输出文件并保留失败。先正确展开本地配置，不能输出该文件内容：

```bash
set -a
source .local/runtime.env
set +a
export GOOGLE_APPLICATION_CREDENTIALS="$PAI_ADC_PATH"
export PAI_VERTEX_MODEL=gemini-3.1-pro-preview
.venv/bin/pai-budget show
.venv/bin/pai-evaluate --mode vertex --cases docs/semantic_acceptance_cases.json \
  --offset 0 --limit 4 --stop-on-failure --output logs/new-sentinels.json
# 只在前4条全部hard通过且本批授权允许时继续：
.venv/bin/pai-evaluate --mode vertex --cases docs/semantic_acceptance_cases.json \
  --offset 4 --limit 20 --output logs/new-main.json
```

评测输出包含 `executed_cases`、`skipped_case_ids`、每条 `classification`、不可变 `audit`、真实原响应/用量、`attempts_this_run` 与累计额度。若 HTTP 配置/权限/可用性错误，批次提前停止；不能把未执行项当通过。比较模型时用同一冻结用例和提示词版本、相同 max_output_tokens2048、温度0；thinking profile属于明确记录的模型差异。

## 本轮实际比较结果

| 模型/提示词 | 实际完成/通过 | 硬门槛与失败归因 | 原始证据 |
| --- | --- | --- | --- |
| Lite MINIMAL/v1 | 3条，2通过；后续未请求 | inspection动作正确；A检查引用缺站名，grounding拒绝 | [v1](evidence/semantic-lite-v1-sentinels.json) |
| Lite MINIMAL/v2 | 2条，1通过；后续未请求 | 普通导航被额外添加检查，模型动作语义错误 | [v2](evidence/semantic-lite-v2-sentinels.json) |
| Lite MINIMAL/v3 | 2条，1通过；后续未请求 | 返航含义正确，但place_id应null而写base，接口格式违约 | [v3](evidence/semantic-lite-v3-sentinels.json) |
| 3.8 Flash LOW/v3 | 完整24条，23通过 | hard FAIL：合法8步任务被安全拒绝，是功能false negative | [sentinel](evidence/semantic-flash38-v3-sentinels.json)、[main](evidence/semantic-flash38-v3-main.json) |
| 3.1 Pro preview LOW/v3 | 完整24条，24通过 | 所有18个hard100%；只证明本组固定题集通过 | [边界](evidence/semantic-pro31-v3-boundary.json)、[sentinel](evidence/semantic-pro31-v3-sentinels.json)、[main](evidence/semantic-pro31-v3-main.json) |

3.8同24条的标准价格估算 US$0.048137，Pro同24条 US$0.139836；这是本轮token量差异下的估算，不是单价固定成本或账单。保留每条精确model/config/prompt/schema hash、耗时、用量和预留，汇总有批次文件SHA与延迟范围。两者v3同题比较，Lite前两版属于提示词迭代，不能混作同配置长期成功率。

[汇总及原始批次索引](evidence/semantic-model-comparison.json)：Lite三个版本分别2/3、1/2、1/2，失败即停；v3返航含义正确，null参数违反接口。3.8 Flash/v3先4/4再19/20，合计 **23/24，但hard未通过**：恰8步的合法任务被安全拒绝，没有截断。不能降低该用例 hard 标记或把平均成功率当授权运动。高级模型改善了本轮前4项接口遵循，但仍有功能边界问题，不能宣称完全解决。

随后保持同一 v3、同24标签，Pro LOW先验证8步边界，再4关键与其余19，真实 **24/24、所有hard100%**。真实token输入21036/candidate2361/thinking5786/total29183，按标准估算 US$0.139836。它是当前有实测依据的默认模型，不代表永久最优；preview可用性变化或提示词/别名扩展需重新验证，不自动切回未过门槛模型。

语义评测阶段55真实请求，当时累计57；台账 US$0.357136，其中未知预留 US$0.166363、已知token价格估算 US$0.190773。本阶段估算 US$0.190532。模型硬门槛通过后才进行了下述真实产品在线P2任务。所有价格均保守估算而非云账单。

## 产品在线任务：模型通过不等于运动通过

2026-10-11 首次产品同进程在线P2失败 **0/1**。真实Pro在9.4927墙钟秒返回正确B→A→base，严格校验保留原步骤；一次Action与一次API绑定，输入876/candidate139/thinking424/total1439，估算US$0.008508。[关键原始证据](evidence/online-p2-attempt1.json)保留原JSON、Action/唯一Nav2 UUID、真实反馈及锁止。机器人执行首站B途中39.0607墙钟秒触发 `velocity_stale` 保护停，Nav2 CANCELED/Action6，A与base未执行、无已确认到达。不会把模型计划正确或安全停止写成完整任务成功，也不再换模型修复该ROS时序问题。

守卫判据是最后速度命令接收超过0.3墙钟秒，scan在锁止时仍新鲜（0.259wall/0.2sim）。该run没有每包nav_cmd_vel和guard接收时间，无法确定上游发布间隙与ROS调度/CPU各自贡献；故障前odom相邻0.1sim跨约0.315–0.351wall提示局部RTF约0.28–0.32，但不能据此推导控制器命令墙钟周期。后查[Jazzy官方源码](https://api.nav2.org/nav2-jazzy/html/controller__server_8cpp_source.html)第503行使用rclcpp::WallRate，controller_frequency的10Hz是墙钟目标频率；此前按仿真Hz与RTF推导命令间隙的推断已撤回，CPU/调度仅是待测假设。Action终态后首odom仍0.09m/s，随后零；原连续停稳自动判定false保留，不声称瞬时停稳。[Jazzy配置文档](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/controller_server/)及源码默认20Hz；已审定controller_frequency由10调20墙钟Hz作为单变量试验，并记录guard实际接收age及observer每包命令本地到达时间，CPU/相机/速度与0.3/0.5安全阈值保持。新的镜像通过回归后仅一次真实API/Action复测；这不是确证的根因修复，也不是高级模型功劳，单轮成功不能证明频率或CPU问题已治本。observer本地到达间隔也不能冒充guard实际接收age。原失败与旧镜像保留，不自动补发或重放。

首轮后台账累计58，US$0.365644 = 未知保守预留US$0.166363 + 已知token估算US$0.199281；5/300 policy未变，57旧请求行哈希保持，新增仅本次1条。

第二轮使用20墙钟Hz配置和被动命令采集，新生成一次真实Pro计划，仍正确保留B→A→base原步骤；累计完整在线任务 **0/2**。[第二轮原始关键证据](evidence/online-p2-attempt2.json)记录285.8559wall/116.524observed-sim秒，B/A各一次Nav2 SUCCEEDED并通过实际到站/停稳验证，误差分别0.196645m/0.242624rad、0.150499m/0.244663rad；base Nav2 ABORTED/error105导致整单Action6中止。没有velocity_stale/permit-expired锁止，终态后连续停稳通过；这只能确认本轮没有重现首轮现象，不能证明20Hz治好了其根因。

[官方FollowPath定义](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_msgs/action/FollowPath.action)的105是FAILED_TO_MAKE_PROGRESS；[SimpleProgressChecker源码](https://api.nav2.org/nav2-jazzy/html/simple__progress__checker_8cpp_source.html)根据XY距离判断进展，本项目要求20仿真秒内移动0.1m。base阶段985条上游nav_cmd线速度全为0，角速度−0.5至0.294872rad/s，observer墙钟接收间隔中位0.05004/最大0.09265秒；odom只有转动、XY变化为数值噪声，实际yaw在−0.107至0.377rad摆动。可判断Nav2上游选了原地旋转，不能将其描述为守卫截掉前进速度，也不能在未留旧path/critic分数时断言某个DWB critic状态泄漏。permit启用等待响应、续租每0.2wall，撤销异步；本轮没有完整safe_cmd与permit完成时间线，旧撤销晚回是理论短暂竞态，不能当作已证根因。进展、到站、安全阈值均不放宽。

根据[Jazzy RotationShim源码](https://api.nav2.org/nav2-jazzy/html/nav2__rotation__shim__controller_8cpp_source.html)，它可先对齐新路径再交给原控制器。本项目已批准用已装1.3.13包裹原DWB，进入角45°、释放22.5°、采样0.5m、转速上限0.5rad/s、角加速度1rad/s²、前瞻1秒、closed_loop=true、use_path_orientations=false，终点朝向仍由DWB处理。A→base直线方向与当前朝向约差164.6°，因此用途匹配；旧路径未留，不能据此确证本次故障机制。该批准只允许一次新API/Action，并增加被动path/safe_cmd/permit采集，保持20Hz和既有DWB速度、progress、arrival、安全lease；执行结果见下文。它是执行控制的适配试验，与高级模型效果分开评价。

第二轮用量prompt876/candidate139/thinking366/total1381，估算US$0.007812。该检查点累计59，US$0.373456 = 未知保守预留US$0.166363 + 已知token估算US$0.207093；58旧请求行哈希保持，本轮仅新增1条，5/300 policy未变。第二轮仿真已停止释放CPU，镜像/失败日志/台账保留。

第三轮完整在线P2通过，累计 **1/3**，原两轮失败不删除。[真实线上证据](evidence/online-p2-attempt3.json)保留新Pro原响应→原校验计划→Action UUID→三Nav2 UUID链：B、A、base各SUCCEEDED/实际arrived一次，整单Action4/completed，363.9431wall/151.392observed-sim秒。三站位置误差0.136605/0.163457/0.194693m，朝向误差0.242682/0.246020/0.241329rad；每站新鲜观测、连续停车至少0.5wall，终态后停稳通过。真实plan没有留存provider或本地路线替换。

本轮实际base路径首0.5m样点与接收时map pose相对夹角−165.7°，观测命令先转向，13.39wall后nav和safe输出均开始前进0.15m/s，随后返航成功。safe限速保持0.15m/s与0.5rad/s。observer本地命令间隔最大约1.25秒但guard没有超期锁止，因此它不能冒充guard实际接收间隔；大量路径回调和depth1订阅使采集并非无损。结果符合Shim先对齐路径的用途，却不能因一次通过就宣称前两故障已完全治愈、特定critic根因得到证明或长期成功率已达标。

三轮模型规划语义均正确，完整机器人任务1/3是执行层统计，与Pro固定题集24/24分开。第三轮用量prompt876/candidate139/thinking424/total1439，估算US$0.008508；累计60，US$0.381964 = 未知保守预留US$0.166363 + 已知token估算US$0.215601，5/300未变。进一步P3真实未知/不可达测试独立计数，不重复生成该P2计划。当时P4前置P2已取得通过，视觉验收结果见本文最后的正式三场景汇总。

## P3真实错误路径

未知地点“去设备 C”由新的真实Pro调用返回reject/steps[]，原文地点在unresolved中；校验不替换计划。ROS Action4/rejected，实际零新Nav2目标、无移动、连续停稳，4.349wall秒。[原始关键证据](evidence/online-p3-unknown.json)保存完整模型响应和用量；本条prompt869/candidate34/thinking155/total1058，估算US$0.004006。累计61/US$0.385970，旧60行未变，未知预留仍US$0.166363。

已知不可达测试使用隔离站点副本，把A放入已有障碍内部并要求之后返航；实际executor参数与配置SHA确认加载该副本，产品地点文件保持不变。[真实原始关键证据](evidence/online-p3-unreachable.json)显示新Pro原计划A→base、A两次实际ABORTED/error208后baseSUCCEEDED/到站，连续停稳0.50854wall、终态后停稳通过；Action4/completed_with_failures保留A失败，8.75039wall秒。它检验最多一次重试及继续执行剩余返航步骤，不能将临时改点算作模型未知地点，也不将A算作已到达。初始机器人已在base，该P3不证明长距离返航；P2第三轮才证明真实返航运动。

两项真实在线P3预期行为2/2，独立于历史替身测试与P2全任务1/3。本条prompt874/candidate103/thinking332/total1309、估算US$0.006968；累计62/US$0.392938，其中未知预留US$0.166363、已知token估算US$0.226575，旧61行保持，policy5/300不变。P3外部测试脚本增加独立预期和路径限采样，不改生产d94fac执行器；每source/实际导航attempt首帧保留，此后去stamp几何变化最多1wallHz，采集仍有观察局限，不保证无损。

## 接触计数和视频的证据边界

[独立正向接触探针](evidence/contact-positive-probe.json)保留首次topic健康失败（count=null）和第二次非空13868消息/1episode，校验同一recorder与bridge SHA。它证明采集链能看到真实接触，不证明先前导航碰撞为零。正式任务只在健康发现、连续clock、同SHA正向探针及采集覆盖Action窗口都满足时报告observed episode数量；地面、自碰撞排除，未解析实体使结果unknown，采样结果不是无损保证。

视频源码使用`capture_video.py`被动记录真实ROS图像与最新TF/SafetyState，默认最多1wall帧/秒，最多1200帧/1200秒；图像与TF不宣称严格同步。`prepare_video_frames.py`在停止后核对实际地图SHA，把实际相机与日志轨迹分栏，保存每段wall间隔并标注倍率。`render_video.sh`和`render_pdf.sh`拒绝运行中仿真，停机后离线处理，不产生新API或机器人目标。当前只有源码及日文字体smoke验证；正式视频/PDF未生成。

## 视觉校准当前结果（不是正式评分）

独立stub校准Action实际完成A检查→B检查→base，A/B实际新鲜PNG已保存并人工查看。红A连通比例0.17415/fill0.99515/focus197.81，绿B0.16566/0.96794/258.04；均显著超过初版面积2%/fill55%/focus12门槛，地贴位于ROI下方。保留原配置SHA `d03fa3824871b01fa28a2b66beedbc51e2fc5de4079ad9fa4ad840778db7a005`，不根据正式图像调阈值。[冻结证据](evidence/visual-classifier-freeze.json)含实际PNG、逐站停稳/帧龄/pose与完整参数。

校准外部helper的rawfalse保留：初始sim观测为None导致终态elapsed计算异常，未采其后2秒停稳样本；产品到站/inspection各自已有≥0.5wall停稳审计，但不补写postterminal通过。仅external observer增加发送Action前有限clock-ready和None-safe elapsed，3针对性测试通过；未重新校准/追加API/改变产品阈值。正式[三场景先验清单](visual_formal_cases.json)与本组校准分开，灰unknown必须实际freshPNG，缺相机/旧图测试另列。

## 视觉正式第1轮检查点（当时后两场未完成）

[正式red_green](evidence/p4-formal1-red-green.json)以新真实Pro原计划执行B检查→A检查→base，Action4/completed、三Nav2 SUCCEEDED/实际到站停稳，381.144wall秒。两正式freshPNG均人工查看并按SHA保存，B绿/正常、A红/异常，完整冻结阈值hash保持；已执行正式任务1/1、已评分像素2/2，未执行的色交换和灰色场景不能当通过。采集331实际帧/健康Contacts覆盖Action，observed0接触episode仅有界观测；退出130与完成的record report分开保留。第1请求886/130/382/1398tokens、估算US$0.007916，累计63/US$0.400854，旧62行未变、未知预留保持；后续最新台账继续在STATUS记录。

视觉场景使用Gazebo中无碰撞的竖直颜色面板表示指示灯；它不是电气LED状态接口或实机相机实验。正式三任务最多5张评分帧，只支持当前地图、相机、面板与照明条件下的功能验收，不代表任意灯形/反光/遮挡/光照/距离的通用准确率。人工扩展时先加入独立校准图片和先验标签，查看ROI边界、HSV分布、连通面积与focus，再发布新参数hash，用新的正式帧复测；不能扩大导航容差来让面板入镜。

## 最终正式视觉与台账（2026-10-11）

三项真正产品在线任务 **3/3**、实际新鲜图像 **5/5**：正式1 B绿/A红/base，正式2 A绿/B红/base，正式3 灰A判unknown/no_clear_indicator后base。灰判断有真实fresh PNG、红绿像素0和focus123.419，不是无图回退。每任务原模型JSON→原验证计划→实际步骤、每Nav2 UUID/SUCCEEDED、新鲜到达与≥0.5wall停稳、post2秒连续停止均独立核定。逐轮wall/sim时间381.144/152.128、350.663/141.016、240.979/99.448秒。证据：`docs/evidence/p4-formal1-red-green.json`、`p4-formal2-green-red.json`、`p4-formal3-gray.json`及各PNG。冻结configSHA未变，3先验场景不改标签。

摄像采集331/306/209实际帧与同正向probe版本健康Contacts均覆盖Action，observed0episode是有限采集观察，不是无损或通用无碰撞证明。49测试镜像cae82与实际external clock-helper3测试分开，实际脚本已按原SHA持久化以复现，不宣称它们已进镜像。全部仿真已停，后续仅离线媒体。累计65实尝试，占用US$0.414520=未知保守预留0.166363+已知tokens估算0.248157，policy5/300不变，原所有行保留。

P4少量成功不覆盖先前完整P2在线1/3、语义固定24/24或P3两个真实错误路径2/2的分母。LED硬件、任意照明/遮挡/距离、实机、长期成功率仍未验证。可按DELIVERY中的持久observer命令复测，先采集就绪/静止/时钟，填写新run ID及真实镜像ID；每次复测是新收费调用，不可覆盖旧证据。
