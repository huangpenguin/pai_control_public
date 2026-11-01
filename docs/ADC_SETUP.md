# ADC 与既有项目权限

查证日期：2026-10-11。浏览器授权已完成，外部 ADC 文件可加载且刷新成功；不需要因为设置 quota project 失败而重复登录。

本次只读检查确认既有项目为 `ACTIVE`，当前登录账号具有 `aiplatform.endpoints.predict` 和 `resourcemanager.projects.get`，但缺少 `serviceusage.services.use` 与 `serviceusage.services.get`。读取 Vertex AI API 状态返回 403，因此 **API 启用状态未知**，不能据此说 API 未启用。脱敏结果见 [project-access-diagnostic.json](evidence/project-access-diagnostic.json)。

请既有项目管理员在 Google Cloud Console 的 **IAM 与管理 → IAM** 中选择该项目，为本次 ADC 浏览器登录的本人账号添加 **Service Usage Consumer**（`roles/serviceusage.serviceUsageConsumer`）。这个标准角色包含使用服务所需的 `serviceusage.services.use`，也包含读取服务状态的 `serviceusage.services.get`；当前已有推理权限，无需额外添加 Vertex AI 角色，也无需 Owner/Editor。依据：[Service Usage IAM](https://docs.cloud.google.com/service-usage/docs/access-control)、[ADC 排障](https://docs.cloud.google.com/docs/authentication/troubleshoot-adc?hl=en)、[模型推理权限](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/access-control)。

管理员补齐权限后，只重试 quota project 设置：

```bash
scripts/auth_adc.sh --quota-only
```

此命令复用现有 ADC 与固定的 gcloud 容器，不启动新的 OAuth 登录。项目仍来自未跟踪的 `.local/runtime.env` 或用户导出的环境配置。用户随后明确授权再尝试一次正确配置请求，该请求已成功返回真实计划；本轮仅执行这一次，没有自动重试、运动、IAM 修改、API 启用、计费变更或资源创建。

已有一次真实 SDK 请求失败需要保留：临时诊断脚本把 shell 默认表达式当成字面项目字段，得到 `403 / PERMISSION_DENIED / CONSUMER_INVALID`。生产启动脚本正确使用 shell 配置，这个错误不代表实际项目无效或缺少推理权限。最初提出的修正请求被自动审批拒绝，进程没有启动、台账没有新增尝试。用户随后直接授权的一次请求按正常审批获准并执行，因此先前拒绝记录仍保留，不能与后续成功混为一项。历史记录见 [首次失败](evidence/vertex-first-probe.json) 和 [自动审批拒绝](evidence/vertex-second-probe-blocked.json)。

本次正确配置的官方 SDK 请求使用 `gemini-2.5-flash`、`global`、HTTP attempts=1，指令为“去设备 A。”。模型原始 JSON 为 `accept`，步骤为 `navigate_to(station_a)`，原文依据“去设备 A”，本地语义校验通过且完整保留模型步骤，没有本地路线替换。耗时 2.45849 秒；真实 `usage_metadata` 为输入 453、输出 42、合计 495 tokens，thinking 字段为 `null` 并原样保留。原始输出、已验证计划、request_id 和用量见 [真实成功请求](evidence/vertex-second-authorized-probe.json)。随后父代理批准两阶段仿真验收，同一份真实模型计划执行成功 1/2：首轮 test-only 辅助漏设仿真时钟而任务 ABORTED，保留原始失败；修正辅助时钟后第二轮真实 Nav2/Action SUCCEEDED 并连续停稳。没有额外模型生成，生产判据不变。见 [两轮执行证据](evidence/recorded-real-plan-execution.json)。

成功请求同时刷新只读权限检查，`serviceusage.services.use/get` 仍为 false、推理权限仍为 true，quota project 仍未设置。因此缺少 Service Usage 角色会阻止 quota 设置与服务状态读取，但不能据此前诊断断言实际推理必然失败；本次真实推理已经成功。API 启用状态的服务管理查询仍未知。

该历史认证检查点的累计台账为 **2 次尝试、1 次未知用量，US$0.166604**：首失败保守预留 US$0.166363 完整保留，本次依据真实 token 按锁定价格向上取整记 US$0.000241。**这些金额不是已确认的 Google 实际费用**。该检查点采用原有 US$1/100 次累计上限，没有删除或重置失败记录；此后用户明确授权提高至 US$5/300 并继续真实模型评测，当前计数与原失败保留见 [STATUS](STATUS.md) 与 [评测说明](AI_EVALUATION.md)。
