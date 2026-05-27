#!/usr/bin/env python3
"""Probe public model metadata and estimate LLM/Agent hardware requirements."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


GIB = 1024**3

PRECISION_BYTES = {
    "fp32": 4.0,
    "float32": 4.0,
    "bf16": 2.0,
    "bfloat16": 2.0,
    "fp16": 2.0,
    "float16": 2.0,
    "half": 2.0,
    "fp8": 1.0,
    "int8": 1.0,
    "8bit": 1.0,
    "int4": 0.55,
    "4bit": 0.55,
    "awq": 0.55,
    "gptq": 0.55,
}

DEFAULT_PROFILES = {
    "agent": {
        "aliases": ("agent", "openclaw", "tool", "workflow", "dify", "langgraph", "crewai", "autogpt"),
        "production_required_inputs": (
            "context_tokens",
            "active_sessions",
            "latency_or_target_tps_per_session",
            "tasks_per_hour_or_request_rate",
            "p95_llm_calls_per_task",
        ),
        "production_guidance": "OpenClaw / Agent 生产部署必须基于实际任务链路估算：上下文长度、活跃会话、p95 LLM calls/task、任务到达率和延迟目标都会显著改变硬件需求。",
        "tiers": {
            "minimum": {
                "context_tokens": 16_384,
                "active_sessions": 1,
                "target_tps": 6,
                "llm_calls_per_task": "avg 3-5",
                "basis": "能跑 OpenClaw 基本任务的低要求基线，不保证复杂工具链体验。",
            },
            "normal": {
                "context_tokens": 32_768,
                "active_sessions": 4,
                "target_tps": 12,
                "llm_calls_per_task": "p95 10-15",
                "basis": "适合小团队或稳定试运行的正常体验基线。",
            },
        },
    },
    "web_agent": {
        "aliases": ("browser", "web agent", "browser-use", "web automation", "网页", "浏览器"),
        "production_required_inputs": ("context_tokens", "active_sessions", "latency_or_target_tps_per_session", "browser_steps_per_task", "tasks_per_hour_or_request_rate"),
        "production_guidance": "Web Agent 生产部署需补充浏览器步骤数、工具失败重试率、任务到达率和延迟目标；网页内容长度会显著影响上下文和 KV cache。",
        "tiers": {
            "minimum": {"context_tokens": 32_768, "active_sessions": 1, "target_tps": 6, "basis": "能跑简单网页任务的低要求基线。"},
            "normal": {"context_tokens": 65_536, "active_sessions": 2, "target_tps": 10, "basis": "适合较长网页状态和多步操作的正常体验基线。"},
        },
    },
    "rag": {
        "aliases": ("rag", "knowledge", "知识库", "检索", "向量库"),
        "production_required_inputs": ("context_tokens", "active_sessions", "latency_or_target_tps_per_session", "retrieved_chunks_per_query", "qps_or_request_rate"),
        "production_guidance": "RAG 生产部署需补充 QPS、检索片段数、上下文拼接长度、重排/embedding 是否同机和延迟目标。",
        "tiers": {
            "minimum": {"context_tokens": 16_384, "active_sessions": 2, "target_tps": 6, "basis": "小规模知识库问答的低要求基线。"},
            "normal": {"context_tokens": 32_768, "active_sessions": 4, "target_tps": 10, "basis": "适合常规检索片段拼接和轻量并发的正常体验基线。"},
        },
    },
    "summarization": {
        "aliases": ("summary", "summarization", "文档总结", "长文档", "long-input", "long context"),
        "production_required_inputs": ("context_tokens", "active_sessions", "latency_or_target_tps_per_session", "document_tokens_p95", "jobs_per_hour"),
        "production_guidance": "文档总结生产部署需补充文档 token p95、批处理/交互模式、jobs/hour 和 SLA。",
        "tiers": {
            "minimum": {"context_tokens": 32_768, "active_sessions": 1, "target_tps": 6, "basis": "能处理短到中等文档的低要求基线。"},
            "normal": {"context_tokens": 65_536, "active_sessions": 2, "target_tps": 10, "basis": "适合长输入总结和少量并发的正常体验基线。"},
        },
    },
    "chat": {
        "aliases": ("chat", "客服", "customer", "support", "bot"),
        "production_required_inputs": ("context_tokens", "active_sessions", "latency_or_target_tps_per_session", "qps_or_request_rate", "conversation_turns_p95"),
        "production_guidance": "客服生产部署需补充峰值 QPS、同时在线用户、p95 对话轮数、上下文保留策略和 SLA。",
        "tiers": {
            "minimum": {"context_tokens": 8_192, "active_sessions": 4, "target_tps": 5, "basis": "低并发客服/聊天的低要求基线。"},
            "normal": {"context_tokens": 16_384, "active_sessions": 16, "target_tps": 8, "basis": "适合轻量客服服务的正常体验基线。"},
        },
    },
    "coding": {
        "aliases": ("code", "coding", "代码", "coder", "代码助手", "代码修复"),
        "production_required_inputs": ("context_tokens", "active_sessions", "latency_or_target_tps_per_session", "repo_context_tokens_p95", "tasks_per_hour_or_request_rate"),
        "production_guidance": "代码助手生产部署需补充仓库上下文 p95、补丁生成长度、并发开发者数、任务到达率和交互延迟目标。",
        "tiers": {
            "minimum": {"context_tokens": 32_768, "active_sessions": 1, "target_tps": 8, "basis": "能做单人代码问答/小补丁的低要求基线。"},
            "normal": {"context_tokens": 65_536, "active_sessions": 2, "target_tps": 12, "basis": "适合代码助手和较长仓库上下文的正常体验基线。"},
        },
    },
    "personal": {
        "aliases": ("local", "personal", "本地助手", "个人助手"),
        "production_required_inputs": ("context_tokens", "active_sessions", "latency_or_target_tps_per_session", "qps_or_request_rate"),
        "production_guidance": "本地助手通常不需要生产档；若要多人服务，需补充并发、上下文和延迟目标。",
        "tiers": {
            "minimum": {"context_tokens": 8_192, "active_sessions": 1, "target_tps": 5, "basis": "个人本地助手的低要求基线。"},
            "normal": {"context_tokens": 16_384, "active_sessions": 2, "target_tps": 8, "basis": "个人或小范围共享的正常体验基线。"},
        },
    },
}


@dataclass
class FetchResult:
    name: str
    url: str
    content_type: str
    status: str
    used: bool = False
    http_status: int | None = None
    error: str | None = None
    elapsed_ms: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "content_type": self.content_type,
            "status": self.status,
            "http_status": self.http_status,
            "used": self.used,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


def fetch_text(name: str, url: str, timeout: float) -> tuple[str | None, FetchResult]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "llm-agent-hardware-sizing-skill/1.0",
            "Accept": "application/json,text/plain,text/markdown,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            result = FetchResult(
                name=name,
                url=url,
                content_type=response.headers.get_content_type(),
                status="ok",
                http_status=response.status,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return text, result
    except urllib.error.HTTPError as exc:
        result = FetchResult(
            name=name,
            url=url,
            content_type="unknown",
            status="http_error",
            http_status=exc.code,
            error=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return None, result
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result = FetchResult(
            name=name,
            url=url,
            content_type="unknown",
            status="error",
            error=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return None, result


def parse_json(text: str | None) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def quote_repo(repo_id: str) -> str:
    return urllib.parse.quote(repo_id.strip("/"), safe="/")


def infer_family(model: str) -> str | None:
    lowered = model.lower()
    if "qwen" in lowered or "qwq" in lowered:
        return "qwen"
    if "deepseek" in lowered:
        return "deepseek"
    if "llama" in lowered:
        return "llama"
    if "mistral" in lowered or "mixtral" in lowered:
        return "mistral"
    if "gemma" in lowered:
        return "gemma"
    return None


def heuristic_candidates(model: str) -> list[str]:
    model = model.strip()
    if "/" in model:
        return [model]

    candidates: list[str] = []
    family = infer_family(model)
    if family == "qwen":
        candidates.append(f"Qwen/{model}")
    elif family == "deepseek":
        candidates.append(f"deepseek-ai/{model}")
    elif family == "llama":
        candidates.append(f"meta-llama/{model}")
    elif family == "mistral":
        candidates.append(f"mistralai/{model}")
    elif family == "gemma":
        candidates.append(f"google/{model}")

    candidates.append(model)
    return dedupe(candidates)


def search_hf_candidates(model: str, timeout: float) -> tuple[list[str], list[FetchResult]]:
    query = urllib.parse.quote(model)
    url = f"https://huggingface.co/api/models?search={query}&limit=10&full=false"
    text, source = fetch_text("huggingface_search", url, timeout)
    rows = parse_json(text)
    results: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            repo_id = row.get("id") if isinstance(row, dict) else None
            if isinstance(repo_id, str):
                results.append(repo_id)
    return rank_candidates(model, results), [source]


def rank_candidates(model: str, candidates: list[str]) -> list[str]:
    family = infer_family(model)
    needle = model.lower().replace("_", "-")

    def score(repo_id: str) -> tuple[int, str]:
        lowered = repo_id.lower().replace("_", "-")
        points = 0
        if lowered.endswith("/" + needle):
            points -= 50
        if needle in lowered:
            points -= 20
        if family == "qwen" and lowered.startswith("qwen/"):
            points -= 20
        if family == "deepseek" and lowered.startswith("deepseek-ai/"):
            points -= 20
        if any(tag in lowered for tag in ("gguf", "awq", "gptq", "exl2")) and not any(
            tag in needle for tag in ("gguf", "awq", "gptq", "exl2")
        ):
            points += 15
        return points, repo_id

    return [repo for repo in sorted(dedupe(candidates), key=score)]


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def first_json_from_urls(
    urls: list[tuple[str, str, str]], timeout: float, sources: list[FetchResult]
) -> tuple[Any | None, str | None]:
    for name, content_type, url in urls:
        text, source = fetch_text(name, url, timeout)
        source.content_type = content_type
        data = parse_json(text)
        if data is not None:
            source.used = True
            sources.append(source)
            return data, url
        sources.append(source)
    return None, None


def first_text_from_urls(
    urls: list[tuple[str, str, str]], timeout: float, sources: list[FetchResult]
) -> tuple[str | None, str | None]:
    for name, content_type, url in urls:
        text, source = fetch_text(name, url, timeout)
        source.content_type = content_type
        if text:
            source.used = True
            sources.append(source)
            return text, url
        sources.append(source)
    return None, None


def resolve_repo(model: str, timeout: float, sources: list[FetchResult]) -> str:
    candidates = heuristic_candidates(model)
    if "/" not in model:
        searched, search_sources = search_hf_candidates(model, timeout)
        sources.extend(search_sources)
        candidates = rank_candidates(model, candidates + searched)

    for repo_id in candidates:
        if "/" not in repo_id:
            continue
        url = f"https://huggingface.co/{quote_repo(repo_id)}/raw/main/config.json"
        text, source = fetch_text("huggingface_config_probe", url, timeout)
        data = parse_json(text)
        if isinstance(data, dict):
            source.used = True
            sources.append(source)
            return repo_id
        sources.append(source)

    return candidates[0]


def collect_model_data(model: str, timeout: float) -> dict[str, Any]:
    sources: list[FetchResult] = []
    repo_id = resolve_repo(model, timeout, sources)
    quoted = quote_repo(repo_id)

    hf_base = f"https://huggingface.co/{quoted}/raw/main"
    ms_base = f"https://modelscope.cn/models/{quoted}/resolve/master"

    config, config_url = first_json_from_urls(
        [
            ("huggingface_config", "config.json", f"{hf_base}/config.json"),
            ("modelscope_config", "config.json", f"{ms_base}/config.json"),
        ],
        timeout,
        sources,
    )
    if not isinstance(config, dict):
        config = {}

    index, index_url = first_json_from_urls(
        [
            ("huggingface_safetensors_index", "model.safetensors.index.json", f"{hf_base}/model.safetensors.index.json"),
            ("modelscope_safetensors_index", "model.safetensors.index.json", f"{ms_base}/model.safetensors.index.json"),
        ],
        timeout,
        sources,
    )
    if not isinstance(index, dict):
        index = {}

    readme, readme_url = first_text_from_urls(
        [
            ("huggingface_readme", "README.md", f"{hf_base}/README.md"),
            ("modelscope_readme", "README.md", f"{ms_base}/README.md"),
        ],
        timeout,
        sources,
    )

    return {
        "input_model": model,
        "resolved_repo": repo_id,
        "config": config,
        "config_url": config_url,
        "safetensors_index": index,
        "safetensors_index_url": index_url,
        "readme": readme or "",
        "readme_url": readme_url,
        "sources": sources,
    }


def parse_model_size_from_name(model: str) -> tuple[float | None, float | None]:
    lowered = model.lower()
    total_b: float | None = None
    active_b: float | None = None

    active_match = re.search(r"(?:^|[-_/])a(\d+(?:\.\d+)?)b(?:$|[-_/])", lowered)
    if active_match:
        active_b = float(active_match.group(1))

    matches = re.findall(r"(\d+(?:\.\d+)?)\s*b", lowered)
    if matches:
        total_b = float(matches[0])

    return total_b, active_b


def parse_model_size_from_readme(readme: str) -> tuple[float | None, float | None]:
    total_b: float | None = None
    active_b: float | None = None
    total_patterns = [
        r"number of parameters[^0-9]{0,40}(\d+(?:\.\d+)?)\s*b",
        r"(\d+(?:\.\d+)?)\s*billion total parameters",
        r"total parameters[^0-9]{0,40}(\d+(?:\.\d+)?)\s*b",
    ]
    active_patterns = [
        r"(\d+(?:\.\d+)?)\s*billion activated parameters",
        r"activated parameters[^0-9]{0,40}(\d+(?:\.\d+)?)\s*b",
        r"active parameters[^0-9]{0,40}(\d+(?:\.\d+)?)\s*b",
    ]
    lowered = readme.lower()
    for pattern in total_patterns:
        match = re.search(pattern, lowered)
        if match:
            total_b = float(match.group(1))
            break
    for pattern in active_patterns:
        match = re.search(pattern, lowered)
        if match:
            active_b = float(match.group(1))
            break
    return total_b, active_b


def precision_to_bytes(value: str | None, default: float = 2.0) -> float:
    if not value:
        return default
    lowered = value.lower()
    for key, bytes_per_value in PRECISION_BYTES.items():
        if key in lowered:
            return bytes_per_value
    return default


def infer_weight_precision(model: str, config: dict[str, Any], requested: str) -> tuple[str, float, list[str]]:
    notes: list[str] = []
    if requested != "auto":
        return requested, precision_to_bytes(requested), notes

    lowered = model.lower()
    for key in ("awq", "gptq", "int4", "4bit", "int8", "8bit", "fp8"):
        if key in lowered:
            canonical = "int4" if key in ("awq", "gptq", "int4", "4bit") else ("int8" if key in ("int8", "8bit") else "fp8")
            notes.append(f"weight precision inferred from model name: {canonical}")
            return canonical, precision_to_bytes(canonical), notes

    dtype = config.get("torch_dtype") or config.get("dtype")
    if isinstance(dtype, str):
        return dtype, precision_to_bytes(dtype), notes

    return "bf16_assumed", 2.0, ["weight precision defaulted to BF16/FP16 class"]


def fallback_architecture(param_b: float | None) -> dict[str, Any]:
    if param_b is None:
        param_b = 32.0
    if param_b <= 3:
        return {"num_hidden_layers": 28, "hidden_size": 2048, "num_attention_heads": 16, "num_key_value_heads": 8, "head_dim": 128}
    if param_b <= 9:
        return {"num_hidden_layers": 32, "hidden_size": 4096, "num_attention_heads": 32, "num_key_value_heads": 8, "head_dim": 128}
    if param_b <= 15:
        return {"num_hidden_layers": 40, "hidden_size": 5120, "num_attention_heads": 40, "num_key_value_heads": 8, "head_dim": 128}
    if param_b <= 34:
        return {"num_hidden_layers": 64, "hidden_size": 5120, "num_attention_heads": 40, "num_key_value_heads": 8, "head_dim": 128}
    if param_b <= 80:
        return {"num_hidden_layers": 80, "hidden_size": 8192, "num_attention_heads": 64, "num_key_value_heads": 8, "head_dim": 128}
    return {"num_hidden_layers": 96, "hidden_size": 12288, "num_attention_heads": 96, "num_key_value_heads": 8, "head_dim": 128}


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(number)


def detect_moe(config: dict[str, Any], model: str, active_b: float | None) -> bool:
    if active_b is not None:
        return True
    lowered = model.lower()
    if "moe" in lowered or re.search(r"[-_/]a\d+(?:\.\d+)?b", lowered):
        return True
    for key in ("num_experts", "num_local_experts", "n_routed_experts", "num_experts_per_tok", "num_experts_per_token"):
        if key in config:
            return True
    return False


def scenario_profile(scenario: str) -> tuple[str, dict[str, Any]]:
    lowered = scenario.lower()
    matches: list[tuple[int, str, dict[str, Any]]] = []
    for name, profile in DEFAULT_PROFILES.items():
        for alias in profile["aliases"]:
            if alias in lowered:
                matches.append((len(alias), name, profile))
    if matches:
        _, name, profile = sorted(matches, reverse=True)[0]
        return name, profile
    return "agent", DEFAULT_PROFILES["agent"]


def production_guidance(scenario_name: str, profile: dict[str, Any], provided: dict[str, Any]) -> dict[str, Any]:
    required = list(profile.get("production_required_inputs", ()))
    missing = [key for key in required if provided.get(key) in (None, "")]
    guidance = profile.get(
        "production_guidance",
        f"{scenario_name} production sizing requires explicit business traffic, context, and latency targets.",
    )
    return {
        "status": "requires_inputs" if missing else "ready_for_explicit_estimate",
        "required_inputs": missing,
        "guidance": guidance,
        "why_no_default": "生产容量没有通用默认值；必须由实际并发、上下文、请求到达率和 SLA 推导，不能把低要求或正常体验基线当成生产承诺。",
    }


def estimate(
    facts: dict[str, Any],
    context_tokens: int,
    active_sessions: int,
    target_tps_per_session: float,
    weight_bytes: float,
    kv_value_bytes: float,
    active_param_b: float | None,
) -> dict[str, Any]:
    layers = facts["num_hidden_layers"]
    kv_heads = facts["num_key_value_heads"]
    head_dim = facts["head_dim"]

    kv_bytes = active_sessions * context_tokens * layers * 2 * kv_heads * head_dim * kv_value_bytes
    runtime_overhead = max(2 * GIB, 0.15 * (weight_bytes + kv_bytes))
    accelerator_memory = weight_bytes + kv_bytes + runtime_overhead
    aggregate_tps = active_sessions * target_tps_per_session
    memory_bandwidth_tbps = weight_bytes * aggregate_tps / 1e12

    active_params = active_param_b if active_param_b is not None else facts.get("parameter_count_b")
    decode_tflops = None
    if active_params is not None:
        decode_tflops = 2 * active_params * 1e9 * aggregate_tps / 1e12

    system_ram = max(32.0, accelerator_memory / GIB * 1.2)
    storage = max(100.0, (weight_bytes / GIB) * 3)

    return {
        "context_tokens": context_tokens,
        "active_sessions": active_sessions,
        "target_tps_per_session": target_tps_per_session,
        "aggregate_decode_tps": round(aggregate_tps, 2),
        "weight_gib": round(weight_bytes / GIB, 2),
        "kv_cache_gib": round(kv_bytes / GIB, 2),
        "runtime_overhead_gib": round(runtime_overhead / GIB, 2),
        "accelerator_memory_required_gib": round(accelerator_memory / GIB, 2),
        "memory_bandwidth_tbps_min": round(memory_bandwidth_tbps, 2),
        "decode_compute_tflops_min": round(decode_tflops, 2) if decode_tflops is not None else None,
        "system_ram_gib_min": round(system_ram, 0),
        "cpu_cores_min": 8 if active_sessions <= 2 else (16 if active_sessions <= 8 else 32),
        "storage_gib_min": round(storage, 0),
        "network_min": "1GbE" if active_sessions <= 2 else ("10GbE" if active_sessions <= 16 else "25GbE+"),
    }


def build_model_facts(data: dict[str, Any], precision: str, kv_precision: str) -> dict[str, Any]:
    model = data["resolved_repo"]
    config = data["config"]
    readme = data["readme"]
    index = data["safetensors_index"]
    missing: list[str] = []
    notes: list[str] = []

    name_total_b, name_active_b = parse_model_size_from_name(model)
    readme_total_b, readme_active_b = parse_model_size_from_readme(readme)

    weight_precision, weight_value_bytes, precision_notes = infer_weight_precision(model, config, precision)
    notes.extend(precision_notes)

    total_size = None
    if isinstance(index.get("metadata"), dict):
        total_size = as_float(index["metadata"].get("total_size"))

    parameter_count_b = None
    parameter_source = None
    if total_size is not None and weight_value_bytes > 0:
        parameter_count_b = total_size / weight_value_bytes / 1e9
        parameter_source = "safetensors_index_total_size / precision"
    elif readme_total_b is not None:
        parameter_count_b = readme_total_b
        parameter_source = "readme"
    elif name_total_b is not None:
        parameter_count_b = name_total_b
        parameter_source = "model_name"

    active_parameter_b = readme_active_b or name_active_b
    if active_parameter_b is None:
        active_parameter_b = parameter_count_b

    fallback = fallback_architecture(parameter_count_b)
    facts: dict[str, Any] = {}
    for key in ("num_hidden_layers", "hidden_size", "num_attention_heads", "num_key_value_heads"):
        value = config.get(key)
        if value is None and key == "num_key_value_heads":
            value = config.get("num_kv_heads")
        if value is None:
            value = fallback[key]
            missing.append(key)
        facts[key] = int(value)

    if config.get("head_dim") is not None:
        facts["head_dim"] = int(config["head_dim"])
    elif facts.get("hidden_size") and facts.get("num_attention_heads"):
        heads = facts["num_attention_heads"] or 1
        facts["head_dim"] = int(facts["hidden_size"] / heads)
        notes.append("head_dim derived from hidden_size / num_attention_heads")
    else:
        facts["head_dim"] = fallback["head_dim"]
        missing.append("head_dim")

    max_context = as_int(config.get("max_position_embeddings") or config.get("n_positions") or config.get("seq_length"))
    if max_context is None:
        max_context = extract_context_from_readme(readme)
    if max_context is None:
        max_context = 32_768
        missing.append("max_position_embeddings")
    readme_contexts = extract_context_candidates_from_readme(readme)

    if total_size is None:
        if parameter_count_b is not None:
            total_size = parameter_count_b * 1e9 * weight_value_bytes
            missing.append("model.safetensors.index.json metadata.total_size")
        else:
            total_size = 32.0 * 1e9 * weight_value_bytes
            missing.append("parameter_count")

    kv_bytes_per_value = precision_to_bytes(kv_precision, precision_to_bytes(str(config.get("torch_dtype") or ""), 2.0))

    facts.update(
        {
            "input_model": data["input_model"],
            "resolved_repo": data["resolved_repo"],
            "model_type": config.get("model_type"),
            "architectures": config.get("architectures"),
            "torch_dtype": config.get("torch_dtype"),
            "weight_precision": weight_precision,
            "weight_bytes_per_value": weight_value_bytes,
            "kv_precision": kv_precision if kv_precision != "auto" else f"auto_from_{config.get('torch_dtype') or 'bf16_assumed'}",
            "kv_bytes_per_value": kv_bytes_per_value,
            "official_weight_size_bytes": int(total_size),
            "official_weight_size_gib": round(total_size / GIB, 2),
            "parameter_count_b": round(parameter_count_b, 2) if parameter_count_b is not None else None,
            "parameter_count_source": parameter_source,
            "active_parameter_count_b": round(active_parameter_b, 2) if active_parameter_b is not None else None,
            "max_position_embeddings": max_context,
            "readme_context_candidates": readme_contexts,
            "rope_scaling": config.get("rope_scaling"),
            "is_moe": detect_moe(config, model, name_active_b or readme_active_b),
        }
    )

    return {"facts": facts, "missing_fields": dedupe(missing), "notes": notes}


def extract_context_from_readme(readme: str) -> int | None:
    patterns = [
        r"context length[^0-9]{0,30}(\d{1,3}(?:,\d{3})+|\d+)\s*k?",
        r"context window[^0-9]{0,30}(\d{1,3}(?:,\d{3})+|\d+)\s*k?",
        r"(\d{2,3})\s*k\s+tokens",
    ]
    lowered = readme.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        value = match.group(1).replace(",", "")
        number = int(value)
        if number < 1024 and "k" in match.group(0):
            number *= 1024
        return number
    return None


def extract_context_candidates_from_readme(readme: str) -> list[int]:
    contexts: list[int] = []
    for line in readme.splitlines():
        lowered = line.lower()
        if "context" not in lowered and "上下文" not in lowered:
            continue
        for match in re.finditer(r"(\d{1,3}(?:,\d{3})+|\d+)\s*k?", lowered):
            raw = match.group(0)
            number = int(match.group(1).replace(",", ""))
            if number < 1024 and "k" in raw:
                number *= 1024
            if number >= 4096:
                contexts.append(number)
    return sorted(set(contexts))


def confidence_level(config: dict[str, Any], index: dict[str, Any], missing_fields: list[str]) -> dict[str, Any]:
    has_config = bool(config)
    has_index = bool(index.get("metadata", {}).get("total_size")) if isinstance(index.get("metadata"), dict) else False
    critical_missing = {"num_hidden_layers", "hidden_size", "num_key_value_heads", "head_dim"}
    if has_config and has_index and not critical_missing.intersection(missing_fields):
        return {"level": "high", "reason": "structured config and safetensors index were found"}
    if has_config and len(critical_missing.intersection(missing_fields)) <= 1:
        return {"level": "medium", "reason": "structured config was found, but weight index or some fields are missing"}
    return {"level": "low", "reason": "estimate relies on model-name/default architecture fallback"}


def human_summary(result: dict[str, Any]) -> str:
    facts = result["model_facts"]
    requested = result["requested_estimate"]
    lines = [
        f"Model: {facts['resolved_repo']}",
        f"Confidence: {result['confidence']['level']} - {result['confidence']['reason']}",
        f"Parameters: {facts.get('parameter_count_b')}B, weights: {facts.get('official_weight_size_gib')} GiB",
        f"Architecture: layers={facts['num_hidden_layers']}, hidden={facts['hidden_size']}, heads={facts['num_attention_heads']}, kv_heads={facts['num_key_value_heads']}, head_dim={facts['head_dim']}",
        f"Requested estimate: context={requested['context_tokens']}, sessions={requested['active_sessions']}, accelerator_memory={requested['accelerator_memory_required_gib']} GiB, kv_cache={requested['kv_cache_gib']} GiB",
        f"Bandwidth min: {requested['memory_bandwidth_tbps_min']} TB/s, decode compute min: {requested['decode_compute_tflops_min']} TFLOPS",
    ]
    if result["missing_fields"]:
        lines.append("Missing/defaulted fields: " + ", ".join(result["missing_fields"]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Probe public LLM metadata and estimate hardware requirements.")
    parser.add_argument("--model", required=True, help="Model name or repo id, e.g. Qwen/Qwen3-32B or Qwen3-32B.")
    parser.add_argument("--scenario", default="Agent", help="Usage scenario, e.g. OpenClaw, RAG, coding agent.")
    parser.add_argument("--context-tokens", type=int, default=None, help="Requested context length in tokens.")
    parser.add_argument("--active-sessions", type=int, default=None, help="Concurrent active sessions.")
    parser.add_argument("--target-tps-per-session", type=float, default=None, help="Target decode tokens/s per active session.")
    parser.add_argument("--production-context-tokens", type=int, default=None, help="Production context length. Enables numeric production estimate when paired with production active sessions.")
    parser.add_argument("--production-active-sessions", type=int, default=None, help="Production concurrent active sessions. Enables numeric production estimate when paired with production context.")
    parser.add_argument("--production-target-tps-per-session", type=float, default=None, help="Production target decode tokens/s per active session.")
    parser.add_argument("--precision", default="auto", help="Weight precision: auto, bf16, fp16, fp32, fp8, int8, int4.")
    parser.add_argument("--kv-precision", default="auto", help="KV cache precision: auto, bf16, fp16, fp8, int8.")
    parser.add_argument("--timeout", type=float, default=12.0, help="Per-request timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    args = parser.parse_args(argv)

    data = collect_model_data(args.model, args.timeout)
    built = build_model_facts(data, args.precision.lower(), args.kv_precision.lower())
    facts = built["facts"]
    scenario_name, profile = scenario_profile(args.scenario)

    normal_profile = profile["tiers"]["normal"]
    context_tokens = args.context_tokens or normal_profile["context_tokens"]
    active_sessions = args.active_sessions or normal_profile["active_sessions"]
    target_tps = args.target_tps_per_session or normal_profile["target_tps"]
    weight_bytes = float(facts["official_weight_size_bytes"])
    kv_value_bytes = float(facts["kv_bytes_per_value"])
    active_param_b = facts.get("active_parameter_count_b")

    requested = estimate(facts, context_tokens, active_sessions, target_tps, weight_bytes, kv_value_bytes, active_param_b)

    tiers = {}
    for tier_name, tier_profile in profile["tiers"].items():
        tier = estimate(
            facts,
            tier_profile["context_tokens"],
            tier_profile["active_sessions"],
            tier_profile["target_tps"],
            weight_bytes,
            kv_value_bytes,
            active_param_b,
        )
        tier["status"] = "estimated_from_scenario_baseline"
        tier["basis"] = tier_profile.get("basis")
        if "llm_calls_per_task" in tier_profile:
            tier["llm_calls_per_task"] = tier_profile["llm_calls_per_task"]
        if tier_name == "normal":
            tier["cpu_cores_min"] = max(tier["cpu_cores_min"], 16)
            tier["system_ram_gib_min"] = max(tier["system_ram_gib_min"], 64)
            tier["network_min"] = "10GbE"
        tiers[tier_name] = tier
    if args.production_context_tokens and args.production_active_sessions:
        production_tps = args.production_target_tps_per_session or target_tps
        production = estimate(
            facts,
            args.production_context_tokens,
            args.production_active_sessions,
            production_tps,
            weight_bytes,
            kv_value_bytes,
            active_param_b,
        )
        production["status"] = "estimated_from_explicit_production_inputs"
        production["cpu_cores_min"] = max(production["cpu_cores_min"], 32)
        production["system_ram_gib_min"] = max(production["system_ram_gib_min"], 128)
        production["storage_gib_min"] = max(production["storage_gib_min"], round(facts["official_weight_size_gib"] * 4, 0))
        production["network_min"] = "25GbE+; multi-node or heavy Agent workloads should plan for 100GbE-class fabric"
        tiers["production"] = production
    else:
        tiers["production"] = production_guidance(
            scenario_name,
            profile,
            {
                "context_tokens": args.production_context_tokens,
                "active_sessions": args.production_active_sessions,
                "latency_or_target_tps_per_session": args.production_target_tps_per_session,
            },
        )

    result = {
        "input": {
            "model": args.model,
            "scenario": args.scenario,
            "context_tokens": args.context_tokens,
            "active_sessions": args.active_sessions,
            "target_tps_per_session": args.target_tps_per_session,
            "production_context_tokens": args.production_context_tokens,
            "production_active_sessions": args.production_active_sessions,
            "production_target_tps_per_session": args.production_target_tps_per_session,
            "precision": args.precision,
            "kv_precision": args.kv_precision,
        },
        "resolved_scenario": scenario_name,
        "default_assumptions": {
            "context_tokens": context_tokens,
            "active_sessions": active_sessions,
            "target_tps_per_session": target_tps,
            "profile_used": scenario_name,
            "tier_used_for_missing_inputs": "normal",
        },
        "model_facts": facts,
        "requested_estimate": requested,
        "deployment_tiers": tiers,
        "missing_fields": built["missing_fields"],
        "notes": built["notes"],
        "confidence": confidence_level(data["config"], data["safetensors_index"], built["missing_fields"]),
        "sources": [source.to_json() for source in data["sources"]],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(human_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
