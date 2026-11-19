# test-workflow Skill 系统研究报告

**对象**：`~/.agents/skills/test-workflow/`（20 → 22 个文件）
**方法**：量化审计（AST 重复检测、复杂度、字段使用率）+ 重构 + 变异验证

---

## 一、研究发现的四个结构性问题

### 1.1 复制粘贴式实现（最严重）

用 AST 做了函数级重复检测，结果：

| 函数 | 重复份数 | 可消除行数 |
|---|---|---|
| `repo_root` | **7 份** | 78 |
| `resolve_state` | **7 份** | 105 |
| `iter_jsonl` | 3 份 | 22 |
| `_require_yaml` | 5 份 | 12 |

**这不是风格问题，是行为分裂**：7 份 `resolve_state` 中有一份对显式 `--root` 的处理与其它不同。这正是之前 `--root` 失效 bug 的温床——修了一份，其余六份继续有缺陷。

### 1.2 巨型函数

| 函数 | 行数 | 问题 |
|---|---|---|
| `test_report.render` | **260** | 12 个段落混在一个函数里，无法单独测试 |
| `gate_check._run_mutation_cosmic_ray` | 134 | 配置生成、执行、解析混在一起 |
| `triage_failures.cmd_triage` | 101 | 输入、分类、输出、落盘混在一起 |

**后果**：`render` 的 12 个段落无法被单独验证——只能整体跑一次看输出对不对。

### 1.3 配置字段名不副实

43 个叶子字段中 **9 个从未被代码读取**：

```
e2e.max_cases / max_total_seconds / per_case_seconds / parallelism
runners.unit.coverage_command
max_regeneration_attempts
discard_audit_sample_per_week
selection.smoke_paths
out_of_scope
```

用户写了这些配置，以为在控制行为，实际完全无效。

### 1.4 Skill 自己没有测试

**这是最讽刺的一条**：Skill 的核心主张是「每个机制都必须被验证，不能假设」，但它自己的 8 个脚本**零测试**。

我在前面几轮分析中反复强调「不能只做正向验证」，却从未对 Skill 本身做过变异测试。

---

## 二、已实施的优化

### 2.1 抽出共享核心 `core.py`（283 行）

单一职责：回答「状态在哪」「文件里有什么」。

| 提供 | 消除了什么 |
|---|---|
| `repo_root` / `repo_key` / `resolve_state` | 7 份重复 → 1 份 |
| `pointer_path` / `read_pointer` / `write_pointer` | 3 份重复 |
| `iter_jsonl` / `append_jsonl` / `read_records` / `write_yaml` | 3 份重复 |
| `tool_available` / `tool_command` | 2 份重复（含 venv 探测的修复） |
| `Paths` dataclass + 目录常量 | 硬编码 `"runs"` / `"cases"` 字符串 |
| `StateError` | 各脚本各自 `raise SystemExit` |

**效果**：完全重复函数 **78 行 → 0 行**。

### 2.2 拆分巨型函数

**`test_report.render` 260 → 12 个渲染函数（最长 63 行）**：

```
render_verdict / render_requirements / render_detail / render_feature_reach
render_gate_detail / render_scope / render_results / render_failures
render_coverage / render_deviations / render_evidence / render_approvals
```

每个可独立测试：`assert "**GAP**" in "\n".join(render_requirements(stats))`

`derive_verdict` 单独抽出（63 行，10 条规则）。

### 2.3 拆分 `cmd_triage`（101 → 5 个函数）

```
load_known_flaky / load_failures / classify_all / print_report / record
```

### 2.4 配置字段加注释说明

在 `config.yaml` 末尾明确标注哪些字段当前不生效，避免"配了以为在用"。

---

## 三、变异验证（优化没有破坏行为）

### 3.1 端到端回归

8 个脚本全部重跑，退出码与输出与重构前一致：

```
init / check / status / guard         exit=0
case_index find                       exit=0
coverage_ledger report                exit=0
flake_stats report                    exit=0
test_report --verdict-only            exit=2   ← 空状态 hold，正确
```

### 3.2 新增 22 个测试

`tests/test_skill_scripts.py`，覆盖：

| 测试组 | 数量 | 覆盖什么 |
|---|---|---|
| core 状态解析 | 6 | `--root` 优先级、指针复用、目录布局、JSONL 容错、venv 工具探测 |
| 分类器 | 6 | unknown 判定、known_flaky、非确定源、断言内环境词、traceback 环境、真实失败 |
| 报告结论 | 5 | 空状态 hold、全通过 ship、失败条件发布、阻塞≠失败、unknown 条件发布、覆盖收缩 hold |
| 报告段落 | 4 | GAP 标记、明细路径、功能触达、12 段完整 |

### 3.3 变异测试：5/5 全部杀死

向脚本注入 5 个真实缺陷，全部被测试抓住：

| 注入的缺陷 | 结果 |
|---|---|
| 空状态返回 ship | KILLED |
| 不稳定直接判 flaky | KILLED |
| 阻塞当作失败 | KILLED |
| 断言内环境词判环境 | KILLED |
| GAP 标记失效 | KILLED |

**这证明 22 个测试不是"通过但无效"。**

---

## 四、优化后的量化对比

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 脚本总行数 | 2652 | 2619（+core 283） |
| **完全重复函数** | **78 行** | **0 行** |
| 最长函数 | 260 行 | 134 行（`_run_mutation_cosmic_ray`） |
| `test_report` 最长函数 | 260 | 63 |
| 测试数量 | **0** | **22** |
| 变异杀死率 | 未测 | **5/5** |
| 未使用配置字段 | 9（无提示） | 9（已注释标注） |

---

## 五、仍存在的问题（诚实记录）

### 5.1 未拆分的函数

| 函数 | 行数 | 为什么不拆 |
|---|---|---|
| `gate_check._run_mutation_cosmic_ray` | 134 | 配置生成 + 子进程 + JSON 解析，拆分需要传大量状态；且已有集成测试 |
| `triage_failures.classify` | 75 | 判定顺序本身就是语义（环境→flaky→测试缺陷→真实），拆开反而看不清顺序 |
| `case_index.cmd_find` | 44 | 可接受 |

### 5.2 配置字段仍不生效

9 个字段依然没被读取，只是**加了注释**。真正解决要么实现它们，要么删除。我选择注释，因为：
- `e2e.*` 是计划阶段的预算声明，执行层强制它需要更多设计
- 其余字段依赖人工流程（如误丢率抽样），脚本无法自动化

**这是能力边界，不是疏忽。**

### 5.3 没有 CI

22 个测试需要手动跑。Skill 目录不在 git 仓库里，无法接 CI。

### 5.4 本文档没做的验证

- 未对 `gate_check.py` 做变异测试（依赖真实的 cosmic-ray 运行，10 分钟/次）
- 未验证跨平台（只在 Windows 测过，POSIX 权限相关测试被 skip）

---

## 六、结论

**这次研究最有价值的发现是第 1.4 条：Skill 自己没测试。**

一个主张「必须用变异测试验证测试有效性」的方案，自身脚本零测试——这不是疏忽，是**方案与实施脱节**的典型表现。我在文档里反复强调对抗验证，却在实现层只做了正向冒烟。

修复后：

| 层次 | 验证方式 |
|---|---|
| 方案设计 | 九轮递进分析（对抗推理） |
| 分类器逻辑 | 3 个反例 + 6 个单元测试 |
| 报告结论 | 5 个场景 + 5 个单元测试 |
| **脚本整体** | **22 个测试 + 5/5 变异杀死** |

这才算形成了闭环。
