# Testar o NANO no teu telemóvel Android (passo a passo)

A app do telemóvel é um comando para o NANO que já tens no PC: usa as mesmas
conversas, a mesma memória e a mesma agenda. O PC continua a fazer o trabalho;
o telemóvel fala com ele pela tua rede, sempre cifrado.

> Se o telemóvel estiver em inglês, a app aparece em inglês; em português,
> aparece em português. Neste guia escrevo os nomes em português, com o
> inglês entre parênteses.

Precisas de: o NANO a funcionar no PC (ver `docs/TESTAR_NO_WINDOWS.md`), um
telemóvel Android 7 ou mais recente, e os dois no **mesmo Wi-Fi**.

---

## Parte 1: preparar o PC (só da primeira vez)

1. **Atualiza o NANO no PC** para a Beta 4 (0.4.0-beta.1 ou mais recente). O
   mais simples é o instalador `Nano-Setup-0.4.0-beta.1-x64.exe` da
   [página da versão](https://github.com/coelho26101009-source/Nano_Assistant/releases/tag/v0.4.0-beta.1),
   que já traz tudo o que é preciso (salta o passo 2). Se usas o código-fonte,
   descarrega outra vez o ZIP do `main` ou faz `git pull`.
2. **Só com o código-fonte: instala as duas bibliotecas novas** (cifra e código
   QR). Fecha o NANO, abre a **Linha de comandos** na pasta do NANO e escreve:

   ```
   python -m pip install --require-hashes --no-build-isolation -r requirements\runtime.lock
   ```

   Se te esqueceres, o NANO avisa no painel do telemóvel e mostra este comando.
3. **Abre o NANO** e vai a **Definições → Geral → Telemóvel**
   (*Settings → General → Phone*).
4. Liga **Acesso do telemóvel** (*Phone access*). Deve aparecer
   "À escuta na porta 47821".
5. Na primeira vez, o **Windows pergunta pela firewall**: escolhe
   **Redes privadas** (*Private networks*) e carrega em Permitir.
   Se já disseste que não, vê o fim deste guia.

## Parte 2: instalar a app no telemóvel

1. No telemóvel, abre este endereço no Chrome:
   <https://github.com/coelho26101009-source/Nano_Assistant/releases/tag/v0.4.0-beta.1>
2. Em **Assets**, toca em **NANO-Android-0.4.0-beta.1.apk** para descarregar.
   (Quem quiser a versão de teste mais recente, feita a cada alteração, encontra-a
   em <https://github.com/coelho26101009-source/Nano_Assistant/releases/tag/android-test>.)
3. Abre o ficheiro descarregado. O Android vai dizer que não permite instalar
   apps desta origem: toca em **Definições**, liga **Permitir desta origem**
   e volta atrás. Toca em **Instalar**.
4. Se aparecer um aviso do Play Protect a dizer que a app é desconhecida,
   toca em **Mais detalhes → Instalar mesmo assim**. É normal: esta versão de
   teste não vem da Play Store.

## Parte 3: ligar o telemóvel ao PC

1. No PC, no painel **Telemóvel**, carrega em **Ligar um telemóvel**.
   Aparece um código QR (dura 5 minutos e só serve uma vez).
2. No telemóvel, abre o **NANO**, passa os ecrãs de boas-vindas e toca em
   **Ligar ao meu PC** (*Connect to my PC*). Aponta para o QR.
3. No **PC** aparece: *"<o teu telemóvel>" quer ligar-se a este NANO*, com um
   **código de 6 dígitos**. O telemóvel mostra o mesmo código. Só carrega em
   **Permitir** se os dois códigos forem iguais. Nada é dado ao telemóvel antes
   disto, e uma fotografia do QR não chega para ligar outro telemóvel.
4. Pronto. O ponto ao lado de "NANO" no topo da app fica verde.

> Se o telemóvel disser que **a app tem de ser atualizada**, instala a versão
> de teste mais recente: um NANO atualizado no PC já não liga telemóveis com a
> app antiga (os que já estavam ligados continuam a funcionar).

## O que experimentar

- **Chat:** escreve "olá" e vê a resposta a aparecer aos poucos.
- **Voz:** carrega no microfone, fala, e a resposta é lida em voz alta
  (podes mudar isto em Definições → Ler as respostas em voz alta).
- **Ler em voz alta:** toca no altifalante debaixo de uma resposta; toca outra
  vez para parar.
- **Autorizações:** pede algo que precise de autorização no PC (por exemplo
  "abre o bloco de notas"). O pedido aparece no telemóvel **e** no PC; quem
  responder primeiro decide.
- **Lembretes:** "lembra-me daqui a 2 minutos de beber água". Bloqueia o
  telemóvel: a notificação deve tocar mesmo sem a app aberta. Na primeira vez
  o Android pede para permitir notificações: diz que sim.
- **Câmara (tu pedes):** no Chat, toca no botão da câmara ao lado do
  microfone. Na primeira vez o Android pede acesso à câmara: diz que sim.
  Aponta para uma coisa (uma caneca, um rótulo), carrega no botão redondo e
  depois em enviar (sem escrever nada, pergunta "O que é isto?"). Enquanto a
  câmara está aberta tem de aparecer o aviso vermelho **Câmara ligada**.
- **Câmara (o NANO pede):** no PC, diz ou escreve "olha para isto pelo
  telemóvel" ou "o que é isto? usa a câmara". No telemóvel aparece o cartão
  **O NANO quer ver pela câmara**: experimenta **Recusar** uma vez (o NANO tem
  de dizer que não viu nada) e depois **Abrir a câmara**. Se não fizeres nada
  durante 90 segundos, o cartão desaparece sozinho.
  Para isto funcionar o PC precisa de um modelo que veja imagens: o mais leve é
  `ollama pull moondream` (fica tudo no PC); sem ele usa a Groq/Gemini se
  tiveres a chave, e o cartão diz onde a foto vai ser vista.
- **Agenda e Memória:** cria e apaga um evento e uma memória, e confirma que
  aparecem também no PC.
- **Conversas:** o botão ☰ no Chat mostra as conversas do PC.
- **Remover:** no PC, em Telemóveis ligados, carrega em **Remover**. A app
  deve dizer que foi removida e deixar de conseguir falar com o PC.

## Fora de casa

Não precisas de configurar nada. Com **Definições → Geral → Telemóvel → Fora
de casa** ligado (vem ligado), o PC faz uma única ligação de saída ao
reencaminhador do NANO e o telemóvel encontra-o lá quando não está no Wi-Fi de
casa. Tudo continua cifrado com a chave do teu telemóvel: o reencaminhador só
passa as mensagens e não as consegue ler. Nenhuma porta é aberta no router.

Para testar:

1. Com o telemóvel em casa, abre a app uma vez (um telemóvel ligado antes
   desta versão aprende o caminho sozinho; não precisas de o ligar de novo).
2. No PC, em Telemóvel, a linha **Fora de casa** deve dizer *Ligado, cifrado
   de ponta a ponta*.
3. No telemóvel, desliga o Wi-Fi e usa os dados móveis. Manda uma mensagem:
   a resposta deve chegar como em casa (o primeiro pedido pode demorar alguns
   segundos enquanto a app percebe que não está em casa).
4. No PC, desliga **Fora de casa**: o telemóvel em dados móveis deve deixar de
   chegar ao PC.

O **Tailscale** continua a funcionar como alternativa, se o preferires.

## Se algo correr mal

| O que vês | O que fazer |
|---|---|
| "Não consigo chegar ao PC" | Confirma que o NANO está aberto e o Acesso do telemóvel ligado. Em casa, os dois no mesmo Wi-Fi; fora de casa, o PC com internet e **Fora de casa** ligado. |
| Funciona no PC mas nunca no telemóvel | Provavelmente a firewall. Windows → *Firewall do Windows Defender* → *Permitir uma aplicação* → procura **Python** e marca **Privada**. |
| "A porta 47821 está ocupada" | Outro programa usa essa porta. Fecha e abre o NANO; se continuar, diz-me. |
| O QR não lê | Usa "Sem câmara? Copia o código" no PC, envia-o para o telemóvel e toca em **Colar o código em vez disso**. |
| Os lembretes não tocam | No Android: Definições → Apps → NANO → Notificações ligadas, e Bateria → **Sem restrições**. |

Quando testares, diz-me o que correu bem e o que não correu, com o modelo do
telemóvel e a versão do Android.
