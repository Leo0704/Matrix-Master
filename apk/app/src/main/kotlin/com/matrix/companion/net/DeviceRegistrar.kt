package com.matrix.companion.net

import android.content.Context
import com.matrix.companion.App
import com.matrix.companion.crypto.HmacSecretStore
import com.matrix.companion.util.Logx
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.UUID

/**
 * Coordinates the bootstrap handshake:
 *
 *   1. read-or-generate device_id, persist in shared prefs
 *   2. POST /api/v1/devices/{device_id}/pair { pair_code, identity... }
 *      → returns { key_id, hmac_key (base64) }
 *
 * P2-3 (added during real-device testing): the master route is
 * `/devices/{device_id}/pair` (URL-path device_id, not in body). The
 * body still carries the full identity block so the master can persist
 * model/android_version/apk_version/tailscale_ip on the device row.
 *
 * Pair-once: HMAC secret is wrapped in Keystore and persisted via
 * [HmacSecretStore.save]. Tailscale mesh ACLs are applied master-side.
 */
class DeviceRegistrar(private val context: Context) {

    sealed class RegistrationState {
        data object Idle : RegistrationState()
        data object Registering : RegistrationState()
        data object Paired : RegistrationState()
        data class Failed(val reason: String) : RegistrationState()
    }

    private val _state = MutableStateFlow<RegistrationState>(RegistrationState.Idle)
    val state: StateFlow<RegistrationState> = _state.asStateFlow()

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private val http = HttpClient(CIO) {
        install(ContentNegotiation) { json(json) }
    }

    @Serializable
    data class PairIdentity(
        val model: String = "",
        val android_version: String = "",
        val apk_version: String = "",
        val tailnet_ip: String = "",
    )

    @Serializable
    data class PairRequest(
        val pair_code: String,
        val identity: PairIdentity,
    )

    @Serializable
    data class PairResponse(
        // P2-1 real-device test fix: master returns flat
        // `{key_id, hmac_key, pair_code}` (DevicePairResponse schema), not
        // the old envelope `{ok, data: {hmac_secret}, error}`. Align here.
        val key_id: String = "",
        val hmac_key: String = "",
        val pair_code: String? = null,
    )

    /**
     * Master's StatusPages handler turns every 4xx/5xx into
     * `{ok:false, error:{code, message, retryable}}`. We deserialize that
     * on failure to surface the human-readable reason in the Toast.
     */
    @Serializable
    data class ErrorEnvelope(
        val ok: Boolean = false,
        val error: ErrorBody? = null,
    )

    @Serializable
    data class ErrorBody(
        val code: String = "",
        val message: String = "",
        val retryable: Boolean = false,
    )

    @Serializable
    data class ApiErrorBody(
        val message: String? = null,
    )

    /**
     * Returns Result.success on pair success, Result.failure otherwise.
     */
    suspend fun pair(pairCode: String): Result<Unit> {
        _state.value = RegistrationState.Registering
        return try {
            val deviceId = deviceId()
            val ip = TailscaleClient.refresh() ?: throw IllegalStateException("no tailnet IP")
            val model = android.os.Build.MODEL ?: "unknown"
            val os = android.os.Build.VERSION.RELEASE ?: "unknown"
            val apkVer = com.matrix.companion.BuildConfig.VERSION_NAME

            // Master has different schemas for 2xx (PairResponse: {key_id, hmac_key})
            // and 4xx (ErrorEnvelope: {ok, error:{code, message, retryable}}).
            // Dispatch on status code so we can surface the human-readable
            // reason from the error envelope.
            // device_id 仍走 URL path（后端现在会忽略 path 里的 device_id、
            // 改由配对码反查真实 device_id），body 只需 pair_code + identity。
            val httpResp = http.post("$MASTER_DEFAULT/api/v1/devices/$deviceId/pair") {
                contentType(ContentType.Application.Json)
                setBody(
                    PairRequest(
                        pairCode,
                        PairIdentity(
                            model = model,
                            android_version = os,
                            apk_version = apkVer,
                            tailnet_ip = ip,
                        ),
                    )
                )
            }
            val isOk = httpResp.status.value in 200..299
            if (isOk) {
                val resp: PairResponse = httpResp.body()
                val secretB64 = resp.hmac_key
                if (secretB64.isBlank()) {
                    throw IllegalStateException(
                        "pair response missing hmac_key (key_id='${resp.key_id}')"
                    )
                }
                val bytes = java.util.Base64.getDecoder().decode(secretB64)
                App.get(context).hmacSecretStore.save(bytes)
                _state.value = RegistrationState.Paired
                Logx.i("Paired; secret=${bytes.size}B stored in Keystore (key_id=${resp.key_id})")
                Result.success(Unit)
            } else {
                val errBody: ErrorEnvelope = try {
                    httpResp.body()
                } catch (_: Throwable) {
                    ErrorEnvelope(error = ErrorBody(message = httpResp.status.description))
                }
                val msg = errBody.error?.message
                    ?: httpResp.status.description
                    ?: "HTTP ${httpResp.status.value}"
                Logx.w("Pair rejected: status=${httpResp.status} msg=$msg")
                throw IllegalStateException(msg)
            }
        } catch (t: Throwable) {
            Logx.e("Pair failed", t)
            _state.value = RegistrationState.Failed(t.message ?: "unknown")
            Result.failure(t)
        }
    }

    fun deviceId(): String {
        val prefs = context.getSharedPreferences("matrix_companion_meta", Context.MODE_PRIVATE)
        prefs.getString(KEY_DEVICE_ID, null)?.let { return it }
        val fresh = UUID.randomUUID().toString()
        prefs.edit().putString(KEY_DEVICE_ID, fresh).apply()
        return fresh
    }

    companion object {
        private const val KEY_DEVICE_ID = "device_id"
        /**
         * Override via [net.DeviceRegistrar.MASTER_URL_OVERRIDE] at runtime
         * by editing BuildConfig field in app/build.gradle.kts.
         */
        const val MASTER_DEFAULT = "http://192.168.1.172:8666"
    }
}
