package com.matrix.companion.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random

/**
 * Pin down the [Jitter] distribution so we don't accidentally ship a
 * constant delay (which would be a free pass for platform detection).
 */
class JitterTest {

    @Test
    fun `base zero stays zero`() {
        // base=0 is a valid "no waiting" sentinel used by ActionExecutor
        // probes; it must not be turned into a positive value.
        assertEquals(0L, Jitter.nextDelay(0L, Random(42)))
    }

    @Test
    fun `delay stays within clamp bounds`() {
        val random = Random(1234)
        // Run a few thousand samples to make sure clamp never breaks.
        repeat(5_000) {
            val v = Jitter.nextDelay(1_000L, random)
            assertTrue("out of lower bound: $v", v >= 500L)
            // 上限不是 2.5×base：5% 的样本会叠加 500–2000ms think pause，
            // 绝对上限 = 2500(clamp) + 1499 + 500 ≈ 4500。
            assertTrue("out of upper bound: $v", v <= 4_500L)
        }
    }

    @Test
    fun `seeded random produces reproducible sequence`() {
        // Anti-detection needs determinism in tests, not necessarily in
        // production, but reproducibility here lets us regression-pin
        // the exact distribution if someone refactors Jitter.
        val a = Jitter.nextDelay(500L, Random(99))
        val b = Jitter.nextDelay(500L, Random(99))
        assertEquals(a, b)
    }

    @Test
    fun `different seeds produce different sequences`() {
        val a = Jitter.nextDelay(500L, Random(1))
        val b = Jitter.nextDelay(500L, Random(2))
        assertNotEquals(a, b)
    }

    @Test
    fun `think pause raises a small fraction of samples`() {
        // The 5% think-pause has to fire occasionally; otherwise we
        // regressed to a pure Gaussian.
        val random = Random(7)
        var withThinkPause = 0
        val n = 4_000
        repeat(n) {
            val v = Jitter.nextDelay(300L, random)
            // Think pauses add >=500ms on top of the Gaussian range.
            // Upper Gaussian bound for base=300 is 750; think pause
            // bumps to >=1250. Use that as the discriminator.
            if (v > 800L) withThinkPause++
        }
        // 5% of n = 200; allow [80, 400] to account for RNG noise.
        assertTrue("think-pause count $withThinkPause out of range", withThinkPause in 80..400)
    }

    @Test
    fun `negative base is rejected`() {
        try {
            Jitter.nextDelay(-1L)
            assert(false) { "expected IllegalArgumentException" }
        } catch (e: IllegalArgumentException) {
            // expected
        }
    }
}