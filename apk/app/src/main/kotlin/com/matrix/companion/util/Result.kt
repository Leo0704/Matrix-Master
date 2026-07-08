package com.matrix.companion.util

/**
 * Backend-agnostic Result. Using Kotlin's stdlib Result is fine but we want
 * a stable wire-level error code that maps 1:1 to the OpenAPI ErrorResponse.
 */
sealed class ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>()
    data class Err(val code: ErrorCode, val message: String, val retryable: Boolean) : ApiResult<Nothing>()
}

enum class ErrorCode {
    DEVICE_OFFLINE,
    APP_NOT_FOUND,
    SELECTOR_NOT_FOUND,
    TIMEOUT,
    IME_ERROR,
    DRAFT_FAILED,
    UPLOAD_FAILED,
    RISK_BLOCKED,
    RATE_LIMITED,
    PARSE_FAILED,
    INVALID_PARAMS,
    INTERNAL_ERROR,
    UNAUTHORIZED,
    REPLAY_DETECTED,
}

inline fun <T, R> ApiResult<T>.map(transform: (T) -> R): ApiResult<R> = when (this) {
    is ApiResult.Ok -> ApiResult.Ok(transform(value))
    is ApiResult.Err -> this
}
