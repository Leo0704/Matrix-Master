package com.matrix.companion.auth

import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.Clock
import com.matrix.companion.util.ErrorCode
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Validates request signature against the stored shared secret.
 * Pure-Kotlin: easily unit-testable, no Android dependencies.
 */
class HmacVerifier(
    private val secretProvider: SecretProvider,
    private val clock: Clock = Clock.Real,
    private val toleranceSeconds: Long = HmacAuth.TIMESTAMP_TOLERANCE_SECONDS,
) {
    sealed class Outcome {
        object Ok : Outcome()
        data class Fail(val reason: Reason) : Outcome()
        enum class Reason { MISSING_HEADERS, BAD_SIGNATURE, EXPIRED_TIMESTAMP }
    }

    fun verify(
        timestampHeader: String?,
        signatureHeader: String?,
        requestIdHeader: String?,
        body: ByteArray,
    ): Outcome {
        if (timestampHeader.isNullOrBlank() ||
            signatureHeader.isNullOrBlank() ||
            requestIdHeader.isNullOrBlank()
        ) return Outcome.Fail(Outcome.Reason.MISSING_HEADERS)

        val ts = timestampHeader.toLongOrNull()
            ?: return Outcome.Fail(Outcome.Reason.MISSING_HEADERS)
        val now = clock.nowSeconds()
        if (kotlin.math.abs(now - ts) > toleranceSeconds) {
            return Outcome.Fail(Outcome.Reason.EXPIRED_TIMESTAMP)
        }

        val bodyHash = sha256Hex(body)
        val canonical = HmacAuth.canonicalMessage(timestampHeader, requestIdHeader, bodyHash)
        val expected = hmacSha256Base64(secretProvider.secret(), canonical)
        if (!constantTimeEquals(expected, signatureHeader)) {
            return Outcome.Fail(Outcome.Reason.BAD_SIGNATURE)
        }
        return Outcome.Ok
    }

    /** Convenience: convert a fail into the wire-level ApiResult shape. */
    fun verifyAsApiResult(
        timestampHeader: String?,
        signatureHeader: String?,
        requestIdHeader: String?,
        body: ByteArray,
    ): ApiResult<Unit> = when (val v = verify(timestampHeader, signatureHeader, requestIdHeader, body)) {
        is Outcome.Ok -> ApiResult.Ok(Unit)
        is Outcome.Fail -> ApiResult.Err(
            code = ErrorCode.UNAUTHORIZED,
            message = "hmac: ${v.reason.name.lowercase()}",
            retryable = v.reason == Outcome.Reason.EXPIRED_TIMESTAMP,
        )
    }

    companion object {
        fun sha256Hex(bytes: ByteArray): String {
            val md = MessageDigest.getInstance("SHA-256")
            return md.digest(bytes).joinToString("") { "%02x".format(it) }
        }

        fun hmacSha256Base64(secret: ByteArray, message: String): String {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(secret, "HmacSHA256"))
            return java.util.Base64.getEncoder().encodeToString(mac.doFinal(message.toByteArray()))
        }

        private fun constantTimeEquals(a: String, b: String): Boolean {
            if (a.length != b.length) return false
            var result = 0
            for (i in a.indices) result = result or (a[i].code xor b[i].code)
            return result == 0
        }
    }
}

/** Pulled from keystore-backed secret store. */
fun interface SecretProvider {
    fun secret(): ByteArray
}
