package com.matrix.companion.api

import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.xhs.XhsPublisher
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/**
 * POST /xhs/publish
 *
 * Wire contract (matches backend/matrix/device/adapters.py):
 *   success → 200, {"ok":true,"data":{"platform_note_id":<非空>,"url":<str>}}
 *   failure → 4xx/5xx, {"ok":false,"code":<ErrorCode.name>,"message":...,"retryable":...}
 *
 * The backend raises RuntimeError if `platform_note_id` is empty, so we
 * must only return 200 when [XhsPublisher] actually produced a real note ID.
 */
fun Route.xhsPublishRoute(publisher: XhsPublisher) {
    post("/xhs/publish") {
        val req: PublishBody = RouteBodies.decode(call, PublishBody.serializer())
        when (val r = publisher.publish(
            XhsPublisher.PublishParams(
                title = req.title,
                content = req.content,
                tags = req.tags,
                visibility = req.visibility,
                imagePaths = req.images,
            )
        )) {
            is ApiResult.Ok -> {
                val outcome = r.value
                if (outcome.noteId.isNullOrBlank()) {
                    // Backend treats empty platform_note_id as a hard failure.
                    // Surface it as DRAFT_FAILED instead of returning 200 with
                    // an empty id (the master would log "missing platform_note_id").
                    call.respond(
                        HttpStatusCode(500, "Err"),
                        errResp(
                            code = ErrorCode.DRAFT_FAILED.name,
                            message = "publish completed but noteId could not be parsed",
                            retryable = false,
                        ),
                    )
                } else {
                    val data = buildJsonObject {
                        put("platform_note_id", JsonPrimitive(outcome.noteId))
                        put("url", JsonPrimitive(outcome.url ?: ""))
                    }
                    call.respond(HttpStatusCode.OK, okResp(data))
                }
            }
            is ApiResult.Err -> {
                val httpCode = when (r.code) {
                    ErrorCode.DRAFT_FAILED -> 400
                    ErrorCode.UPLOAD_FAILED -> 403
                    ErrorCode.RISK_BLOCKED, ErrorCode.RATE_LIMITED -> 429
                    else -> 500
                }
                call.respond(
                    HttpStatusCode(httpCode, "Err"),
                    errResp(code = r.code.name, message = r.message, retryable = r.retryable),
                )
            }
        }
    }
}

@Serializable
data class PublishBody(
    val title: String,
    val content: String,
    val images: List<String>,
    val tags: List<String>,
    val visibility: String = "public",
    val request_id: String,
    // Backend also sends account_id; we accept it but do not use it
    // here — APK assumes the device is already paired with exactly one
    // XHS account (1 device : 1 account invariant).
    val account_id: String? = null,
)