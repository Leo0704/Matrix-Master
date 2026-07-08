package com.matrix.companion.api

import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.Selector
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.post
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.JsonClassDiscriminator

/** POST /action/tap — supports TapByCoord and TapBySelector via polymorphic body. */
fun Route.tapRoute(driver: AccessibilityDriver) {
    post("/action/tap") {
        val req: TapReq = RouteBodies.decode(call, TapReq.serializer())
        val result: ApiResult<Unit> = when (req) {
            is TapReq.ByCoord -> driver.tap(req.coord.x, req.coord.y)
            is TapReq.BySelector -> {
                val sel = when (req.selector.type) {
                    SelectorType.RESOURCE_ID -> Selector.ResourceId(req.selector.value)
                    SelectorType.CONTENT_DESC -> Selector.ContentDesc(req.selector.value)
                    SelectorType.TEXT -> Selector.Text(req.selector.value)
                    SelectorType.XPATH -> Selector.XPath(req.selector.value)
                }
                driver.tapBySelector(sel)
            }
        }
        when (result) {
            is ApiResult.Ok -> call.respond(HttpStatusCode.OK, OkResp(true))
            is ApiResult.Err -> {
                val httpCode = if (result.code == ErrorCode.SELECTOR_NOT_FOUND) HttpStatusCode(404, "Selector Not Found") else HttpStatusCode(500, "Internal Error")
                call.respond(httpCode, ErrResp(ok = false, result.code.name, result.message, result.retryable))
            }
        }
    }
}

@JsonClassDiscriminator("kind")
@Serializable
sealed class TapReq {
    @Serializable
    @SerialName("coord")
    data class ByCoord(val coord: Coord, val request_id: String) : TapReq()

    @Serializable
    @SerialName("selector")
    data class BySelector(
        val selector: Sel,
        @SerialName("fallback_vlm") val fallbackVlm: Boolean = false,
        val request_id: String,
    ) : TapReq()
}

@Serializable
data class Coord(val x: Int, val y: Int)

@Serializable
data class Sel(val type: SelectorType, val value: String)

@Serializable
enum class SelectorType {
    @SerialName("resource_id") RESOURCE_ID,
    @SerialName("content_desc") CONTENT_DESC,
    @SerialName("text") TEXT,
    @SerialName("xpath") XPATH,
}
