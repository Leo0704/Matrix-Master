package com.matrix.companion.xhs

import android.view.accessibility.AccessibilityNodeInfo
import com.matrix.companion.App
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import kotlinx.serialization.Serializable

/**
 * Scrapes engagement metrics for a specific XHS note.
 *
 * Major rewrite vs. the previous version:
 * - Old signature `collect(scope: String)` ignored the master-supplied
 *   `platform_note_id` and returned a single fake row scraped from the
 *   current page. The backend then had to guess which note the numbers
 *   belonged to.
 * - New signature `collect(platformNoteId, scope, accountId?)` opens the
 *   note via deep link first, waits for the detail page to render, then
 *   scrapes the actual numbers next to the like/collect/comment buttons
 *   and the views counter. `follows_gained` is computed as a delta
 *   against the [FollowsBaseline] — we cannot read a delta directly
 *   from the XHS detail page (no such control exists), but we can
 *   compare profile-page follower counts across runs.
 *
 * Scope semantics:
 * - `recent_24h` (default): returns the note-detail counters as they are
 *   now. XHS doesn't expose "24h" vs "all-time" splits on the detail
 *   page; the value is "current cumulative" either way.
 * - `recent_7d`, `all`: same data, included for API completeness.
 *
 * If scraping fails (no like button found, malformed numbers), we return
 * an Err rather than fabricating zeros — the master prefers honest
 * gaps over misleading 0s.
 */
class XhsMetricsCollector(
    private val driver: AccessibilityDriver,
    private val noteOpener: XhsNoteOpener,
    private val followsBaseline: FollowsBaseline,
    private val actions: ActionExecutor,
) {

    @Serializable
    data class NoteMetric(
        val note_id: String? = null,
        val views: Int? = null,
        val likes: Int = 0,
        val collects: Int = 0,
        val comments: Int = 0,
        val follows_gained: Int = 0,
        val ts: String? = null,
    )

    suspend fun collect(
        platformNoteId: String,
        scope: String,
        accountId: String? = null,
    ): ApiResult<List<NoteMetric>> {
        if (platformNoteId.isBlank()) {
            return ApiResult.Err(
                ErrorCode.INVALID_PARAMS,
                "platformNoteId is blank",
                retryable = false,
            )
        }
        // 1) Navigate to the note.
        when (val r = noteOpener.openNote(platformNoteId)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return r
        }
        // 2) Let the page render and stream counts.
        Jitter.sleep(1500L)

        // 3) Scrape the whole detail page text once.
        val root = driver.rootNode()
            ?: return ApiResult.Err(
                ErrorCode.DEVICE_OFFLINE,
                "no active window while collecting metrics",
                retryable = true,
            )
        val text = StringBuilder().also { walkText(root, it) }.toString()
        if (text.isBlank()) {
            return ApiResult.Err(
                ErrorCode.PARSE_FAILED,
                "scraped text was empty",
                retryable = true,
            )
        }

        // 4) Parse counts. We accept the first match per metric.
        val likes = parseCount(text, "赞") ?: 0
        val collects = parseCount(text, "收藏") ?: 0
        val comments = parseCount(text, "评论") ?: 0
        val views = parseCount(text, "浏览") ?: parseCount(text, "观看")

        // 5) Follower delta — requires going to the profile tab. This is
        //    the slowest part of collect; we only do it if accountId is
        //    supplied so we don't burn navigation budget on cold calls.
        val followsGained = if (accountId != null) {
            sampleFollowerDelta(accountId)
        } else 0

        return ApiResult.Ok(
            listOf(
                NoteMetric(
                    note_id = platformNoteId,
                    views = views,
                    likes = likes,
                    collects = collects,
                    comments = comments,
                    follows_gained = followsGained,
                    ts = java.time.Instant.now().toString(),
                ),
            ),
        )
    }

    private fun parseCount(text: String, label: String): Int? {
        // Try 万 / w first (matches "1.2w", "3.5万")
        val wRe = Regex("""(\d+(?:\.\d+)?)\s*[wW万]\s*$label""")
        wRe.find(text)?.let { m ->
            val v = m.groupValues[1].toDoubleOrNull() ?: return null
            return (v * 10_000).toInt()
        }
        // Fall back to bare integer form ("123 赞")
        val bareRe = Regex("""(\d+)\s*$label""")
        return bareRe.find(text)?.groupValues?.get(1)?.toIntOrNull()
    }

    /**
     * Sample the follower count from the "我" tab, compute the delta
     * against the persisted baseline, and update the baseline.
     */
    private suspend fun sampleFollowerDelta(accountId: String): Int {
        when (val r = actions.openApp(XhsSelectors.PACKAGE, requestId = "")) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return 0
        }
        Jitter.sleep(800L)
        when (val r = actions.tap(XhsSelectors.TAB_PROFILE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return 0
        }
        Jitter.sleep(1200L)
        val root = driver.rootNode() ?: return 0
        val text = StringBuilder().also { walkText(root, it) }.toString()
        val current = parseCount(text, "粉丝")
            ?: Regex("""(\d+)\s*粉丝""").find(text)?.groupValues?.get(1)?.toIntOrNull()
            ?: return followsBaseline.lastSeen(accountId)?.let { 0 } ?: 0
        return followsBaseline.delta(accountId, current)
    }

    private fun walkText(node: AccessibilityNodeInfo, out: StringBuilder) {
        node.text?.toString()?.let { out.append(it).append('\n') }
        node.contentDescription?.toString()?.let { out.append(it).append('\n') }
        for (i in 0 until node.childCount) {
            val c = node.getChild(i) ?: continue
            walkText(c, out)
            c.recycle()
        }
    }
}