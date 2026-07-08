package com.matrix.companion.xhs

import android.view.accessibility.AccessibilityNodeInfo
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import kotlinx.serialization.Serializable

/**
 * Reads the user's profile page ("我" tab) and parses per-note metrics.
 * Output feeds the master's metrics loop so the agent can see whether
 * a publish actually drove engagement.
 *
 * Parsing is regex-based because the view-hierarchy "100+ 赞" spans do
 * not carry stable accessibility ids across XHS versions.
 */
class XhsMetricsCollector(private val driver: AccessibilityDriver) {

    @Serializable
    data class NoteMetric(
        val note_id: String? = null,
        val views: Int = 0,
        val likes: Int = 0,
        val collects: Int = 0,
        val comments: Int = 0,
        val follows_gained: Int = 0,
        val ts: String? = null,
    )

    private val RE_COUNT_W = Regex("""(\d+(?:\.\d+)?)\s*[wW]\s*赞""")
    private val RE_COUNT_LIKE = Regex("""(\d+)\s*赞""")
    private val RE_COUNT_COMMENT = Regex("""(\d+)\s*评论""")
    private val RE_COUNT_COLLECT = Regex("""(\d+)\s*收藏""")

    suspend fun collect(scope: String): ApiResult<List<NoteMetric>> {
        val root = driver.rootNode()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "no active window", retryable = true)

        val blob = StringBuilder()
        walkText(root, blob)
        val text = blob.toString()

        val likes = RE_COUNT_W.find(text)?.groupValues?.get(1)?.toCount(multiplier = 10_000)
            ?: RE_COUNT_LIKE.find(text)?.groupValues?.get(1)?.toCount() ?: 0
        val comments = RE_COUNT_COMMENT.find(text)?.groupValues?.get(1)?.toCount() ?: 0
        val collects = RE_COUNT_COLLECT.find(text)?.groupValues?.get(1)?.toCount() ?: 0

        return ApiResult.Ok(
            listOf(
                NoteMetric(
                    note_id = null,
                    views = 0,
                    likes = likes,
                    collects = collects,
                    comments = comments,
                )
            )
        )
    }

    private fun walkText(node: AccessibilityNodeInfo, out: StringBuilder) {
        node.text?.toString()?.let { out.append(it).append('\n') }
        for (i in 0 until node.childCount) {
            val c = node.getChild(i) ?: continue
            walkText(c, out)
            c.recycle()
        }
    }

    private fun String.toCount(multiplier: Int = 1): Int =
        toDoubleOrNull()?.let { (it * multiplier).toInt() } ?: 0
}
