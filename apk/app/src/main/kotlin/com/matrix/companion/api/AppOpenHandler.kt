package com.matrix.companion.api

import com.matrix.companion.accessibility.ActionExecutor
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer

/** POST /app/open { package, request_id } */
fun Route.appOpenRoute(executor: ActionExecutor) {
    post("/app/open") {
        val req: OpenBody = RouteBodies.decode(call, OpenBody.serializer())
        when (val r = executor.openApp(req.`package`, req.request_id)) {
            is com.matrix.companion.util.ApiResult.Ok -> call.respond(HttpStatusCode.OK, OkResp(true))
            is com.matrix.companion.util.ApiResult.Err -> {
                val httpCode = if (r.code == com.matrix.companion.util.ErrorCode.APP_NOT_FOUND) HttpStatusCode(404, "App Not Found") else HttpStatusCode(500, "Internal Error")
                call.respond(httpCode, ErrResp(ok = false, r.code.name, r.message, r.retryable))
            }
        }
    }
}

@Serializable
data class OpenBody(val `package`: String, val request_id: String)
