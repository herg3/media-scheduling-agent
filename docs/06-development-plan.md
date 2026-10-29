# 素材审核与发布排期 Agent 开发计划

> For agentic workers: 后续实施使用 executing-plans，按任务逐项实现和检查。本轮只交付文档，不启动实现；没有授权分派子 Agent。

**Goal:** 完成真实模型驱动、边界清楚、可运行和可验证的素材审核与排期草稿 CLI。

**Architecture:** 原生单 Agent 循环协调六项工具；确定性策略检查审核、确认和证据；SQLite 事务保证草稿与占位一致。独立 Mock 适配器支持无密钥运行，真实模型路径单独评估。

**Tech Stack:** Python 3.12、Pydantic 2 系列、httpx、tzdata、SQLite、pytest；依赖精确版本在目标环境中锁定。

## 1. 全局约束

- 只保存草稿，无真实发布入口。
- 每请求 1～3 项素材；Asia/Shanghai；整点时段；窗口左闭右开。
- 默认 24 次模型请求、20 次工具尝试、180 秒，所有重试计数。
- 相同 request_id + material_id 草稿唯一；pending 不占位。
- 所有状态、字段和错误码遵循 [接口契约](07-contracts.md)。
- 文档中的测试预期不当作运行结果；生成证据后才填写实际结果。
- 构建、测试、真实模型和候选人复核分别记录。
- 不安装未确认来源的软件，不将真实密钥写入仓库或聊天。

## 2. 预计时间和删减顺序

以下是后续开发的预算，不是已投入时间，也不保证首次开发能在三小时内完成。

| 任务 | 预算分钟 | 优先级 |
|---|---:|---|
| 1 环境、Schema、合成资料 | 20 | P0 |
| 2 规则、时间与确认策略 | 20 | P0 |
| 3 SQLite 持久化与事务 | 30 | P0 |
| 4 工具与循环、Mock | 30 | P0 |
| 5 真实模型适配与路径验证 | 25 | P0 |
| 6 CLI、确认与端到端场景 | 20 | P0 |
| 7 评估与必要修复 | 20 | P0 |
| 8 文档、个人复核与交付检查 | 15 | P0 |
| 合计 | 180 | 按实际超时记录 |

时间不足时依次暂缓：提交后响应丢失恢复、并发压力扩展、额外重复试次。不得删减六项验收、真实模型循环、执行上限、待确认边界、幂等和明确保存失败。P0未完成就如实列为未完成，不用 Mock 冒充 live。

## 3. 目标目录

~~~text
README.md
pyproject.toml
requirements.txt
requirements-dev.txt
.env.example
.gitignore
src/scheduling_agent/
  __init__.py
  __main__.py
  contracts.py
  agent.py
  model.py
  prompts.py
  tools.py
  repository.py
  policy.py
  evidence.py
  fixtures.py
  cli.py
tests/
  conftest.py
  test_contracts.py
  test_rules.py
  test_repository.py
  test_confirmation.py
  test_agent.py
  test_guardrails.py
  test_cli.py
  test_live_eval.py
data/
examples/
docs/
~~~

work/ 放虚拟运行文件和本地数据库，默认忽略；examples/ 只提交经脱敏且可复核的选定证据。根目录代码、测试、依赖文件本轮尚未创建。

## 4. 公共接口边界

以下为待实现签名，字段对应契约，不是现成代码：

~~~python
# contracts.py: Pydantic 模型
# SchedulingRequest, SchedulingItem, PublishRequirement, RunResult,
# ItemResult, ToolCall, ToolObservation, ModelAction, ConfirmationContext

# policy.py
def fingerprint_request(request: SchedulingRequest) -> str: ...
def evaluate_rules(material: dict, rules: list[dict], now: datetime) -> dict: ...
def is_originally_authorized(requirement: PublishRequirement, proposed_at: datetime) -> bool: ...

# repository.py
class Repository:
    def register_request(self, request: SchedulingRequest) -> None: ...
    def read_draft(self, request_id: str, material_id: str) -> dict | None: ...
    def save_draft(self, command: dict, context: ToolContext) -> ToolObservation: ...

# tools.py: ToolContext 为 dataclass，含契约中可信上下文，不能从模型参数创建
def execute_tool(call: ToolCall, context: ToolContext) -> ToolObservation: ...

# model.py
class ModelAdapter(Protocol):
    def next_action(self, messages: list[dict], tools: list[dict], timeout_seconds: float) -> ModelAction: ...

# agent.py
def run_agent(request: SchedulingRequest, model: ModelAdapter, context: ToolContext) -> RunResult: ...
~~~

类型均由对应文件定义和导出，禁止测试和 CLI 各自维护一套同名字段。实现中可调整签名，但必须同步契约、调用方与测试，记录原因。

## 5. 任务一：环境与请求契约

