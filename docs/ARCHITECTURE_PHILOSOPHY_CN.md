# local-engine 架构思想

## 一句话概括

> 把一个自然语言需求，变成一次可追溯、可恢复、可交付的本地执行记录。

`local-engine` 不是一个"多智能体框架"，也不是一个"AI 代码助手"。它是一个**本地任务图运行时（Task Graph Runtime）**——拿到一个仓库，听懂一句话需求，规划出一张任务图，调度 Claude CLI 逐个执行，对每一步做质量把关，最终把产物写到项目里，把完整证据链留在 `.local_engine/runs/` 下。

## 核心哲学：Runtime 为中心，不是 Agent 为中心

大多数 AI 编程工具的组织方式是"智能体社会"——多个 agent 互相聊天、辩论、协作。这条路的尽头是 token 燃烧和不可控。

local-engine 的选择正好相反：

```
自然语言需求
  → Planner 构建 TaskGraph（任务图）
    → Runtime 执行 TaskGraph
      → Executor 执行单个任务
        → Artifact 层保存证据和交付物
          → Report 层解释发生了什么
```

**中心是 Runtime。** Planner、Executor、Agent、Artifact、Report 都是围绕 Runtime 的支撑层。Agent 只是"执行器的一种配置声明"——指定用什么模型、什么 prompt、什么 skill——它不做决策，不聊天，不"思考"。决策在 Graph 里，执行在 Runtime 里。

这是整个项目最重要的架构决策。

## 四个声明式注册表

Runtime 之上的业务逻辑由四层 YAML 声明组成，不是写死在代码里的：

| 注册表 | 目录 | 职责 | 例子 |
|--------|------|------|------|
| **Intents** | `intents/` | 意图 → 任务模板的映射 | `AUDIT` → 需要 audit_scan + audit_analyze + audit_report |
| **Task Templates** | `task_templates/` | 任务 ID、依赖关系、输出路径、风险标签 | build_backend 依赖 build_plan，输出 `src/` 下的代码文件 |
| **Skills** | `skills/` | 任务能力声明 + prompt 模板 | `audit_repo`：扫描仓库，输出审计发现 |
| **Agents** | `agents/` | 执行器支持的 skill 列表、模型、重试上限 | `backend` agent 支持 backend 类 skill，使用 claude CLI |

这四层之间的关系是：

```
用户说"审计这个项目"
  → Intent 分类器匹配到 AUDIT（置信度 > 0.6）
    → Intent 声明要求执行 [audit_scan, audit_analyze, audit_report]
      → 每个 task 通过 Skill 找到对应的 prompt 模板
        → 每个 task 通过 Agent 找到对应的执行器配置
          → Runtime 调度执行
```

**声明式的意义**：加一种新意图（比如 `DEPLOY`）不需要改任何 Python 代码，只需要写一个 `deploy.yaml` 和对应的 skill 文件夹。注册表可以完全替换——设一个环境变量 `LOCAL_ENGINE_INTENTS_DIR` 指向自定义目录即可。

## TaskGraph：一次运行的"施工图纸"

TaskGraph 是整个系统的核心数据结构。它不是 LLM 自由发挥的对话，而是一张**确定性的 DAG（有向无环图）**：

```yaml
tasks:
  - id: audit_scan
    title: 扫描项目结构
    skill: audit_repo
    depends_on: []
    task_type: scan

  - id: audit_analyze
    title: 深度分析
    skill: audit_repo
    depends_on: [audit_scan]
    task_type: analysis

  - id: audit_report
    title: 生成审计报告
    skill: audit_repo
    depends_on: [audit_analyze]
    task_type: report
    expected_output: deliverables/AUDIT_REPORT.md
```

几个关键设计决策：

1. **DAG 而非列表**：依赖关系明确，audit_analyze 绝不会在 audit_scan 完成前启动。并行调度器自动识别无依赖任务并发执行。
2. **图质量检查（Graph Quality Check）**：图构建完成后，会经过确定性的质量校验——孤立节点检测、循环依赖检测、必需任务缺失检测。不合格的图会被打回修复，不会进入执行。
3. **图修复（Graph Repair）**：如果质量检查失败，Runtime 会尝试自动修复——补缺失任务、断孤立边、解循环依赖。修复失败才报 `needs_human`。

## SIP：永远不信任 LLM 的输出

这是整个系统最工程的决策。

Claude 的输出**从不被直接信任**。每一份模型输出都要经过 SIP Parser：

```
Claude 原始输出 (raw.txt)
  → SIP Parser 解析
    → 成功：产出结构化 YAML (sip.yaml)，包含 findings、recommendations、decisions
    → 失败：进入 FORMAT 恢复流程，要求 Claude 按严格的 SIP 合约重新输出
```

