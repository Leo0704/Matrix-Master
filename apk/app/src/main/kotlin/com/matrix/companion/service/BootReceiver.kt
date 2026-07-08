package com.matrix.companion.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.matrix.companion.util.Logx

/**
 * Bring services back after reboot, package replacement, or locked-boot.
 * The MainActivity handles first-launch pairing; this receiver only
 * re-launches the *running* services if the device restarts.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                Logx.i("BootReceiver: restarting services for ${intent.action}")
                CompanionService.start(context)
                WatchdogService.start(context)
            }
        }
    }
}
