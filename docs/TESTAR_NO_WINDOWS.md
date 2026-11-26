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

1. Abre <https://github.com/coelho97sc/Nano_Assistant>.
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
- [ ] 36. (Mudou a 2026-11-22, ver itens 53 a 55.) Com **Ilha sempre no ecrã** ligado, aparece uma pílula preta pequena em cima, ao centro. Passa o rato por cima: cresce e mostra `Ctrl Shift Espaço`. Clicar abre o NANO.
- [ ] 37. Com a janela do NANO à frente, a ilha esconde-se; minimiza o NANO e ela volta.
- [ ] 38. `Ctrl+Shift+Espaço`: a ilha passa a "A ouvir…" com o tempo a contar e barras a mexer, depois "A processar…", "A falar…" e "Pronto".
- [ ] 39. Escreve no telemóvel (ou no NANO e minimiza logo) "pesquisa na web o tempo em Lisboa": a ilha mostra "A pensar…" e depois "A pesquisar na web…".
- [ ] 40. Pede algo que precise de autorização (ex.: "fecha a janela do Bloco de notas") com o NANO minimizado: a ilha fica amarela "O NANO precisa da tua aprovação". Clica nela: abre o NANO com o pedido. Decide lá (a ilha não tem botão de aceitar, de propósito).
- [ ] 41. Cria um lembrete para daqui a 1 minuto e minimiza o NANO: quando chega, a ilha abre um cartão com o sino a tocar.
- [ ] 42. Abre o **Spotify** e põe uma música: a ilha abre o leitor com a capa e o nome durante uns segundos e depois fica pequena com a capa e barras a mexer. Passa o rato por cima: aparecem a barra de tempo e os botões anterior / pausa / seguinte. Experimenta os três.
- [ ] 43. Muda o volume (teclas do teclado ou o ícone do som do Windows): a ilha mostra "Volume" com a barra a acompanhar. Tira o som: mostra "Sem som".
- [ ] 44. No ícone do NANO no tabuleiro: "Ilha sempre no ecrã" ligado deixa a pílula sempre lá; desligado (o normal) esconde-a quando não há nada a mostrar; "Ilha do NANO" desligado esconde-a por completo. As mesmas opções estão em **Definições → Geral**.

### Investigação profunda com fontes (P2)
- [ ] 45. Escreve no chat `investiga a fundo se vale a pena trocar a GTX 1660 Ti por uma RTX 4060`. Enquanto trabalha aparece "A investigar a fundo: várias pesquisas e sítios…" (e na ilha "A investigar a fundo…"). Demora uns 15 a 40 segundos.
- [ ] 46. O relatório tem título, **Resumo**, **O que as fontes dizem** com números entre parênteses retos ([1], [2]...), **Onde não concordam** e **Confiança**. No fim vem **Fontes** com ligações numeradas: carrega numa e abre o sítio certo no navegador.
- [ ] 47. Carrega no botão "Abrir ao lado" por baixo do relatório: abre-o no painel lateral. No telemóvel, escreve `pesquisa com fontes qual o melhor portátil para a escola até 600 euros`: o relatório e as fontes aparecem lá também, e "Ler em voz alta" não lê os [1] nem as ligações.

### Memória M3: lembrar conversas antigas
- [ ] 48. Numa conversa nova escreve duas mensagens tuas, por exemplo `Quero montar um PC novo para jogar` e `Decidi ficar com a RTX 4060 e o Ryzen 5 7600.` Depois abre **Memória → Conversas antigas**: aparece essa conversa com o resumo e a decisão.
- [ ] 49. Começa outra conversa (Nova conversa) e pergunta `Lembras-te que placa gráfica escolhi?`. O NANO responde com a RTX 4060, diz de que conversa e de que dia vem, e por baixo da resposta aparece **Lembrei-me de** com o nome da conversa. Clica no nome: abre essa conversa.
- [ ] 50. Em **Conversas antigas**, carrega em **Editar**, muda a decisão para `Ficou a RTX 4070` e guarda. Pergunta outra vez numa conversa nova: o NANO usa a tua versão.
- [ ] 51. Carrega em **Esquecer** nessa conversa: passa para **Esquecidas** e o NANO deixa de a lembrar (a conversa continua na barra lateral). **Voltar a lembrar** trá-la de volta.
- [ ] 52. **Definições → Memória → Lembrar conversas antigas** desligado: o NANO deixa de lembrar outras conversas.

