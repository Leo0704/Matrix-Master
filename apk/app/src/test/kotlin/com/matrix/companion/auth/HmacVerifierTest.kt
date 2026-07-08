package com.matrix.companion.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class HmacVerifierTest {

    private val secret = "0123456789abcdef0123456789abcdef".toByteArray()
    private val provider = SecretProvider { secret }
    private val fixedClock = object : com.matrix.companion.util.Clock {
        override fun nowSeconds(): Long = 1_000_000L
    }
    private val verifier = HmacVerifier(provider, fixedClock)

    @Test
    fun `valid signature is accepted`() {
        val ts = "1000000"
        val rid = "11111111-1111-1111-1111-111111111111"
        val body = """{"hello":"world"}""".toByteArray()
        val bodyHash = HmacVerifier.sha256Hex(body)
        val canonical = HmacAuth.canonicalMessage(ts, rid, bodyHash)
        val sig = HmacVerifier.hmacSha256Base64(secret, canonical)

        val out = verifier.verify(ts, sig, rid, body)
        assertEquals(HmacVerifier.Outcome.Ok, out)
    }

    @Test
    fun `wrong signature is rejected`() {
        val ts = "1000000"
        val rid = "11111111-1111-1111-1111-111111111111"
        val body = "{}".toByteArray()
        val out = verifier.verify(ts, "AAAA", rid, body)
        assertTrue(out is HmacVerifier.Outcome.Fail)
        assertEquals(HmacVerifier.Outcome.Reason.BAD_SIGNATURE, (out as HmacVerifier.Outcome.Fail).reason)
    }

    @Test
    fun `expired timestamp is rejected`() {
        val ts = "999900" // 100s in the past > 5min tolerance, but with tolerance = 300, this is 100 < 300, ok.
        val ts2 = "999000" // 1000s in the past > tolerance
        val rid = "11111111-1111-1111-1111-111111111111"
        val body = "{}".toByteArray()
        val sig = HmacVerifier.hmacSha256Base64(secret, HmacAuth.canonicalMessage(ts2, rid, HmacVerifier.sha256Hex(body)))
        val out = verifier.verify(ts2, sig, rid, body)
        assertTrue(out is HmacVerifier.Outcome.Fail)
        assertEquals(HmacVerifier.Outcome.Reason.EXPIRED_TIMESTAMP, (out as HmacVerifier.Outcome.Fail).reason)
        // silence unused warning
        assertFalse(ts == ts2)
    }

    @Test
    fun `missing headers are rejected`() {
        val out = verifier.verify(null, null, null, ByteArray(0))
        assertTrue(out is HmacVerifier.Outcome.Fail)
        assertEquals(HmacVerifier.Outcome.Reason.MISSING_HEADERS, (out as HmacVerifier.Outcome.Fail).reason)
    }

    @Test
    fun `asymmetric header set is rejected`() {
        val out = verifier.verify("1000000", "xx", null, ByteArray(0))
        assertTrue(out is HmacVerifier.Outcome.Fail)
    }
}
