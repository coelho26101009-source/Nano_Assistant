import type { AppProps } from "next/app";
import Head from "next/head";
import "../styles/globals.css";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        {/* eel.js é servido automaticamente pelo Eel Python na raiz. */}
        <script src="/eel.js" />
        {/* nano_bridge.js tem de vir DEPOIS do eel.js e tem de continuar a ser
            JS puro em /public: o Eel procura o texto literal `eel.expose(` nos
            ficheiros servidos, e o minificador do Next apaga esse token se as
            chamadas ficarem dentro do bundle. Sem este ficheiro, o Python não
            consegue chamar a UI (streaming, wake word, confirmações). */}
        <script src="/nano_bridge.js" />
      </Head>
      <Component {...pageProps} />
    </>
  );
}
