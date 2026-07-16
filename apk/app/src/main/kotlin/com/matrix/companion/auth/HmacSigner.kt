package com.matrix.companion.auth

/**
 * Sign outbound requests so the master controller's HMAC verifier accepts
 * them. This is the "write side" of [HmacVerifier].
 *
 * Wire format MUST match [HmacAuth.canonicalMessage] — both sides
 * compute `"{timestamp}\n{request_id}\n{body_sha256_hex}"` and HMAC-SHA256
 * it with the shared secret. Any drift breaks the round-trip.
 *
 * The master uses [hmac.compare_digest] / `constantTimeEquals`, so
 * generating the exact same base64 output is what matters, not the
 * canonical string's encoding details.
 */
class HmacSigner(
    private val secretProvider: SecretProvider,
) {

    /**
     * Sign the request body bytes. Returns the base64-encoded HMAC-SHA256
     * signature to put in the X-Signature header.
     *
     * @throws IllegalStateException if the secret store is not provisioned.
     */
    fun sign(
        body: ByteArray,
        timestamp: String,
        requestId: String,
    ): String {
        val bodyHash = HmacVerifier.sha256Hex(body)
        val canonical = HmacAuth.canonicalMessage(timestamp, requestId, bodyHash)
        return HmacVerifier.hmacSha256Base64(secretProvider.secret(), canonical)
    }

    /** Convenience overload for UTF-8 string bodies. */
    fun sign(
        body: String,
        timestamp: String,
        requestId: String,
    ): String = sign(body.toByteArray(Charsets.UTF_8), timestamp, requestId)
}