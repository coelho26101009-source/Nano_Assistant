import type { AppProps } from "next/app";
import Head from "next/head";
import "../styles/globals.css";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        {/* eel.js é servido automaticamente pelo Eel Python na raiz */}
        <script src="/eel.js" />
      </Head>
      <Component {...pageProps} />
    </>
  );
}
