package com.matrix.companion.net

import com.matrix.companion.util.Logx
import java.net.NetworkInterface

/**
 * Peek at the device's Tailscale IP. Real implementation embeds libtailscale
 * (https://github.com/tailscale/tailscale/tree/main/cmd/tsnet-android) — that
 * gives us a `tsnet.LocalClient` we could query for the local IP.
 *
 * Without that dep, we degrade to scanning `getNetworkInterfaces()` for a
 * 100.x.y.z (CGNAT range) address — Tailscale's default allocation. Good
 * enough to bootstrap; the master controller does the strict auth check.
 */
object TailscaleClient {
    private const val TAILNET_PREFIX = "100."

    @Volatile private var cached: String? = null

    fun refresh(): String? {
        try {
            cached = NetworkInterface.getNetworkInterfaces()
                ?.toList()
                ?.flatMap { it.inetAddresses.toList() }
                ?.map { it.hostAddress ?: "" }
                ?.firstOrNull { it.startsWith(TAILNET_PREFIX) }
            return cached
        } catch (t: Throwable) {
            Logx.w("tailscale refresh failed: ${t.message}")
            return cached
        }
    }

    fun peekIp(): String? = cached ?: refresh()

    fun status(): String = if (peekIp() == null) "disconnected" else "connected"
}
