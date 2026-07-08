package com.matrix.companion.api

import kotlinx.serialization.Serializable

@Serializable
internal data class OkResp(val ok: Boolean, val data: kotlinx.serialization.json.JsonElement? = null) {
    companion object { val falseFlag: Boolean = false }
}

@Serializable
internal data class ErrResp(
    val ok: Boolean = false,
    val code: String,
    val message: String,
    val retryable: Boolean,
)
