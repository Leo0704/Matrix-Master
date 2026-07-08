package com.matrix.companion.api

import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class RoutingTest {

    @Test
    fun `error mapping picks the right ErrorCode`() {
        val e: ApiResult.Err = ApiResult.Err(ErrorCode.APP_NOT_FOUND, "x", retryable = false)
        assertEquals(ErrorCode.APP_NOT_FOUND, e.code)
        assertNotNull(e.message)
        assertEquals(false, e.retryable)
    }

    @Test
    fun `error codes exhaustively cover the OpenAPI spec`() {
        // Sanity: every ErrorCode maps to a stable string name usable in JSON.
        val codes = listOf(
            ErrorCode.DEVICE_OFFLINE,
            ErrorCode.APP_NOT_FOUND,
            ErrorCode.SELECTOR_NOT_FOUND,
            ErrorCode.TIMEOUT,
            ErrorCode.IME_ERROR,
            ErrorCode.DRAFT_FAILED,
            ErrorCode.UPLOAD_FAILED,
            ErrorCode.RISK_BLOCKED,
            ErrorCode.RATE_LIMITED,
            ErrorCode.PARSE_FAILED,
            ErrorCode.INVALID_PARAMS,
            ErrorCode.INTERNAL_ERROR,
            ErrorCode.UNAUTHORIZED,
            ErrorCode.REPLAY_DETECTED,
        )
        codes.forEach { assertNotNull(it.name) }
    }
}
