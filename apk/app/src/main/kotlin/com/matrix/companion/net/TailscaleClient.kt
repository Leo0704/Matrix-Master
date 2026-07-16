package com.matrix.companion.net

import com.matrix.companion.util.Logx
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Peek at the device's Tailscale IP. Real implementation embeds libtailscale
 * (https://github.com/tailscale/tailscale/tree/main/cmd/tsnet-android) — that
 * gives us a `tsnet.LocalClient` we could query for the local IP.
 *
 * Without that dep, we degrade to scanning `getNetworkInterfaces()` for a
 * 100.x.y.z (CGNAT range) address — Tailscale's default allocation. Good
 * enough to bootstrap; the master controller does the strict auth check.
 *
 * **Local-dev fallback (added during real-device testing):** if no 100.x
 * address is found (device doesn't have Tailscale installed), fall back to
 * the first non-loopback IPv4 on a WiFi / cellular interface. The pair
 * endpoint accepts whatever IP the device reports; the master still does
 * the HMAC check. This is what lets us test without Tailscale on the
 * phone.
 */
object TailscaleClient {
    private const val TAILNET_PREFIX = "100."

    @Volatile private var cached: String? = null

    fun refresh(): String? {
        try {
            // 1) Preferred: 100.x (Tailscale CGNAT range)
            val tailscale = NetworkInterface.getNetworkInterfaces()
                ?.toList()
                ?.flatMap { it.inetAddresses.toList() }
                ?.map { it.hostAddress ?: "" }
                ?.firstOrNull { it.startsWith(TAILNET_PREFIX) }
            if (tailscale != null) {
                cached = tailscale
                return cached
            }
            // 2) Fallback: first non-loopback IPv4 (WiFi or cellular). Skip
            //    loopback + link-local (169.254.x.x).
            val fallback = NetworkInterface.getNetworkInterfaces()
                ?.toList()
                ?.flatMap { ni -> ni.inetAddresses.toList().map { ni.name to it } }
                ?.firstOrNull { (_, addr) ->
                    addr is Inet4Address &&
                        !addr.isLoopbackAddress &&
                        !addr.isLinkLocalAddress
                }
                ?.second
                ?.hostAddress
                ?.removePrefix("/") // Kotlin wraps InetAddress.hostAddress oddly
            if (fallback != null) {
                Logx.w("tailscale not found; falling back to local IP $fallback")
                cached = fallback
                return cached
            }
            Logx.w("no usable IP found (no Tailscale, no WiFi/cellular IPv4)")
            return cached
        } catch (t: Throwable) {
            Logx.w("tailscale refresh failed: ${t.message}")
            return cached
        }
    }

    fun peekIp(): String? = cached ?: refresh()

    fun status(): String = if (peekIp() == null) "disconnected" else "connected"
}
