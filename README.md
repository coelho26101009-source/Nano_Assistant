# ☀️ H.E.L.I.O.S. (Hybrid Executive Logic & Intelligent Operating System)

Um assistente virtual avançado inspirado no J.A.R.V.I.S., desenvolvido em Python. O H.E.L.I.O.S. utiliza uma arquitetura de "cérebro híbrido", alternando entre processamento na nuvem de alta velocidade (Groq) e execução local e segura (Ollama), garantindo eficiência máxima.

## 🚀 Funcionalidades Principais
* **Cérebro Híbrido:** Transição automática entre Groq (rápido/online) e Ollama local (fallback/offline, desactivável).
* **Memória a Longo Prazo:** O histórico em SQLite é carregado no arranque, os factos sobre o utilizador são injectados no system prompt e os PDFs indexados alimentam o RAG (ChromaDB).
* **Sempre ligado:** Arranca com o Windows apenas no tray, escuta por wake-word e dispara lembretes e alertas em background.
* **Sistema de Plugins:** Arquitetura modular que permite adicionar novas ferramentas sem alterar o código principal.
* **Guardrails de Segurança:** O sistema pede confirmação humana (Human-in-the-loop) antes de executar comandos críticos no sistema operativo.

## 🧠 Memória persistente
Em cada resposta o H.E.L.I.O.S. tem acesso a:

1. **Histórico** — as últimas mensagens (`memory.history_messages`) recuperadas de `data/helios.db`, truncadas para caberem na janela de contexto (`memory.max_history_chars`).
2. **Factos** — pares chave/valor gravados com a ferramenta `remember_fact` ("vive no Porto", "trabalha das 9h às 18h") e injectados no system prompt.
3. **RAG** — excertos dos PDFs indexados (`rag_index_pdf`) relevantes para a pergunta actual.

Ferramentas relacionadas: `remember_fact`, `list_facts`, `forget_fact`, `memory_search_history`.

## 🔊 Escuta contínua (wake-word)
Com `voice.wake_word_enabled: true` uma thread daemon escuta o microfone e acorda o assistente quando ouve a palavra de activação; a resposta é falada e a conversa aparece na UI se ela estiver aberta.

```bash
pip install pvporcupine     # Picovoice — leve, grátis para uso pessoal (PICOVOICE_ACCESS_KEY no .env)
pip install openwakeword    # 100% offline, sem chave
```

```yaml
voice:
  wake_word_enabled: true
  wake_word_phrase: "hey jarvis"   # incluídas: porcupine → jarvis/computer/alexa…
                                   #            openwakeword → hey jarvis/hey mycroft/alexa
  wake_word_backend: "auto"        # auto | porcupine | openwakeword
  wake_word_sensitivity: 0.6
  wake_word_keyword_path: ""       # .ppn/.onnx próprio para dizeres mesmo "Helios"
```

Para a palavra **"Helios"** é preciso um modelo próprio: cria um `.ppn` gratuito em
[console.picovoice.ai](https://console.picovoice.ai) ou treina um `.onnx` com o
[openWakeWord](https://github.com/dscripka/openWakeWord) e aponta `wake_word_keyword_path` para ele.

## 🎙️ Reconhecimento de fala local
Por omissão (`voice.stt_provider: auto`) o H.E.L.I.O.S. usa **faster-whisper** local se estiver instalado — sem rede, sem enviar áudio para terceiros — e só recorre ao Google como último recurso.

```bash
pip install faster-whisper
```

```yaml
voice:
  stt_provider: "faster_whisper"   # auto | faster_whisper | google
  whisper_model: "tiny"            # tiny | base | small
  stop_on_silence: true            # pára de gravar assim que acabas de falar
```

## 🪟 Arrancar com o Windows (background, baixo impacto)
* Botão direito no ícone do tray → **"🪟 Iniciar com o Windows"**.
* Nesse arranque a app fica só no tray (`--hidden`); clica no ícone para abrir a janela.
* Instância única: abrir de novo traz a janela existente para a frente.

Para reduzir ainda mais o consumo em PCs modestos:

```yaml
ollama_enabled: false        # só nuvem (Groq), zero carga local
# ou um modelo local minúsculo:
ollama_model: "qwen2.5:1.5b" # ~1GB RAM  (alternativas: llama3.2:1b, qwen2.5:3b)
```

## 🔌 Plugins incluídos
| Plugin | Ferramentas |
| --- | --- |
| `memory_tools` | `remember_fact`, `list_facts`, `forget_fact`, `memory_search_history` |
| `reminders` | `set_reminder`, `list_reminders`, `cancel_reminder` — disparam em background e notificam o telemóvel |
| `calendar` | `calendar_add_event`, `calendar_list_events`, `calendar_delete_event`, `calendar_import_ics` (+ Google Calendar opcional) |
| `system_monitor` | `monitor_status`, `monitor_start`, `monitor_stop` — alertas proactivos de CPU/RAM/disco/bateria |
| `god_mode` | PowerShell, volume, brilho, Wi-Fi, Bluetooth, ficheiros |
| `web_vision` | Pesquisa, navegação, extracção de preços, screenshots |
| `ghost_organizer` | Organização de downloads, limpeza de cache, estatísticas |
| `smart_life` | IoT, notificações push, indexação e pesquisa de PDFs (RAG) |
| `context_switcher` | Modos de trabalho (hacker, foco, relax) |

Google Calendar (opcional, só leitura):

```bash
pip install google-api-python-client google-auth-oauthlib
```
`calendar.google_enabled: true` + credenciais OAuth em `data/google_credentials.json`.

## ⚙️ Configuração
Tudo em [`config/settings.yaml`](config/settings.yaml) (com comentários) e segredos no `.env`:

```
GROQ_API_KEY=...             # obrigatória
ELEVENLABS_API_KEY=...       # opcional (voz)
OPENAI_API_KEY=...           # opcional (voz)
PICOVOICE_ACCESS_KEY=...     # opcional (wake-word Porcupine)
```

Guia completo de comandos e exemplos: [`AJUDA.txt`](AJUDA.txt).

## 🖥️ Hardware Recomendado
* **Processador:** AMD Ryzen 7 5700X (ou equivalente)
* **Placa Gráfica:** NVIDIA GTX 1660 Ti (6GB VRAM) ou superior
* **RAM:** 16GB+

Em máquinas mais modestas: `ollama_enabled: false`, `whisper_model: tiny` e wake-word com Porcupine.
