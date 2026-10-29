# 07 接口与数据契约

状态：设计。此文档是字段、枚举、时间语义和错误码的权威定义。所有 JSON 片段都是规格示例，不是运行输出。

## 1. 输入

~~~json
{
  "request_id": "REQ-DEMO-001",
  "channel": "short_video",
  "account_id": "account-demo",
  "timezone": "Asia/Shanghai",
  "items": [
    {
      "material_id": "M01",
      "requirement": {
        "mode": "EXACT",
        "requested_at": "2026-11-02T18:00:00+08:00"
      }
    }
  ]
}
~~~

| 字段 | 约束 |
|---|---|
| request_id | 1～64 字符；字母、数字、连字符、下划线；不得包含个人身份信息 |
| channel | 本版仅 short_video |
| account_id | 1～64 字符，限定合成账号 |
| timezone | 本版仅 Asia/Shanghai |
| items | 1～3 项，material_id 不重复；顺序决定处理优先级 |
| material_id | 合成资料 ID，不接受文件路径或 URL |
| requirement | 可以为 null，表示发布要求缺失；结构存在时严格校验 |

EXACT 必须且只包含 mode、requested_at。WINDOW 必须且只包含 mode、window_start、window_end：

~~~json
{
  "mode": "WINDOW",
  "window_start": "2026-11-02T18:00:00+08:00",
  "window_end": "2026-11-02T21:00:00+08:00"
}
~~~

窗口为左闭右开，即允许 18:00、19:00、20:00。时分秒必须整点，时间偏移为 +08:00，与本版时区一致；不得默默将 18:30 四舍五入。过去时间通过业务校验返回需补充/调整要求；测试时钟由测试装配层固定并记录，模型不能修改。

输入 JSON 中出现未知字段即拒绝。模型后端、预算、故障配置和输出路径属于 CLI 运行配置，不属于业务输入。

## 2. 请求指纹与建议指纹

request_fingerprint = SHA-256(规范化业务 JSON 的 UTF-8 字节)：

1. 规范化合法时间至 UTC 表示，移除无意义空白。
2. 对象键排序、固定 JSON 分隔符；保留 items 顺序。
3. 纳入 request_id、channel、account_id、timezone、items 全部业务字段。
4. 不纳入 backend、模型 ID、Trace 路径、测试时钟或故障配置。

同一 request_id 不同指纹：IDEMPOTENCY_CONFLICT，不调用模型、不写草稿。

proposal_hash = SHA-256(规范化的 request_fingerprint、material_id、proposed_at、material_version、rules_version)。候选时间或审核事实改变，哈希改变，pending 草稿的 revision 增加。

时间确认绑定到旧 revision / proposal_hash 时，不适用于新版本。

## 3. 输出与状态

输出结构包括 run_id、backend、run_status、termination_reason、items、usage、trace_path、warnings。每项至少包含：

| 字段 | 定义 |
|---|---|
| material_id | 当前素材 |
| task_status | completed / awaiting_confirmation / needs_input / blocked / failed / incomplete |
| review_status | unknown / pass / needs_information / needs_review / blocked |
| risks | rule_id、severity、field、message、source_id 列表 |
| original_requirement | 原输入，不能替换成建议时间 |
| suggested_at | 建议时间；未形成建议时为 null |
| effective_at | 仅 reserved 且当前验证有效时有值；pending 时必须为 null |
| confirmation_status | not_applicable / not_required / pending / confirmed |
| pending_questions | 字段缺失或改期需要用户确认的问题 |
| draft | draft_id、revision、reservation_status、save_status、error_code、is_current |
| evidence_refs | 对应工具调用 ID、素材/规则版本和读回证明 |
| message | 依据可信状态生成的用户可读说明 |

reservation_status：none / pending / reserved。

save_status：not_attempted / saved / already_exists / failed / unknown。

saved 表示本轮提交后读回成功；already_exists 表示核实既有记录且未进行新写入；failed 表示已知本次操作未提交；unknown 表示是否提交尚未核实。先前已保存草稿与本次更新失败可以同时出现：保留旧 draft_id / revision，save_status=failed，task_status 不得为 completed。

is_current 表示草稿是否适用于当前已核实事实。旧建议已被占用、事实版本变化或新建议未保存时为 false；尚未核实为 null。suggested_at 可以是本轮新建议，但旧 draft 必须 is_current=false，不能把旧草稿 ID 当作新建议已保存的凭证。

示例：冲突后的预期字段片段，**不代表已经运行**：

