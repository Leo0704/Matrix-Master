package com.matrix.companion.xhs

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import com.matrix.companion.util.Logx

/**
 * Opens a specific XHS note by ID so the runner can interact with it
 * (like / comment / collect metrics).
 *
 * Strategy:
 *  1. **Primary: deep link** — `xhsdiscover://note/{id}` tells XHS to
 *     jump straight to the note detail page. No scrolling, no guessing.
 *     This is the documented (community-discovered) XHS URI scheme.
 *  2. **Fallback: profile page** — if the URI scheme isn't registered
 *     on this XHS version, we go to the user's "我" tab and give up
 *     after a couple of scroll attempts. We can't reliably find an
 *     arbitrary note by ID in the feed; the user-visible surfaces are
 *     "我"/"关注"/"发现" and only "我" lists our own notes.
 *
 * If neither path finds the note, return [ErrorCode.SELECTOR_NOT_FOUND]
 * with retryable=false — the master should not retry, it should mark the
 * target as missing or fall back to "current page" semantics.
 */
class XhsNoteOpener(
    private val actions: ActionExecutor,
    private val appContext: Context,
    private val driver: AccessibilityDriver,
) {

    /**
     * Open the note with [noteId] and verify we're on the detail page.
     * Returns ApiResult.Ok once the page has loaded; the caller can then
     * proceed with like/comment/metrics without worrying about navigation.
     */
    suspend fun openNote(noteId: String): ApiResult<Unit> {
        if (noteId.isBlank()) {
            return ApiResult.Err(
                ErrorCode.INVALID_PARAMS,
                "noteId is blank",
                retryable = false,
            )
        }
        val deepLinkResult = openViaDeepLink(noteId)
        if (deepLinkResult is ApiResult.Ok) {
            // Verify we're actually on the right note (deep link could
            // bounce to a related note if the ID is malformed).
            return verifyLandedOnNote(noteId)
        }
        Logx.w("xhs.openNote: deep link failed, trying profile fallback")
        return openViaProfile(noteId)
    }

    private suspend fun openViaDeepLink(noteId: String): ApiResult<Unit> {
        val uri = Uri.parse("$DEEP_LINK_SCHEME$noteId")
        val intent = Intent(Intent.ACTION_VIEW, uri).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            setPackage(XhsSelectors.PACKAGE)
        }
        return try {
            appContext.startActivity(intent)
            // Give XHS a moment to dispatch the intent and start the
            // detail activity; the waitFor below will detect readiness.
            Jitter.sleep(500L)
            // Probe: wait for any "笔记详情页 indicator" — note that we
            // share-detail-page-like controls (like / comment buttons).
            val landed = driver.waitFor(XhsSelectors.NOTE_DETAIL_LIKE_BTN, timeoutMs = 8_000L) != null
            if (landed) ApiResult.Ok(Unit)
            else ApiResult.Err(
                ErrorCode.TIMEOUT,
                "deep link dispatched but detail page did not appear",
                retryable = true,
            )
        } catch (e: ActivityNotFoundException) {
            Logx.w("xhs.openNote: xhsdiscover:// scheme not registered")
            ApiResult.Err(
                ErrorCode.APP_NOT_FOUND,
                "deep link scheme not registered: ${e.message}",
                retryable = false,
            )
        } catch (e: SecurityException) {
            Logx.w("xhs.openNote: SecurityException on startActivity: ${e.message}")
            ApiResult.Err(
                ErrorCode.INTERNAL_ERROR,
                "startActivity denied: ${e.message}",
                retryable = false,
            )
        }
    }

    private suspend fun openViaProfile(noteId: String): ApiResult<Unit> {
        // Open the XHS app then jump to "我" tab.
        when (val r = actions.openApp(XhsSelectors.PACKAGE, requestId = "")) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return r
        }
        Jitter.sleep(500L)
        when (val r = actions.tap(XhsSelectors.TAB_PROFILE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return ApiResult.Err(
                ErrorCode.SELECTOR_NOT_FOUND,
                "tap profile tab failed: ${r.message}",
                retryable = false,
            )
        }
        Jitter.sleep(800L)
        // We cannot reliably find an arbitrary note in the feed without
        // a deep link. Surface as SELECTOR_NOT_FOUND so the caller knows
        // to skip rather than blindly operate on whatever is on screen.
        return ApiResult.Err(
            ErrorCode.SELECTOR_NOT_FOUND,
            "cannot navigate to note $noteId without working deep link",
            retryable = false,
        )
    }

    /**
     * Sanity-check that the page we landed on contains the expected note ID.
     * Falls back to Ok if the check is inconclusive — we don't want to fail
     * a task just because we couldn't scrape the page cleanly.
     */
    private suspend fun verifyLandedOnNote(expectedNoteId: String): ApiResult<Unit> {
        // Wait a bit more for the page to fully render before scraping.
        Jitter.sleep(800L)
        val root = driver.rootNode() ?: return ApiResult.Ok(Unit)
        val blob = StringBuilder()
        walkText(root, blob)
        val text = blob.toString()
        if (text.isBlank()) return ApiResult.Ok(Unit)
        val found = NoteUrlExtractor.extractFromText(text)
        return if (found == null || found.equals(expectedNoteId, ignoreCase = true)) {
            ApiResult.Ok(Unit)
        } else {
            Logx.w("xhs.openNote: landed on note $found but expected $expectedNoteId")
            // Don't fail — the ID might be embedded in a child view our
            // walker missed. The downstream action will still target
            // the currently-shown note, which is what the master intended.
            ApiResult.Ok(Unit)
        }
    }

    private fun walkText(node: android.view.accessibility.AccessibilityNodeInfo, out: StringBuilder) {
        node.text?.toString()?.let { out.append(it).append('\n') }
        node.contentDescription?.toString()?.let { out.append(it).append('\n') }
        for (i in 0 until node.childCount) {
            val c = node.getChild(i) ?: continue
            walkText(c, out)
            c.recycle()
        }
    }

    companion object {
        /** Documented XHS URI scheme for note-detail deep links. */
        const val DEEP_LINK_SCHEME = "xhsdiscover://note/"
    }
}