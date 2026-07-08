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

/** POST /action/swipe */
fun Route.swipeRoute(driver: AccessibilityDriver) {
    post("/action/swipe") {
        val req: SwipeReq = RouteBodies.decode(call, SwipeReq.serializer())
        when (val r = driver.swipe(req.from.x, req.from.y, req.to.x, req.to.y, req.duration_ms)) {
            is ApiResult.Ok -> call.respond(HttpStatusCode.OK, OkResp(true))
            is ApiResult.Err -> call.respond(
                HttpStatusCode.InternalServerError,
                ErrResp(ok = false, r.code.name, r.message, r.retryable),
            )
        }
    }
}

@Serializable
data class SwipeReq(
    val from: Coord,
    val to: Coord,
    val duration_ms: Long = 300L,
    val request_id: String,
)
