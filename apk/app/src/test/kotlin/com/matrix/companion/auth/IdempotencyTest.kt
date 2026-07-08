package com.matrix.companion.auth

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class IdempotencyTest {

    private val fakeClock = object : com.matrix.companion.util.Clock {
        var ms = 0L
        override fun nowSeconds() = ms / 1000L
    }

    @Test
    fun `fresh request_id passes`() {
        val cache = IdempotencyCache(retentionMillis = 1_000L, clock = fakeClock)
        assertTrue(cache.claimIfFresh("a"))
        assertFalse(cache.claimIfFresh("a"))
        assertFalse(cache.claimIfFresh("a"))
    }

    @Test
    fun `distinct ids pass`() {
        val cache = IdempotencyCache()
        assertTrue(cache.claimIfFresh("a"))
        assertTrue(cache.claimIfFresh("b"))
    }
}
