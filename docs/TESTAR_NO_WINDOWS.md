# Testar o NANO no teu Windows (passo a passo)

Este guia é para experimentares o NANO no teu próprio PC, a partir do código
que está no `main` do GitHub. Não precisas de saber Git. Demora uns 20 a 30
minutos da primeira vez; depois basta um duplo-clique.

> Os nomes dos menus aparecem em português se o Windows estiver em português,
> e em inglês se estiver em inglês. Podes mudar em **Definições → Geral → Idioma**.
> Neste guia escrevo os nomes em português, com o inglês entre parênteses.

---

## Parte 1: preparar o PC (só da primeira vez)

### 1. Instalar o que o NANO precisa

1. **Python 3.12**: descarrega em <https://www.python.org/downloads/windows/>
   a versão 3.12 (Windows installer 64-bit). No primeiro ecrã do instalador
   **marca a caixa "Add python.exe to PATH"** e carrega em *Install Now*.
2. **Node.js 22 (LTS)**: descarrega em <https://nodejs.org/> e instala com as
   opções por omissão.
3. **Um "cérebro" para o NANO** (escolhe um, ou os dois):
   - **Na nuvem, grátis e mais simples:** cria uma conta em
     <https://console.groq.com/> e gera uma *API key*. Guarda-a; vais colá-la no NANO.
   - **Local, sem internet:** instala o Ollama em <https://ollama.com/download>
     e depois, numa *Linha de comandos*, escreve `ollama pull qwen3:8b`
     (descarrega uns 5 GB; precisa de pelo menos 16 GB de RAM para correr bem).

Para confirmar: abre a **Linha de comandos** (tecla Windows, escreve `cmd`,
Enter) e escreve `python --version` e depois `node --version`. Tem de aparecer
`Python 3.12...` e `v22...`. Se disser que não encontra, reinicia o PC e tenta outra vez.

### 2. Descarregar o NANO

1. Abre <https://github.com/coelho26101009-source/Nano_Assistant>.
2. Botão verde **Code → Download ZIP**.
3. Extrai o ZIP para uma pasta simples, por exemplo `C:\NANO`
   (evita a pasta Transferências e pastas com OneDrive).

### 3. Instalar as bibliotecas do NANO

Na Linha de comandos, vai para a pasta e corre estes dois comandos (um de cada vez):

```bat
cd C:\NANO\Nano_Assistant-main
python -m pip install --require-hashes -r requirements\build.lock
python -m pip install --require-hashes --no-build-isolation -r requirements\runtime.lock
```

Opcional, só se quiseres testar a **voz** (microfone e transcrição local):

```bat
python -m pip install --require-hashes --no-build-isolation -r requirements\optional.lock
```

---

## Parte 2: abrir o NANO

Faz **duplo-clique em `NANO_DESKTOP.bat`** dentro da pasta.

- Da primeira vez instala o Electron e prepara a interface: pode demorar
  2 a 5 minutos. A janela preta mostra cada passo com `OK`.
- Se algum passo disser `FAILED`, a própria janela diz a razão e o comando
  para resolver. Tira uma fotografia e manda-me.
- Fechar a janela do NANO **não** o desliga: fica no tabuleiro (ao pé do relógio).
  Para sair mesmo, clica com o botão direito no ícone e escolhe **Sair do Nano**.

Alternativa sem Electron: `NANO.bat` abre o NANO no navegador (sem tabuleiro,
sem atalho global, sem overlay de voz).

---

## Parte 3: lista para testar

Marca cada linha à medida que testas. Se algo falhar, anota o número da linha
e o que aconteceu.

### Primeiro arranque
- [ ] 1. Aparece o guia inicial em 3 passos (cérebro, privacidade, nome).
- [ ] 2. Em **Definições → IA (AI)** colas a chave Groq e carregas em *Testar*; fica verde.
      (Ou, com Ollama, escolhes o modelo `qwen3:8b`.)
- [ ] 3. A página inicial cumprimenta-te pelo nome e pela hora do dia.

### Conversa
- [ ] 4. Escreves "olá" e o NANO responde. Enquanto pensa aparece "A pensar...".
- [ ] 5. Na última resposta, **Tentar de novo** gera outra; na tua última pergunta, **Editar** funciona.
- [ ] 6. Escreves em inglês e ele responde em inglês; em português responde em português.
- [ ] 7. Sobes na conversa durante uma resposta longa: aparece um botão para voltar ao fim.
- [ ] 8. Botão de anexar (clipe): anexas um `.txt` ou `.md` e perguntas sobre ele. Uma imagem ou PDF é recusado com a razão.
- [ ] 9. Barra lateral: mudar o nome, apagar e pesquisar conversas.

