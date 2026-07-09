package com.matrix.companion

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import com.matrix.companion.auth.IdempotencyCache
import com.matrix.companion.auth.HmacVerifier
import com.matrix.companion.crypto.HmacSecretStore
import com.matrix.companion.crypto.KeystoreManager
import com.matrix.companion.status.StatusProvider
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.net.LogForwarder
import com.matrix.companion.util.Logx

/**
 * Process-wide singletons. We avoid Hilt/Dagger here — APK is small enough
 * that hand-rolled DI keeps the deploy surface down.
 *
 * Boot order:
 *   App.onCreate() → KeystoreManager().ensureKey() → HmacSecretStore
 *                 → IdempotencyCache
 *                 → StatusProvider / TailscaleClient (constructed but not started)
 *                 → register notification channel
 *   MainActivity onClick "Start" → start CompanionService
 *   CompanionService.onCreate()  → bind Tailscale → register device → start HttpServer
 */
class App : Application() {
    lateinit var keystoreManager: KeystoreManager
        private set
    lateinit var hmacSecretStore: HmacSecretStore
        private set
    lateinit var hmacVerifier: HmacVerifier
        private set
    lateinit var idempotency: IdempotencyCache
        private set
    lateinit var statusProvider: StatusProvider
        private set
    lateinit var driver: AccessibilityDriver
        private set
    lateinit var executor: ActionExecutor
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        try {
            keystoreManager = KeystoreManager().also { it.ensureKey() }
            hmacSecretStore = HmacSecretStore(this)
            hmacVerifier = HmacVerifier(secretProvider = hmacSecretStore)
            idempotency = IdempotencyCache()
            statusProvider = StatusProvider(this)
            driver = AccessibilityDriver(serviceRef = { accessibilityServiceInstance })
            executor = ActionExecutor(driver, this)
            ensureNotificationChannel()
            // Plant the LogForwarder Tree so every Logx.i/w/e (and any
            // direct Timber call) is shipped to master. Must run after
            // shared-prefs is readable (always true here — App is the
            // first thing the process touches).
            LogForwarder.install(this)
            Logx.i("App initialized")
        } catch (t: Throwable) {
            Logx.e("App init failed", t)
        }
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                getString(R.string.notif_channel),
                getString(R.string.app_name),
                NotificationManager.IMPORTANCE_LOW,
            )
            nm.createNotificationChannel(channel)
        }
    }

    companion object {
        @Volatile var accessibilityServiceInstance: android.accessibilityservice.AccessibilityService? = null

        @Volatile lateinit var instance: App

        fun get(context: Context): App = context.applicationContext as App
    }
}

/** Strongly-typed Context wrapper used by ActionExecutor. See App.kt. */
val Context.app: App get() = App.get(this)