SIP（Strict Internal Protocol）接受多种格式——裸 YAML、fenced code block、内嵌 JSON——但不接受"看起来差不多"的输出。格式不对就是不对，不会猜测意图。

**为什么这样做？** 因为后续步骤依赖这个输出做决策。如果 audit_scan 的输出解析错了，audit_analyze 就是基于错误信息分析，audit_report 就是生成一份看起来正确但内容有问题的报告。级联错误在 agent 链条里极其常见，SIP 是唯一的防线。

## 五类失败，五种恢复策略

Runtime 不会对失败一视同仁。每种失败有自己的恢复路径：

| 失败类型 | 含义 | 恢复策略 |
|----------|------|----------|
| **NETWORK** | API 调用超时、连接中断 | 复用原始 prompt 重试，可切换到 fallback executor |
| **FORMAT** | SIP 解析失败，输出不符合合约 | 附加严格的 SIP 合约，要求重新输出 |
| **TIMEOUT** | 任务执行超时 | 压缩上下文、延长超时后重试，可切换到 fallback executor |
| **LOGIC** | 业务逻辑错误，需要人类判断 | **停止，标记 needs_human**，不做自动重试 |
| **UNKNOWN** | 无法分类的异常 | 最多一次保守重试，仍失败则标记 needs_human |

关键态度：**NETWORK/FORMAT/TIMEOUT 是工程问题，可以自动恢复。LOGIC 是语义问题，必须人来判断。** 不会假装自己理解了逻辑错误然后瞎修。

## 质量闸门：不合格的输出不传播

每个任务完成后，要过三道检查，才能在依赖链上继续传播：

```
任务完成
  → Output Quality Gate: 检查输出长度、置信度、SIP findings 是否为空
    → 不合格：生成 context_patch（警告下游任务"这个结论不靠谱"）
      → Review Gate（条件触发）：高风险任务、低置信度、代码变更、SIP 警告
        → 不合格：进入 review → revision 循环（最多 max_rounds 轮）
          → Revision 完成，下游任务才被释放
```

闸门设计的关键点：

1. **Quality Gate 是非阻塞的**：低质量输出不会被丢弃，而是打上标记继续。宁可让下游带着"前面的结论可能不靠谱"的提示继续工作，也不要整条流水线卡死。
2. **Review Gate 是条件触发**：不是所有任务都要 review。只有 `risky_task_types`（backend/frontend/refactor）或 `risky_tags`（code_change/architecture/security）或低置信度任务才会触发。扫一个 README 不需要 review。
3. **Review 在依赖传播之前**：如果 task B 依赖 task A，task A 的 review 必须通过，task B 才能开始。这是防止级联错误的关键机制。

## 任务缓存：不改的东西不再跑

仓库指纹（repository fingerprint）是整个缓存系统的基石：

```python
fingerprint = stable_hash(
    repo_hash           # 整个仓库的内容哈希
    + watched_paths     # skill 声明了"关注哪些路径"
    + dependency_hashes # 所有直接依赖任务是否也缓存命中
)
```

缓存命中的条件非常严格：

1. 仓库内容没有变化（`repo_hash` 一致）
2. 该 skill 声明的 watched paths 下的文件没有变化
3. 所有直接依赖任务也都缓存命中
4. 缓存的输出是完整的，且置信度 ≥ 0.5

**四个条件全部满足，任务才被跳过。** 宽松的缓存是 bug 来源，宁可重跑也不要用了过期的结果。

## 安全边界：引擎只写自己该写的地方

```text
允许写的：
  ✅ <project>/.local_engine/          # 项目本地运行时状态
  ✅ ~/.local_engine/                   # 全局运行时状态
  ✅ 项目根目录下的生成文件（apply 模式，需 --yes 或 --auto-approve）

拒绝的：
  ❌ 项目根目录外的任何路径
  ❌ .git/ 目录的任何改动
  ❌ 文件删除操作
  ❌ rm -rf、git push、destructive SQL
```

Plan 模式**从不**应用任何代码变更。Apply 模式必须有明确的审批（`--yes` 或 `--auto-approve project`）才会写入。

这是一个原则问题：**引擎是工具，工具不能替人做决定。**

## 产物协议：生成文件要显式声明

引擎鼓励 worker 使用显式的产物协议：

```yaml
artifact_protocol: local-engine.artifacts.v1
artifacts:
  - type: file
    path: src/utils/helper.py
    content: |
      def helper():
          pass
```

而不是依赖"从 markdown 代码块里提取文件"这种不可靠的启发式方法。旧格式（fenced code block）仍然兼容解析，但新代码应该走显式协议。

## 一次运行的文件结构

每运行一次 `local-engine run`，会在项目下生成完整的证据链：

