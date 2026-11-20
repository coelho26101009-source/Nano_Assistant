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
- [ ] 30. A ilha do NANO (em cima, ao centro) mostra "A ouvir…" com o tempo a contar e responde falado.

### Novidades JARVIS (P1)
- [ ] 31. **Definições → Voz → Preparar a voz**: instala e descarrega com barra de progresso (uns minutos). No fim pergunta se queres ligar "Ei NANO"; responde "Sim, ligar".
- [ ] 32. Pergunta algo por voz (Ctrl + Shift + Space) que tenha resposta comprida: o NANO começa a falar logo depois da primeira frase, sem esperar pelo resto.
- [ ] 33. **Definições → Geral → Personalidade**: escolhe "Formal", escreve em "Como o NANO te trata" `chefe` e pergunta "olá". A resposta muda de tom e trata-te por chefe.
- [ ] 34. **PC → Modos → Os teus protocolos**: carrega no modelo "Modo jogo", guarda, e escreve no chat `modo jogo`. O NANO abre o Steam e o Discord (pede autorização se for preciso) e diz a frase do fim.
- [ ] 35. **Avisos do PC**: descarrega um ficheiro qualquer no navegador; quando acabar, o NANO avisa "Download terminado". Desliga o Wi-Fi uns 2 minutos: avisa "Sem rede" e, quando voltas a ligar, "A ligação à rede voltou".

### A ilha do NANO (Modo ambiente)
Antes, na pasta do NANO, instala as bibliotecas novas uma vez:
`python -m pip install --require-hashes --no-build-isolation -r requirements\runtime.lock`
- [ ] 36. Com o NANO aberto e a janela minimizada, aparece uma pílula preta pequena em cima, ao centro. Passa o rato por cima: cresce e mostra `Ctrl Shift Espaço`. Clicar abre o NANO.
- [ ] 37. Com a janela do NANO à frente, a ilha esconde-se; minimiza o NANO e ela volta.
- [ ] 38. `Ctrl+Shift+Espaço`: a ilha passa a "A ouvir…" com o tempo a contar e barras a mexer, depois "A processar…", "A falar…" e "Pronto".
- [ ] 39. Escreve no telemóvel (ou no NANO e minimiza logo) "pesquisa na web o tempo em Lisboa": a ilha mostra "A pensar…" e depois "A pesquisar na web…".
- [ ] 40. Pede algo que precise de autorização (ex.: "fecha a janela do Bloco de notas") com o NANO minimizado: a ilha fica amarela "O NANO precisa da tua aprovação". Clica nela: abre o NANO com o pedido. Decide lá (a ilha não tem botão de aceitar, de propósito).
- [ ] 41. Cria um lembrete para daqui a 1 minuto e minimiza o NANO: quando chega, a ilha abre um cartão com o sino a tocar.
- [ ] 42. Abre o **Spotify** e põe uma música: a ilha abre o leitor com a capa e o nome durante uns segundos e depois fica pequena com a capa e barras a mexer. Passa o rato por cima: aparecem a barra de tempo e os botões anterior / pausa / seguinte. Experimenta os três.
- [ ] 43. Muda o volume (teclas do teclado ou o ícone do som do Windows): a ilha mostra "Volume" com a barra a acompanhar. Tira o som: mostra "Sem som".
- [ ] 44. No ícone do NANO no tabuleiro: "Ilha sempre no ecrã (modo ambiente)" desligado esconde a pílula quando não há nada a mostrar; "Ilha do NANO" desligado esconde-a por completo. As mesmas opções estão em **Definições → Geral**.

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
