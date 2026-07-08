package com.matrix.companion.api

import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.util.ApiResult
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer

/** POST /action/input { text, request_id } */
fun Route.inputRoute(driver: AccessibilityDriver) {
    post("/action/input") {
        val req: InputReq = RouteBodies.decode(call, InputReq.serializer())
        when (val r = driver.inputText(req.text)) {
            is ApiResult.Ok -> call.respond(HttpStatusCode.OK, OkResp(true))
            is ApiResult.Err -> call.respond(
                HttpStatusCode.InternalServerError,
                ErrResp(ok = false, r.code.name, r.message, r.retryable),
            )
        }
    }
}

@Serializable
data class InputReq(val text: String, val request_id: String)
