package com.matrix.companion.net

import android.content.Context
import com.matrix.companion.auth.HmacAuth
import com.matrix.companion.auth.HmacSigner
import com.matrix.companion.util.Logx
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.get
import io.ktor.client.request.headers
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
import java.util.UUID

/**
 * 定期检测小红书登录状态并上报到后端。
 *
 * 流程：
 * 1. 启动时通过 `GET /accounts?device_id=xxx` 查询当前设备绑定的 account_id
 * 2. 每 [intervalMs] 执行一次 [LoginStateChecker.check]
 * 3. 把检测结果（success/failed/captcha）通过 HMAC 签名上报到
 *    `POST /api/v1/devices/{device_id}/login_state`
 * 4. 后端收到 success 后自动把账号状态改为 active
 */
class LoginStateReporter(
    private val context: Context,
    private val checker: com.matrix.companion.xhs.LoginStateChecker,
    private val masterUrl: String,
    private val secretProvider: com.matrix.companion.auth.SecretProvider,
    private val intervalMs: Long = 300_000L, // 5 分钟
) {

    @Serializable
    private data class AccountItem(val id: String)

    @Serializable
    private data class AccountListResponse(val items: List<AccountItem>)

    @Serializable
    private data class ReportBody(
        val account_id: String,
        val result: String,
        val risk_signal: String? = null,
        val error_message: String? = null,
    )

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val client = HttpClient(CIO) { install(ContentNegotiation) { json(json) } }
    private val signer = HmacSigner(secretProvider)
    private var job: Job? = null

    @Volatile
    private var cachedAccountId: String? = null

    fun start(scope: CoroutineScope, deviceId: () -> String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            // 先等 AccessibilityService 连接（服务启动时可能还没绑定）
            delay(15_000)
            while (isActive) {
                val did = deviceId()
                val accountId = resolveAccountId(did)
                if (accountId != null) {
                    runOnce(did, accountId)
                } else {
                    Logx.d("login_state_reporter: no account bound to device $did, skip")
                }
                delay(intervalMs)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    /**
     * 查询设备当前绑定的 account_id（带缓存）。
     */
    private suspend fun resolveAccountId(deviceId: String): String? {
        cachedAccountId?.let { return it }
        return try {
            val resp = client.get("$masterUrl/api/v1/accounts?device_id=$deviceId")
            if (resp.status.value in 200..299) {
                val body: AccountListResponse = resp.body()
                body.items.firstOrNull()?.id?.also { cachedAccountId = it }
            } else {
                Logx.w("login_state_reporter: query account failed status=${resp.status.value}")
                null
            }
        } catch (t: Throwable) {
            Logx.w("login_state_reporter: query account transport error: ${t.message}")
            null
        }
    }

    suspend fun runOnce(deviceId: String, accountId: String) {
        val result = checker.check()
        val (resultStr, riskSignal, errorMessage) = when (result) {
            is com.matrix.companion.xhs.LoginStateChecker.Result.Success ->
                Triple("success", null, null)
            is com.matrix.companion.xhs.LoginStateChecker.Result.Failed ->
                Triple("failed", result.reason, null)
            is com.matrix.companion.xhs.LoginStateChecker.Result.Captcha ->
                Triple("captcha", result.hint, null)
            is com.matrix.companion.xhs.LoginStateChecker.Result.Unknown ->
                Triple("failed", result.hint, "unknown page state")
        }

        val body = json.encodeToString(
            ReportBody.serializer(),
            ReportBody(accountId, resultStr, riskSignal, errorMessage)
        ).toByteArray(Charsets.UTF_8)

        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val requestId = UUID.randomUUID().toString()
        val signature = signer.sign(body, timestamp, requestId)

        try {
            val resp = client.post("$masterUrl/api/v1/devices/$deviceId/login_state") {
                contentType(ContentType.Application.Json)
                headers {
                    append(HmacAuth.HEADER_TIMESTAMP, timestamp)
                    append(HmacAuth.HEADER_REQUEST_ID, requestId)
                    append(HmacAuth.HEADER_SIGNATURE, signature)
                }
                setBody(body)
            }
            if (resp.status.value in 200..299) {
                Logx.i("login_state_reporter: reported $resultStr")
            } else {
                Logx.w("login_state_reporter: report failed status=${resp.status.value}")
            }
        } catch (t: Throwable) {
            Logx.w("login_state_reporter: transport error: ${t.message}")
        }
    }
}
