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

/** POST /xhs/publish */
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
            is ApiResult.Ok -> call.respond(
                HttpStatusCode.OK,
                mapOf(
                    "ok" to true,
                    "data" to mapOf(
                        "platform_note_id" to (r.value.noteId ?: ""),
                        "url" to (r.value.url ?: ""),
                    ),
                ),
            )
            is ApiResult.Err -> {
                val code = when (r.code) {
                    ErrorCode.DRAFT_FAILED -> 400
                    ErrorCode.UPLOAD_FAILED -> 403
                    ErrorCode.RISK_BLOCKED, ErrorCode.RATE_LIMITED -> 429
                    else -> 500
                }
                call.respond(HttpStatusCode(code, "Err"), ErrResp(ok = false, r.code.name, r.message, r.retryable))
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
)
