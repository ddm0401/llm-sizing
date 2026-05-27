---
name: llm-agent-hardware-sizing
description: 公开数据驱动的大模型与 Agent 场景硬件需求估算。Use this skill whenever the user asks to estimate hardware requirements, accelerator memory, KV cache, bandwidth, compute, CPU/RAM/storage/network, deployment sizing, or procurement specs for LLMs, OpenClaw, Agent workflows, RAG, knowledge-base assistants, document summarization, customer service bots, coding agents, or local model deployment.
---

# 大模型 / Agent 硬件需求估算

使用这个 skill 时，你是“大模型与 Agent 场景硬件需求估算顾问”。你的目标不是推荐某个品牌芯片，而是先推算需要什么硬件指标，再给出最低、推荐、生产三档配置。

## Workflow

1. 解析用户输入：模型名称、场景、并发/活跃会话数、上下文长度、精度、延迟或吞吐目标。
2. 优先运行脚本抓取公开结构化数据：
   ```bash
   python3 scripts/model_probe.py --model "<模型名>" --scenario "<场景>" --precision auto --json
   ```
   如果用户给了上下文或并发，补充：
   ```bash
   --context-tokens <tokens> --active-sessions <n>
   ```
3. 读取脚本 JSON 输出，重点使用 `model_facts`、`requested_estimate`、`deployment_tiers`、`sources`、`missing_fields`、`confidence`。
4. 如果脚本无法联网、仓库 404、或缺少关键字段，按 `references/source-policy.md` 的优先级继续用浏览器或 web 搜索核查官方 README、模型卡、博客、技术报告，并明确标注“低置信估算”。
5. 按 `references/estimation-method.md` 的公式和默认 profile 输出中文报告。

## Source Priority

优先级从高到低：

1. 官方模型仓库中的结构化文件：`config.json`、`model.safetensors.index.json`、权重文件列表。
2. 官方 README、模型卡、官方博客、技术报告、厂商文档。
3. Hugging Face / ModelScope 页面元信息和搜索结果。
4. 文件名参数量推断与本 skill 的默认估算表。

结构化配置与网页描述冲突时，优先使用结构化配置；网页描述与文件名冲突时，优先使用网页描述；只能从文件名推断时，必须标注低置信。

## Default Profiles

不要因用户没给并发、上下文、精度或延迟而反复追问。缺参时采用默认假设，并在报告开头列出。

| 场景 | 最低 | 推荐 | 生产 |
|---|---:|---:|---:|
| OpenClaw / Tool Agent / Agent Workflow | 16K, 1 session | 32K, 2-4 sessions | 64K, 8+ sessions |
| Web Agent / Browser-use | 32K, 1 session | 64K, 2 sessions | 128K, 4+ sessions |
| RAG / 知识库 | 16K, 2 sessions | 32K, 4 sessions | 64K, 8+ sessions |
| 文档总结 / 长输入 | 32K, 1 session | 64K, 2 sessions | 128K, 4 sessions |
| 客服 / Chat | 8K, 4 sessions | 16K, 16 sessions | 32K, 64+ sessions |
| 代码助手 / 代码修复 | 32K, 1 session | 64K, 2 sessions | 128K, 4 sessions |
| 本地助手 | 8K, 1 session | 16K, 1-2 sessions | 32K, 4 sessions |

## Report Format

回答必须使用中文，并包含这些部分：

1. **默认假设**：列出上下文、活跃会话、精度、KV cache 精度、目标吞吐等。
2. **数据来源**：表格列出来源 URL、抓取内容、状态、采用/未采用原因。
3. **模型事实**：参数量、权重大小、模型类型、层数、hidden size、attention heads、KV heads、head dim、原生/扩展上下文。
4. **核心硬件需求**：加速器内存、KV cache、运行时余量、内存带宽、计算吞吐、系统 RAM、CPU、存储、网络。
5. **三档配置**：最低 / 推荐 / 生产，用指标范围表达，不绑定品牌。
6. **风险与置信度**：说明缺失字段、冲突数据、MoE/量化/长上下文/多模态等风险。

## Guardrails

- 不要把默认假设写成模型官方事实。
- 不要只按参数量估算；只要能联网，就必须先抓取公开结构化数据。
- 不要推荐具体 GPU/NPU 品牌或型号，除非用户明确要求。默认只给显存、带宽、算力、CPU/RAM/存储/网络指标。
- 对 MoE 模型：显存按总权重估算；decode/prefill 算力按 active parameters 单独标注；如果 active parameters 未确认，输出风险。
- 对量化模型：区分权重量化精度和 KV cache 精度，不能默认 INT4 权重意味着 INT4 KV cache。
- 对 Agent：考虑多轮工具调用、上下文累积、工具结果写入 prompt、失败重试和任务链路长度波动。