```text
.local_engine/runs/2026-07-21-001/
├── raw_input.md                     # 原始输入
├── normalized_requirement.yaml      # 标准化后的需求
├── state.json                       # 运行状态机
├── task_graph.yaml                  # 任务图
├── prompts/<task_id>.prompt.md      # 每个任务的编译后 prompt
├── agent_outputs/<task_id>.raw.txt  # Claude 原始输出
├── agent_outputs/<task_id>.sip.yaml # 解析后的结构化输出
├── agent_outputs/<task_id>.md       # 人类可读的任务记录
├── reviews/<task_id>.round1.md      # 每轮 review
├── patches/<task_id>.patch          # 生成的补丁
├── artifacts/errors/<task_id>.json  # 分类后的失败证据
├── artifacts/retries/<task_id>.json # 恢复尝试记录
├── artifacts/context_quality.*      # 上下文质量报告
├── artifacts/graph_quality.json     # 图质量报告
├── artifacts/hooks.jsonl            # 内置钩子事件
├── artifacts/task_results.yaml      # 所有任务的汇总结果
├── deliverables/FINAL_DELIVERY.md   # 最终交付文档
├── integration_review.md            # 集成审查
├── eval_report.md                   # 评估报告
├── memory_update.md                 # 记忆更新建议
└── final_report.md                  # 最终报告
```

**可追溯是第一原则。** 每一个决策、每一份输出、每一次失败、每一次重试都有文件记录。三个月后回来看，能完整还原"当时发生了什么、为什么这样做"。

## 整体架构图

```
                          ┌──────────────────────┐
                          │    CLI (Typer/Rich)   │
                          │  init scan run graph  │
                          │  report doctor status │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │     Intake 层          │
                          │  输入加载 → 需求标准化   │
                          │  → 意图分类             │
                          └──────────┬───────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼────────┐  ┌─────────▼────────┐  ┌─────────▼────────┐
    │  Context Builder │  │  Graph Builder   │  │  Memory Loader   │
    │  仓库扫描→上下文  │  │  意图→任务图构建  │  │  项目记忆加载     │
    └─────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     │
                          ┌──────────▼───────────┐
                          │    ⭐ Runtime 核心 ⭐   │
                          │                       │
                          │  ┌─────────────────┐  │
                          │  │ Scheduler       │  │
                          │  │ 并行任务调度      │  │
                          │  └────────┬────────┘  │
                          │           │           │
                          │  ┌────────▼────────┐  │
                          │  │ ExecutorManager │  │
                          │  │ 执行器选择+管理   │  │
                          │  └────────┬────────┘  │
                          │           │           │
                          │  ┌────────▼────────┐  │
                          │  │ Quality + Review│  │
                          │  │ 质量闸门+审查     │  │
                          │  └────────┬────────┘  │
                          │           │           │
                          │  ┌────────▼────────┐  │
                          │  │ Artifact Applier│  │
                          │  │ 产物写入项目      │  │
                          │  └─────────────────┘  │
                          └───────────────────────┘
                                     │
                          ┌──────────▼───────────┐
                          │     Worker 层          │
                          │  Claude CLI Worker     │
                          │  (Mock Worker for test)│
                          └───────────────────────┘
```

## 注册表驱动 vs 代码驱动

传统做法是把"审计项目要做什么"写在 Python 代码里：

```python
def run_audit(project):
    scan()        # 步骤 1
    analyze()     # 步骤 2
    report()      # 步骤 3
```

local-engine 的做法是**声明**：

```yaml
# intents/audit.yaml
name: AUDIT
keywords: [审计, audit, 代码审查]
required_tasks: [audit_scan, audit_analyze, audit_report]
optional_tasks: [integration_review, memory_update]
```

区别在哪？前者要改代码、跑测试、发布新版本才能加一个意图。后者加一个 YAML 文件和一个 skill 文件夹就够了。**意图分类器、图构建器、调度器、质量闸门——全部通用，不为任何一个意图写特殊逻辑。**


## 项目技术栈

```
语言:        Python 3.11+
CLI 框架:     Typer + Rich
数据格式:     YAML (声明) + JSON (运行时状态) + Markdown (文档/报告)
Worker:       Claude CLI (本地子进程调用)
测试:         pytest (unit: 快速回归, integration: 端到端 CLI 测试)
打包:         setuptools + 自定义 release 脚本
```

依赖极简。`typer` + `PyYAML` + `rich` 三个运行时依赖，没有任何重量级框架。**每一个依赖都需要挣得自己的位置。**

---

*这份文档描述的是 local-engine 的架构思想和设计决策——为什么 Runtime 是中心、为什么用声明式、为什么做 SIP、为什么分五种失败类型。具体的技术实现和 API 参数，请参考 `README.md` 和 `docs/` 下的其他文档。*
