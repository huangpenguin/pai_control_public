# 验收项与证据来源

日期2026-10-11。以下结果按来源分别计数；替身、留存响应、在线真实请求、纯校验和真实传感器观测不混合。未执行/缺证项不计成功。

| 要求 | 当前实际结果与边界 | 证据 |
| --- | --- | --- |
| P1真实计划驱动导航 | 真Gemini2.5 Flash原输出→严格绑定留存→真Gazebo执行，1个输出/2次授权执行，成功1/2。首test时钟失败保留，非同进程在线 | [请求](evidence/vertex-second-authorized-probe.json)、[两次执行](evidence/recorded-real-plan-execution.json) |
| P2命名站点顺序/返航 | 真产品在线新Pro计划，完整任务1/3。最后B→A→base均Nav2 SUCCEEDED、鲜度/位置/朝向/连续停稳通过；此前两故障保留 | [三次原结果](evidence/online-p2-attempt3.json)，第1/2同目录 |
| P3已实际绕开障碍 | 已有正式1起点→B段103个实际map-TF样点，在中心箱x带穿越时y=1.260–1.662m，超过箱上沿0.7m；观察中心最小距离0.4885m。采样TF定位折线7.687m，端点直线7.051m；名义base→B直线穿箱。非连续ground truth或完整物理碰撞证明，不新发任务 | [样点/首真实path/原SHA](evidence/obstacle-detour.json)、[工程图](evidence/obstacle-detour.svg) |
| P3未知整单拒绝 | 真在线1/1：设备C reject/空steps，0Nav2新目标/无移动。白名单未知不静默映射 | [真实未知](evidence/online-p3-unknown.json) |
| P3已知不可达继续返航 | 真在线预期行为1/1：隔离A在既有箱内，两次实际ABORTED208后剩余base SUCCEEDED/停稳，产品坐标未变。已在base起始，不把它当长距离返回 | [真实隔离不可达](evidence/online-p3-unreachable.json) |
| P3人工停止 | 实际Gazebo运动中独立stop：1/1，首ROS安全零命令约0.00418s、连续odom停车约0.77053s，自stop请求起算。模型是明确stub，非真实Vertex该项 | [运动/停止原始摘要](evidence/motion-acceptance.json)中的manual-stop |
| P3模型等待超时与晚回 | 注入SlowPlanner约36s后返回合法计划；应用35.0219s超时，晚回再观察0Nav2/0位移。替身注入1/1；没有真实Vertex HTTP超时发生的验收声明 | [同摘要](evidence/motion-acceptance.json)中的model-timeout与model_timeout_note |
| HTTP单尝试/超时配置 | 官方SDK attempts1/timeout30s由transport double针对性测试；真实调用usage/失败记录另列。不将该double称真实HTTP timeout | `tests/core/test_vertex.py::test_sdk_uses_adc_single_attempt_timeout_and_accounts`、[61回归](evidence/core-budget-audit-tests.txt) |
| P3无效参数/计划 | schema额外字段/类型、place/op限制、错误顺序、缺检查/依据、长度>512、step>8、未知/歧义/注入均有纯核心回归。未执行独立ROS无效参数live任务，不能写live n/N | `tests/core/test_plan.py`，尤其`test_strict_json`/`test_limits_and_evidence`与[61回归](evidence/core-budget-audit-tests.txt) |
| 必要安全传感器/控制故障 | guard-loss/bridge-loss/pause/manual四项真实运动安全行为4/4；原自动判定3/4保留。相关模型stub，guard-loss派生复核不覆盖原false | [独立复核/原始误判](evidence/motion-acceptance.json) |
| P4到站实际RGB | 正式三场景独立核通过：任务3/3、实际评分帧5/5；灰板必须fresh实际PNG判unknown后继续base，不把合成像素或校准图混入 | [冻结先验](visual_formal_cases.json)、[校准freeze](evidence/visual-classifier-freeze.json)、[正式1](evidence/p4-formal1-red-green.json)、[正式2](evidence/p4-formal2-green-red.json)、[正式3](evidence/p4-formal3-gray.json) |
| 接触观测 | 同SHA正向probe真非空1episode；三正式场景健康采集覆盖Action、observed0episode。不是无损流或普遍零碰撞证明，历史P2/P3无该测量 | [正向probe](evidence/contact-positive-probe.json)、[正式1覆盖](evidence/p4-formal1-red-green.json) |
| P5本地PDF/视频 | 日本语PDF6页、正式实际相机视频127.8秒/3倍速已停机离线生成、字体/全文/页面与首中末画面验收；不公开/不上传，占位账号保留 | [本地包清单](DELIVERY.md)、[日文稿](REPORT_JA.md)、[媒体SHA/覆盖](evidence/delivery-media.json) |

模型Pro preview、CPU llvmpipe、单幅停稳后判色、固定地图与竖直色板的限制见[效果与人工改进](AI_EVALUATION.md)。高级模型固定24题合格不掩盖ROS故障；Nav2能力名称也不替代实际绕行路径。