### Ilha escondida, "Ei Nano" e ouvir até acabares (2026-11-22)
Antes: atualiza a pasta do NANO e corre `NANO_DESKTOP.bat` como sempre. A voz
tem de estar preparada (item 31).
- [ ] 53. **Ilha escondida.** Com o NANO minimizado e nada a acontecer, não se vê ilha nenhuma. Encosta o rato ao **topo do ecrã, ao centro**, e espera meio segundo: a ilha desce a deslizar. Afasta o rato: passado um segundo sobe e desaparece.
- [ ] 54. Com o rato em cima da ilha chamada, ela não foge. Se a voz por nome estiver ligada (item 56), mostra `Diz "Ei Nano" ou Ctrl Shift Espaço`. Clicar abre o NANO.
- [ ] 55. Com música no Spotify: a ilha **não** fica sempre à vista. Muda de música: o cartão aparece uns segundos e some. Pausa / continua (teclas do teclado): a pílula da música aparece uns 2 segundos. Muda o volume: aparece a barra e some. Encosta o rato ao topo: aparece a música com os botões.
- [ ] 56. **Definições → Voz → Chamar o NANO por voz**: liga "Ouvir “ei nano”". O Windows mostra o ícone do microfone na barra de tarefas. No ícone do NANO no tabuleiro aparece "Ouvir "Ei Nano"" com visto, e ao passar o rato no ícone diz "A ouvir...".
- [ ] 57. Com o NANO minimizado diz **"Ei Nano"** e pára: ouves o som de confirmação, a ilha mostra "A ouvir…", e depois dizes o pedido (ex.: "que horas são?"). Repara que o NANO **pára de gravar logo que acabas de falar**, não espera 7 segundos.
- [ ] 58. Diz tudo de uma vez: **"Ei Nano, abre o Bloco de Notas"**. O NANO não pergunta outra vez: faz logo (ou pede autorização, se for preciso).
- [ ] 59. **Enganos:** conversa normalmente perto do PC (ou põe um vídeo do YouTube a falar) durante 2 minutos, incluindo frases como "o nano é fixe". O NANO **não** deve acordar. Anota se acordar sozinho e com que frase.
- [ ] 60. Desliga "Ouvir "Ei Nano"" no tabuleiro: o visto sai, o ícone do microfone do Windows desaparece, e "Ei Nano" deixa de funcionar. O atalho `Ctrl+Shift+Espaço` continua a funcionar.
- [ ] 61. Com o atalho, diz uma frase comprida (uns 10 segundos, com uma pausa pequena a meio para respirar). O NANO não te corta a meio. Se cortar, em **Definições → Voz → Como o NANO te ouve** aumenta "Pausa que termina o pedido" e repete.
- [ ] 62. (Opcional, ~1,6 GB) **Qualidade do reconhecimento → Melhor**: descarrega o modelo maior. Depois experimenta frases difíceis (nomes, barulho de fundo). Anota se percebe melhor e quanto tempo a mais demora. Volta a **Rápida** se ficar lento demais.
- [ ] 63. Muda a língua para inglês (**Definições → Geral → Idioma**): a frase passa a "Hey Nano" e funciona da mesma maneira.

### Voz do NANO: voz offline, ElevenLabs e nunca calado (2026-11-24)
- [ ] 64. **Definições → Voz → Voz do NANO**: carrega em **Ouvir**. Ouves a voz e aparece "Ouviste: Microsoft · Duarte".
- [ ] 65. Carrega em **Instalar voz offline**: aparece a barra de progresso (cerca de 90 MB em português) e no fim a escolha "Voz offline". Muda o motor para **Voz offline natural** e carrega em **Ouvir**: fala sem internet. Anota quanto tempo demorou a começar.
- [ ] 66. **Sem internet** (desliga o Wi-Fi), com o motor em **Automática**: faz uma pergunta por voz. O NANO responde com a voz offline (ou a do Windows, se não instalaste a offline). Antes ficava calado.
- [ ] 67. Mexe na **Velocidade** (+15%) e carrega em **Ouvir**: fala mais depressa.
- [ ] 68. Pede uma resposta comprida por voz (ex.: "explica-me a fotossíntese"). Repara se há pausas entre as frases: devia falar seguido.
- [ ] 69. (Opcional, precisa de conta) Motor **ElevenLabs**: cola a tua chave, escolhe uma voz e carrega em **Ouvir**. Anota se soa melhor que o Duarte.
- [ ] 70. Põe a língua em inglês e carrega em **Ouvir**: a voz passa a "Ryan (UK)". Com a voz offline em inglês, descarrega o Kokoro (350 MB) e experimenta "George · Butler (UK)" (a voz por defeito em inglês, ver o item 101) e "George (UK)".
- [ ] 71. **Ei Nano + voz offline juntos:** com "Ouvir “ei nano”" ligado e o motor em **Voz offline natural**, diz "Ei Nano, que horas são?". O NANO responde com a voz offline e **não fecha** (as duas partes usam o mesmo motor de IA por baixo, o onnxruntime; no Linux funcionam juntas, no Windows ainda não foi visto).

