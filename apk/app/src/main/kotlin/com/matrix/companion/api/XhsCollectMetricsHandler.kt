package com.matrix.companion.api

import com.matrix.companion.util.ApiResult
import com.matrix.companion.xhs.XhsMetricsCollector
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer

/** POST /xhs/collect_metrics */
fun Route.xhsCollectMetricsRoute(collector: XhsMetricsCollector) {
    post("/xhs/collect_metrics") {
        val req: CollectReq = RouteBodies.decode(call, CollectReq.serializer())
        when (val r = collector.collect(req.scope)) {
            is ApiResult.Ok -> call.respond(HttpStatusCode.OK, mapOf("ok" to true, "data" to r.value))
            is ApiResult.Err -> call.respond(
                HttpStatusCode.InternalServerError,
                ErrResp(ok = false, r.code.name, r.message, r.retryable),
            )
        }
    }
}

@Serializable
data class CollectReq(val scope: String, val request_id: String)
