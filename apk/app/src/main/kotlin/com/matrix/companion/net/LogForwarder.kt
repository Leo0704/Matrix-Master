package com.matrix.companion.net

import android.content.Context
import android.util.Log
import com.matrix.companion.BuildConfig
import io.ktor.client.HttpClient
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
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import timber.log.Timber
import java.util.UUID

/**
 * Periodically ships INFO+ log lines to the master controller's
 * `/api/v1/logs` ingest endpoint.
 *
 * Architecture:
 *   - [LogForwarderTree] is a [Timber.Tree] that filters by priority,
 *     appends to an in-memory buffer (max 500 lines), and is planted
 *     once at process start.
 *   - [LogForwarder] owns a coroutine on [Dispatchers.IO] that flushes
 *     the buffer every [FLUSH_INTERVAL_MS]; failures retry once then drop.
 *
 * Buffering is bounded and best-effort: at most ~500 lines * 30 s of
 * forwarder cadence before older entries are dropped. Master ingest is
 * expected to be lossy.
 */
object LogForwarder {

    private const val TAG = "LogForwarder"
    private const val FLUSH_INTERVAL_MS = 30_000L
    private const val BUFFER_MAX = 500

    @Serializable
    data class LogLine(
        val level: String,
        val event: String? = null,
        val message: String,
        val attrs: Map<String, String> = emptyMap(),
        val throwable: String? = null,
    )

    @Serializable
    data class LogBatch(
        val device_id: String,
        val app_version: String,
        val trace_id: String,
        val lines: List<LogLine>,
    )

    @Volatile private var deviceId: String = "unknown"
    @Volatile private var appVersion: String = BuildConfig.VERSION_NAME

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private val http = HttpClient(CIO) {
        install(ContentNegotiation) { json(json) }
        expectSuccess = false
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var flushJob: Job? = null

    private val buffer = ArrayDeque<LogLine>(BUFFER_MAX)
    private val bufferLock = Any()

    /**
     * Plant the forwarder tree and start the flush loop. Idempotent —
     * safe to call multiple times; only the first call wires up Timber.
     */
    @Synchronized
    fun install(context: Context) {
        // Capture identity from prefs so we don't re-read on every flush.
        // Use DeviceRegistrar so a fresh UUID is generated on first boot
        // (otherwise we'd ship logs tagged "unknown" until the user pairs).
        deviceId = try {
            DeviceRegistrar(context.applicationContext).deviceId()
        } catch (_: Throwable) {
            "unknown"
        }
        if (Timber.forest().none { it is LogForwarderTree }) {
            Timber.plant(LogForwarderTree())
        }
        if (flushJob == null) {
            flushJob = scope.launch { runFlushLoop() }
        }
    }

    private suspend fun runFlushLoop() {
        while (true) {
            delay(FLUSH_INTERVAL_MS)
            flush()
        }
    }

    /**
     * Drain the buffer and POST one batch. Visible for tests.
     * Network failures: retry once, then drop the batch (do not requeue).
     */
    internal suspend fun flush() {
        val snapshot = synchronized(bufferLock) {
            if (buffer.isEmpty()) return
            val drained = buffer.toList()
            buffer.clear()
            drained
        }

        val batch = LogBatch(
            device_id = deviceId,
            app_version = appVersion,
            trace_id = UUID.randomUUID().toString(),
            lines = snapshot,
        )

        val url = "${DeviceRegistrar.MASTER_DEFAULT}/api/v1/logs"
        if (!postOnce(url, batch)) {
            // One retry before dropping.
            delay(1_000)
            if (!postOnce(url, batch)) {
                Log.w(TAG, "dropping ${snapshot.size} log lines after retry")
            }
        }
    }

    private suspend fun postOnce(url: String, batch: LogBatch): Boolean = try {
        http.post(url) {
            contentType(ContentType.Application.Json)
            setBody(batch)
        }
        true
    } catch (t: Throwable) {
        Log.w(TAG, "log POST failed: ${t.javaClass.simpleName}: ${t.message}")
        false
    }

    /**
     * Filters by priority, truncates, and pushes into the forwarder buffer.
     * Planted into [Timber] by [install]; do not plant manually.
     */
    class LogForwarderTree : Timber.Tree() {
        override fun log(priority: Int, tag: String?, message: String, t: Throwable?) {
            if (priority < Log.INFO) return  // skip DEBUG/VERBOSE — keep noise down
            val level = when (priority) {
                Log.INFO -> "info"
                Log.WARN -> "warn"
                Log.ERROR -> "error"
                Log.ASSERT -> "fatal"
                else -> "info"
            }
            val throwableStr = t?.let { stackTrace(it) }
            val line = LogLine(
                level = level,
                event = tag,
                message = message,
                throwable = throwableStr,
            )
            synchronized(bufferLock) {
                if (buffer.size == BUFFER_MAX) buffer.removeFirst()
                buffer.addLast(line)
            }
        }

        private fun stackTrace(t: Throwable): String {
            val sw = java.io.StringWriter()
            t.printStackTrace(java.io.PrintWriter(sw))
            return sw.toString().take(4_000)  // cap to keep POST body sane
        }
    }
}
