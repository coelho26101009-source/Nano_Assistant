# Nano Architecture

## Purpose

Nano is a local-first personal agent that combines local execution, persistent memory, task orchestration and tool use in a safe, explainable loop.

## Core layers

1. Interaction layer
   - chat UI
   - voice input/output
   - wake word detection
   - event streaming

2. Agent core
   - AgentOrchestrator
   - ContextEngine
   - PermissionManager
   - task queue and event bus

3. Model layer
   - Ollama as default local model provider
   - optional cloud providers for fallback and augmentation
   - model router chosen by workload type

4. Memory layer
   - conversation history
   - preference store
   - fact store
   - retrieval/search layer
   - future memory graph and profile

5. Tooling layer
   - plugins
   - desktop tools
   - browser tools
   - project tools
   - notifications

6. Security layer
   - guardrails
   - permission classification
   - explicit confirmation for sensitive actions
   - action logging

## Runtime model

The agent loop is:

user request
-> context build
-> plan creation
-> task queue persistence
-> tool execution / tool selection
-> verification
-> report

## Persistent queue

The queue is implemented with SQLite and supports:

- QUEUED
- PLANNING
- RUNNING
- WAITING
- WAITING_FOR_PERMISSION
- PAUSED
- RETRYING
- FAILED
- COMPLETED
- CANCELLED

## Current implementation

The repo now contains the first-generation foundations:

- TaskEngine
- EventBus
- PermissionManager
- ContextEngine
- AgentOrchestrator
- user profile in MemoryEngine
- task endpoints exposed via the Python runtime

## Planned next steps

- background workers for long-running automation
- browser automation abstraction
- desktop control abstraction with policy enforcement
- coding project agent for GitHub/project work
- notification manager
- vision provider abstraction
- stronger local-first model routing