~~~json
{
  "material_id": "M01",
  "task_status": "awaiting_confirmation",
  "review_status": "pass",
  "original_requirement": {
    "mode": "EXACT",
    "requested_at": "2026-11-02T18:00:00+08:00"
  },
  "suggested_at": "2026-11-02T19:00:00+08:00",
  "effective_at": null,
  "confirmation_status": "pending",
  "pending_questions": ["是否同意将此素材的建议时间调整为 2026-11-02 19:00？"],
  "draft": {
    "draft_id": "SPEC-ONLY-DRAFT",
    "revision": 1,
    "reservation_status": "pending",
    "save_status": "saved",
    "error_code": null,
    "is_current": true
  }
}
~~~

批量 run_status 规则：

- 所有 items 均 completed：completed。
- 至少一项 completed，且至少一项未完成：partial。
- 其他情况：incomplete。

termination_reason 描述全局停止原因，例如 FINISHED、MODEL_UNAVAILABLE、MODEL_STEP_LIMIT、TOOL_CALL_LIMIT、DEADLINE_EXCEEDED。单项的业务原因放在其 message 和 error_code，不被全局状态覆盖。

CLI 退出码设计：0=全部完成，2=业务待确认/待补充/阻断，3=输入或幂等冲突，4=工具/模型失败或执行上限，5=部分完成。仍以 output.json 逐项状态为准。

优先规则：语法/幂等错误为3；一旦有已完成项且非全部完成为5；其余未完成若有故障/预算原因则为4，否则为2。该优先级避免混合输入的退出码被最后一个素材覆盖。

输入无法形成合法请求时使用独立错误信封：run_id、backend、run_status=incomplete、error={code,message}、items=[]。不伪造不存在的 material_id。请求登记失败前不调用业务模型。

状态生成顺序：阻断风险优先 blocked；资料/人工复核未解决为 needs_input；当前保存失败为 failed；上限或服务不可用使当前任务为 incomplete；有效pending且未失败为 awaiting_confirmation；只有当前审核、授权、占位和提交全部满足才为 completed。未知保存结果为 incomplete，草稿旧状态仍单列保留。全局终止不把已经 completed 的其他项改成失败。

### 草稿状态转换

| 原状态 | 动作与条件 | 新状态 |
|---|---|---|
| none | 审核通过、原授权内、事务查空闲并提交 | reserved，revision=1 |
| none | 审核通过、授权外建议、事务查空闲并提交 | pending，revision=1 |
| pending | 同内容重复保存 | pending，版本不变，already_exists |
| pending | 新建议、expected_revision正确 | pending，revision+1，旧确认失效 |
| pending | 完全匹配的显式确认、事实有效、空闲 | reserved，revision+1 |
| pending | 确认旧版本/新冲突/风险 | 不占位；保留旧行并报告当前未完成 |
| reserved | 同内容、事实仍有效的重复提交或确认 | reserved，同ID及版本，already_exists |
| reserved | 模型要求变更时间或新风险 | 不自动迁移；当前结果未完成，保留历史记录 |

风险和保存失败不会删除已有草稿。历史记录不代表当前审核仍通过，输出必须表明是否仍适用。

## 4. 模型动作与观察

一个模型响应只能包含一个工具调用或 finish 动作。每个调用须带简短 decision_summary，说明动作目的，不能要求内部思维过程。

工具观察统一为：

~~~json
{
  "tool_call_id": "call-001",
  "ok": true,
  "data": {},
  "error": null,
  "evidence": {
    "source_ids": [],
    "material_version": null,
    "rules_version": null,
    "observed_at": "2026-11-01T09:00:00+08:00"
  }
}
~~~

失败时 ok=false，data 可包含已核实的部分信息；error={code,message,retryable}。不将空结果与网络失败混为一谈。观察摘要可截短日志中的长字段，模型收到的业务字段不能因日志截断而丢失。

工具上下文由程序注入：run_id、request_id、当前素材、目标账号、时钟、剩余预算、可信确认及数据库连接。模型不能在参数中覆盖账号、请求号或确认。

## 5. 六项工具

### read_material

参数：material_id、decision_summary。

结果：metadata、missing_fields、material_version、source_id。metadata 包含 title、duration_seconds、copyright_status、authorization_expires_at、content_tags、claims、supplement_available。

未知素材：MATERIAL_NOT_FOUND。资料字段为空时返回成功观察及 missing_fields，不伪装为工具故障。

### read_material_supplement

参数：material_id、fields、decision_summary。fields 为限定字段名列表，不能是查询代码。

结果：found_fields、missing_fields、source_ids、material_version。无补充资料返回成功且 found_fields 为空。补充与主资料矛盾时返回 SOURCE_CONFLICT，模型只能请求人工核对。

### check_rules

参数：material_id、decision_summary。模型不能传入自制素材或自报审核状态。

结果：review_status、findings、material_version、rules_version、valid_until。findings 每条含 rule_id、severity、field、message、source_id。valid_until 为不包含的有效期终点，建议必须满足 proposed_at < valid_until；无期限限制为 null。模型选择建议时应参考，保存门禁仍逐条核实。

