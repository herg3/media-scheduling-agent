# 素材审核与发布排期 Agent

选题：1。目标：帮助内容运营人员完成素材检查、时段协商和排期草稿保存。

**当前交付为需求与开发文档 v0.1（2026-10-29），业务程序尚未实现，尚不构成可提交的完整笔试项目。** 本目录没有真实模型运行记录、业务测试结果、GitHub URL 或最终提交 SHA。文中的命令、接口、数据和结果预期均属于开发规格，不能当作已经运行的证据。

## 从这里评审

| 阅读顺序 | 文档 | 用途 |
|---|---|---|
| 1 | [需求分析](docs/01-requirements.md) | 用户、范围、验收条件和不能违反的业务约束 |
| 2 | [技术方案](docs/02-solution.md) | 模型循环、状态、幂等、事务与技术取舍 |
| 3 | [接口与数据契约](docs/07-contracts.md) | 输入输出、工具、错误码、数据库与确认凭据 |
| 4 | [开发计划](docs/06-development-plan.md) | 实施顺序、文件职责、测试入口和预计时间 |
| 5 | [评估设计与结果台账](docs/03-evaluation.md) | 验收矩阵、真实模型评估、当前未运行事实 |
| 6 | [AI 使用记录](docs/04-ai-usage.md) | 本轮辅助范围、候选人贡献边界与验证要求 |
| 7 | [阶段复盘](docs/05-retrospective.md) | 当前设计难点、学习材料、后续验证问题 |
| 8 | [提交检查](docs/08-submission-checklist.md) | 交付规范、保密范围、证据及提交前门禁 |

辅助说明：[合成数据设计](data/README.md)、[运行证据组织](examples/README.md)。

## 方案概要

- 使用 Python、单 Agent、原生工具调用循环、SQLite 和 CLI。
- 模型选择读取、补查、检查、查询、保存或结束；每次工具观察回传模型后再作下一步决定。
- 程序控制参数、证据、确认权限、步数和数据库约束。
- 只保存草稿。已确认且占位成功的草稿也不等于发布，程序没有发布工具。
- 原发布时间要求、建议时间、用户确认和保存结果分别记录。
- 合成元数据规则检查只能验证本题流程，不能证明真实视频内容安全或版权合法。

## 运行入口设计（尚不可执行）

目标解释器为 Python 3.12；开发时在干净虚拟环境中锁定确切依赖。当前本机默认解释器探查结果是 Python 3.9.21，尚未安装或验证目标环境。

以下命令是将来 CLI 必须实现的接口。依赖文件、源代码和用例输入尚不存在；完成实现并实际执行后，才能把本节改为“已验证运行步骤”。

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps

.\.venv\Scripts\python.exe -m scheduling_agent init-db --data-dir data --db work/demo.sqlite
.\.venv\Scripts\python.exe -m scheduling_agent init-db --data-dir data --db work/live.sqlite
.\.venv\Scripts\python.exe -m scheduling_agent run --input examples/cases/E01/input.json --backend mock --db work/demo.sqlite --output-dir work/run-E01
.\.venv\Scripts\python.exe -m scheduling_agent run --input examples/cases/E04/input.json --backend live --db work/live.sqlite --output-dir work/run-E04
.\.venv\Scripts\python.exe -m pytest -q --junitxml=examples/test-results/junit.xml
~~~

凭据由本机环境变量提供，不在聊天、代码或运行记录中填写真实值。配置名称与离线行为见[接口与配置契约](docs/07-contracts.md#8-配置契约)。

init-db 只初始化不存在的数据库，读取 fixture-manifest.json 并记录合成时钟；已有数据库拒绝覆盖。evaluate 为独立试次建立隔离数据库，但同一用例内的重复/确认共享数据库。运行结果明确展示合成场景与固定时钟。

## 当前验证边界

| 项目 | 当前状态 |
|---|---|
| 需求与设计 | 已形成供评审的文档 |
| 代码与锁定依赖 | 未实现 |
| 离线流程测试 | 未运行 |
| 真实模型决策评估 | 未运行 |
| 三类实际输入、输出、Trace | 未生成 |
| Git 仓库、远端权限、最终 SHA | 未建立或验证 |

文档检查只验证文件、链接和需求追踪，不验证未来程序正确性。文档检查的实际结果单独记录在 docs/document-validation.json。
