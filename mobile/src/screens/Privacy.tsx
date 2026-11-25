/**
 * The privacy policy, in the app. The same text is published at
 * mobile/PRIVACY.md (the address the Play Store listing points to): change both.
 */
import { t } from "../lib/i18n";

export function PrivacyPolicy() {
  return (
    <div className="prose">
      <h2 style={{ marginTop: 0 }}>{t("Política de privacidade")}</h2>
      <p>{t("O NANO para Android é um comando para o NANO que corre no teu próprio PC. Não tem servidores nossos, contas, publicidade nem estatísticas de utilização.")}</p>
      <h2>{t("O que fica no telemóvel")}</h2>
      <ul>
        <li>{t("A chave de emparelhamento com o teu PC, guardada no cofre do Android (Keystore).")}</li>
        <li>{t("As tuas escolhas: idioma, tema e voz.")}</li>
        <li>{t("Os lembretes que a app agenda como notificações.")}</li>
      </ul>
      <p>{t("A cópia de segurança do Android está desligada para esta app, por isso nada disto sai do telemóvel.")}</p>
      <h2>{t("O que vai para o teu PC")}</h2>
      <p>{t("As mensagens que escreves ou dizes, as tuas respostas às autorizações e as alterações à agenda e à memória. Vão para o teu PC cifradas com AES-256-GCM e uma chave que só o teu telemóvel e o teu PC conhecem: diretamente pela tua rede ou, fora de casa, pelo reencaminhador do NANO (um Cloudflare Worker), que só passa as mensagens cifradas, não as consegue ler nem alterar, não guarda nada e só vê o teu endereço IP, o tamanho das mensagens e quando foram enviadas. Quem o NANO do PC usa para responder (um modelo local ou um serviço de IA que escolheste) é decidido no PC, como sempre.")}</p>
      <h2>{t("Câmara")}</h2>
      <p>{t("A app só usa a câmara quando lhe pedes: quando tocas no botão da câmara no chat, ou quando o NANO do PC pede para ver algo e tocas em Abrir a câmara no pedido (podes sempre Recusar; o NANO nunca abre a câmara sozinho e não há \"permitir sempre\"). Enquanto a câmara está aberta vês o aviso vermelho Câmara ligada. Tira-se uma só foto quando carregas no botão: sem vídeo nem som. Não fica guardada no telemóvel nem na galeria. Vai cifrada para o teu PC, onde um modelo de visão a descreve (primeiro um local, se o PC tiver; senão o serviço de IA escolhido no PC, como diz o pedido) e depois é deitada fora. Só a descrição escrita fica na conversa.")}</p>
      <h2>{t("Voz")}</h2>
      <p>{t("Quando carregas no microfone, o reconhecimento de voz do Android transforma a fala em texto. Dependendo do teu telemóvel, isso pode ser feito no aparelho ou pelo serviço de voz da Google. A app não grava áudio. Ler as respostas em voz alta usa o motor de voz do Android.")}</p>
      <h2>{t("Atualizações (só na versão de teste)")}</h2>
      <p>{t("A versão de teste que instalas a partir do GitHub procura uma versão nova no máximo duas vezes por dia, descarregando um pequeno ficheiro assinado do github.com (desliga-se em Definições → Sobre). O GitHub vê o teu endereço IP, como em qualquer download; nada sobre ti ou as tuas conversas é enviado. Instalar pede sempre a tua confirmação. A versão da Play Store não tem este sistema: é a Google Play que a atualiza.")}</p>
      <h2>{t("Permissões")}</h2>
      <ul>
        <li>{t("Rede: para falar com o teu PC.")}</li>
        <li>{t("Microfone: só quando carregas no microfone.")}</li>
        <li>{t("Câmara: só enquanto o ecrã da câmara está aberto, depois de tocares para o abrir.")}</li>
        <li>{t("Notificações e alarmes exatos: para os lembretes tocarem na hora certa.")}</li>
        <li>{t("Instalar apps (só na versão de teste): para abrir o instalador do Android com uma versão nova. O Android pergunta-te sempre.")}</li>
      </ul>
      <p>{t("O leitor de QR é o da Google: devolve só o código lido e não usa o acesso da app à câmara.")}</p>
      <h2>{t("Apagar os teus dados")}</h2>
      <p>{t("Em Definições → Esquecer este PC, o telemóvel apaga a chave e o PC deixa de o aceitar. Desinstalar a app apaga tudo o que ela guardou. As conversas e memórias vivem no PC e apagam-se lá.")}</p>
      <h2>{t("Contacto")}</h2>
      <p>{t("Dúvidas ou pedidos: abre uma questão em github.com/coelho97sc/Nano_Assistant.")}</p>
      <p className="dim">{t("Última atualização: 20 de novembro de 2026.")}</p>
    </div>
  );
}
