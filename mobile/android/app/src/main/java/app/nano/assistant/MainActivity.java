package app.nano.assistant;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // NANO's own plugin (updates for the test APK). Registered before the
        // bridge starts, as Capacitor requires for local plugins.
        registerPlugin(NanoUpdaterPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
