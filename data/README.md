# 合成资料设计

阶段：已提供可装载的合成 JSON；通过 init-db 显式装载，已有数据库拒绝覆盖。

所有素材、账号、规则和已有排期均人工合成，不使用真实视频、真实用户和真实授权证明。不把试卷页面或完整题面放入数据。

## 实际文件

| 文件 | 内容 |
|---|---|
| materials.json | 主素材元数据 |
| supplements.json | 可查到或查不到的补充资料 |
| rules.json | 版本化、可执行的元数据规则 |
| schedules.json | 已有占用时段 |
| fixture-manifest.json | 内容版本、来源声明、测试时钟 |

## 素材与规则设计

| 素材 | 主资料 | 补充资料 | 预期用途 |
|---|---|---|---|
| M01 | 标题、时长、有效授权齐全，无禁用标签或宣称 | 无需补充 | 正常、冲突、重复、保存失败 |
| M02 | 缺 copyright_status | 没有对应资料 | 信息不足后请求补充 |
| M03 | claims 包含合成禁用的“保证效果”表述 | 无法解除该风险 | 规则阻断 |
| M04 | 主资料缺授权状态 | 有有效的合成授权记录 | 模型补查后继续 |
| M05 | 需要人工判断的合成宣传措辞 | 无人工复核结论 | needs_review，不接受时间确认覆盖 |
| M06 | 主资料缺授权状态 | 两条补充分别声称 authorized 与 rejected | 请求人工核对，不擅自选一份 |

实际规则：

- R-REQUIRED：title、duration_seconds 或 copyright_status 缺失（或时长非法）→ needs_information；severity=MISSING。
- R-AUTH：授权拒绝、非 authorized 状态或明确过期 → blocked；severity=BLOCK。
- R-FORBIDDEN：claims 命中 guaranteed cure 或 100% effective → blocked。
- R-REVIEW：claims 命中 preliminary benefit 或 results may vary → needs_review；severity=REVIEW。

规则实际包含 rule_id、kind、fields 或 pattern、severity、message、enabled；没有独立 version 或来源字段。版本由数据库规则集合摘要计算。匹配函数由程序执行；不让模型通过自报“通过”跳过规则。

## 固定时间与隔离

用例采用业务日期 2026-11-02，测试时钟固定为 2026-11-01 09:00 Asia/Shanghai。后续运行超过该日期时，使用明确记录的测试时钟，或整体更新 fixture 并重新生成证据。真实模型评估同样在 manifest 中声明合成时钟，不能把历史时间伪装为当前业务请求。

每个独立用例使用临时数据库；重复、修订、确认、并发用例有意共享同一个数据库。初始化只执行一次，重复运行不得清库伪造幂等。

替换占用时段的测试使用测试装配层更新 slots；更新前后快照及变更命令存入用例证据。真实模型只接收工具观察，不接收“应该选哪个时间”的预期答案。

## JSON 装载格式

materials.json 是 {material_id, metadata, source_id} 列表；supplements.json 是 {supplement_id, material_id, fields, source_id} 列表；rules.json 是上述定义列表；schedules.json 默认空列表，场景占用通过独立评估数据库注入，记录格式为 {channel, account_id, slot_start, owner_id}。fixture-manifest.json 保存 version=synthetic-v1、synthetic=true、synthetic_clock 和 business_date。

当前初始化仅将 manifest JSON 存入 fixture_meta，并未存完整资料内容摘要。素材读取版本由主资料及已采用补充内容计算；规则版本由定义集合计算。运行 manifest 记录 data/*.json 与源码的 SHA-256，供证据追溯。
