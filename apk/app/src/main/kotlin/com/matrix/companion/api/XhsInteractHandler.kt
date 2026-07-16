package com.matrix.companion.api

import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.xhs.XhsInteractor
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer

/**
 * POST /xhs/interact
 *
 * Wire contract:
 *   success → 200, {"ok":true,"data":null}
 *   unknown action → 400, INVALID_PARAMS
 *   rate/risk    → 429
 *   other errors → 500
 *
 * `target.note_id` is now actually used: the runner opens that note via
 * deep link before performing like/comment/etc. (see XhsInteractor).
 */
fun Route.xhsInteractRoute(interactor: XhsInteractor) {
    post("/xhs/interact") {
        val req: InteractBody = RouteBodies.decode(call, InteractBody.serializer())
        val action = runCatching { XhsInteractor.Action.valueOf(req.action.uppercase()) }
            .getOrNull()
            ?: return@post call.respond(
                HttpStatusCode.BadRequest,
                errResp(
                    code = ErrorCode.INVALID_PARAMS.name,
                    message = "unknown action ${req.action}",
                    retryable = false,
                ),
            )
        val target = XhsInteractor.Target(noteId = req.target.note_id, userId = req.target.user_id)
        when (val r = interactor.run(action, target, req.content)) {
            is ApiResult.Ok -> call.respond(HttpStatusCode.OK, okResp())
            is ApiResult.Err -> {
                val httpCode = when (r.code) {
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
data class InteractBody(
    val action: String,
    val target: InteractTarget,
    val content: String? = null,
    val request_id: String,
    // Backend also sends account_id; ignored here for the same reason as
    // PublishBody (1 device : 1 account invariant).
    val account_id: String? = null,
)

@Serializable
data class InteractTarget(
    val note_id: String? = null,
    val user_id: String? = null,
)