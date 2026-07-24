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
import com.matrix.companion.MainActivity
import com.matrix.companion.R
import com.matrix.companion.util.Logx
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

/**
 * Sibling foreground service that does nothing except poll
 * [CompanionService] and [TaskPollerService] liveness every 30 s.
 * If either dies, relaunch it.
 */
class WatchdogService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(this, getString(R.string.notif_channel))
            .setContentTitle("Matrix Watchdog")
            .setContentText("守护 Companion")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentIntent(tap)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIF_ID, n)
        }

        scope.launch {
            while (isActive) {
                delay(TimeUnit.SECONDS.toMillis(30))
                if (!isServiceAlive(CompanionService::class.java.name)) {
                    Logx.w("Watchdog: CompanionService missing, relaunching")
                    CompanionService.start(this@WatchdogService)
                }
                if (!isServiceAlive(TaskPollerService::class.java.name)) {
                    Logx.w("Watchdog: TaskPollerService missing, relaunching")
                    TaskPollerService.start(this@WatchdogService)
                }
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun isServiceAlive(className: String): Boolean = try {
        val am = getSystemService(Context.ACTIVITY_SERVICE) as android.app.ActivityManager
        am.getRunningServices(Int.MAX_VALUE)
            .any { it.service.className == className && it.started }
    } catch (_: Throwable) { true } // if we can't tell, don't kill it

    companion object {
        private const val NOTIF_ID = 43

        fun start(ctx: Context) {
            val i = Intent(ctx, WatchdogService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i)
            else ctx.startService(i)
        }
    }
}
