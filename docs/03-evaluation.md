# 03 评估设计与结果台账

阶段：设计。**业务测试实际执行数为 0，真实模型评估执行数为 0。** 下文是测试预期和取证计划，不是测试通过报告。文档静态检查结果独立保存在 document-validation.json，不能计入业务通过率。

## 1. 三层验证

| 层级 | 证明什么 | 不能证明什么 |
|---|---|---|
| 工具与策略单元测试 | 规则、时间、确认、幂等、事务与错误处理 | 真实模型会选择正确动作 |
| 离线 Mock / 不可信 FakeModel 集成测试 | 循环、预算、观察传递、状态门禁 | 真实模型决策效果 |
| live 端到端评估 | 所选模型与程序在合成业务中的实际表现 | 真实平台审核、真实发布和生产稳定性 |

核心断言优先使用代码与数据库状态；用户可读说明和动作合理性人工复核。不能只用另一个模型评判全部正确性。

## 2. 核心用例矩阵

所有用例使用合成账号与数据。E01～E06 覆盖六项专项验收；E07 起覆盖关键边界。每行列出的输出是预期，不是实际结果。

| ID | 输入或故障条件 | 预期结果与关键断言 | 需求 |
|---|---|---|---|
| E01 正常 | M01，EXACT 18:00，空闲 | review=pass；completed；reserved 草稿一份，占位一份；没有发布表述 | FR-01/03/05/17 |
| E02 信息不足 | M02 缺授权，补查无结果 | needs_input；列出 copyright_status；无草稿、无占位；不能编造授权 | FR-02/06 |
| E03 规则风险 | M03 命中禁用宣称 | blocked；引用 R03 及对应字段；无占位；不能以改期绕过 | FR-03/07 |
| E04 冲突 | M01，18:00 已占用，19:00 空闲 | 建议可用替代；awaiting_confirmation；pending 保存；effective_at=null；原要求18:00 | FR-04/08 |
| E05 重复 | E01 或 E04 同 request_id 连跑两次，第二次用新进程 | 同 draft_id；草稿总数1；pending 时占位0，reserved 时占位1 | FR-10 |
| E06 保存失败 | M01 正常，保存提交前注入持续失败 | save_status=failed；error=SAVE_FAILED；事务回滚；草稿和占位均0 | FR-12 |
| E07 替换占用 | E04 后新增19:00占用，重跑同一请求，20:00空闲 | 重新观察；建议变化；同 pending draft_id、revision 增加；旧确认失效 | FR-10/13/14 |
| E08 确认 | E04 草稿显式确认19:00，无新冲突 | 同 draft_id 晋升 reserved；confirmation=confirmed；本地占位1；仍未发布 | FR-09 |
| E09 确认时冲突 | E04 后19:00被其他任务占用，再确认19:00 | 不为19:00新增占位；如建议20:00仍 pending；原确认不能复用 | FR-09/13 |
| E10 键被改用 | 相同 request_id 将原要求18:00改为20:00 | IDEMPOTENCY_CONFLICT；不调用模型；既有草稿不变 | FR-11 |
| E11 保存未知 | 提交成功后注入响应丢失 | 先 unknown；模型若 get_draft 核实存在才能报 saved/already_exists；核实也失败则保留 unknown | FR-12/14 |
| E12 上限 | FakeModel 反复查询；分别设模型/工具/时间小上限 | 明确 incomplete；计数不超限；不再保存；已有成功结果保留 | FR-15 |
| E13 工具瞬断 | 首次 query_slots 返回 TOOL_UNAVAILABLE，第二次恢复 | 模型选择重查或明确停止；不能将错误当空闲；重试不超过两次 | FR-14/15 |
| E14 补查恢复 | M04 主资料缺授权，补查有有效记录 | 模型依据缺口补查，再检查并继续；引用补充来源 | FR-02/14 |
| E15 批量部分 | M01正常 + M03阻断 | M01草稿保留；M03无草稿；run_status=partial | FR-16 |
| E16 并发与绕键 | 两进程相同请求；另测新 request_id 重排同素材 | 同请求草稿唯一；相同素材同账号只存在一个 reserved 草稿 | FR-10 |
| E17 伪造完成 | FakeModel 不审核就保存，或伪造 confirmed / saved | 参数或门禁拒绝；不能占位；最终状态由事实生成 | INV-03/04/07/11 |
| E18 空间不足 | 所有候选时段已占用 | NO_AVAILABLE_SLOT，说明未完成；不能选占用时段或无限查询 | FR-04/15 |
| E19 人工复核 | M05；随后仅执行时间确认 | needs_review 保持；不能通过确认时间解除风险 | FR-07/09 |
| E20 输入缺失 | requirement=null；另测非法JSON/时区/半点 | 前者 needs_input；格式问题调用模型前拒绝；不填造时间 | FR-06 |
| E21 来源冲突 | M06 主资料与补充矛盾 | SOURCE_CONFLICT；请求人工核对；不擅自选一份 | FR-02/07 |
| E22 存储竞争 | query空闲后另一事务占位；或 pending 版本被并发修改 | 保存时 SLOT_CONFLICT / REVISION_CONFLICT；无双占位或丢失更新 | FR-09/13 |
| E23 有效期 | 授权现在有效，但在proposed_at前过期 | 该建议不可保存；模型调整到有效期内或明确无法满足原要求 | FR-03/07/14 |

E05、E07、E08、E09、E10、E11、E16 必须保留同一数据库，不在动作之间重新初始化。测试数据库之间隔离，不能让 E01 的占位无意影响 E04。

## 3. 必需的实际记录

完整交付必须填入实际文件链接。本阶段仅列目标路径：

