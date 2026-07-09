package com.matrix.companion.util

import android.util.Log
import timber.log.Timber

/**
 * Logging facade. Internal implementation:
 * - Always writes to [android.util.Log] (logcat always shows it, even before
 *   any Timber Tree is planted — critical for crash diagnostics in early
 *   init).
 * - Also forwards to [Timber] so any installed [Timber.Tree]
 *   (e.g. [com.matrix.companion.net.LogForwarderTree]) receives every line.
 * - Mirrors to an in-memory ring buffer so [MainActivity] can render a live
 *   tail without paying the cost of `logcat -d`.
 */
object Logx {
    private const val TAG = "MatrixCompanion"

    fun d(msg: String) {
        Log.d(TAG, msg)
        Timber.tag(TAG).d(msg)
        append("D", msg)
    }

    fun i(msg: String) {
        Log.i(TAG, msg)
        Timber.tag(TAG).i(msg)
        append("I", msg)
    }

    fun w(msg: String, t: Throwable? = null) {
        if (t != null) { Log.w(TAG, msg, t); Timber.tag(TAG).w(t, msg) }
        else { Log.w(TAG, msg); Timber.tag(TAG).w(msg) }
        append("W", msg)
    }

    fun e(msg: String, t: Throwable? = null) {
        if (t != null) { Log.e(TAG, msg, t); Timber.tag(TAG).e(t, msg) }
        else { Log.e(TAG, msg); Timber.tag(TAG).e(msg) }
        append("E", msg)
    }

    /** In-memory ring buffer mirrored to MainActivity log view. */
    private val ring = ArrayDeque<String>(512)

    @Synchronized
    fun tail(maxLines: Int = 200): List<String> =
        if (ring.size <= maxLines) ring.toList() else ring.toList().takeLast(maxLines)

    @Synchronized
    fun append(level: String, msg: String) {
        val line = "[${System.currentTimeMillis() % 100000}] $level $msg"
        if (ring.size == 512) ring.removeFirst()
        ring.addLast(line)
    }
}
