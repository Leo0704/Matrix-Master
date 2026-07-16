package com.matrix.companion.api

import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.xhs.XhsMetricsCollector
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.serializer

/**
 * POST /xhs/collect_metrics
 *
 * Wire contract (matches backend/matrix/agent/nodes/collect.py):
 *   success → 200, {"ok":true,"data":[<NoteMetric>, ...]}
 *     - `data` MUST be a non-empty list; backend falls back to rows[0]
 *       if no row matches `platform_note_id`, so the order matters.
 *     - Missing int fields are dropped by backend (treated as 0 at
 *       insert), so prefer sending null/unset over 0 to avoid
 *       misleading "no engagement" stats for uncollectable fields.
 *
 * The collector now receives the `platform_note_id` so it can navigate
 * to that specific note rather than scraping the current page (which
 * was the old broken behaviour).
 */
fun Route.xhsCollectMetricsRoute(
    collector: XhsMetricsCollector,
    json: Json = Json { ignoreUnknownKeys = true; explicitNulls = false },
) {
    post("/xhs/collect_metrics") {
        val req: CollectReq = RouteBodies.decode(call, serializer<CollectReq>())
        if (req.platform_note_id.isBlank()) {
            call.respond(
                HttpStatusCode.BadRequest,
                errResp(
                    code = ErrorCode.INVALID_PARAMS.name,
                    message = "platform_note_id is required",
                    retryable = false,
                ),
            )
            return@post
        }
        when (val r = collector.collect(req.platform_note_id, req.scope)) {
            is ApiResult.Ok -> {
                val data: JsonElement = json.encodeToJsonElement(
                    ListSerializer(serializer<XhsMetricsCollector.NoteMetric>()),
                    r.value,
                )
                call.respond(HttpStatusCode.OK, okResp(data))
            }
            is ApiResult.Err -> {
                val httpCode = when (r.code) {
                    ErrorCode.SELECTOR_NOT_FOUND, ErrorCode.PARSE_FAILED -> 404
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
data class CollectReq(
    val platform_note_id: String,
    val scope: String,
    val request_id: String,
    val account_id: String? = null,
)