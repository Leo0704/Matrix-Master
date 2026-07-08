package com.matrix.companion.status

import com.matrix.companion.net.TailscaleClient
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.post
import io.ktor.client.request.setBody
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

/**
 * Periodic POST to master's /api/v1/devices/heartbeat. On miss the master
 * flags the device offline and (per threat-model.md §4) will refuse further
 * signed requests after grace window.
 */
class Heartbeat(
    private val provider: StatusProvider,
    private val masterUrl: String,
    private val intervalMs: Long = 30_000L,
) {
    @Serializable
    private data class Payload(
        val device_id: String,
        val tailscale_ip: String,
        val online: Boolean,
        val app: String?,
        val battery: Int,
    )

    private val json = Json { ignoreUnknownKeys = true }
    private val client = HttpClient(CIO) { install(ContentNegotiation) { json(json) } }

    private var job: Job? = null

    fun start(scope: CoroutineScope, deviceId: () -> String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                val ip = TailscaleClient.peekIp() ?: "0.0.0.0"
                val s = provider.snapshot()
                runCatching {
                    client.post("$masterUrl/api/v1/devices/heartbeat") {
                        contentType(ContentType.Application.Json)
                        setBody(Payload(deviceId(), ip, s.online, s.app, s.battery))
                    }.body<String>()
                }
                delay(intervalMs)
            }
        }
    }

    fun stop() { job?.cancel(); job = null }
}
