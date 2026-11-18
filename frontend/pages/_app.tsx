import type { AppProps } from "next/app";
import Head from "next/head";
import { useEffect, useState } from "react";
import "../styles/globals.css";

export default function App({ Component, pageProps }: AppProps) {
  // The interface language is only known in the browser (?lang= or the
  // browser's own language), so nothing is rendered at build time: a
  // pre-rendered English page hydrated in Portuguese would mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);
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
      {mounted && <Component {...pageProps} />}
    </>
  );
}
