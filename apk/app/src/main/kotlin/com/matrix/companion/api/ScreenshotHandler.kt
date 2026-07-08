package com.matrix.companion.api

import com.matrix.companion.accessibility.AccessibilityDriver
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.call
import io.ktor.server.response.respondBytes
import io.ktor.server.response.respondText
import io.ktor.server.routing.Route
import io.ktor.server.routing.get

/**
 * GET /screen/screenshot — returns image/png binary.
 * Optional `?region=x,y,w,h` query for cropping; ignored in MVP.
 */
fun Route.screenshotRoute(driver: AccessibilityDriver) {
    get("/screen/screenshot") {
        when (val r = driver.screenshot()) {
            is com.matrix.companion.util.ApiResult.Ok -> call.respondBytes(r.value, ContentType.Image.PNG)
            is com.matrix.companion.util.ApiResult.Err -> {
                val code = if (r.code == com.matrix.companion.util.ErrorCode.DEVICE_OFFLINE) 502 else 500
                call.respondText(
                    """{"ok":false,"code":"${r.code.name}","message":"${r.message.replace("\"", "'")}","retryable":${r.retryable}}""",
                    ContentType.Application.Json,
                    HttpStatusCode(code, "Err"),
                )
            }
        }
    }
}
