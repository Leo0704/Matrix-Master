package com.matrix.companion.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Round-trip test: what the [HmacSigner] produces must be accepted by
 * [HmacVerifier]. If these diverge the master heartbeat endpoint
 * 401s every time (which is exactly the bug Phase 5 fixes).
 */
class HmacSignerTest {

    private val secret = "0123456789abcdef0123456789abcdef".toByteArray() // 32 bytes

    private val provider = SecretProvider { secret }

    @Test
    fun `signed body verifies against the same canonical message`() {
        val verifier = HmacVerifier(provider)
        val signer = HmacSigner(provider)

        val body = """{"device_id":"abc","tailscale_ip":"100.1.2.3","online":true,"app":null,"battery":80}""".toByteArray()
        val ts = "1700000000"
        val rid = "11111111-2222-3333-4444-555555555555"

        val signature = signer.sign(body, ts, rid)
        val outcome = verifier.verify(ts, signature, rid, body)
        assertTrue("expected verify Ok, got $outcome", outcome is HmacVerifier.Outcome.Ok)
    }

    @Test
    fun `signed body verifies with String overload`() {
        val verifier = HmacVerifier(provider)
        val signer = HmacSigner(provider)

        val body = """{"k":1}"""
        val ts = "1700000000"
        val rid = "11111111-2222-3333-4444-555555555555"

        val sigBytes = signer.sign(body.toByteArray(), ts, rid)
        val sigString = signer.sign(body, ts, rid)
        assertEquals("byte and String overload must produce the same signature", sigBytes, sigString)
    }

    @Test
    fun `different body yields different signature`() {
        val signer = HmacSigner(provider)
        val ts = "1700000000"
        val rid = "11111111-2222-3333-4444-555555555555"

        val sig1 = signer.sign("hello", ts, rid)
        val sig2 = signer.sign("world", ts, rid)
        assertNotEquals(sig1, sig2)
    }

    @Test
    fun `different request_id yields different signature`() {
        val signer = HmacSigner(provider)
        val ts = "1700000000"
        val body = "hello".toByteArray()

        val sig1 = signer.sign(body, ts, "rid-1")
        val sig2 = signer.sign(body, ts, "rid-2")
        assertNotEquals(sig1, sig2)
    }

    @Test
    fun `different timestamp yields different signature`() {
        val signer = HmacSigner(provider)
        val rid = "rid"
        val body = "hello".toByteArray()

        val sig1 = signer.sign(body, "1700000000", rid)
        val sig2 = signer.sign(body, "1700000001", rid)
        assertNotEquals(sig1, sig2)
    }

    @Test
    fun `signature is base64 (no padding chars beyond standard)`() {
        val signer = HmacSigner(provider)
        val sig = signer.sign("hello".toByteArray(), "1700000000", "rid")
        // base64 alphabet: A-Z a-z 0-9 + / = — strict check
        assertTrue("not valid base64: $sig", sig.matches(Regex("^[A-Za-z0-9+/]+=*$")))
    }

    @Test
    fun `verify rejects bad signature`() {
        val verifier = HmacVerifier(provider)
        val body = "hello".toByteArray()
        val outcome = verifier.verify("1700000000", "AAAA", "rid", body)
        assertTrue(outcome is HmacVerifier.Outcome.Fail)
    }
}