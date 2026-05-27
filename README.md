# LLM Agent Hardware Sizing Skill

公开数据驱动的大模型与 Agent 场景硬件需求估算 Codex skill。

这个 skill 会优先抓取 Hugging Face、ModelScope 和官方模型文档中的公开结构化数据，再估算部署所需的加速器内存、KV cache、内存带宽、计算吞吐、系统内存、CPU、存储、网络和最低/推荐/生产三档配置。

## Install

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo ddm0401/llm-sizing \
  --path . \
  --name llm-agent-sizing
```

安装后重启 Codex。

## CLI

```bash
python3 scripts/model_probe.py --model Qwen/Qwen3-32B --scenario OpenClaw --context-tokens 32768 --active-sessions 2 --precision auto --json
```

## License

MIT License. See [LICENSE](LICENSE).
