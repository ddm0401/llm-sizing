# 估算方法

本文档用于把公开模型事实转换成硬件指标。除非用户给出更明确目标，否则使用这些默认公式和余量。

## 关键字段

优先从 `config.json` 或模型卡读取：

- `num_hidden_layers`: 层数
- `hidden_size`: hidden size
- `num_attention_heads`: Q heads
- `num_key_value_heads`: KV heads；缺失时按 MHA 处理，即等于 Q heads
- `head_dim`: 每个 attention head 的维度；缺失时用 `hidden_size / num_attention_heads`
- `max_position_embeddings`: 原生或配置上下文长度
- `torch_dtype`: 默认权重精度
- `num_experts`、`num_experts_per_tok`、`n_routed_experts`、`num_experts_per_token`: MoE 线索

优先从 `model.safetensors.index.json` 读取：

- `metadata.total_size`: 官方权重文件总字节数

## 显存

权重显存：

```text
weight_bytes = official_total_size
```

如果没有官方权重大小：

```text
weight_bytes = parameter_count * bytes_per_weight
```

默认字节数：

| 精度 | bytes_per_weight |
|---|---:|
| FP32 | 4.0 |
| BF16 / FP16 | 2.0 |
| FP8 / INT8 | 1.0 |
| INT4 / AWQ / GPTQ 4bit | 0.55 |

INT4 使用 0.55 而不是 0.5，是为了给 scale、zero point、group metadata 和框架开销留余量。

KV cache：

```text
kv_bytes = active_sessions * context_tokens * layers * 2 * kv_heads * head_dim * bytes_per_kv_value
```

其中 `2` 代表 K 和 V。GQA/MQA 会降低 `kv_heads`，不能用 `num_attention_heads` 直接代替，除非配置缺失。

运行时余量：

```text
runtime_overhead_bytes = max(2 GiB, 15% * (weight_bytes + kv_bytes))
accelerator_memory_required = weight_bytes + kv_bytes + runtime_overhead_bytes
```

生产档建议额外保留 10-20% 空间给连续批处理、CUDA graph、碎片、LoRA、embedding/reranker、监控 agent 和滚动升级。

## 带宽与算力

decode 阶段常受权重读取和 KV cache 访问限制。无实测基准时使用保守下限：

```text
aggregate_decode_tps = active_sessions * target_tps_per_session
memory_bandwidth_tbps = weight_bytes * aggregate_decode_tps / 1e12
decode_tflops = 2 * active_parameters * aggregate_decode_tps / 1e12
```

默认 `target_tps_per_session`：

| 场景 | tokens/s/session |
|---|---:|
| Agent / OpenClaw / Coding | 12 |
| Web Agent | 10 |
| RAG / 文档总结 | 10 |
| Chat / 客服 | 8 |
| 本地助手 | 8 |

MoE 模型：

- 显存按总权重。
- decode/prefill 算力按 active parameters。
- 如果只知道总参数，不知道 active parameters，按 dense 保守估算并标注风险。

## 系统资源

系统 RAM：

```text
minimum = max(32 GiB, 1.2 * accelerator_memory_required)
recommended = max(64 GiB, 1.5 * accelerator_memory_required)
production = max(128 GiB, 2.0 * accelerator_memory_required)
```

CPU：

- 最低：8-16 cores，适合单用户 PoC。
- 推荐：16-32 cores，适合轻量服务、RAG、工具调用。
- 生产：32+ cores，按网关、检索、重排、日志、监控和并发调度扩展。

存储：

```text
storage = max(100 GiB, 3 * official_weight_size)
```

生产环境按 4-6 倍权重大小规划，覆盖多版本权重、量化副本、缓存、日志和回滚。

网络：

- 单机 PoC：1GbE 可用。
- 推荐服务：10GbE。
- 多机推理、分布式 KV、集中式向量库或高并发 Agent：25GbE 起，生产优先 100GbE 或同级低延迟互联。

## 置信度

- 高：抓到结构化 `config.json` 和权重索引，关键字段完整。
- 中：抓到配置但无权重索引，或权重大小只能由参数量推算。
- 低：只能从文件名、README 文本或默认表推断。

