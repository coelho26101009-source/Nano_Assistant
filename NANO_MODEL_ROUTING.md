# Nano Model Routing

## Overview

The Nano brain now routes tasks through a Model Router instead of assuming a single hardcoded model. The routing decision is made centrally and is policy-aware, hardware-aware, privacy-aware, and capability-aware.

## Core idea

User -> Nano Brain -> Task Classification -> Model Router -> Best Available Model -> Tool / Agent / Response

The Model Router is responsible for choosing the best provider and model for the active task, while preserving the existing safety constraints from the Policy Engine.

## Providers

### OllamaProvider

- Local-first provider
- Uses the Ollama API
- Discovers models with /api/tags
- Marks model capabilities based on actual metadata when available and conservative heuristics when not
- Offline state is handled without crashing the Nano

### CloudProvider

- Optional fallback when API credentials are configured
- Uses the configured cloud model
- Kept as an explicit fallback, not the default path

### FutureProvider

- Extensible hook for future provider integrations

## Model registry

The registry holds a central list of ModelInfo objects with metadata similar to:

- name
- provider
- context_window
- supports_tools
- supports_vision
- supports_coding
- supports_reasoning
- supports_streaming
- supports_json
- local
- estimated_memory
- speed_class
- quality_class

## Selection model

The router accepts a ModelRequest containing:

- task_type
- complexity
- requires_tools
- requires_vision
- requires_coding
- requires_reasoning
- privacy_level
- context_size
- latency_preference
- local_only

Selection uses a score/rank approach with weighted factors:

- capability match
- quality fit
- latency fit
- locality
- context fit
- resource fit

## Privacy policy

Privacy levels:

- LOW
- NORMAL
- HIGH
- STRICT_LOCAL

Behavior:

- HIGH and STRICT_LOCAL prefer local-only models.
- The router never bypasses the existing Policy Engine.
- Cloud fallback is only used when allowed and when privacy constraints permit it.

## Hardware awareness

The router uses conservative filtering and resource scoring based on:

- estimated memory footprint
- local-only requirement
- configured context budget
- approximate hardware profile

This avoids absurd choices such as selecting a large model on a machine that cannot realistically run it.

## Fallback

The router prefers a primary candidate, but if the selected model/provider is unavailable it will attempt a compatible alternative. It will not silently switch to a model that lacks the required capability.

Examples:

- vision task -> vision-capable model only
- tool task -> tool-capable model only
- strict local privacy -> local-only model only

## Brain integration

The Brain now owns a router and uses it for model selection and default local routing, while retaining backward compatibility for the current local/cloud execution flow.

This is done without forcing the rest of the Nano to depend on model-specific implementation details.

## Configuration

Example configuration block:

```yaml
model_router:
  enabled: true
  default_provider: ollama
  local_first: true
  default_privacy: normal
  routing:
    capability_weight: 4.0
    quality_weight: 2.5
    speed_weight: 2.0
    privacy_weight: 4.0
    context_weight: 1.5
    resource_weight: 1.5
```

## Extending the system

To add a new provider:

1. implement a ModelProvider subclass
2. register it in the router constructor
3. expose a list_models() implementation
4. implement generate or stream as needed
5. ensure health checks are conservative and auditable

To add a new model:

1. add it to the provider registry or discovery result
2. populate ModelInfo metadata
3. let the router score it automatically under task requirements

## Ollama live test status

Live Ollama verification is environment-dependent. If Ollama is installed and available, this can be checked with the provider health discovery step. If it is not installed, the correct state is:

Ollama live test: NOT AVAILABLE

## Limitations

- This phase is about a stable router and abstraction layer, not a complete GPU scheduler.
- Model capability metadata from Ollama is partially inferred when the provider does not expose richer details.
- The router is intentionally conservative to avoid unjustified model selection.
