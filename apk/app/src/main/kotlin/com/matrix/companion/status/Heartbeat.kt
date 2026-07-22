package com.matrix.companion.status

import android.content.Context
import com.matrix.companion.auth.HmacAuth
import com.matrix.companion.auth.HmacSigner
import com.matrix.companion.auth.SecretProvider
import com.matrix.companion.net.NetworkTypeMonitor
import com.matrix.companion.net.TailscaleClient
import com.matrix.companion.util.Logx
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.headers
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.UUID

/**
 * Periodic POST to master's /api/v1/devices/{id}/heartbeat. On miss the master
 * flags the device offline and (per threat-model.md §4) will refuse further
 * signed requests after grace window.
 *
 * Bug fix vs. the previous version:
 * - The master verifier requires an HMAC signature on every write endpoint.
 *   The old Heartbeat posted without `X-Signature`, so the master replied
 *   401 UNAUTHORIZED and silently dropped every heartbeat — devices
 *   appeared offline even though they were running fine.
 * - Now we sign the body with the same shared secret used by the inbound
 *   verifier; both sides use the canonical "{ts}\n{rid}\n{sha256(body)}"
 *   format defined in [HmacAuth.canonicalMessage].
 * - Added bounded retry-with-backoff (1s/3s/10s) on transient network
 *   failure so a single bad ping doesn't cascade into "device offline".
 */
class Heartbeat(
    private val appContext: Context,
    private val provider: StatusProvider,
    private val masterUrl: String,
    private val intervalMs: Long = 30_000L,
    private val secretProvider: SecretProvider,
) {
    @Serializable
    private data class Payload(
        val device_id: String,
        val tailscale_ip: String,
        val online: Boolean,
        val app: String?,
        val battery: Int,
        val network: String,
    )

    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val client = HttpClient(CIO) { install(ContentNegotiation) { json(json) } }
    private val signer = HmacSigner(secretProvider)

    private var job: Job? = null

    fun start(scope: CoroutineScope, deviceId: () -> String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                runOnce(deviceId())
                delay(intervalMs)
            }
        }
    }

    fun stop() { job?.cancel(); job = null }

    /**
     * Send one heartbeat. Public for tests. Internal retry is bounded so a
     * wedged network doesn't block the loop forever.
     */
    suspend fun runOnce(deviceId: String): HttpResponse? {
        val ip = TailscaleClient.peekIp() ?: "0.0.0.0"
        val s = provider.snapshot()
        val network = NetworkTypeMonitor.current(appContext)
        val payload = Payload(deviceId, ip, s.online, s.app, s.battery, network)
        val body = json.encodeToString(Payload.serializer(), payload).toByteArray(Charsets.UTF_8)

        val backoffsMs = longArrayOf(0L, 1_000L, 3_000L, 10_000L)
        for (delayMs in backoffsMs) {
            if (delayMs > 0L) delay(delayMs)
            val response = try {
                sendOnce(deviceId, body)
            } catch (t: Throwable) {
                Logx.w("heartbeat.send_failed: ${t.javaClass.simpleName}: ${t.message}")
                null
            }
            if (response != null && response.status.value !in 500..599) {
                if (response.status.value >= 400) {
                    Logx.w("heartbeat.non_2xx: status=${response.status.value}")
                }
                return response
            }
        }
        Logx.w("heartbeat.give_up_after_retries")
        return null
    }

    private suspend fun sendOnce(deviceId: String, body: ByteArray): HttpResponse? {
        return try {
            val timestamp = (System.currentTimeMillis() / 1000L).toString()
            val requestId = UUID.randomUUID().toString()
            val signature = signer.sign(body, timestamp, requestId)

            client.post("$masterUrl/api/v1/devices/$deviceId/heartbeat") {
                contentType(ContentType.Application.Json)
                headers {
                    append(HmacAuth.HEADER_TIMESTAMP, timestamp)
                    append(HmacAuth.HEADER_REQUEST_ID, requestId)
                    append(HmacAuth.HEADER_SIGNATURE, signature)
                }
                setBody(body)
            }
        } catch (t: Throwable) {
            Logx.w("heartbeat.transport_error: ${t.message}")
            null
        }
    }
}