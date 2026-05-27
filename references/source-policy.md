# 来源策略

估算时要区分“公开事实”和“估算假设”。公开事实必须能追溯到 URL 或本地官方文件；估算假设必须在报告中单列。

## 来源优先级

1. 官方模型仓库结构化文件：`config.json`、`model.safetensors.index.json`、权重文件列表。
2. 官方 README、模型卡、官方博客、技术报告、厂商文档。
3. Hugging Face / ModelScope 页面元信息和搜索结果。
4. 文件名参数量推断。
5. 本 skill 默认 profile 和默认架构表。

## 冲突处理

- `config.json` 与 README 冲突：使用 `config.json`，并在风险中说明 README 描述不一致。
- Hugging Face 与 ModelScope 文件一致：视为交叉验证。
- Hugging Face 与 ModelScope 文件冲突：优先使用用户指定仓库对应的平台；若用户未指定，优先使用官方组织仓库，并列出冲突。
- README 与文件名冲突：使用 README，但保留文件名推断为低权重线索。
- 任何来源缺失关键字段时，不要静默补齐；补齐值必须进入 `missing_fields` 或“默认假设”。

## 常用入口

Hugging Face：

```text
https://huggingface.co/api/models/<repo_id>?expand=config
https://huggingface.co/<repo_id>/raw/main/config.json
https://huggingface.co/<repo_id>/raw/main/model.safetensors.index.json
https://huggingface.co/<repo_id>/raw/main/README.md
```

ModelScope：

```text
https://modelscope.cn/models/<repo_id>/resolve/master/config.json
https://modelscope.cn/models/<repo_id>/resolve/master/model.safetensors.index.json
https://modelscope.cn/models/<repo_id>/resolve/master/README.md
```

官方文档示例：

- Qwen 官方博客与文档
- DeepSeek 官方模型卡、技术报告、GitHub 仓库
- 模型发布方的 README、论文、release note

## 报告要求

数据来源表必须至少包含：

- 来源名称
- URL
- 抓取内容
- 状态
- 是否用于估算

如果脚本输出 `confidence = low`，最终报告必须明确提醒：该估算只能用于 L1 方案级预算，不适合作为采购或上线容量承诺。

