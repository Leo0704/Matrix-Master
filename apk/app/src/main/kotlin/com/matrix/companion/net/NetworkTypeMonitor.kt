package com.matrix.companion.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build

/**
 * 判断当前手机用的是移动数据、WiFi 还是 VPN。
 *
 * 风控场景需要确保每台手机走自己的移动数据出口，所以心跳里要带这个字段，
 * 后台发现设备长时间走 WiFi 可以告警。
 */
object NetworkTypeMonitor {

    fun current(context: Context): String {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return "unknown"

        val activeNetwork = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            cm.activeNetwork
        } else {
            @Suppress("DEPRECATION")
            cm.activeNetworkInfo?.let { return legacyTypeName(it.type, it.subtype) }
        } ?: return "unknown"

        val caps = cm.getNetworkCapabilities(activeNetwork) ?: return "unknown"

        // VPN 优先级最高：哪怕底层是 WiFi，只要开了 VPN，出口 IP 就可能和别人一样。
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
            return "vpn"
        }
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
            return "wifi"
        }
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) {
            return "mobile"
        }
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) {
            return "ethernet"
        }
        return "unknown"
    }

    @Suppress("DEPRECATION")
    private fun legacyTypeName(type: Int, subtype: Int): String = when (type) {
        ConnectivityManager.TYPE_WIFI -> "wifi"
        ConnectivityManager.TYPE_MOBILE -> "mobile"
        ConnectivityManager.TYPE_VPN -> "vpn"
        ConnectivityManager.TYPE_ETHERNET -> "ethernet"
        else -> "unknown"
    }
}