### Tarefas encadeadas e rotinas por voz (2026-11-24)
Precisa de um modelo com ferramentas (Groq/Gemini/Mistral, ou o local).
- [ ] 72. Escreve `abre o Notion e o Chrome, põe o Chrome à esquerda e o volume a 30`. O NANO **não pergunta "Avanço?"** (são tudo coisas seguras): por baixo vês "1/4 · Abrir Notion", "2/4 · Abrir Chrome"... e o Chrome fica mesmo encostado à esquerda **depois** de a janela aparecer. No fim responde curto.
- [ ] 73. Logo a seguir diz `guarda isto como rotina setup de estudo`. Abre **PC → Modos**: aparece "Setup de estudo" com os 4 passos em "Outros passos". Fecha o Notion e o Chrome e escreve (ou diz) `prepara o meu setup de estudo`: faz tudo outra vez, sem modelo.
- [ ] 74. Faz uma rotina com uma app que não tens (ex.: `cria uma rotina teste que abre o Photoshop e põe o Photoshop à direita`) e corre-a: o NANO diz "Não fiz: Abrir Photoshop (...); Pôr Photoshop à direita (Dependia de abrir...)" e "Diz “tenta outra vez”". Diz `tenta outra vez`: repete **só** esses dois passos.
- [ ] 75. Um plano com algo sensível, ex.: `fecha o Discord, abre o Spotify e põe o volume a 20`: só o "Fechar Discord" pede confirmação; o resto faz sozinho.
- [ ] 76. Carrega na **paragem de emergência** a meio de uma rotina comprida: os passos que faltavam aparecem como "não cheguei lá", nada mais corre.
- [ ] 77. `que rotinas tenho?` lista as tuas rotinas; `apaga a rotina teste` pede confirmação antes de apagar.

### NANO proativo, sem ser chato (2026-11-24)
- [ ] 78. **Definições → Geral → O NANO fala primeiro**: escreve a tua cidade em "Cidade para o tempo" (ex.: `Porto`) e carrega em **Mostrar o resumo agora**. O resumo traz uma linha **Tempo** com a temperatura e, se for chover, "leva guarda-chuva". A linha acaba em "Dados: Open-Meteo.com." (o crédito que a licença dos dados pede), mas essa parte não é dita em voz alta.
- [ ] 79. Com "Dizer os avisos em voz alta" em **Só o resumo e os importantes** e a voz ligada: muda a hora do resumo para daqui a 2 minutos e espera ao PC. O NANO diz bom dia **em voz alta** (curto) e mostra o resumo completo no chat.
- [ ] 80. Muda a hora do resumo para daqui a 2 minutos e **afasta-te do PC 12 minutos** (sem mexer no rato). Quando voltas e mexes no rato, o resumo aparece nessa altura, não antes.
- [ ] 81. Escreve `não me incomodes durante uma hora`. O NANO responde "fico calado até às …". Em **Definições → Geral → Não incomodar** aparece "Em silêncio até às …". Escreve `podes voltar a falar`: o silêncio acaba.
- [ ] 82. Abre um jogo em **ecrã inteiro** e descarrega um ficheiro durante o jogo. Não aparece nada por cima do jogo. Quando sais do jogo, aparece o aviso do download.
- [ ] 83. Abre muitas abas no Chrome até a memória passar dos 90% (ou um jogo pesado) e espera 2 minutos: aparece "Memória quase cheia" com o nome da app e o botão **Fechar o …**. Carrega: o NANO pede autorização antes de fechar. Experimenta também responder só `sim` no chat.
- [ ] 84. Por baixo de um aviso carrega em **Não me avises disto**. Em **Não incomodar → Avisos que desligaste** aparece esse tipo; carrega nele para voltar a ligar.
- [ ] 85. **PC → Modos → Rotinas com hora**: cria uma rotina com o teu "Modo estudo" para daqui a 2 minutos, no dia de hoje. À hora, o NANO pergunta "São …: começo o Modo estudo?". Responde `sim` (por voz ou escrito): o protocolo corre.
- [ ] 86. Ativa o mesmo protocolo à mesma hora em 3 dias diferentes. No 4.º dia, a essa hora, o NANO pergunta se o quer ativar ("Costumas ativar…").
- [ ] 87. Depois da meia-noite, ainda ao PC: aparece "Já é tarde" (sem som e sem voz), com o que tens de manhã se houver algo na agenda antes das 11h.
- [ ] 88. **Definições → Geral → Não incomodar → Últimos avisos**: vês o que o NANO mostrou, guardou para depois ou não mostrou, e porquê.

