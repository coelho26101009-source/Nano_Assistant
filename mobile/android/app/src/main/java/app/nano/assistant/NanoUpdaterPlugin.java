package app.nano.assistant;

import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import androidx.core.content.FileProvider;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.net.ssl.HttpsURLConnection;

/**
 * Updates for the test APK: download, check, and hand over to Android.
 *
 * The web side (src/lib/updates.ts) has already checked the signed update
 * feed. This class then trusts nothing it is given:
 *   - the URL must be HTTPS on github.com under this repository's releases,
 *     and the download must end on GitHub's own hosts;
 *   - the file must have exactly the size and SHA-256 the feed promised;
 *   - the APK must be this app (same package), newer than what is installed,
 *     and signed with the same certificate (Android 9+; older Android checks
 *     the certificate itself when installing).
 * Only then is Android's package installer opened. Android always asks the
 * person to confirm, and refuses an APK signed with another key.
 *
 * Release (Play Store) builds have SELF_UPDATE = false and no
 * REQUEST_INSTALL_PACKAGES permission: Google Play updates them, and its rules
 * forbid an app from updating itself.
 */
@CapacitorPlugin(name = "NanoUpdater")
public class NanoUpdaterPlugin extends Plugin {

    private static final String REPO_PATH = "/coelho26101009-source/Nano_Assistant/releases/download/";
    private static final long MAX_SIZE = 300L * 1024 * 1024;
    private static final Set<String> DOWNLOAD_HOSTS = new HashSet<>(Arrays.asList(
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com"
    ));

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);

    @PluginMethod
    public void status(PluginCall call) {
        JSObject result = new JSObject();
        result.put("supported", BuildConfig.SELF_UPDATE);
        result.put("allowed", canInstallPackages());
        result.put("versionCode", installedVersionCode());
        call.resolve(result);
    }

    @PluginMethod
    public void openInstallSettings(PluginCall call) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Intent intent = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + getContext().getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
        }
        call.resolve();
    }

    @PluginMethod
    public void downloadAndInstall(PluginCall call) {
        if (!BuildConfig.SELF_UPDATE) {
            call.reject("not_supported", "not_supported");
            return;
        }
        final String url = call.getString("url", "");
        final String sha256 = call.getString("sha256", "");
        final Double sizeValue = call.getDouble("size");
        final long size = sizeValue == null ? -1 : sizeValue.longValue();
        if (!isAllowedDownloadUrl(url) || !sha256.matches("^[0-9a-f]{64}$") || size <= 0 || size > MAX_SIZE) {
            call.reject("feed_format", "feed_format");
            return;
        }
        if (!canInstallPackages()) {
            JSObject result = new JSObject();
            result.put("status", "needs_permission");
            call.resolve(result);
            return;
        }
        if (!busy.compareAndSet(false, true)) {
            call.reject("busy", "busy");
            return;
        }
        executor.execute(() -> {
            try {
                File apk = download(url, sha256, size);
                checkArchive(apk);
                openInstaller(apk);
                JSObject result = new JSObject();
                result.put("status", "installer_opened");
                call.resolve(result);
            } catch (UpdateException e) {
                call.reject(e.code, e.code);
            } catch (Exception e) {
                call.reject("download_failed", "download_failed");
            } finally {
                busy.set(false);
            }
        });
    }

    /* ── Download ───────────────────────────────────────────────────────── */

    private File download(String url, String expectedSha, long expectedSize) throws Exception {
        File dir = new File(getContext().getCacheDir(), "updates");
        if (!dir.isDirectory() && !dir.mkdirs()) throw new UpdateException("download_failed");
        File partial = new File(dir, "nano-update.apk.part");
        File apk = new File(dir, "nano-update.apk");
        //noinspection ResultOfMethodCallIgnored
        apk.delete();
        //noinspection ResultOfMethodCallIgnored
        partial.delete();

        HttpsURLConnection connection = (HttpsURLConnection) new URL(url).openConnection();
        connection.setInstanceFollowRedirects(true);
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(60000);
        connection.setRequestProperty("User-Agent", "NanoAndroid/" + installedVersionCode());
        try {
            int code = connection.getResponseCode();
            URL landed = connection.getURL();
            if (!"https".equals(landed.getProtocol())
                    || !DOWNLOAD_HOSTS.contains(landed.getHost().toLowerCase(Locale.ROOT))) {
                throw new UpdateException("bad_redirect");
            }
            if (code != 200) throw new UpdateException("download_http_" + code);

            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long received = 0;
            int lastPercent = -1;
            try (InputStream in = connection.getInputStream(); FileOutputStream out = new FileOutputStream(partial)) {
                byte[] buffer = new byte[64 * 1024];
                int read;
                while ((read = in.read(buffer)) != -1) {
                    received += read;
                    if (received > expectedSize) throw new UpdateException("download_too_large");
                    digest.update(buffer, 0, read);
                    out.write(buffer, 0, read);
                    int percent = (int) (received * 100 / expectedSize);
                    if (percent != lastPercent && percent % 2 == 0) {
                        lastPercent = percent;
                        JSObject progress = new JSObject();
                        progress.put("progress", received / (double) expectedSize);
                        notifyListeners("updateProgress", progress);
                    }
                }
            }
            if (received != expectedSize) throw new UpdateException("download_incomplete");
            if (!expectedSha.equals(hex(digest.digest()))) throw new UpdateException("download_bad_hash");
            if (!partial.renameTo(apk)) throw new UpdateException("download_failed");
            return apk;
        } catch (Exception e) {
            //noinspection ResultOfMethodCallIgnored
            partial.delete();
            throw e;
        } finally {
            connection.disconnect();
        }
    }

    /* ── Checks on the downloaded APK ──────────────────────────────────── */

    @SuppressWarnings("deprecation")
    private void checkArchive(File apk) throws UpdateException {
        PackageManager pm = getContext().getPackageManager();
        int flags = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? PackageManager.GET_SIGNING_CERTIFICATES : 0;
        PackageInfo archive = pm.getPackageArchiveInfo(apk.getAbsolutePath(), flags);
        if (archive == null || !getContext().getPackageName().equals(archive.packageName)) {
            throw new UpdateException("apk_wrong_app");
        }
        if (versionCodeOf(archive) <= installedVersionCode()) throw new UpdateException("apk_not_newer");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            try {
                PackageInfo installed = pm.getPackageInfo(getContext().getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
                if (archive.signingInfo == null || installed.signingInfo == null) throw new UpdateException("apk_wrong_signer");
                Set<String> mine = signerSet(installed.signingInfo.getApkContentsSigners());
                Set<String> theirs = signerSet(archive.signingInfo.getApkContentsSigners());
                if (mine.isEmpty() || !mine.equals(theirs)) throw new UpdateException("apk_wrong_signer");
            } catch (PackageManager.NameNotFoundException e) {
                throw new UpdateException("apk_wrong_signer");
            }
        }
    }

    private void openInstaller(File apk) {
        Uri uri = FileProvider.getUriForFile(getContext(), getContext().getPackageName() + ".fileprovider", apk);
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(uri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(intent);
    }

    /* ── Helpers ───────────────────────────────────────────────────────── */

    private boolean canInstallPackages() {
        if (!BuildConfig.SELF_UPDATE) return false;
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return true;
        return getContext().getPackageManager().canRequestPackageInstalls();
    }

    @SuppressWarnings("deprecation")
    private long installedVersionCode() {
        try {
            PackageInfo info = getContext().getPackageManager().getPackageInfo(getContext().getPackageName(), 0);
            return versionCodeOf(info);
        } catch (PackageManager.NameNotFoundException e) {
            return 0;
        }
    }

    @SuppressWarnings("deprecation")
    private static long versionCodeOf(PackageInfo info) {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? info.getLongVersionCode() : info.versionCode;
    }

    private static boolean isAllowedDownloadUrl(String value) {
        try {
            URL url = new URL(value);
            return "https".equals(url.getProtocol())
                && "github.com".equals(url.getHost())
                && url.getPort() == -1
                && url.getUserInfo() == null
                && url.getQuery() == null
                && url.getRef() == null
                && url.getPath().startsWith(REPO_PATH)
                && !url.getPath().contains("..");
        } catch (Exception e) {
            return false;
        }
    }

    private static Set<String> signerSet(Signature[] signers) throws UpdateException {
        Set<String> set = new HashSet<>();
        if (signers == null) return set;
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Signature signer : signers) set.add(hex(digest.digest(signer.toByteArray())));
        } catch (Exception e) {
            throw new UpdateException("apk_wrong_signer");
        }
        return set;
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) out.append(String.format(Locale.ROOT, "%02x", b));
        return out.toString();
    }

    private static final class UpdateException extends Exception {
        final String code;

        UpdateException(String code) {
            super(code);
            this.code = code;
        }
    }
}
