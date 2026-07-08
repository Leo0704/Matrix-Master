package com.matrix.companion.api

import com.matrix.companion.App
import com.matrix.companion.status.StatusProvider
import io.ktor.server.application.call
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.get

/** GET /device/status */
fun Route.deviceStatusRoute(provider: StatusProvider) {
    get("/device/status") {
        call.respond(provider.snapshot())
    }
}
