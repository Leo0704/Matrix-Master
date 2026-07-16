package com.matrix.companion.util

import kotlinx.coroutines.delay
import java.util.Random
import java.util.concurrent.ThreadLocalRandom

/**
 * Anti-detection jitter. Every UI action in the XHS scripts MUST call
 * [sleep] instead of `kotlinx.coroutines.delay(...)` directly.
 *
 * Why:
 * - Real users have variable timing — fixed delays are a textbook
 *   machine-detect signal. XHS / similar platforms look for it.
 * - A normal-distributed spread + occasional "think pauses" is the
 *   simplest model that survives casual pattern analysis.
 *
 * Distribution:
 * - Gaussian centered on `baseMs`, sigma = `baseMs * 0.3`
 * - Clamped to `[baseMs * 0.5, baseMs * 2.5]` so we don't starve
 *   the page-load budget
 * - 5% of calls add an extra 500–2000 ms "human hesitation"
 *
 * Tested in [com.matrix.companion.util.JitterTest].
 */
object Jitter {

    private const val THINK_PAUSE_PROBABILITY = 0.05
    private const val THINK_PAUSE_MIN_MS = 500L
    private const val THINK_PAUSE_MAX_MS = 2000L

    /**
     * Production overload — pulls from [ThreadLocalRandom], which avoids
     * the cost of constructing a `Random` per call while still giving us
     * a Box-Muller normal sample (`nextGaussian` is a JDK 7+ method).
     */
    fun nextDelay(baseMs: Long): Long = nextDelay(baseMs, ThreadLocalRandom.current())

    /**
     * Test-friendly overload — accepts an explicit [Random] so unit tests
     * can pin the distribution with a fixed seed. Production code should
     * call the no-arg overload.
     */
    fun nextDelay(baseMs: Long, random: Random): Long {
        require(baseMs >= 0) { "baseMs must be non-negative, got $baseMs" }
        if (baseMs == 0L) return 0L

        val sigma = baseMs * 0.3
        val raw = (random.nextGaussian() * sigma + baseMs)
        val clamped = raw.coerceIn(baseMs * 0.5, baseMs * 2.5).toLong()

        return if (random.nextDouble() < THINK_PAUSE_PROBABILITY) {
            // java.util.Random#nextLong() has no (origin, bound) overload,
            // so build the range manually. nextLong() returns a uniformly
            // distributed long in [Long.MIN_VALUE, Long.MAX_VALUE]; we
            // narrow it to [0, range) via mod. The modulo is slightly
            // biased but the bias is negligible for our 1500 ms window.
            val range = THINK_PAUSE_MAX_MS - THINK_PAUSE_MIN_MS
            clamped + (kotlin.math.abs(random.nextLong()) % range) + THINK_PAUSE_MIN_MS
        } else {
            clamped
        }
    }

    /** Coroutine sleep using [nextDelay]. Suspends for the jittered duration. */
    suspend fun sleep(baseMs: Long) {
        delay(nextDelay(baseMs))
    }
}