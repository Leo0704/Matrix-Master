package com.matrix.companion.crypto

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.matrix.companion.auth.SecretProvider
import com.matrix.companion.util.Logx
import java.security.SecureRandom
import java.util.Base64

/**
 * Persists the HMAC shared secret issued by the main controller during pairing.
 *
 * Storage chain:
 *   1) Secret bytes generated on the master, transmitted over Tailscale + HMAC-only onboarding,
 *   2) Persisted into EncryptedSharedPreferences via a Keystore-backed master key,
 *   3) Read at request time by [HmacVerifier].
 */
class HmacSecretStore(context: Context) : SecretProvider {

    private val masterKey: MasterKey = MasterKey.Builder(context.applicationContext)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context.applicationContext,
        PREFS_NAME,
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    override fun secret(): ByteArray {
        val b64 = prefs.getString(KEY_SECRET, null)
            ?: error("HMAC secret not provisioned; pair device first")
        return Base64.getDecoder().decode(b64)
    }

    fun save(secret: ByteArray) {
        require(secret.size in MIN_SECRET_SIZE..MAX_SECRET_SIZE) {
            "HMAC secret length out of range"
        }
        prefs.edit().putString(KEY_SECRET, Base64.getEncoder().encodeToString(secret)).apply()
        Logx.i("HMAC secret persisted (${secret.size} bytes)")
    }

    fun isProvisioned(): Boolean = prefs.contains(KEY_SECRET)

    fun clear() { prefs.edit().remove(KEY_SECRET).apply() }

    companion object {
        private const val PREFS_NAME = "matrix_companion_secrets"
        private const val KEY_SECRET = "hmac_secret"
        private const val MIN_SECRET_SIZE = 16
        private const val MAX_SECRET_SIZE = 64

        fun generateSecret(): ByteArray {
            val out = ByteArray(32)
            SecureRandom().nextBytes(out)
            return out
        }
    }
}
