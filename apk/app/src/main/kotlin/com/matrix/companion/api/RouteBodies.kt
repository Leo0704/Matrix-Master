package com.matrix.companion.api

import com.matrix.companion.service.HttpServer
import io.ktor.server.application.ApplicationCall
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json

/**
 * Helpers for handlers that need the request body. Because the auth
 * middleware reads it first (for HMAC verification), each handler must
 * pull from [HttpServer.BODY_ATTR] instead of calling `call.receive<T>()`.
 */
object RouteBodies {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }

    fun <T> decode(call: ApplicationCall, serializer: KSerializer<T>): T {
        val bytes = call.attributes[HttpServer.BODY_ATTR]
        return json.decodeFromString(serializer, String(bytes, Charsets.UTF_8))
    }
}
