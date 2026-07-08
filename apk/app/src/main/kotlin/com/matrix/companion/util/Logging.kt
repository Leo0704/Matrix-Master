package com.matrix.companion.util

import android.util.Log

/**
 * Thin wrapper around android.util.Log that other modules import.
 * Centralized so we can later swap in a file-backed sink for the in-app log view
 * without touching call sites.
 */
object Logx {
    private const val TAG = "MatrixCompanion"

    fun d(msg: String) { Log.d(TAG, msg) }
    fun i(msg: String) { Log.i(TAG, msg) }
    fun w(msg: String, t: Throwable? = null) { Log.w(TAG, msg, t) }
    fun e(msg: String, t: Throwable? = null) { Log.e(TAG, msg, t) }

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
