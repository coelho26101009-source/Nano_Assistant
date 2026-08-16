"""Model routing, registry and provider abstraction for Nano.

The router is intentionally local-first, hardware-aware and policy-aware. It is
responsible for choosing the best model/provider for a given task while keeping
all model decisions behind a single abstraction layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
from typing import Any, Iterable

import httpx


class CapabilityState(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"

from core.config import load_config
from core.local_runtime import choose_model

logger = logging.getLogger("nano.model_router")


class PrivacyLevel(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    STRICT_LOCAL = "strict_local"


class TaskType(str, Enum):
    CHAT = "chat"
    GENERAL_REASONING = "general_reasoning"
    CODING = "coding"
    RESEARCH = "research"
    VISION = "vision"
    TOOL_USE = "tool_use"
    PLANNING = "planning"
    SUMMARIZATION = "summarization"
    MEMORY = "memory"
    CLASSIFICATION = "classification"


@dataclass
class ModelInfo:
    name: str
    provider: str
    context_window: int = 4096
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_coding: bool | None = None
    supports_reasoning: bool | None = None
    supports_streaming: bool | None = None
    supports_json: bool | None = None
    local: bool = False
    estimated_memory: float = 1.0
    speed_class: str = "medium"
    quality_class: str = "medium"
    online: bool = True
    health: str = "online"
    metadata: dict[str, Any] = field(default_factory=dict)
    capability_states: dict[str, str] = field(default_factory=dict)


@dataclass
class ModelRequest:
    task_type: str | TaskType = TaskType.CHAT
    complexity: str = "normal"
    requires_tools: bool = False
    requires_vision: bool = False
    requires_coding: bool = False
    requires_reasoning: bool = False
    privacy_level: str | PrivacyLevel = PrivacyLevel.NORMAL
    context_size: int = 2048
    latency_preference: str = "balanced"
    local_only: bool = False
    preferred_provider: str | None = None
    max_model_memory_gb: float | None = None


class ModelProvider:
    """Abstract provider contract for model execution."""

    def __init__(self, name: str, *, online: bool = True):
        self.name = name
        self.online = bool(online)
        self._models: list[ModelInfo] = []

    @property
    def models(self) -> list[ModelInfo]:
        return list(self._models)

    @property
    def health(self) -> str:
        return "online" if self.online else "offline"

    async def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "health": self.health, "online": self.online, "models": len(self.models)}

    def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    def get_model(self, name: str) -> ModelInfo | None:
        for model in self._models:
            if model.name == name:
                return model
        return None

    async def generate(self, request: ModelRequest, messages: list[dict[str, Any]], *, stream: bool = False, **kwargs):
        raise NotImplementedError

    async def stream(self, request: ModelRequest, messages: list[dict[str, Any]], **kwargs):
        raise NotImplementedError


class OllamaProvider(ModelProvider):
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        super().__init__("ollama", online=False)
        self.base_url = base_url.rstrip("/")
        self.api_url = self.base_url + "/api"

    def _capability_states(self, *, model_name: str, capabilities: Iterable[str], details: dict[str, Any] | None = None) -> dict[str, str]:
        capability_names = {str(value).lower() for value in capabilities}
        lower_name = model_name.lower()
        details = details or {}
        family = str(details.get("family") or "").lower()
        families = [str(value).lower() for value in (details.get("families") or [])]

        is_coder = "coder" in capability_names or "coding" in capability_names or any(token in lower_name for token in ("coder", "code")) or any(token in family for token in ("coder", "code")) or any(token in token_list for token_list in families for token in ("coder", "code"))
        is_vision = "vision" in capability_names or any(token in lower_name for token in ("vision", "llava", "miniomni")) or any(token in family for token in ("vision", "llava")) or any(token in family_list for family_list in families for token in ("vision", "llava"))
        is_reasoning = "thinking" in capability_names or "reasoning" in capability_names or any(token in lower_name for token in ("qwen3", "deepseek", "phi", "mistral", "llama3"))
        has_tools = "tools" in capability_names or any(token in lower_name for token in ("qwen", "llama", "mistral", "phi", "deepseek"))
        has_streaming = True
        has_json = "json" in capability_names or "structured" in capability_names

        states = {
            "tools": CapabilityState.SUPPORTED if has_tools else CapabilityState.UNKNOWN,
            "vision": CapabilityState.SUPPORTED if is_vision else CapabilityState.UNKNOWN,
            "coding": CapabilityState.SUPPORTED if is_coder else CapabilityState.UNKNOWN,
            "reasoning": CapabilityState.SUPPORTED if is_reasoning else CapabilityState.UNKNOWN,
            "streaming": CapabilityState.SUPPORTED if has_streaming else CapabilityState.UNKNOWN,
            "json": CapabilityState.SUPPORTED if has_json else CapabilityState.UNKNOWN,
        }
        if "tool" in capability_names or "tools" in capability_names:
            states["tools"] = CapabilityState.SUPPORTED
        if "vision" in capability_names and "llava" not in capability_names:
            states["vision"] = CapabilityState.SUPPORTED
        if "coding" in capability_names or "coder" in capability_names:
            states["coding"] = CapabilityState.SUPPORTED
        if "thinking" in capability_names or "reasoning" in capability_names:
            states["reasoning"] = CapabilityState.SUPPORTED
        return {key: value.value for key, value in states.items()}

    def _build_model_entry(self, raw_model: Any) -> ModelInfo:
        raw = raw_model or {}
        name = str(raw.get("name") or raw.get("model") or raw)
        details = raw.get("details") or {}
        capabilities = [str(item).lower() for item in (raw.get("capabilities") or [])]
        metadata = {
            "source": "ollama_tags",
            "details": details,
            "capabilities": capabilities,
            "size": raw.get("size"),
            "modified_at": raw.get("modified_at"),
        }
        family = str((details.get("family") or "")).lower()
        families = [str(item).lower() for item in (details.get("families") or [])]
        parameter_size = str(details.get("parameter_size") or "")
        context_length = details.get("context_length") or 4096
        lower_name = name.lower()
        supports_vision = any(token in lower_name for token in ("vision", "llava", "miniomni")) or any(token in family for token in ("vision", "llava")) or any(token in families for token in ("vision", "llava"))
        supports_coding = any(token in lower_name for token in ("coder", "code")) or any(token in family for token in ("coder", "code")) or any(token in families for token in ("coder", "code")) or "coding" in capabilities or "coder" in capabilities
        supports_reasoning = "thinking" in capabilities or "reasoning" in capabilities or any(token in lower_name for token in ("qwen3", "deepseek", "phi", "mistral", "llama3")) or any(token in family for token in ("qwen3", "deepseek", "phi", "mistral", "llama3"))
        supports_tools = "tools" in capabilities or any(token in lower_name for token in ("qwen", "llama", "mistral", "phi", "deepseek"))
        supports_streaming = True
        supports_json = "json" in capabilities or "structured" in capabilities
        estimated_memory = 1.5
        if any(token in lower_name for token in ("7b", "8b", "8b-instruct", "7b-instruct")):
            estimated_memory = 5.0
        elif any(token in lower_name for token in ("3b", "3b-instruct", "1.5b")):
            estimated_memory = 2.0
        elif any(token in lower_name for token in ("14b", "13b", "32b")):
            estimated_memory = 8.0
        if supports_vision:
            estimated_memory = max(estimated_memory, 4.5)
        capability_states = self._capability_states(model_name=name, capabilities=capabilities, details=details)
        return ModelInfo(
            name=name,
            provider="ollama",
            context_window=int(context_length or 4096),
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            supports_coding=supports_coding,
            supports_reasoning=supports_reasoning,
            supports_streaming=supports_streaming,
            supports_json=supports_json,
            local=True,
            estimated_memory=estimated_memory,
            speed_class="fast" if any(token in lower_name for token in ("3b", "1.5b")) else "balanced",
            quality_class="strong" if any(token in lower_name for token in ("7b", "8b", "14b", "32b")) else "balanced",
            online=True,
            health="online",
            metadata={**metadata, "parameter_size": parameter_size, "family": family},
            capability_states=capability_states,
        )

    def discover_models(self) -> list[ModelInfo]:
        try:
            response = httpx.get(self.base_url + "/api/tags", timeout=5.0)
            if not response.is_success:
                self.online = False
                self._models = []
                return []
            payload = response.json() if response.content else {}
            models = payload.get("models") or []
            discovered: list[ModelInfo] = []
            for model in models:
                entry = self._build_model_entry(model)
                if entry.name:
                    discovered.append(entry)
            self.online = bool(discovered)
            self._models = discovered
            return discovered
        except Exception:
            self.online = False
            self._models = []
            return []

    async def health_check(self) -> dict[str, Any]:
        discovered = self.discover_models()
        return {"provider": self.name, "health": "online" if discovered else "offline", "online": bool(discovered), "models": len(discovered)}

    async def generate(self, request: ModelRequest, messages: list[dict[str, Any]], *, stream: bool = False, **kwargs):
        model_name = kwargs.get("model_name") or (self._models[0].name if self._models else "llama3.2")
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": bool(stream),
            "options": {"temperature": kwargs.get("temperature", 0.7), "num_ctx": kwargs.get("num_ctx", max(2048, request.context_size))},
        }
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        async with httpx.AsyncClient(timeout=kwargs.get("timeout", 120.0)) as client:
            response = await client.post(self.base_url + "/api/chat", json=payload)
            response.raise_for_status()
            return response.json()


class CloudProvider(ModelProvider):
    def __init__(self, api_key: str | None = None, default_model: str = "llama-3.3-70b-versatile"):
        super().__init__("cloud", online=bool(api_key))
        self.api_key = api_key or ""
        self.default_model = default_model
        self._models = [
            ModelInfo(
                name=default_model,
                provider="cloud",
                context_window=128000,
                supports_tools=True,
                supports_vision=False,
                supports_coding=True,
                supports_reasoning=True,
                supports_streaming=True,
                supports_json=True,
                local=False,
                estimated_memory=12.0,
                speed_class="balanced",
                quality_class="high",
                online=bool(api_key),
                health="online" if api_key else "offline",
            )
        ]

    async def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "health": "online" if self.online else "offline", "online": self.online, "models": len(self.models)}

    async def generate(self, request: ModelRequest, messages: list[dict[str, Any]], *, stream: bool = False, **kwargs):
        if not self.api_key:
            raise RuntimeError("cloud_provider_unavailable")
        payload = {"model": kwargs.get("model_name") or self.default_model, "messages": messages, "stream": bool(stream), "temperature": kwargs.get("temperature", 0.7)}
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        async with httpx.AsyncClient(timeout=kwargs.get("timeout", 120.0)) as client:
            response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            response.raise_for_status()
            return response.json()


class FutureProvider(ModelProvider):
    def __init__(self, name: str = "future_provider"):
        super().__init__(name, online=False)


class ModelRegistry:
    def __init__(self, providers: Iterable[ModelProvider] | None = None):
        self.providers = list(providers or [])

    def register(self, provider: ModelProvider) -> None:
        self.providers.append(provider)

    def models(self) -> list[ModelInfo]:
        result: list[ModelInfo] = []
        for provider in self.providers:
            result.extend(provider.list_models())
        return result

    def by_provider(self, provider_name: str) -> list[ModelInfo]:
        return [model for model in self.models() if model.provider == provider_name]

    def get(self, name: str) -> ModelInfo | None:
        for model in self.models():
            if model.name == name:
                return model
        return None


class ModelRouter:
    """Selects the best available provider/model for the active task."""

    def __init__(self, config: dict | None = None, *, cloud_api_key: str | None = None):
        cfg = config if config is not None else load_config()
        model_cfg = (cfg.get("model_router") or {})
        local_cfg = (cfg.get("local") or {})
        self.config = cfg
        self.local_first = bool(model_cfg.get("local_first", True))
        self.default_provider = str(model_cfg.get("default_provider") or "ollama")
        self.providers = [
            OllamaProvider(base_url=str(local_cfg.get("url") or "http://127.0.0.1:11434")),
            CloudProvider(api_key=cloud_api_key or str(cfg.get("groq_api_key") or ""), default_model=str(cfg.get("groq_model") or "llama-3.3-70b-versatile")),
        ]
        self.registry = ModelRegistry(self.providers)
        self.routing_weights = {
            "capability": float(model_cfg.get("routing", {}).get("capability_weight", 4.0)),
            "quality": float(model_cfg.get("routing", {}).get("quality_weight", 2.5)),
            "latency": float(model_cfg.get("routing", {}).get("speed_weight", 2.0)),
            "privacy": float(model_cfg.get("routing", {}).get("privacy_weight", 4.0)),
            "context": float(model_cfg.get("routing", {}).get("context_weight", 1.5)),
            "resource": float(model_cfg.get("routing", {}).get("resource_weight", 1.5)),
        }
        self.refresh()

    async def health(self) -> dict[str, Any]:
        statuses = []
        for provider in self.providers:
            statuses.append(await provider.health_check())
        return {"providers": statuses, "default_provider": self.default_provider}

    def refresh(self) -> None:
        for provider in self.providers:
            if hasattr(provider, "discover_models"):
                provider.discover_models()

    def models(self) -> list[ModelInfo]:
        self.refresh()
        return self.registry.models()

    def _capability_state(self, model: ModelInfo, capability: str) -> str:
        states = getattr(model, "capability_states", {}) or {}
        key = str(capability).lower().replace("-", "_")
        if key in states:
            return str(states[key]).upper()
        mapping = {
            "tools": model.supports_tools,
            "vision": model.supports_vision,
            "coding": model.supports_coding,
            "reasoning": model.supports_reasoning,
            "streaming": model.supports_streaming,
            "json": model.supports_json,
        }
        value = mapping.get(key)
        if value is True:
            return CapabilityState.SUPPORTED.value
        if value is False:
            return CapabilityState.UNSUPPORTED.value
        return CapabilityState.UNKNOWN.value

    def _privacy_allowed(self, privacy_level: str | PrivacyLevel) -> bool:
        value = str(privacy_level).lower()
        if value in {"high", "strict_local"}:
            return False
        return True

    def _resource_fit(self, model: ModelInfo, request: ModelRequest) -> float:
        limit = request.max_model_memory_gb
        if limit is not None and model.estimated_memory > limit:
            return -999
        return 1.0 / max(1.0, model.estimated_memory)

    def _score_model(self, model: ModelInfo, request: ModelRequest) -> float:
        score = 0.0
        if request.requires_tools:
            state = self._capability_state(model, "tools")
            if state == CapabilityState.SUPPORTED.value:
                score += self.routing_weights["capability"] * 3
            elif state == CapabilityState.UNKNOWN.value:
                score += 0.4
        if request.requires_vision:
            state = self._capability_state(model, "vision")
            if state == CapabilityState.SUPPORTED.value:
                score += self.routing_weights["capability"] * 4
            elif state == CapabilityState.UNKNOWN.value:
                score += 0.5
        if request.requires_coding:
            state = self._capability_state(model, "coding")
            if state == CapabilityState.SUPPORTED.value:
                score += self.routing_weights["capability"] * 4
            elif state == CapabilityState.UNKNOWN.value:
                score += 0.6
        if request.requires_reasoning:
            state = self._capability_state(model, "reasoning")
            if state == CapabilityState.SUPPORTED.value:
                score += self.routing_weights["capability"] * 3
            elif state == CapabilityState.UNKNOWN.value:
                score += 0.5
        if request.task_type in {TaskType.CHAT, TaskType.SUMMARIZATION, TaskType.MEMORY}:
            score += self.routing_weights["latency"] * (1.5 if model.speed_class == "fast" else 0.8)
        if request.task_type in {TaskType.GENERAL_REASONING, TaskType.CODING}:
            score += self.routing_weights["quality"] * (1.5 if model.quality_class in {"strong", "high"} else 0.8)
        if request.context_size and model.context_window >= request.context_size:
            score += self.routing_weights["context"]
        if not model.local:
            score -= self.routing_weights["privacy"] * 2.0
        score += self.routing_weights["resource"] * self._resource_fit(model, request)
        if request.privacy_level in {PrivacyLevel.HIGH, PrivacyLevel.STRICT_LOCAL}:
            if model.local:
                score += self.routing_weights["privacy"] * 2.0
            else:
                score -= self.routing_weights["privacy"] * 10.0
        if request.latency_preference == "fast" and model.speed_class == "fast":
            score += self.routing_weights["latency"]
        if request.local_only and not model.local:
            score -= 999
        return score

    def _capability_ok(self, model: ModelInfo, requirement: str, *, required: bool) -> bool:
        state = self._capability_state(model, requirement)
        if state == CapabilityState.SUPPORTED.value:
            return True
        if state == CapabilityState.UNKNOWN.value:
            return not required
        return False

    def explain_selection(self, request: ModelRequest | dict[str, Any]) -> dict[str, Any]:
        if isinstance(request, dict):
            request = ModelRequest(**request)
        privacy = request.privacy_level if isinstance(request.privacy_level, PrivacyLevel) else PrivacyLevel(str(request.privacy_level).lower())
        request.privacy_level = privacy
        candidates = [model for model in self.models() if model.online]
        if request.local_only:
            candidates = [model for model in candidates if model.local]
        if privacy in {PrivacyLevel.STRICT_LOCAL, PrivacyLevel.HIGH}:
            candidates = [model for model in candidates if model.local]
        if request.requires_vision:
            candidates = [model for model in candidates if self._capability_ok(model, "vision", required=True)]
        if request.requires_tools:
            candidates = [model for model in candidates if self._capability_ok(model, "tools", required=True)]
        if request.requires_coding:
            candidates = [model for model in candidates if self._capability_ok(model, "coding", required=True)]
        if request.requires_reasoning:
            candidates = [model for model in candidates if self._capability_ok(model, "reasoning", required=True)]
        scored = []
        for model in candidates:
            score = self._score_model(model, request)
            scored.append({
                "model": model.name,
                "provider": model.provider,
                "score": score,
                "accepted": True,
                "rejection_reason": None,
                "capability_states": {
                    "tools": self._capability_state(model, "tools"),
                    "vision": self._capability_state(model, "vision"),
                    "coding": self._capability_state(model, "coding"),
                    "reasoning": self._capability_state(model, "reasoning"),
                },
            })
        selection = self.select(request)
        explain = {
            "task": request.task_type,
            "selected": selection.get("model"),
            "provider": selection.get("provider"),
            "reason": selection.get("reason"),
            "candidates": scored,
        }
        return explain

    def select(self, request: ModelRequest | dict[str, Any], *, allow_fallback: bool = True) -> dict[str, Any]:
        if isinstance(request, dict):
            request = ModelRequest(**request)
        privacy = request.privacy_level if isinstance(request.privacy_level, PrivacyLevel) else PrivacyLevel(str(request.privacy_level).lower())
        request.privacy_level = privacy

        candidates = [model for model in self.models() if model.online]
        if request.local_only:
            candidates = [model for model in candidates if model.local]
        if privacy == PrivacyLevel.STRICT_LOCAL:
            candidates = [model for model in candidates if model.local]
        if privacy == PrivacyLevel.HIGH:
            candidates = [model for model in candidates if model.local]
        if request.requires_vision:
            candidates = [model for model in candidates if self._capability_ok(model, "vision", required=True)]
        if request.requires_tools:
            candidates = [model for model in candidates if self._capability_ok(model, "tools", required=True)]
        if request.requires_coding:
            candidates = [model for model in candidates if self._capability_ok(model, "coding", required=True)]
        if request.requires_reasoning:
            candidates = [model for model in candidates if self._capability_ok(model, "reasoning", required=True)]
        if request.max_model_memory_gb is not None:
            candidates = [model for model in candidates if model.estimated_memory <= request.max_model_memory_gb]
        if not candidates:
            return {
                "provider": "none",
                "model": None,
                "selected": None,
                "reason": "No compatible model available under current privacy and hardware constraints.",
                "fallback": False,
                "health": "unavailable",
            }

        ranked = sorted(candidates, key=lambda model: self._score_model(model, request), reverse=True)
        winner = ranked[0]
        if request.preferred_provider and winner.provider != request.preferred_provider:
            preferred = [model for model in ranked if model.provider == request.preferred_provider]
            if preferred:
                winner = preferred[0]
        reason = "capability fit"
        if request.task_type == TaskType.CODING:
            reason = "coding capability + local availability"
        elif request.task_type == TaskType.VISION:
            reason = "vision support + task fit"
        elif request.task_type == TaskType.TOOL_USE:
            reason = "tool support + provider readiness"
        elif request.privacy_level in {PrivacyLevel.HIGH, PrivacyLevel.STRICT_LOCAL}:
            reason = "privacy-first local model"
        elif request.latency_preference == "fast":
            reason = "low-latency model fit"
        return {
            "provider": winner.provider,
            "model": winner.name,
            "selected": winner,
            "reason": reason,
            "fallback": allow_fallback,
            "health": winner.health,
            "privacy_level": str(request.privacy_level),
        }

    async def generate(self, request: ModelRequest | dict[str, Any], messages: list[dict[str, Any]], *, stream: bool = False, **kwargs):
        selection = self.select(request)
        if selection["model"] is None:
            raise RuntimeError("no_model_available")
        provider = next((p for p in self.providers if p.name == selection["provider"]), None)
        if provider is None:
            raise RuntimeError("provider_not_found")
        return await provider.generate(request if isinstance(request, ModelRequest) else ModelRequest(**request), messages, stream=stream, model_name=selection["model"], **kwargs)

    async def stream(self, request: ModelRequest | dict[str, Any], messages: list[dict[str, Any]], **kwargs):
        selection = self.select(request)
        if selection["model"] is None:
            raise RuntimeError("no_model_available")
        provider = next((p for p in self.providers if p.name == selection["provider"]), None)
        if provider is None:
            raise RuntimeError("provider_not_found")
        return await provider.generate(request if isinstance(request, ModelRequest) else ModelRequest(**request), messages, stream=True, model_name=selection["model"], **kwargs)
