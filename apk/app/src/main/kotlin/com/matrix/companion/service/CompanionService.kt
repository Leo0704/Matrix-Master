package com.matrix.companion.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.matrix.companion.App
import com.matrix.companion.MainActivity
import com.matrix.companion.R
import com.matrix.companion.net.DeviceRegistrar
import com.matrix.companion.net.LoginStateReporter
import com.matrix.companion.net.MasterConfig
import com.matrix.companion.net.TailscaleClient
import com.matrix.companion.status.Heartbeat
import com.matrix.companion.util.Logx
import com.matrix.companion.xhs.LoginStateChecker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Long-running foreground service. Owns:
 *   - [HttpServer] bound to 0.0.0.0:8765
 *   - [Heartbeat] pumping status to master
 *   - Tailscale refresh loop (1 min cadence)
 *
 * Killed only by user (stop button) or by Android under memory pressure.
 * [WatchdogService] is a sibling that re-launches this one if it dies.
 */
class CompanionService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var httpServerJob: Job? = null
    private lateinit var heartbeat: Heartbeat
    private lateinit var loginStateReporter: LoginStateReporter

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat()
        TailscaleClient.refresh()
        val masterUrl = MasterConfig.get(this)
        heartbeat = Heartbeat(
            appContext = this,
            provider = App.get(this).statusProvider,
            masterUrl = masterUrl,
            secretProvider = App.get(this).hmacSecretStore,
        )
        heartbeat.start(scope) { DeviceRegistrar(this).deviceId() }

        // 启动登录状态检测上报
        loginStateReporter = LoginStateReporter(
            context = this,
            checker = LoginStateChecker(App.get(this).driver, App.get(this).executor),
            masterUrl = masterUrl,
            secretProvider = App.get(this).hmacSecretStore,
        )
        loginStateReporter.start(scope) { DeviceRegistrar(this).deviceId() }

        if (com.matrix.companion.BuildConfig.ENABLE_HTTP_SERVER) {
            httpServerJob = scope.launch {
                // Defer so App init completes if we boot from BOOT_COMPLETED.
                delay(500)
                HttpServer(App.get(this@CompanionService)).start()
            }
        }

        Logx.i("CompanionService started")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        httpServerJob?.cancel()
        Logx.w("CompanionService destroyed")
        super.onDestroy()
    }

    private fun startForegroundCompat() {
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(this, getString(R.string.notif_channel))
            .setContentTitle(getString(R.string.notif_title))
            .setContentText(getString(R.string.notif_text))
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setContentIntent(tap)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIF_ID,
                n,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(NOTIF_ID, n)
        }
    }

    companion object {
        private const val NOTIF_ID = 42

        fun start(ctx: Context) {
            val i = Intent(ctx, CompanionService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, CompanionService::class.java))
        }
    }
}