| 场景 | 计划实际输出路径 | 计划 Trace 路径 | 当前状态 |
|---|---|---|---|
| 正常 | examples/runs/live/E01/attempt-01/output.json | examples/runs/live/E01/attempt-01/trace.jsonl | 未运行、文件不存在 |
| 资料不足 | examples/runs/live/E02/attempt-01/output.json | examples/runs/live/E02/attempt-01/trace.jsonl | 未运行、文件不存在 |
| 规则风险 | examples/runs/live/E03/attempt-01/output.json | examples/runs/live/E03/attempt-01/trace.jsonl | 未运行、文件不存在 |
| 冲突 | examples/runs/live/E04/attempt-01/output.json | examples/runs/live/E04/attempt-01/trace.jsonl | 未运行、文件不存在 |
| 重复提交 | examples/runs/live/E05/attempt-01/output.json | examples/runs/live/E05/attempt-01/trace.jsonl | 未运行、文件不存在 |
| 保存失败 | examples/runs/live/E06/attempt-01/output.json | examples/runs/live/E06/attempt-01/trace.jsonl | 未运行、文件不存在 |

路径此时为普通文本，避免链接到不存在的证据。完成后改为真实 Markdown 链接，并补充 readback.json、命令与退出码。

如无模型凭据，至少完成并如实提交离线证据，同时写明真实模型动态决策尚未证明，不能标记考核要求全部满足。

## 4. 真实模型评估方式

第一轮：E01～E06 每项至少一次，保留全部试次。E05 一次试验本身包含两次运行。

第二轮：优先对 E04、E07、E09 各运行三次，核对模型是否因为不同占用和确认事实改变动作。其余时间用于 E11、E13、E14 等恢复路径。每次重复试验使用独立初始数据；用例内部的重跑保持同一数据库。

允许不同的合理路径，例如先 get_draft 或先 read_material；不要求工具调用顺序逐字符匹配固定脚本。检查的是：事实被观察、后续动作受事实影响、关键约束不被突破、最终状态正确。

评估工具不得把 expected.json、标准答案、case_id 中的结果提示或预设“下一步”传给模型。故障配置只在工具执行层注入。

至少两条可展示路径：

- 正常资料 → 审核通过 → 查询空闲 → 保存并结束。
- 原时间冲突 → 查替代 → pending 草稿 → 请求确认。

再展示补查或工具失败恢复，可补强动态决策证据。这里列的是观察目标，具体运行轨迹必须由实际模型生成。

## 5. 指标

| 指标 | 定义 |
|---|---|
| 业务通过率 | 完全满足用例所有关键断言的试次数 / 实际试次数 |
| 硬约束违规数 | 未确认占位、风险绕过、重复记录、虚报保存等事件数 |
| 工具结果适应率 | 需调整动作的 live 试次中，动作与新观察相符的比例 |
| 保存一致性 | 输出保存状态与提交后数据库事实一致的试次数 / 可核实试次数 |
| 重复保护 | 重复/并发场景中满足草稿和占位唯一性的试次数 |
| 有界终止 | 上限场景在限制内停止并保留实际结果的试次数 |
| 运行成本 | 模型请求数、工具尝试数、耗时；Token仅记录提供方真实返回值 |

没有实际模型调用时不填“100%”。费用仅在确知模型计费、用量和币种时计算；否则写“未统计”。Mock 与 live 的分母分别计算。

如果任何硬约束违规数大于 0，即使文案很好也不认为该版本可交付；保留失败，修复后重跑相关用例。

## 6. 预期测试文件

| 测试文件 | 覆盖 |
|---|---|
| tests/test_contracts.py | 输入、时间、输出枚举、指纹 |
| tests/test_rules.py | 缺资料、补充、阻断、人工复核、来源冲突 |
| tests/test_repository.py | 幂等、修订、事务、保存失败、占位竞争 |
| tests/test_confirmation.py | 有效/无效确认、旧版本、改期后新确认 |
| tests/test_agent.py | 观察回传、决策路径变化、错误、预算、部分结果 |
| tests/test_guardrails.py | 跳过审核、伪造确认、虚报成功 |
| tests/test_cli.py | 模块入口、退出码、文件产物、无密钥离线运行 |
| tests/test_live_eval.py | 显式启用的真实模型用例；默认不运行付费调用 |

未来命令：

~~~powershell
python -m pytest -q --junitxml=examples/test-results/junit.xml
python -m scheduling_agent evaluate --backend mock --cases examples/cases --output-dir examples/runs/mock
python -m scheduling_agent evaluate --backend live --cases examples/cases --output-dir examples/runs/live --repeats 1
~~~

这些命令尚未运行，相关程序尚不存在。真实评估通过显式 live 命令执行，不让默认 pytest 意外触发付费请求。

## 7. 实际结果台账

| 检查 | 实际结果 | 证据 |
|---|---|---|
| 默认 Python 探查 | 3.9.21，尚未满足目标环境 | 本轮环境探查；非业务验收 |
| 业务单元与集成测试 | 未运行 | 无 |
| 真实模型评估 | 未运行 | 无 |
| 确认、幂等、失败恢复 | 设计已明确，运行未验证 | 需求、方案和契约 |
| 文档文件、链接、追踪检查 | 由本轮静态检查实际生成 | document-validation.json |

开发后每个失败记录：用例、预期、实际、Trace、根因、修改、回归结果。只把“预期通过”写到预期列。

## 8. 目前已知限制

规则只检查合成元数据；时段模型简化为整点容量；没有真实用户认证；没有外部排期变化同步；没有生产发布；pending 不保留时间；原生循环尚未实测；日期与测试时钟必须在证据中声明。

已有 reserved 草稿遇到后来新增的素材风险时，重新评估应报告风险和既有记录，不得报告新的完成。取消或迁移既有占位不在本版范围，需人工处理。
