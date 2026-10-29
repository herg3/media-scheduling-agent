# 03 实际评估与限制

更新：2026-10-29。下列数字来自实际执行，预期与结果分开。业务资料和工具为本地合成场景；live确实调用了DashScope的qwen-plus，Mock仅验证流程。

## 1. 当前结果

| 验证层 | 实际结果 | 证据 |
|---|---|---|
| 自动化测试 | **74通过，0失败**，25.13秒 | [pytest输出](../examples/test-results/pytest.txt)、[JUnit](../examples/test-results/junit.xml) |
| 最终离线业务评估 | **13/13满足断言** | [Mock汇总](../examples/runs/mock/final-02/summary.json) |
| 最终真实模型完整回归 | **13/13满足严格断言** | [live汇总](../examples/runs/live/final-02/summary.json) |
| 最终稳定性重复 | **12/12满足断言**，四场景各3次 | [稳定性汇总](../examples/runs/live/stability-02/summary.json) |
| 干净环境复现 | 安装和运行按实际日志核对 | [复现记录](../examples/test-results/reproduction.json) |

“评估通过”表示满足场景预期。例如保存失败场景应返回failed并回滚；这不等于排期任务成功。真实模型结果不能用Mock通过率代替。

下面第2～4节为保留的final-01完整回归说明；最新source 0e0004d的验证结果另列第10节。旧失败不因后续成功而覆盖。

## 2. 专项验收映射

每个目录均包含input.json、output.json、trace.jsonl、trace-summary.json、model-messages.json、readback.json及manifest.json。下表直接链接本轮实际输出与公开工具轨迹，不是预写的示例。

| 场景 | 实际结果 | 输出 / 工具轨迹 |
|---|---|---|
| E01正常 | 审核pass；原要求18:00，reserved草稿一份、占位一份；completed | [输出](../examples/runs/live/final-01/E01-attempt-001/run-01/output.json) / [Trace](../examples/runs/live/final-01/E01-attempt-001/run-01/trace-summary.json) |
| E02资料不足 | M02缺授权，补查无结果；needs_input，无草稿 | [输出](../examples/runs/live/final-01/E02-attempt-001/run-01/output.json) / [Trace](../examples/runs/live/final-01/E02-attempt-001/run-01/trace-summary.json) |
| E03规则风险 | M03触发R-FORBIDDEN；blocked，无草稿和占位 | [输出](../examples/runs/live/final-01/E03-attempt-001/run-01/output.json) / [Trace](../examples/runs/live/final-01/E03-attempt-001/run-01/trace-summary.json) |
| E04时段冲突 | 保留原18:00；建议19:00，pending保存，待确认且无占位 | [输出](../examples/runs/live/final-01/E04-attempt-001/run-01/output.json) / [Trace](../examples/runs/live/final-01/E04-attempt-001/run-01/trace-summary.json) |
| E05重复提交 | 第二次already_exists，同draft_id，数据库仍一份草稿和占位 | [第二次输出](../examples/runs/live/final-01/E05-attempt-001/run-02/output.json) / [数据库](../examples/runs/live/final-01/E05-attempt-001/run-02/readback.json) |
| E06保存失败 | SAVE_FAILED有限重试，failed；草稿/占位均0 | [输出](../examples/runs/live/final-01/E06-attempt-001/run-01/output.json) / [Trace](../examples/runs/live/final-01/E06-attempt-001/run-01/trace-summary.json) |

以上已覆盖正常、资料不足、工具失败三类记录以及全部六项专项验收。对应原始输入在同目录input.json，故障配置在examples/cases各场景的fixture.json；expected.json不进入模型上下文。

## 3. 真实动态路径与确认边界

正常链路E01：read_material → check_rules → get_draft（不存在）→ query_slots（原时间空闲）→ save_schedule_draft → finish。

冲突链路E04：query_slots观察18:00占用、19:00空闲后，模型选择保存19:00建议；工具将其保存为pending，effective_at=null。模型不能将建议变成用户已确认的要求。

补查链路E14：主资料缺信息 → read_material_supplement → check_rules → 查询和保存。[实际轨迹](../examples/runs/live/final-01/E14-attempt-001/run-01/trace-summary.json)。

