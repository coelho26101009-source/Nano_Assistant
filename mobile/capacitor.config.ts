import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  // The Play Store identity. Changing it later makes a different app.
  appId: "app.nano.assistant",
  appName: "NANO",
  webDir: "dist",
  android: {
    // Requests to the PC go through Android's own HTTP stack (CapacitorHttp),
    // not the WebView, so the PC needs no CORS and no TLS certificate. Every
    // body is sealed with AES-256-GCM in src/lib/crypto.ts.
    allowMixedContent: false,
    backgroundColor: "#181818",
  },
  plugins: {
    CapacitorHttp: { enabled: true },
    SplashScreen: {
      launchShowDuration: 600,
      backgroundColor: "#181818",
      showSpinner: false,
      androidScaleType: "CENTER_INSIDE",
    },
    StatusBar: { style: "DARK", backgroundColor: "#181818", overlaysWebView: false },
    LocalNotifications: { smallIcon: "ic_stat_nano", iconColor: "#4C8DF6" },
  },
};

export default config;
