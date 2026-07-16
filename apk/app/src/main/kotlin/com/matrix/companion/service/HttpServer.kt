package com.matrix.companion.service

import com.matrix.companion.App
import com.matrix.companion.api.appOpenRoute
import com.matrix.companion.api.deviceStatusRoute
import com.matrix.companion.api.errResp
import com.matrix.companion.api.inputRoute
import com.matrix.companion.api.okResp
import com.matrix.companion.api.screenshotRoute
import com.matrix.companion.api.swipeRoute
import com.matrix.companion.api.tapRoute
import com.matrix.companion.api.xhsCollectMetricsRoute
import com.matrix.companion.api.xhsInteractRoute
import com.matrix.companion.api.xhsPublishRoute
import com.matrix.companion.auth.HmacAuth
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.xhs.XhsInteractor
import com.matrix.companion.xhs.XhsMetricsCollector
import com.matrix.companion.xhs.XhsPublisher
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.ApplicationCallPipeline
import io.ktor.server.application.call
import io.ktor.server.application.install
import io.ktor.server.cio.CIO
import io.ktor.server.engine.ApplicationEngine
import io.ktor.server.engine.embeddedServer
import io.ktor.server.plugins.callloging.CallLogging
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.plugins.statuspages.StatusPages
import io.ktor.server.request.path
import io.ktor.server.request.receive
import io.ktor.server.response.respond
import io.ktor.server.routing.Routing
import io.ktor.server.routing.get
import io.ktor.server.routing.routing
import io.ktor.util.AttributeKey
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/**
 * Ktor CIO server bound to 0.0.0.0:8765.
 *
 *   1. `/health` is unauthenticated.
 *   2. Every other route goes through HMAC + idempotency middleware, which
 *      reads the body once via `call.receive<ByteArray>()`, verifies the
 *      signature, and caches the bytes via [BODY_ATTR]. Handlers should
 *      use the helpers in [com.matrix.companion.api.RouteBodies] rather
 *      than `call.receive<T>()` because Ktor's request channel is consumed
 *      after the first read.
 */
class HttpServer(private val app: App) {

    @Volatile private var engine: ApplicationEngine? = null

    fun start() {
        if (engine != null) return
        runBlocking {
            engine = embeddedServer(CIO, port = 8765, host = "0.0.0.0") {
                install(ContentNegotiation) {
                    json(Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false })
                }
                install(CallLogging)
                install(StatusPages) {
                    exception<Throwable> { call, cause ->
                        call.respond(
                            HttpStatusCode.InternalServerError,
                            errResp(
                                code = ErrorCode.INTERNAL_ERROR.name,
                                message = cause.message ?: cause::class.simpleName ?: "internal",
                                retryable = true,
                            ),
                        )
                    }
                }
                routing {
                    get("/health") {
                        val data = buildJsonObject {
                            put("app", JsonPrimitive("matrix-companion"))
                            put("version", JsonPrimitive("0.1.0"))
                            put("accessibility", JsonPrimitive(app.driver.isReady()))
                        }
                        call.respond(okResp(data))
                    }
                    authenticatedBlock()
                }
            }.also { it.start(wait = false) }
        }
    }

    fun stop() {
        engine?.stop(500, 1500)
        engine = null
    }

    private fun Routing.authenticatedBlock() {
        intercept(ApplicationCallPipeline.Plugins) {
            val path = call.request.path()
            if (path == "/health") return@intercept

            // Buffer body once.
            val body: ByteArray = try { call.receive() } catch (_: Throwable) { ByteArray(0) }

            val ts = call.request.headers[HmacAuth.HEADER_TIMESTAMP]
            val sig = call.request.headers[HmacAuth.HEADER_SIGNATURE]
            val rid = call.request.headers[HmacAuth.HEADER_REQUEST_ID]
            if (ts == null || sig == null || rid == null) {
                call.respond(
                    HttpStatusCode.Unauthorized,
                    errResp(
                        code = ErrorCode.UNAUTHORIZED.name,
                        message = "missing hmac headers",
                        retryable = false,
                    ),
                )
                return@intercept
            }

            val verifyResult = app.hmacVerifier.verifyAsApiResult(ts, sig, rid, body)
            if (verifyResult is com.matrix.companion.util.ApiResult.Err) {
                call.respond(
                    HttpStatusCode.Unauthorized,
                    errResp(
                        code = verifyResult.code.name,
                        message = verifyResult.message,
                        retryable = verifyResult.retryable,
                    ),
                )
                return@intercept
            }

            val method = call.request.local.method.value.uppercase()
            if (method in WRITE_METHODS && !app.idempotency.claimIfFresh(rid)) {
                call.respond(
                    HttpStatusCode.Conflict,
                    errResp(
                        code = ErrorCode.REPLAY_DETECTED.name,
                        message = "request_id already seen",
                        retryable = false,
                    ),
                )
                return@intercept
            }

            call.attributes.put(BODY_ATTR, body)
        }

        deviceStatusRoute(app.statusProvider)
        appOpenRoute(app.executor)
        tapRoute(app.driver)
        swipeRoute(app.driver)
        inputRoute(app.driver)
        screenshotRoute(app.driver)
        xhsPublishRoute(
            XhsPublisher(
                actions = app.executor,
                driver = app.driver,
                imagePipeline = app.imagePipeline,
                imagePicker = app.imagePicker,
            ),
        )
        xhsInteractRoute(XhsInteractor(app.executor, app.noteOpener))
        xhsCollectMetricsRoute(
            XhsMetricsCollector(
                driver = app.driver,
                noteOpener = app.noteOpener,
                followsBaseline = app.followsBaseline,
                actions = app.executor,
            ),
        )
    }

    companion object {
        val BODY_ATTR: AttributeKey<ByteArray> = AttributeKey("matrix_raw_body")
        private val WRITE_METHODS = setOf("POST", "PUT", "PATCH", "DELETE")
    }
}