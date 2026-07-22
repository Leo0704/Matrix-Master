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
import com.matrix.companion.auth.HmacAuth
import com.matrix.companion.auth.HmacSigner
import com.matrix.companion.net.DeviceRegistrar
import com.matrix.companion.net.MasterConfig
import com.matrix.companion.util.ApiResult
import kotlin.random.Random
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Logx
import com.matrix.companion.xhs.XhsPublisher
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.headers
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.util.UUID

/**
 * 前台轮询服务：手机主动向后端拉任务并执行。
 *
 * v0.7 Phase 6：替代 HttpServer 成为主要生产路径，解决后端无法直连手机的问题。
 * 当前只实现 device_publish；collect/interact 后续补充。
 */
class TaskPollerService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var pollJob: Job? = null
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val http = HttpClient(CIO) {
        install(ContentNegotiation) { json(json) }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startForegroundCompat()
        pollJob = scope.launch {
            delay(2_000) // 等 App/服务初始化完成
            var consecutiveEmpty = 0
            while (isActive) {
                val claimed = try {
                    pollOnce()
                } catch (t: Throwable) {
                    Logx.e("TaskPoller.poll_failed", t)
                    false
                }
                consecutiveEmpty = if (claimed) 0 else consecutiveEmpty + 1
                val baseDelayMs = when {
                    claimed -> POLL_INTERVAL_CLAIMED_MS
                    consecutiveEmpty < 4 -> POLL_INTERVAL_IDLE_FAST_MS
                    else -> POLL_INTERVAL_IDLE_SLOW_MS
                }
                val jitterMs = Random.nextLong(baseDelayMs / 10)
                delay(baseDelayMs + jitterMs)
            }
        }
        Logx.i("TaskPollerService started")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        pollJob?.cancel()
        scope.cancel()
        Logx.w("TaskPollerService destroyed")
        super.onDestroy()
    }

    private suspend fun pollOnce(): Boolean {
        val app = App.get(this)
        val deviceId = DeviceRegistrar(app).deviceId()
        val masterUrl = MasterConfig.get(this)
        val signer = HmacSigner(app.hmacSecretStore)

        val bodyBytes = "{}".toByteArray(Charsets.UTF_8)
        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val requestId = UUID.randomUUID().toString()
        val signature = signer.sign(bodyBytes, timestamp, requestId)

        val resp = try {
            http.post("$masterUrl/api/v1/devices/$deviceId/tasks/next") {
                contentType(ContentType.Application.Json)
                headers {
                    append(HmacAuth.HEADER_TIMESTAMP, timestamp)
                    append(HmacAuth.HEADER_REQUEST_ID, requestId)
                    append(HmacAuth.HEADER_SIGNATURE, signature)
                }
                setBody(bodyBytes)
            }
        } catch (t: Throwable) {
            Logx.w("TaskPoller.next_request_failed: ${t.message}")
            return false
        }

        if (resp.status.value !in 200..299) {
            Logx.w("TaskPoller.next_non_2xx: status=${resp.status}")
            return false
        }

        val envelope: NextResponse = try {
            resp.body()
        } catch (t: Throwable) {
            Logx.e("TaskPoller.next_parse_failed", t)
            return false
        }

        val task = envelope.data ?: return false
        Logx.i("TaskPoller.claimed task=${task.id} action=${task.action}")

        // 在独立 coroutine 里执行，避免阻塞轮询。
        scope.launch {
            executeTask(app, deviceId, task, masterUrl)
        }
        return true
    }

    private suspend fun executeTask(app: App, deviceId: String, task: TaskItem, masterUrl: String) {
        val signer = HmacSigner(app.hmacSecretStore)

        val (ok, platformNoteId, platformUrl, errorCode, errorMessage) = when (task.action) {
            "device_publish" -> executePublish(app, task.payload)
            // TODO: collect / interact
            else -> {
                Logx.w("TaskPoller.unknown_action: ${task.action}")
                TaskResult(false, null, null, "UNKNOWN_ACTION", "unknown action ${task.action}")
            }
        }

        val completeBody = CompleteBody(
            ok = ok,
            platform_note_id = platformNoteId,
            platform_url = platformUrl,
            error_code = errorCode,
            error_message = errorMessage,
        )
        val bodyBytes = json.encodeToString(CompleteBody.serializer(), completeBody)
            .toByteArray(Charsets.UTF_8)
        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val requestId = UUID.randomUUID().toString()
        val signature = signer.sign(bodyBytes, timestamp, requestId)

        try {
            val resp = http.post(
                "$masterUrl/api/v1/devices/$deviceId/tasks/${task.id}/complete"
            ) {
                contentType(ContentType.Application.Json)
                headers {
                    append(HmacAuth.HEADER_TIMESTAMP, timestamp)
                    append(HmacAuth.HEADER_REQUEST_ID, requestId)
                    append(HmacAuth.HEADER_SIGNATURE, signature)
                }
                setBody(bodyBytes)
            }
            Logx.i("TaskPoller.complete status=${resp.status} task=${task.id}")
        } catch (t: Throwable) {
            Logx.e("TaskPoller.complete_failed task=${task.id}", t)
        }
    }

    private suspend fun executePublish(app: App, payload: JsonObject): TaskResult {
        val publisher = XhsPublisher(
            actions = app.executor,
            driver = app.driver,
            imagePipeline = app.imagePipeline,
            imagePicker = app.imagePicker,
            appContext = app,
        )
        val params = XhsPublisher.PublishParams(
            title = payload["title"]?.jsonPrimitive?.content ?: "",
            content = payload["content"]?.jsonPrimitive?.content ?: "",
            tags = payload["tags"]?.let { json.decodeFromJsonElement(ListSerializer(String.serializer()), it) } ?: emptyList(),
            visibility = payload["visibility"]?.jsonPrimitive?.content ?: "public",
            imagePaths = payload["images"]?.let { json.decodeFromJsonElement(ListSerializer(String.serializer()), it) } ?: emptyList(),
        )
        return when (val r = publisher.publish(params)) {
            is ApiResult.Ok -> {
                val outcome = r.value
                // noteId 取不到不算失败：发布动作本身已成功（个人页标题校验通过），
                // id 缺失只影响后续按 id 回采，不应把任务标成 DRAFT_FAILED。
                if (outcome.noteId.isNullOrBlank()) {
                    Logx.w("TaskPoller.publish: note_id not scraped; reporting success without id")
                }
                TaskResult(true, outcome.noteId, outcome.url, null, null)
            }
            is ApiResult.Err -> TaskResult(
                false, null, null, r.code.name, r.message
            )
        }
    }

    private fun startForegroundCompat() {
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(this, getString(R.string.notif_channel))
            .setContentTitle("Matrix Task Poller")
            .setContentText("正在轮询任务")
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
        private const val NOTIF_ID = 44
        // 刚拉到任务后：快速确认下一次任务（比如发布失败后立刻重试）。
        private const val POLL_INTERVAL_CLAIMED_MS = 5_000L
        // 前 4 次没任务：较快轮询，让首次发布延迟不要太高。
        private const val POLL_INTERVAL_IDLE_FAST_MS = 15_000L
        // 长期没任务：省电省流量。
        private const val POLL_INTERVAL_IDLE_SLOW_MS = 60_000L

        fun start(ctx: Context) {
            val i = Intent(ctx, TaskPollerService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, TaskPollerService::class.java))
        }
    }
}

@Serializable
private data class NextResponse(
    val ok: Boolean = false,
    val data: TaskItem? = null,
)

@Serializable
private data class TaskItem(
    val id: String,
    val action: String,
    val payload: JsonObject = JsonObject(emptyMap()),
    val request_id: String = "",
)

@Serializable
private data class CompleteBody(
    val ok: Boolean,
    val platform_note_id: String? = null,
    val platform_url: String? = null,
    val error_code: String? = null,
    val error_message: String? = null,
)

private data class TaskResult(
    val ok: Boolean,
    val platformNoteId: String?,
    val platformUrl: String?,
    val errorCode: String?,
    val errorMessage: String?,
)