| 边界 | 本轮结果 | 证据 |
|---|---|---|
| E07新增占用后重跑 | 把模型实际建议19:00改为占用；下一轮改选20:00，同ID、revision=2，仍pending。期间模型错误传版本，观察REVISION_CONFLICT后重新读取并修正 | [变化记录](../examples/runs/live/final-01/E07-attempt-001/changes.json)、[后续Trace](../examples/runs/live/final-01/E07-attempt-001/run-02/trace-summary.json) |
| E08显式确认 | 确认具体19:00/版本后同ID晋升reserved，占位一份，仍未发布 | [输出](../examples/runs/live/final-01/E08-attempt-001/run-02/output.json) |
| E09确认时冲突 | 原建议被占用；模型尝试20:00，被CONFIRMATION_MISMATCH拒绝，最终failed，无新增占位。保留旧pending草稿；尚未自动保存新pending建议 | [输出](../examples/runs/live/final-01/E09-attempt-001/run-02/output.json)、[Trace](../examples/runs/live/final-01/E09-attempt-001/run-02/trace-summary.json) |
| E11响应丢失 | 保存返回unknown；模型get_draft核实实际提交后才完成，无重复记录 | [Trace](../examples/runs/live/final-01/E11-attempt-001/run-01/trace-summary.json) |
| E15批量部分结果 | M01成功保留、M03阻断，run_status=partial | [输出](../examples/runs/live/final-01/E15-attempt-001/run-01/output.json) |

这些动作来自真实模型工具调用和工具观察，顺序未写死；执行框架负责协议、预算和不可越过的保存门禁。decision_summary是简短公开动作依据，不包含内部思维过程。

## 4. 首轮未通过的恢复目标（历史）

E13在首次query_slots得到retryable=true的TOOL_UNAVAILABLE后，模型选择finish并请求人工核对。实际为incomplete、not_attempted、0草稿、0占位。它没有虚报保存成功；但严格评估预期是有限重试后completed，因此判失败，保留该断言。[失败输出](../examples/runs/live/final-01/E13-attempt-001/run-01/output.json)、[失败Trace](../examples/runs/live/final-01/E13-attempt-001/run-01/trace-summary.json)。

这是首轮观察到的模型恢复策略稳定性缺口；第10节给出提示词修订后的实测结果。提示词提供有限重试能力，当前没有强制编排接管恢复，也不将安全停止算成自动恢复成功。后续可针对retryable观察优化工具错误说明并扩大重复评估；需要新真实运行证明，不能只改报告。

## 5. 开发失败与改进记录

| 批次 | 结果 | 发现与修改 |
|---|---|---|
| [core-attempt-01](../examples/runs/live/core-attempt-01/summary.json) | 5/6 | E06保存失败事实被后续通用错误掩盖；独立保存save_error，增加回归 |
| [validated-01](../examples/runs/live/validated-01/summary.json) | 9/13 | E04/E08选更早时段；E07第一轮提前结束；E09固定注入19:00未必等于实际建议。改进pending说明和候选偏好，注入实际建议 |
| [final-01](../examples/runs/live/final-01/summary.json) | 12/13 | 六项核心验收通过；保留E13恢复未完成 |

旧批次可能在未提交或修订中的源码上产生，只用于学习改进证据。不能把三个批次合成一个当前通过率，也没有删除失败试次。AI代码审查还发现两个输出问题：新观察证明pending时段占用却仍标is_current，以及最终读回异常丢失已保存项；均添加回归并修复，见[审查记录](09-code-review.md)。

## 6. 自动化边界覆盖

| 原计划场景 | 实际可执行证据 | 范围说明 |
|---|---|---|
| E10同键改参数 | tests/test_repository.py：test_same_key_different_input_is_rejected；CLI测试 | 模型调用前拒绝 |
| E12执行上限 | tests/test_limits.py；test_agent.py；[实际Mock输出](../examples/runs/mock/limit-01/output.json) | 模型/工具/超时有界；晚到响应不能写入 |
| E16并发/绕键 | test_concurrent_connections_preserve_one_draft_and_slot及跨连接测试 | 本地连接竞争，未做生产压力测试 |
| E17伪造完成/授权 | tests/test_guardrails.py | 非法finish、缺证据、过期观察、失败被覆盖、unknown保持 |
| E18无时段 | test_empty_slots_have_explicit_uncompleted_reason | 明确未完成 |
| E19人工复核 | tests/test_rules.py及risk_gate参数化 | 规则层阻断保存；未声称真实模型重复稳定性 |
| E20缺要求/格式 | tests/test_contracts.py、test_cli.py | 业务缺失与格式错误区分 |
| E21来源冲突 | tests/test_rules.py | 不擅选矛盾资料 |
| E22保存时竞争 | tests/test_repository.py | 事务重查/唯一约束/版本校验 |
| E23授权有效期 | test_expiry_must_cover_proposed_time_exclusively | 授权覆盖建议时间 |

没有把所有边界都声称为live实测。JUnit仅将本机hostname字段替换为LOCAL-REDACTED，测试数量、名称、耗时和结果保留，见[脱敏记录](../examples/test-results/redaction.json)。74项测试有参数化用例；数量不是独立业务场景数。测试中的FakeModel故意发出错误动作，用于验证门禁，不计真实模型效果。

## 7. 环境、来源与复现

实际Windows、CPython3.13.14、SQLite3.50.4；依赖精确版本见requirements文件。远端Linux/Python3.12 CI仅配置，未执行。未采用Agent框架，原生循环承担工具协议、观察回传和步数控制；Pydantic负责输入约束，httpx负责HTTP，SQLite负责本地事务。