文件：pyproject.toml、requirements.txt、requirements-dev.txt、.gitignore、src/scheduling_agent/contracts.py、policy.py、tests/test_contracts.py、data/ 下合成 JSON。

- [ ] 探查可用 Python 3.12；若缺失先配置独立解释器，不改系统默认 Python。
- [ ] 创建虚拟环境，安装依赖，在干净环境验证并写入确切版本。
- [ ] 先写输入和指纹测试：EXACT/WINDOW、缺要求、重复素材、未知字段、半点、错误时区、同键变更。
- [ ] 运行测试确认针对未实现行为失败；实现 Schema、规范化和指纹。
- [ ] 按 data/README.md 生成 M01～M06、规则和排期；禁止模型接收测试预期。
- [ ] 运行本任务测试通过后记录命令、结果和差异。

核心测试示意，使用项目真实入口：

~~~python
def test_same_business_input_has_stable_fingerprint(normal_request):
    first = SchedulingRequest.model_validate(normal_request)
    second = SchedulingRequest.model_validate(dict(normal_request))
    assert fingerprint_request(first) == fingerprint_request(second)

def test_changed_requirement_changes_fingerprint(normal_request):
    first = SchedulingRequest.model_validate(normal_request)
    changed = first.model_copy(deep=True)
    changed.items[0].requirement.requested_at += timedelta(hours=1)
    assert fingerprint_request(first) != fingerprint_request(changed)
~~~

normal_request 在 tests/conftest.py 由 E01 输入建立；字段必须与契约一致。未来验证命令：python -m pytest tests/test_contracts.py -q。交付判断：合法请求可解析，非法输入可定位，指纹只基于规范化业务输入。

## 6. 任务二：规则、时段与确认策略

文件：policy.py、contracts.py、tests/test_rules.py、tests/test_confirmation.py。

- [ ] 先测试授权缺失、补查成功、确定风险、软风险、来源冲突及 BLOCK 优先。
- [ ] 实现共享资料合并和审核函数，工具与保存门禁共用。
- [ ] 测试 EXACT、窗口边界、过去时间、非整点，禁止自动改时间。
- [ ] 测试确认绑定 revision、proposal_hash 和具体时间；仅时间确认不能解除风险。
- [ ] 实现策略并运行相关测试，记录未通过项。

~~~python
def test_window_end_is_not_authorized(window_requirement):
    assert is_originally_authorized(
        window_requirement,
        window_requirement.window_end,
    ) is False

def test_hard_risk_is_blocked(risky_material, rules, fixed_now):
    result = evaluate_rules(risky_material, rules, fixed_now)
    assert result["review_status"] == "blocked"
    assert any(item["rule_id"] == "R03" for item in result["findings"])
~~~

fixtures 在 conftest.py 从合成资料加载，fixed_now 为 2026-11-01 09:00 +08:00。命令：python -m pytest tests/test_rules.py tests/test_confirmation.py -q。交付判断：风险、时间与确认权限有确定性断言。

## 7. 任务三：持久化与幂等保存

文件：repository.py、fixtures.py、tests/test_repository.py。

- [ ] 建 SQLite 表与约束，开启外键；显式初始化 fixture，重跑不清库。
- [ ] 先写同请求重复、同键不同输入、pending修订、reserved唯一、事务失败测试。
- [ ] 实现 register_request、read_draft、save_draft；短事务内重查资料、规则和冲突。
- [ ] 注入提交前失败，断言草稿和占位都未写入。
- [ ] 用两个连接制造“查询后新占用”，断言无双占位。
- [ ] 核心完成后加提交后响应丢失和跨进程重复验证。

实际断言必须包括：

~~~python
assert first["draft_id"] == repeated["draft_id"]
assert draft_count == 1
assert reserved_slot_count in (0, 1)
assert before_commit_failure["save_status"] == "failed"
assert rolled_back_draft_count == 0
assert rolled_back_slot_count == 0
~~~

上面变量由测试直接调用真实 Repository 并 SELECT COUNT(*) 获得，不通过 Mock 仓库伪造。命令：python -m pytest tests/test_repository.py -q。交付判断：数据库事实支持输出，重复不会产生第二份，竞争失败可解释。

## 8. 任务四：工具、循环与可信输出

文件：tools.py、agent.py、model.py、prompts.py、evidence.py、tests/test_agent.py、tests/test_guardrails.py。

- [ ] 注册六项工具和 finish；Schema 严格排除 confirmed、force、published。
- [ ] 先用 FakeModel 验证观察传递和循环停止，再实现通用执行循环。
- [ ] 每次模型下一轮收到上一轮真实工具结果和关联调用 ID。
- [ ] 实现 tool/model/time 三种上限、同参数最多两次、非法动作两次终止。
- [ ] 用同一 Mock 适配器配不同工具观察，验证下一步动作不同。
- [ ] 用不可信 FakeModel 尝试跳过审核、伪造保存、确认越权，验证输出依据事实。
- [ ] 生成 JSONL 与结果文件，写入 backend 标识，完成单项和批量部分结果测试。

