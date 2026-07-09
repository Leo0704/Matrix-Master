package com.matrix.companion.crypto

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import java.security.KeyStore

/**
 * Manages a per-install AES-256 key inside the Android Keystore (TEE-backed
 * where available). This key never leaves the secure element; we use it to
 * wrap the HMAC shared secret before persisting to EncryptedSharedPreferences.
 */
class KeystoreManager(
    private val keyAlias: String = DEFAULT_ALIAS,
    private val keyStoreProvider: String = ANDROID_KEYSTORE,
) {
    private val keystore: KeyStore = KeyStore.getInstance(keyStoreProvider).apply { load(null) }

    fun ensureKey(): SecretKey {
        keystore.getKey(keyAlias, null)?.let { return it as SecretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, keyStoreProvider)
        val spec = KeyGenParameterSpec.Builder(
            keyAlias,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setRandomizedEncryptionRequired(true)
            .build()
        generator.init(spec)
        return generator.generateKey()
    }

    companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val DEFAULT_ALIAS = "matrix_companion_master_key"
    }
}