最终完整回归来源提交fbb4b75ff47579574043c91d0642a08799847b80；每次manifest另含source_file_sha256及source_tree_sha256。source_dirty=true是运行时data/README.md等文档正在同步，不能描述为干净工作区。最终提交核对逐文件业务源码、数据和依赖哈希，证据补交不改业务代码。原始输出哈希也保留在manifest。Windows生成文件有CRLF；最终.gitattributes保留原始字节，避免Git换行转换使已记录的SHA-256失效。与来源提交的8份文件表示差异经归一化核对仅为CRLF/LF，不含业务逻辑改动。归档后再逐项验证原始哈希。

真实模型请求采用qwen-plus别名、temperature=0、单工具动作；模型别名仍可能更新，采样结果不足以保证未来每次一致。记录provider/model_id、公开消息、调用量、运行时间；不保存密钥、Header或内部思维过程。

运行命令见README。重复与确认用例的子运行保留同一SQLite；各试次隔离。数据库文件本身忽略，readback.json保存数量及行状态；cases可重建。

## 8. 限制

只验证合成元数据和本地草稿，不能证明真实视频审核、版权或外部平台发布。pending不占位，可用性是观察快照；保存前事务复查，确认绑定版本和时间。CLI无生产身份认证。SQLite固定最多2秒锁等待，尚未按全局剩余时限缩短。输出使用dict，输入使用严格Pydantic模型。候选人独立复现、个人修改、GitHub远端与评审权限尚待本人完成。


## 9. 首轮稳定性重复（保留失败）

在final-01同一份业务代码上，E04/E07/E09/E13各运行3次，共12次，10次满足断言。

| 场景 | 通过/试次 | 观察 |
|---|---|---|
| E04冲突建议 | 3/3 | 均保存pending、原要求未变 |
| E07新增占用后重排 | 2/3 | 一次两次错传expected_revision=2（当前为1），达到同参数上限，草稿未被错误覆盖 |
| E09确认时新冲突 | 3/3 | 均拒绝原确认授权新时间；无新增占位。安全停止通过，自动保存新建议尚未达成 |
| E13瞬断恢复 | 2/3 | 一次未重试直接停止，两次重试后成功 |

[完整汇总](../examples/runs/live/stability-01/summary.json)。这12次与final-01分开报告。根据错误动作补充expected_revision字段说明和retryable观察处理指引，后续验证另设批次，不覆盖本批记录。


## 10. 最终修订后验证（source 0e0004d）

本轮修改仅澄清expected_revision取工具返回的当前值、不要自行加一；query_slots返回retryable=true时优先在预算内重试一次。没有根据用例ID硬编码动作，没有修改验收断言或确认门禁。

| 批次 | 实际结果 | 证据 |
|---|---|---|
| 最终完整live | 13/13，含六项专项验收和E13恢复 | [summary](../examples/runs/live/final-02/summary.json) |
| 最终重复live | 12/12；E04/E07/E09/E13各3次 | [summary](../examples/runs/live/stability-02/summary.json) |
| 最终Mock | 13/13 | [summary](../examples/runs/mock/final-02/summary.json) |
| 自动化测试 | 74/74；新虚拟环境再次74/74 | [复现stdout](../examples/test-results/reproduction-final.txt) |

最终批次实际输入和输出均位于对应run目录。六项核心记录：[正常](../examples/runs/live/final-02/E01-attempt-001/run-01/output.json)、[资料不足](../examples/runs/live/final-02/E02-attempt-001/run-01/output.json)、[规则风险](../examples/runs/live/final-02/E03-attempt-001/run-01/output.json)、[时段冲突](../examples/runs/live/final-02/E04-attempt-001/run-01/output.json)、[重复](../examples/runs/live/final-02/E05-attempt-001/run-02/output.json)、[保存失败](../examples/runs/live/final-02/E06-attempt-001/run-01/output.json)。

动态决策：[E07占用变化后查到20:00，使用当前版本1更新为版本2](../examples/runs/live/final-02/E07-attempt-001/run-02/trace-summary.json)；[E13观察到TOOL_UNAVAILABLE后重新查询并保存](../examples/runs/live/final-02/E13-attempt-001/run-01/trace-summary.json)。正常、信息不足和故障三类证据、以及至少两条不同观察路径均可定位。

E09仍以CONFIRMATION_MISMATCH安全停止，而没有保存新的pending建议；它通过的是“旧确认不得授权新时间、无新增占位”的断言，不是“自动重排成功”。这属于明确的体验改进项。上述25次场景试次全部符合当前预期，样本有限，不宣称未来模型调用100%可靠；旧批次失败完整保留。

最终来源完整提交0e0004dd68859db931dca5265af59deab870626d。静态交付检查核对本轮所有源文件、数据、依赖和输出哈希。后续本地提交只追加文档/证据及保留换行字节，不改变这些业务文件的内容。
