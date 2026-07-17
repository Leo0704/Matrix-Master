package com.matrix.companion.status

import android.content.Context
import android.os.BatteryManager
import android.os.SystemClock
import com.matrix.companion.App
import com.matrix.companion.net.TailscaleClient
import kotlinx.serialization.Serializable

/**
 * Snapshot of the device state as returned to the master controller on
 * GET /device/status. Cheap to compute; safe to invoke on every heartbeat.
 */
class StatusProvider(private val context: Context) {

    fun snapshot(): DeviceStatus = DeviceStatus(
        online = true,
        busy = App.accessibilityServiceInstance == null, // not bound == can't act == "blocked"
        app = foregroundApp(),
        battery = batteryPercent(),
        network = networkGeneration(),
        signal_dbm = null,
        tailscale_state = TailscaleClient.status(),
        uptime_sec = ((System.currentTimeMillis() - bootTimeMs) / 1000L).toInt(),
    )

    fun foregroundApp(): String? {
        // Android 5.0+ deprecated getRunningTasks — it only returns the
        // caller's own app. Use the accessibility driver's rootNode()
        // which traverses getWindows() on HyperOS where rootInActiveWindow
        // is broken and returns our own window.
        return try {
            App.instance.driver.rootNode()?.packageName?.toString()
        } catch (_: Throwable) { null }
    }

    private val bootTimeMs: Long
        get() = try { System.currentTimeMillis() - SystemClock.elapsedRealtime() }
        catch (_: Throwable) { 0L }

    private fun batteryPercent(): Int = try {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
        bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
    } catch (_: Throwable) { -1 }

    private fun networkGeneration(): String = try {
        when (context.getSystemService(Context.TELEPHONY_SERVICE)?.let {
            (it as android.telephony.TelephonyManager).dataNetworkType
        }) {
            android.telephony.TelephonyManager.NETWORK_TYPE_NR -> "5G"
            android.telephony.TelephonyManager.NETWORK_TYPE_LTE,
            android.telephony.TelephonyManager.NETWORK_TYPE_HSPAP -> "4G"
            else -> "none"
        }
    } catch (_: Throwable) { "none" }
}

@Serializable
data class DeviceStatus(
    val online: Boolean,
    val busy: Boolean,
    val app: String?,
    val battery: Int,
    val network: String,
    val signal_dbm: Int?,
    val tailscale_state: String,
    val uptime_sec: Int,
)
