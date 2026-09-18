# Nano 0.1.0-beta.1 — guia de validação

Esta é uma Beta para Windows x64, ainda sem lançamento público. O instalador
principal é `Nano-Setup-0.1.0-beta.1-x64.exe` (NSIS, por utilizador). O MSI com
o mesmo nome destina-se a instalação gerida. Python e a interface já vão
incluídos; o utilizador não precisa de instalar Python, Node.js ou npm.

Os instaladores não estão assinados. O Windows pode mostrar um aviso de
editor desconhecido ou SmartScreen. Confirma a origem e o SHA256 antes de
decidir instalar; não desatives a proteção do Windows. Não há atualizador.

## Primeira execução

1. Instala e abre **Nano Assistant** pelo menu Iniciar (NSIS).
2. O guia inicial permite configurar a IA ou explorar sem configurar.
3. Em **Definições → IA**, escolhe uma configuração suportada:
   - **Cloud:** adiciona e testa uma chave de Groq, Google ou Mistral, escolhe
     um modelo que a conta disponibilize e o provedor preferido.
   - **Local:** instala o Ollama separadamente, descarrega um modelo que caiba
     na memória do PC, seleciona-o no Nano e usa o modo Local.
   - **Auto:** usa a ordem de provedores configurada e pode recorrer ao Ollama.
     Preferência cloud e ordem de fallback são configurações diferentes.
4. Basta uma destas configurações; o Ollama é opcional para cloud. Sem chave
   válida nem modelo local disponível, o Nano abre mas ainda não pode responder.
5. A voz é opcional. O pacote base não inclui faster-whisper nem os seus pesos;
   a transcrição local precisa das dependências opcionais documentadas em
   [VOICE.md](VOICE.md). O chat escrito funciona sem microfone. Não atives a
   voz se não estiver pronta. A wake phrase começa desligada.

O guia pode ser reaberto em **Definições → Sobre**. Fechar a janela esconde-a
no tabuleiro; escolhe **Sair do Nano** no tabuleiro para encerrar completamente.

## Dados e privacidade

Conversas, Memória e Second Brain ficam em
`%LOCALAPPDATA%\NanoAssistant\helios.db`. A mesma pasta guarda
`user_settings.json`, `permission_policies.json`, `secrets.dat`, tarefas e logs.
O caminho efetivo aparece em **Sobre**; `NANO_DATA_DIR` permite um perfil separado.
Estado da janela e cache Electron ficam no perfil Electron em `%APPDATA%`.

As chaves são protegidas por DPAPI no Windows. Uma falha de encriptação impede
a gravação. O guia não ativa serviços nem concede permissões de PC Control.
Ações sensíveis mantêm a cadeia política → permissão → executor → ferramenta.

Cloud envia mensagens e contexto ao provedor usado. Local mantém a inferência
no Ollama, mas respostas faladas usam Microsoft Edge TTS e enviam o texto a
ler. Não existe envio automático de diagnósticos. Consulta [PRIVACY.md](../PRIVACY.md).

## Problemas comuns

| Situação | Ação |
| --- | --- |
| Chat sem provedor | Abre IA, configura uma chave válida ou um modelo Ollama e verifica novamente |
| Chave inválida, quota ou serviço indisponível | Testa a ligação em IA, verifica a conta; não partilhes a chave |
| Ollama ausente ou sem modelo | Instala/inicia o Ollama e descarrega um modelo, ou configura cloud |
| Voz indisponível | Usa texto; verifica dependências opcionais, dispositivo e permissões do Windows |
| Arranque falhou | Fecha outras instâncias, verifica espaço e acesso à pasta de dados; reinstala a mesma versão se necessário |
| Motor parou | Usa Reiniciar o Nano no tabuleiro ou sai e volta a abrir |
| Interface não carrega | Após duas tentativas automáticas, escolhe tentar novamente ou sair |

**Sobre → Copiar diagnóstico** prepara apenas versão, plataforma, arquitetura,
estado técnico e códigos limitados. Se a cópia for recusada, seleciona o texto
apresentado. Uma falha inicial também guarda `logs/startup-diagnostics.json`
quando a pasta é gravável. Logs normais podem conter caminhos e atividade:
nunca publiques o ficheiro completo sem o rever.

## Desinstalar e atualizar manualmente

A desinstalação remove os binários e preserva os dados por omissão. Não apaga
conversas, Memória ou chaves. Não existe limpeza automática ou sincronização.
Para backup, sai do Nano antes de copiar a pasta de dados completa. As chaves
DPAPI só podem ser decifradas pelo utilizador Windows original.

Préreleases MSI partilham a versão numérica `0.1.0`; não se deve prometer um
upgrade MSI entre `beta.1` e `beta.2` sem uma política numérica nova. Até existir
uma política validada, desinstala os binários antes de instalar outra Beta,
mantém um backup e consulta as notas dessa versão. Nunca troques versões sobre
uma base de dados mais recente sem confirmação de compatibilidade.
