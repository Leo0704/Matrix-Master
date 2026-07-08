package com.matrix.companion.auth

/**
 * Headers and constants for HMAC-SHA256 request signing.
 * Spec lives in docs/api/apk-http.openapi.yaml § securitySchemes.
 */
object HmacAuth {
    const val HEADER_SIGNATURE = "X-Signature"
    const val HEADER_TIMESTAMP = "X-Timestamp"
    const val HEADER_REQUEST_ID = "X-Request-Id"

    /** Sign-content format: "{timestamp}\n{request_id}\n{body_sha256}". */
    fun canonicalMessage(timestamp: String, requestId: String, bodySha256Hex: String): String =
        "$timestamp\n$requestId\n$bodySha256Hex"

    /** Max clock-skew window we accept. */
    const val TIMESTAMP_TOLERANCE_SECONDS: Long = 300

    /** Idempotency cache retention for replay protection. */
    const val REPLAY_RETENTION_DAYS: Int = 7
}
