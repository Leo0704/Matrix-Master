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

/** POST /xhs/interact */
fun Route.xhsInteractRoute(interactor: XhsInteractor) {
    post("/xhs/interact") {
        val req: InteractBody = RouteBodies.decode(call, InteractBody.serializer())
        val target = XhsInteractor.Target(noteId = req.target.note_id, userId = req.target.user_id)
        val action = runCatching { XhsInteractor.Action.valueOf(req.action.uppercase()) }
            .getOrNull()
            ?: return@post call.respond(
                HttpStatusCode.BadRequest,
                ErrResp(ok = false, ErrorCode.INVALID_PARAMS.name, "unknown action ${req.action}", retryable = false)
            )
        when (val r = interactor.run(action, target, req.content)) {
            is ApiResult.Ok -> call.respond(HttpStatusCode.OK, OkResp(true))
            is ApiResult.Err -> {
                val code = if (r.code == ErrorCode.RISK_BLOCKED || r.code == ErrorCode.RATE_LIMITED) 429 else 500
                call.respond(HttpStatusCode(code, "Err"), ErrResp(ok = false, r.code.name, r.message, r.retryable))
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
)

@Serializable
data class InteractTarget(
    val note_id: String? = null,
    val user_id: String? = null,
)
