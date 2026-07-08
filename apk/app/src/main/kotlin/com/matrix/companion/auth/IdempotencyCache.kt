package com.matrix.companion.auth

import com.matrix.companion.util.Clock
import java.util.concurrent.ConcurrentHashMap

/**
 * Tracks processed request_id values for [REPLAY_RETENTION_DAYS] so we can
 * reject duplicate writes that fall inside the HMAC tolerance window but
 * arrived twice (e.g. from the master's retry pipeline).
 *
 * Pure in-memory map; on process restart we'll redrive from the database's
 * agent_run table, so duplicates after restart are absorbed by the action
 * layer's "if note already published, return success" semantics.
 */
class IdempotencyCache(
    private val retentionMillis: Long = HmacAuth.REPLAY_RETENTION_DAYS.toLong() * 24L * 3600L * 1000L,
    private val clock: Clock = Clock.Real,
) {
    private data class Entry(val firstSeenMillis: Long)

    private val seen = ConcurrentHashMap<String, Entry>()

    /** Returns true if this id is fresh and we should proceed; false if seen. */
    fun claimIfFresh(requestId: String): Boolean {
        val now = clock.nowSeconds() * 1000L
        // Lazy GC: drop old entries inline.
        seen.entries.removeIf { now - it.value.firstSeenMillis > retentionMillis }
        val existing = seen.putIfAbsent(requestId, Entry(now))
        return existing == null
    }

    fun size(): Int = seen.size
}