### Revisão total: segurança e pequenos erros (2026-11-25)
- [ ] 89. Escreve `abre a pasta \\192.168.1.1\partilha`. O NANO responde que só mexe em pastas deste PC, **sem** o Windows tentar ligar-se a esse endereço (não aparece janela de rede nem pedido de palavra-passe).
- [ ] 90. Escreve `abre o Windows PowerShell` e depois `abre o Windows PowerShell ISE`. O NANO recusa os dois: "é uma linha de comandos".
- [ ] 91. Escreve `esquece que a minha cor favorita é azul` (depois de lhe teres dito isso). Aparece o pedido de autorização; ao aceitar, o facto desaparece de **Memória**. Antes era sempre bloqueado.
- [ ] 92. Pede um plano que feche duas janelas da mesma app (ex.: `fecha o Bloco de notas "a" e o Bloco de notas "b"`). Aceitar o plano conta para esses dois fechos, não para outros que o NANO tente a seguir.
- [ ] 93. Pede uma resposta em voz alta comprida e carrega em **parar** logo na primeira frase. Não se ouve mais nenhuma frase depois de parar.

### Melhorias da revisão total (2026-11-25)
- [ ] 94. Escreve `limpa a cache do Windows` e aceita. A resposta diz quantos MB apagou e quantos ficheiros ficaram por serem **recentes (menos de 24 h)**, **estarem em uso** ou **sem permissão para apagar**. Os programas abertos continuam a funcionar.
- [ ] 95. Pede ao assistente de código `cria um ficheiro teste.bat no Ambiente de trabalho com echo ola`. O NANO recusa **sem perguntar** (é um script fora do projeto). Pede `cria notas.txt no Ambiente de trabalho com ola`: pede autorização e, ao aceitar, o ficheiro aparece.
- [ ] 96. Com dois fornecedores ligados (ex.: Groq e Google) e o modo **AUTO**, desliga a internet do preferido (ou põe uma chave errada nele) e pergunta algo. A resposta chega e, por baixo, aparece uma nota como **"Groq: … Respondeu Google."** com o outro fornecedor.
- [ ] 97. Com o Ollama instalado, corre `ollama pull qwen3:1.7b`. Em **Definições → IA → Modelo rápido para respostas curtas** escolhe `qwen3:1.7b` e põe o modo **LOCAL**. `olá` responde depressa; `abre o bloco de notas` continua a funcionar (usa o modelo principal).
- [ ] 98. Pede `pesquisa na web o preço do Raspberry Pi 5` e depois `lê esta página: https://www.wikipedia.org`. As duas funcionam como antes (a verificação de endereços não pode estragar a pesquisa normal).
- [ ] 99. Pede `lê esta página: http://192.168.1.1` (o teu router). O NANO recusa: "endereço privado".
- [ ] 100. **A atualização automática, de ponta a ponta.** Com a **0.4.0-beta.3 instalada** (a do instalador, não a da pasta do projeto) e a 0.4.0-beta.4 já publicada: Definições → Sobre → Atualizações → **Procurar agora**. Deve dizer que há a 0.4.0-beta.4, descarregar, e instalar quando fechas o NANO (**Reiniciar e atualizar**). Depois confirma em Sobre que a versão é a 0.4.0-beta.4. É a primeira vez que isto se experimenta num PC real e, com o nome novo do GitHub, é o teste mais importante da Beta.
- [ ] 101. **A voz "mordomo" (estilo Jarvis).** Põe a língua em **inglês**, em Definições → Voz → Voz do NANO escolhe o motor **Voz offline natural** (instala o Kokoro se ainda não estiver) e a voz **George · Butler (UK)**. Carrega em **Ouvir**: deve soar calma, cheia e com um eco muito leve de sala, como a amostra `voz-realista/estilo-jarvis/1-EN-George-estilo-Jarvis-RECOMENDADA.mp3`. Compara com "George (UK)". Depois faz uma pergunta por voz e anota se a primeira frase começa a falar tão depressa como antes (o estilo acrescenta só uns centésimos de segundo).

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