工具读取主资料及此前由补查工具发现的补充资料。保存门禁使用相同的合并器与规则函数，并直接核实当前数据库事实。

### query_slots

参数：material_id、search_start、search_end、decision_summary；起止时间格式同输入，范围最多 24 小时。资源来自可信请求上下文。

结果：requested_slot、requested_slot_available、available_slots、occupied_slots、schedule_version。候选按时间升序返回，每次最多 6 个；如仍有更多，返回 has_more。这里只查询，不占位。

已存在的同素材 reserved 草稿返回 existing_draft_id，避免模型另建重复排期。

### get_draft

参数：material_id、decision_summary。

结果：草稿的完整可信字段、当前 revision、proposal_hash、reservation_status。没有记录为 DRAFT_NOT_FOUND，且 retryable=false。用于重跑时读取已有草稿、保存不确定时核实结果。

### save_schedule_draft

参数：material_id、proposed_at、expected_revision、decision_summary。首次保存 expected_revision=null，更新 pending 草稿时必须是实际旧版本。

程序自行判断：

- 原授权内且审核通过：reserved。
- 超出原授权且无有效确认：pending。
- 超出原授权且存在完全匹配的可信确认：重新检查后 reserved。
- 风险、缺资料、无审核证据、过期确认或冲突：拒绝。

结果：save_status、draft_id、revision、proposal_hash、reservation_status、effective_at、pending_questions、is_current。

所有新保存或修订都在事务中检查建议时间仍空闲、授权有效期覆盖该时间；pending不写占位，因此提交后仍可能被其他请求占用。reserved同内容重跑核实的是属于自身的占位，不把自身占位当作外部冲突。

当前运行必须有 read_material、check_rules 和包含该建议时段的 query_slots 证据。版本过期或从未查询过该时段时，保存返回 FACTS_CHANGED 或 MISSING_SLOT_EVIDENCE，要求模型重新观察。程序重查是并发安全检查，不能替代模型接收工具结果的要求。

不提供 confirmed、published、force、override_rules 参数。不可接受模型传入任意草稿 ID、SQL 或文件路径。相同内容重复保存返回 already_exists；pending 更新比较 expected_revision；reserved 不可自动迁移。

### finish 控制动作

参数：decision_summary、suggested_questions。它不是业务工具，不计工具次数，但占用一次模型请求。程序通过台账生成最终状态；模型声称完成而证据不足时，输出 incomplete 和 OUTPUT_GROUNDED_BY_EVIDENCE 警示，保留原响应以供评估。

## 6. 确认命令

以下仅为将来接口：

~~~powershell
python -m scheduling_agent confirm --draft-id DRAFT-001 --revision 1 --proposed-at "2026-11-02T19:00:00+08:00" --backend live --db work/live.sqlite --output-dir work/confirm-DRAFT-001
~~~

命令入口读取草稿和原请求，构造不可由模型编辑的 ConfirmationContext：

~~~text
request_id, material_id, draft_id, revision,
proposal_hash, proposed_at_utc, source="cli",
confirmed_at
~~~

只确认命令明确指定的时间。确认旧版本、错误时间或错误草稿时，在模型执行前返回 CONFIRMATION_MISMATCH。确认期间新的事实变化或占用变化使保存失败；程序不能把对 19:00 的确认套到 20:00。

already reserved 且参数完全匹配的重复确认返回原记录，不再占位。时间确认不解除 needs_review / blocked。

## 7. SQLite 结构及约束

数据只在显式 init-db 命令中从合成 JSON 装载；已有数据库拒绝覆盖。重跑不自动清库或重置排期；修改测试场景使用独立数据库或测试专用事务更新，且记录变化。业务工具不修改素材和规则。

init-db 将 fixture-manifest.json 的版本、内容摘要、合成时钟存入 fixture_meta。run/confirm 从该元信息注入工具时钟，并在输出和manifest明确标记 synthetic_clock；它不是系统当前时间，也不能由模型修改。evaluate 每个独立试次初始化一次，同一试次的重复与确认保留数据库。

| 表 | 关键字段与约束 |
|---|---|
| materials | material_id 主键，metadata_json，source_id，version |
| supplements | supplement_id 主键，material_id 外键，fields_json，source_id，version |
| rules | rule_id 主键，definition_json，version，enabled |
| fixture_meta | singleton_key 主键，manifest_json，synthetic_clock，content_hash |
| requests | request_id 主键，fingerprint NOT NULL，input_json，created_at |
| drafts | draft_id 主键，request_id 外键，material_id 外键，revision，proposed_at_utc，reservation_status，proposal_hash，material_version，rules_version，confirmation_json，updated_at |
| slots | channel，account_id，slot_start_utc，owner_type，owner_id |

必须具备的约束：

