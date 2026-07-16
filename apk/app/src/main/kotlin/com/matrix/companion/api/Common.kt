package com.matrix.companion.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Unified wire envelope. Every endpoint returns one of:
 * - `{"ok": true, "data": <payload-or-null>}` on success
 * - `{"ok": false, "code": "<ErrorCode.name>", "message": "...", "retryable": <bool>}`
 *   on failure
 *
 * `data` is typed as [JsonElement] so handlers can embed any serializable
 * payload (string, list, typed DTO) without falling back to `mapOf<String, Any>`
 * — which is fragile under kotlinx-serialization because it has no
 * built-in serializer for `Any`.
 *
 * Builders use [okResp] / [errResp] to keep call-sites tidy.
 */
@Serializable
internal data class OkResp(
    val ok: Boolean,
    val data: JsonElement? = null,
)

@Serializable
internal data class ErrResp(
    val ok: Boolean = false,
    val code: String,
    val message: String,
    val retryable: Boolean,
)

internal fun okResp(data: JsonElement? = null): OkResp = OkResp(ok = true, data = data)

internal fun errResp(code: String, message: String, retryable: Boolean): ErrResp =
    ErrResp(ok = false, code = code, message = message, retryable = retryable)