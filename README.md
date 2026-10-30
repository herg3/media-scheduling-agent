# 素材审核与发布排期 Agent

选题1。帮助内容运营人员读取素材、核对规则、处理时段冲突并保存排期草稿。

Python单Agent，原生工具调用循环，SQLite持久化，CLI运行。**只保存草稿，没有真实发布能力。** 元数据、规则和已有排期都是合成资料。

## 评审入口

| 想核对的能力 | 入口 |
|---|---|
| 用户、范围、成功标准 | [需求分析](docs/01-requirements.md) |
| 模型动态决策、确认与幂等 | [技术方案](docs/02-solution.md)、[循环实现](src/scheduling_agent/agent.py) |
| 工具接口与保存事务 | [接口契约](docs/07-contracts.md)、[工具](src/scheduling_agent/tools.py)、[事务](src/scheduling_agent/repository.py) |
| 实际输入、输出、Trace和失败 | [评估报告](docs/03-evaluation.md)、[证据目录](examples/README.md) |
| AI辅助与个人贡献边界 | [AI使用记录](docs/04-ai-usage.md)、[实际复盘](docs/05-retrospective.md) |
| 开发顺序与最终提交 | [开发计划](docs/06-development-plan.md)、[提交清单](docs/08-submission-checklist.md) |

当前本地已有完整代码：74项自动化测试通过、Mock13/13、最终live13/13、四类重复评估12/12。通过数与失败限制以[实际评估](docs/03-evaluation.md)为准；已推送至[私有GitHub仓库](https://github.com/herg3/media-scheduling-agent)，评审访问权限待配置。

## 安装

最低Python3.12；本地实际验证环境为 **Windows + CPython3.13.14**。其他系统和Python3.12的CI已配置，尚未在远端运行。依赖均锁定确切版本。

Windows PowerShell：

~~~powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
~~~

Linux/macOS：

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e . --no-deps
~~~

以下示例使用Windows解释器路径；其他系统替换对应路径。仅运行业务程序安装requirements.txt即可。

## 一分钟离线运行

Mock不需要密钥、不访问网络，只验证流程。数据库和输出目录首次使用时必须不存在或为空，避免覆盖运行记录。

~~~powershell
.\.venv\Scripts\python.exe -m scheduling_agent init-db --data-dir data --db work/demo.sqlite
.\.venv\Scripts\python.exe -m scheduling_agent run --input examples/cases/E01/input.json --backend mock --db work/demo.sqlite --output-dir work/demo-run
~~~

查看work/demo-run/output.json、trace-summary.json和readback.json。正常场景退出码0，审核通过、草稿reserved、保存成功；仍未发布。

一次复现全部合成场景：

~~~powershell
.\.venv\Scripts\python.exe -m scheduling_agent evaluate --backend mock --cases examples/cases --output-dir work/mock-evaluation
.\.venv\Scripts\python.exe -m pytest -q --junitxml=work/junit.xml
~~~

evaluate通过表示实际结果满足场景预期；“预期保存失败”的场景也可以通过评估，不会把业务失败改为成功。

## 真实模型运行

使用通用Chat Completions工具调用协议，经本机DashScope的qwen-plus实测。程序直接读取进程环境变量，不自动加载.env。配置见[.env.example](.env.example)。

通用提供方设置LLM_API_KEY、LLM_BASE_URL、LLM_MODEL。DashScope也接受已有DASHSCOPE_API_KEY，并默认北京兼容接口和qwen-plus；地域不匹配时显式设置接口和模型。密钥仅在本地配置，不提交或粘贴到聊天。

~~~powershell
.\.venv\Scripts\python.exe -m scheduling_agent evaluate --backend live --cases examples/cases --case-ids E01,E02,E03,E04,E05,E06 --output-dir work/live-evaluation
~~~

live产生真实模型调用和费用；配置缺失或模型失败时明确错误，不自动降级Mock。实际记录见评估报告。模型别名可能更新，证据保留请求模型ID与时间。

## 冲突与确认

E04原要求18:00被占用，19:00可用。模型保存19:00的pending建议，保留原要求18:00，effective_at=null，pending不占位。

确认命令参数格式如下；草稿ID和版本必须取自自己的output.json，示例ID不能直接复用：

~~~powershell
.\.venv\Scripts\python.exe -m scheduling_agent confirm --draft-id D-实际草稿ID --revision 1 --proposed-at "2026-11-02T19:00:00+08:00" --backend mock --db work/demo.sqlite --output-dir work/confirm-run
~~~

完整确认与新冲突演示：

~~~powershell
.\.venv\Scripts\python.exe -m scheduling_agent evaluate --backend mock --cases examples/cases --case-ids E04,E07,E08,E09 --output-dir work/confirmation-evaluation
~~~

确认绑定具体草稿版本和时间。新占用或建议变化后，旧确认失效；确认后仍只有本地草稿。

## 限制与退出码

默认24次模型请求、20次工具尝试、180秒；同工具同业务参数最多两次。达到限制明确未完成，保留已成功项。

| 退出码 | 含义 |
|---|---|
| 0 | 全部完成；或evaluate全部预期断言通过 |
| 2 | 待确认、待补充、规则阻断 |
| 3 | 输入/确认/幂等冲突，或拒绝覆盖证据 |
| 4 | 模型/工具失败、上限；或评估断言不符 |
| 5 | 批量部分完成 |

## 证据和能力边界

实际记录包含输入、输出、工具Trace、公开动作依据、数据库读回、代码来源哈希；首次开发失败也保留。见[评估报告](docs/03-evaluation.md)。

- 只检查合成元数据，不读取真实视频，不证明生产内容安全或版权。
- 同账号每整点一个容量，仅Asia/Shanghai。
- CLI确认代表本地操作者授权，没有生产身份认证。
- reserved草稿不能自动迁移；取消、真实平台同步和部署未实现。
- 固定合成时钟2026-11-01，业务日期2026-11-02，输出明确标记。
- 候选人独立复现尚未记录；GitHub已推送，指定评审账号权限待配置。