应检查而非仅断言文本相同：

~~~python
assert "tool" in [message["role"] for message in recorded_messages]
assert failed_run.items[0].draft.save_status == "failed"
assert looping_run.termination_reason == "TOOL_CALL_LIMIT"
assert looping_run.usage.tool_calls_used <= configured_limit
~~~

recorded_messages 由 RecordingModelAdapter 捕获 next_action 的真实入参；在测试文件内定义，不用未声明的全局对象。命令：python -m pytest tests/test_agent.py tests/test_guardrails.py -q。交付判断：循环、门禁、预算与状态可复核；Mock 边界清楚。

## 9. 任务五：真实模型接入

文件：model.py、prompts.py、tests/test_live_eval.py、.env.example。

- [ ] 使用本机已有或用户自行配置的合法凭据；不从聊天收集密钥。
- [ ] 选一个经官方文档支持并实测可用的工具调用模型；记录实际提供方、ID和参数。
- [ ] 先验证最小“调用工具→回传结果→下一动作”，排除仅输出工具名称的假调用。
- [ ] 接入同一业务工具，运行 E01 和 E04；保存失败试次。
- [ ] 若失败，先检查协议、工具描述、观察是否正确传回，再改提示词；记录实际修改。
- [ ] live 不可用则明确记录；不自动降级、不称完成真实模型要求。

命令：python -m scheduling_agent evaluate --backend live --cases examples/cases --case-ids E01,E04 --output-dir examples/runs/live --repeats 1。此命令需在任务六 CLI 已可用后执行；若先验证适配器，直接使用显式 live 测试入口。交付判断：至少两条模型实际路径，工具观察后有可定位的下一动作。

## 10. 任务六：CLI 与确认闭环

文件：cli.py、__main__.py、tests/test_cli.py、tests/test_confirmation.py。

- [ ] 实现 init-db、run、confirm、show-draft、evaluate，帮助信息说明仅草稿；init-db拒绝覆盖已有数据库。
- [ ] 按契约输出退出码，确保异常也产生可读失败结果和已完成项。
- [ ] 运行无密钥 Mock 命令，检查 input/output/trace/readback/manifest 文件。
- [ ] 确认 E04 的具体草稿版本；核对同 ID、reserved 状态和占位。
- [ ] 新增占用后确认旧建议，验证不得将旧确认套给新建议。
- [ ] 重复运行保持同一数据库，验证幂等；不靠删除数据库使测试通过。

命令：python -m pytest tests/test_cli.py tests/test_confirmation.py -q。交付判断：评审可用模块命令完整复现，确认入口确实生效而非文档说明。

## 11. 任务七：评估、修复与证据

文件：examples/cases、examples/runs、examples/test-results、docs/03-evaluation.md。

- [ ] 运行完整离线测试与 E01～E06，保留 stdout、JUnit 和退出码。
- [ ] 显式运行 live E01～E06，至少保留正常、信息不足、工具失败三类实际记录。
- [ ] E04/E07/E09 作为优秀目标重复三次；不得只保留成功试次。
- [ ] 自动核对输出状态、数据库数量、原要求、确认与风险，不把固定动作序列当唯一答案。
- [ ] 对失败登记根因、实际修改和回归结果；受影响场景重跑。
- [ ] 生成分后端汇总，填写指标分母和缺口。

命令以评估文档为准；预期不写固定通过数，实际收集数量后再报告。交付判断：验收点有对应真实文件，声明与证据一致。

## 12. 任务八：文档同步与交付

文件：README、docs/01～08、依赖锁、.env.example、examples。

- [ ] 将 README 中“设计命令”替换成实际验证命令、解释器与依赖版本。
- [ ] 将评估中的计划路径改为真实文件链接；保留未完成项。
- [ ] 如实填写 AI 辅助范围、候选人实际修改、独立复现结果和工时。
- [ ] 在干净环境从 README 跑一次；扫描密钥、隐私、完整试卷和本机绝对路径。
- [ ] 进行候选人代码走读：冲突、确认、幂等、失败各解释一个例子。
- [ ] 按提交清单建立或更新 GitHub 仓库；验证评审可读权限；记录最终40位SHA。

尚未授权向外部发布，本轮不建立远端或上传。未来正式提交前按保密许可决定私有仓库与指定评审权限，不公开原始试卷。

## 13. 进入实现前的文档核对

实现时首先核对本版产品假设：整点容量、pending不占位、EXACT需具体确认、已确认草稿不自动迁移。若调整这些假设，同步需求、契约和测试，不能只改提示词。

本轮交付完成条件仅为需求和开发文档可读、可追踪且无伪造结果；业务实现的完成条件另见需求与评估。
