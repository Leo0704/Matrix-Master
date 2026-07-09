package com.matrix.companion.util

import timber.log.Timber

/**
 * Logging facade. Internal implementation forwards to [Timber] so any
 * installed [Timber.Tree] (e.g. [com.matrix.companion.net.LogForwarderTree])
 * receives every log line. We keep the static methods so the 17 existing
 * call sites don't need to change.
 *
 * The in-memory ring buffer mirrors every log line so [MainActivity] can
 * render a live tail without paying the cost of `logcat -d`.
 */
object Logx {
    private const val TAG = "MatrixCompanion"

    fun d(msg: String) {
        Timber.tag(TAG).d(msg)
        append("D", msg)
    }

    fun i(msg: String) {
        Timber.tag(TAG).i(msg)
        append("I", msg)
    }

    fun w(msg: String, t: Throwable? = null) {
        if (t != null) Timber.tag(TAG).w(t, msg) else Timber.tag(TAG).w(msg)
        append("W", msg)
    }

    fun e(msg: String, t: Throwable? = null) {
        if (t != null) Timber.tag(TAG).e(t, msg) else Timber.tag(TAG).e(msg)
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
