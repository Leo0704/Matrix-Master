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
 *   2. POST /api/v1/devices/pair { device_id, pair_code, tailscale_ip, model, os_version }
 *      → returns { hmac_secret (base64) }
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
    data class PairRequest(
        val device_id: String,
        val pair_code: String,
        val tailscale_ip: String,
        val model: String,
        val os_version: String,
    )

    @Serializable
    data class PairResponse(
        val ok: Boolean,
        val data: PairData? = null,
        val error: ApiErrorBody? = null,
    )

    @Serializable
    data class PairData(
        val hmac_secret: String,
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

            val resp: PairResponse = http.post("$MASTER_DEFAULT/api/v1/devices/pair") {
                contentType(ContentType.Application.Json)
                setBody(PairRequest(deviceId, pairCode, ip, model, os))
            }.body()

            val secret = resp.data?.hmac_secret
                ?: throw IllegalStateException(resp.error?.message ?: "pair response missing secret")
            val bytes = java.util.Base64.getDecoder().decode(secret)
            App.get(context).hmacSecretStore.save(bytes)
            _state.value = RegistrationState.Paired
            Logx.i("Paired; secret=${bytes.size}B stored in Keystore")
            Result.success(Unit)
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
        const val MASTER_DEFAULT = "http://127.0.0.1:8666"
    }
}