### Atalhos de teclado
- [ ] 10. `?` (fora de uma caixa de texto) ou `Ctrl+/` mostra a lista de atalhos.
- [ ] 11. `Ctrl+Shift+O` nova conversa, `Ctrl+K` paleta de comandos, `Ctrl+B` esconde a barra lateral, `Ctrl+,` abre Definições.
- [ ] 12. `Ctrl+Shift+Espaço` em qualquer programa chama o NANO por voz (precisa da parte opcional de voz).

### Lembretes e Agenda
- [ ] 13. Pede "lembra-me daqui a 2 minutos de beber água". Passados 2 minutos aparece uma **notificação do Windows**, mesmo com a janela fechada no tabuleiro.
- [ ] 14. A página **Agenda** mostra o lembrete; consegues criar um à mão.
- [ ] 15. Num lembrete escolhe **Repetir** (todos os dias, semanas...) e confirma que fica marcado como recorrente.

### Controlar o PC
- [ ] 16. "Abre o Bloco de Notas" abre o Notepad.
- [ ] 17. "Põe o volume a 30%" muda o volume.
- [ ] 18. Com **duas janelas do Chrome/Edge** abertas, "fecha o Chrome": o NANO pergunta **qual delas** com botões. Ao escolher, fecha só essa.
- [ ] 19. Ações que fecham, apagam ou capturam o ecrã pedem confirmação dentro da conversa, com a ação e o alvo à vista.
- [ ] 20. **PC → Estado (Status)** mostra CPU, RAM, disco e bateria sem texto sobreposto.

### Modos de trabalho
- [ ] 21. **PC → Modos (Modes)** mostra Foco, Hacker e Relax. Ativar um abre/fecha o que está descrito e o NANO avisa no fim.

### Ler o texto do ecrã (OCR)
- [ ] 22. Em **Definições → Privacidade (Privacy)** ativa **Ler texto do ecrã**.
- [ ] 23. Com uma janela aberta, pergunta "o que diz esta janela?". Pede confirmação e depois responde com o texto. Num gestor de palavras-passe recusa.

### Definições e privacidade
- [ ] 24. **Definições → Geral**: mudar o idioma (Sistema / English / Português) recarrega a interface na língua certa.
- [ ] 25. Tema claro/escuro/seguir o sistema e "reduzir movimento" mantêm-se depois de fechar e abrir o NANO.
- [ ] 26. **Modo avançado** desligado esconde as páginas técnicas (Agentes, Componentes); ligado, mostra-as.
- [ ] 27. **Memória**: diz "lembra-te que gosto de café" e depois vê se aparece em **Memória → Memórias**.
- [ ] 28. **Definições → Privacidade → Apagar todos os dados** pede para escreveres `APAGAR TUDO` (ou `DELETE EVERYTHING`). Testa isto **por último**: apaga mesmo tudo.

### Voz (só se instalaste a parte opcional)
- [ ] 29. **Definições → Voz**: o teste do microfone ouve-te e transcreve.
- [ ] 30. O overlay de voz aparece em baixo, ao centro, e responde falado.

---

## O que já sabemos que ainda não está perfeito

- Ainda **ninguém correu esta versão num Windows real**: os testes automáticos
  passam todos (no Linux e no Windows do GitHub), mas é exatamente para isso
  que serve esta lista.
- As páginas mais recentes (anexos, modo de voz, painel lateral, projetos) ainda
  estão a ser acabadas noutro trabalho; se uma delas não aparecer, é normal.
- O instalador `.exe` não está assinado: se um dia o usares, o Windows avisa
  "editor desconhecido". Isto não afeta o arranque pelo `NANO_DESKTOP.bat`.
- Sem chave da nuvem e sem Ollama, o NANO abre mas não consegue responder.

## Onde ficam os teus dados

Tudo fica no teu PC, em `%LOCALAPPDATA%\NanoAssistant` (conversas, memória,
definições, chaves encriptadas pelo Windows). Para começar do zero, usa o
item 28 ou apaga essa pasta com o NANO fechado.
