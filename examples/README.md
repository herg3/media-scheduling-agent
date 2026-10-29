# 运行证据索引

本目录含真实程序生成的记录，Mock与live严格分开。每次运行包括input.json、output.json、trace.jsonl、trace-summary.json、model-messages.json、readback.json和manifest.json。

## 场景定义

cases/有13组输入、预期断言和故障配置：E01正常、E02资料不足、E03规则风险、E04冲突、E05重复、E06保存失败、E07占用变化重跑、E08确认、E09确认时新冲突、E11响应丢失、E13工具瞬断、E14补查恢复、E15批量部分完成。

expected.json仅供评估器断言，不进入模型上下文。E07/E09占用上一轮模型实际建议的时段，changes.json保存前后读回。

## 实际运行

| 批次 | 说明 |
|---|---|
| runs/live/probe-normal-01 | 开发时第一条真实模型正常链路；探索性证据 |
| runs/live/core-attempt-01 | 初轮6场景，5通过1失败；失败保留 |
| runs/live/validated-01 | 提高断言强度后的13场景，9通过4失败；含提示词和测试注入缺陷 |
| runs/live/final-01 | 中间完整回归12/13，E13安全停止未恢复 |
| runs/live/final-02 | 最终完整回归13/13，[汇总](runs/live/final-02/summary.json) |
| runs/live/stability-02 | 最终4场景各3次，12/12，[汇总](runs/live/stability-02/summary.json) |
| runs/live/stability-01 | 首轮4场景各3次，共10/12；失败保留，随后优化版本与重试说明 |
| runs/mock/validated-01 | 早期13场景离线评估，全部满足当时断言 |
| runs/mock/final-01 | 中间13场景离线评估13/13 |
| runs/mock/final-02 | 最终13场景离线评估13/13，[汇总](runs/mock/final-02/summary.json) |
| runs/mock/limit-01 | 达到模型步数上限，明确incomplete；仅离线证据 |

开发中的探索记录可能在代码尚未提交或正在修订时产生，不能用旧source_commit假定精确代码。最终manifest含source_dirty、source_tree_sha256和逐文件哈希以核对来源。模型ID为实际请求的别名qwen-plus，记录日期和提供方；不将别名当不可变模型快照。

## 验证边界

- Mock和FakeModel只证明流程和门禁，不证明真实模型决策。
- live为真实模型调用业务工具，但资料和工具仍是本地合成场景。
- 保存失败、被阻断和待确认是有意保留的业务结果；评估通过表示结果符合预期。
- 失败试次不删除。正式引用以评估报告指定的批次为准。
- .databases/中的本地SQLite文件不提交，readback.json提供结构化状态；从cases重新运行可重建。
- manifest不含密钥或请求Header；model-messages只保留公开动作、参数和工具观察，不含内部思维过程。