~~~sql
CREATE UNIQUE INDEX uq_draft_request_material
ON drafts(request_id, material_id);

CREATE UNIQUE INDEX uq_slot_resource
ON slots(channel, account_id, slot_start_utc);

CREATE UNIQUE INDEX uq_reserved_material
ON drafts(channel, account_id, material_id)
WHERE reservation_status = 'reserved';
~~~

drafts 同时存 channel、account_id，必须由原请求注入并与原请求一致；其余完整建表语句在实现时生成。字段均采用 NOT NULL / CHECK 约束与必要索引，pending 草稿不能写 slots。

slots 的 owner_type=existing_schedule 或 draft；owner_id 指向合成已有排期 ID 或 draft_id。草稿和占位在同一事务内写入；启用 PRAGMA foreign_keys=ON。draft 所有者的完整性除外键可表达的部分外，也由事务代码验证，不能声称 owner_id 的多态引用全部由外键保证。

版本说明：material_version 是规范化主资料加已采用补充资料的内容摘要；rules_version 是启用规则集合摘要；schedule_version 是当前资源占用快照摘要。它们是可复核版本，不使用每次变化的时间戳充当内容版本。

## 8. 配置契约

未来 .env.example 只列名称与安全示例，不包含真实密钥；程序直接读取环境变量，不要求自动加载 .env。

| 名称 | 含义与默认值 |
|---|---|
| LLM_API_KEY | live 必填；mock 不读取 |
| LLM_BASE_URL | live 必填；来自实际提供方配置 |
| LLM_MODEL | live 必填；记录实际模型 ID |
| LLM_TIMEOUT_SECONDS | 默认 30，上限受剩余总时间限制 |
| MAX_MODEL_STEPS | 默认 24 |
| MAX_TOOL_CALLS | 默认 20 |
| MAX_RUNTIME_SECONDS | 默认 180 |

不通过配置暴露 force_confirm、skip_review 或 publish。live 配置缺失时明确失败，不降级 Mock。内部故障注入只能在测试装配层或显式测试场景启动，模型不能调用。

运行依赖计划：Pydantic 2 系列、httpx、tzdata。测试依赖计划：pytest。精确版本、完整锁定文件和哈希/来源在实现环境中确定，当前不虚构已验证版本组合。

## 9. 错误码

| 类别 | 错误码 | 后续行为 |
|---|---|---|
| 输入 | INVALID_INPUT、UNSUPPORTED_TIMEZONE、IDEMPOTENCY_CONFLICT | 拒绝并说明，不调用模型 |
| 资料 | MATERIAL_NOT_FOUND、SOURCE_CONFLICT | 请求补充/核对，不保存 |
| 审核 | REVIEW_REQUIRED、RULE_BLOCKED、MISSING_REVIEW_EVIDENCE、MISSING_SLOT_EVIDENCE | 不占位，补齐所需观察 |
| 确认 | CONFIRMATION_MISMATCH、CONFIRMATION_REQUIRED | 请求具体确认 |
| 排期 | SLOT_CONFLICT、NO_AVAILABLE_SLOT、MATERIAL_ALREADY_RESERVED | 改查候选或明确未完成 |
| 修订 | REVISION_CONFLICT、FACTS_CHANGED、CONFIRMED_DRAFT_IMMUTABLE | 重读；禁止自动迁移已确认草稿 |
| 保存 | SAVE_FAILED、SAVE_OUTCOME_UNKNOWN、DB_BUSY | 核实或有限重试，不虚报成功 |
| 查询 | DRAFT_NOT_FOUND、TOOL_UNAVAILABLE | 按是否瞬时失败决定恢复 |
| 模型 | MODEL_UNAVAILABLE、INVALID_MODEL_ACTION | 明确未完成，保留已保存结果 |
| 上限 | MODEL_STEP_LIMIT、TOOL_CALL_LIMIT、DEADLINE_EXCEEDED | 停止新动作，返回实际部分结果 |

## 10. Trace 最小格式

trace.jsonl 每行一个事件，包括：

~~~text
schema_version, run_id, case_id, backend, provider, model_id,
step, event_type, tool_call_id, tool, arguments,
observation_summary, evidence_versions,
decision_summary, next_action, elapsed_ms,
model_steps_used, tool_calls_used, error_code
~~~

事件类型：run_started、model_action、tool_result、run_finished、protocol_error。

工具结果事件的 next_action 在模型下一次响应到达时关联填充为另一个事件引用；上限结束时指向 termination。不预先写入“下一步”。原始事件保持追加，关联索引写入 trace-summary.json，避免修改原始 JSONL。

另外保存 model-messages.json：仅用户合成输入、公开动作说明、工具调用和返回，不含 API Header、密钥和模型内部思维过程。每条工具响应关联真实 tool_call_id。原始服务响应不直接完整落盘，以免携带不需要的敏感字段。
