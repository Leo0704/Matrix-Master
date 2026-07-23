package com.matrix.companion.xhs

import android.content.Context
import com.matrix.companion.util.Logx

/**
 * Tracks the device-account follower count between metric-collect runs so
 * [XhsMetricsCollector] can report `follows_gained` as a delta instead of
 * pretending it's always 0.
 *
 * Storage:
 * - Plain `SharedPreferences` is fine here: the value is the
 *   `account_id → last_seen_follower_count` mapping, which is non-secret.
 *   Using `EncryptedSharedPreferences` would add 30+ ms per write with no
 *   security benefit.
 *
 * Lifecycle:
 * - [record] is called whenever [XhsMetricsCollector] collects metrics for
 *   a note, after the page has been scraped.
 * - [delta] returns `currentCount - lastSeen` and updates the stored value.
 *
 * If we never saw the account before, [delta] returns 0 (no baseline to
 * subtract from) and stores the current count as the new baseline.
 */
class FollowsBaseline(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /**
     * Compute the gain since the last collection for [accountId] and
     * persist the new baseline. Returns 0 if this is the first observation.
     */
    fun delta(accountId: String?, currentCount: Int): Int {
        if (accountId.isNullOrBlank()) return 0
        val key = prefsKey(accountId)
        val previous = prefs.getInt(key, Int.MIN_VALUE)
        prefs.edit().putInt(key, currentCount).apply()
        return if (previous == Int.MIN_VALUE) {
            Logx.i("follows_baseline.first_observation account=$accountId count=$currentCount")
            0
        } else {
            (currentCount - previous).coerceAtLeast(0)
        }
    }

    /** For tests / diagnostics. */
    fun lastSeen(accountId: String): Int? {
        val v = prefs.getInt(prefsKey(accountId), Int.MIN_VALUE)
        return if (v == Int.MIN_VALUE) null else v
    }

    private fun prefsKey(accountId: String): String = "follows_$accountId"

    companion object {
        private const val PREFS_NAME = "matrix_companion_follows_baseline"
    }
}