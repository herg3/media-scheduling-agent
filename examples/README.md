# 执行证据目录规范

**当前没有实际运行证据。** 本文件只定义未来证据结构；不将设计中的预期输出或人工写的路径标为真实 Trace。

每组用例采用：

~~~text
examples/
  cases/
    E01/
      input.json
      expected.json
      fixture.json
  runs/
    mock/
      E01/attempt-01/
    live/
      E01/attempt-01/
        input.json
        output.json
        trace.jsonl
        trace-summary.json
        model-messages.json
        readback.json
        manifest.json
  test-results/
    pytest.txt
    junit.xml
    offline-summary.json
    live-summary.json
~~~

expected.json 只表达断言，不能复制进 output.json。每个实际运行目录包含：

| 文件 | 要求 |
|---|---|
| input.json | 本次真实使用的合成业务输入 |
| output.json | 程序实际输出，保留失败与未完成状态 |
| trace.jsonl | 程序生成的追加事件，含工具参数、观察与动作 |
| trace-summary.json | 步骤关联、后续动作和简短依据 |
| model-messages.json | 脱敏消息链，不保留内部思维过程 |
| readback.json | 提交后实际数据库读回；未保存时列 count=0 和查询条件 |
| manifest.json | 时间、后端、模型、版本、时钟、命令、退出码与文件哈希 |

manifest 的 source_commit 字段记录生成证据时的代码提交；最终交付 SHA 可能因追加证据而不同，不能要求一个包含自身哈希的循环字段。提交前确认生成证据之后业务代码未变；变更则重新运行。

重复用例的 readback 必须包含前后草稿 ID、版本、草稿总数和占位数。保存失败用例区分“提交前失败”和“提交后响应丢失”。至少三组实际记录覆盖正常、资料不足、工具失败，完整用例仍须覆盖六项专项验收。

Mock、FakeModel 与 live 分目录、分统计。失败试次保留，不只提交成功记录。评估文档应链接具体文件，而非只说“见截图”。
