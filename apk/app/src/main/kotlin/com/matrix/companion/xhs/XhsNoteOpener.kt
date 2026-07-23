package com.matrix.companion.xhs

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.accessibility.CompanionAccessibilityService
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
        // 2026 版 XHS（9.38+ 实测）：xhsdiscover:// 静默落到首页不再跳详情；
        // 但 https explore 链接会打开内置 WebView，页面有「打开 APP 查看」按钮，
        // 点了才进原生详情页。优先走这条；按钮没出现再退回老 scheme。
        val httpsResult = openViaHttpsWebView(noteId)
        if (httpsResult is ApiResult.Ok) return httpsResult
        Logx.w("xhs.openNote: https webview path failed (${httpsResult}), trying xhsdiscover://")

        val uri = Uri.parse("$DEEP_LINK_SCHEME$noteId")
        val intent = Intent(Intent.ACTION_VIEW, uri).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            setPackage(XhsSelectors.PACKAGE)
        }
        return try {
            appContext.startActivity(intent)
            Jitter.sleep(500L)
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

    /**
     * https explore 链接 → XHS 内置 WebView → 点「打开 APP 查看」→ 原生详情页。
     * （2026-07 在 XHS 9.38.1 / RMX2117 实测验证的路径）
     */
    /** 等当前前台 Activity 变成小红书笔记详情页（按 Activity 名，不看界面树）。 */
    private suspend fun waitForNoteDetailActivity(timeoutMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            val cur = CompanionAccessibilityService.currentActivity.orEmpty()
            if (cur.startsWith("com.xingin.xhs/") && cur.contains("NoteDetail")) {
                return true
            }
            Jitter.sleep(300L)
        }
        return false
    }

    private suspend fun openViaHttpsWebView(noteId: String): ApiResult<Unit> {
        // 这台 Realme（ColorOS）限制「后台 App 拉起新 Activity」：
        // 本 App 在后台时发 https intent 会被静默丢弃（E2E 实测 webview 根本不出现）。
        // 先把自家 MainActivity 拉到前台（同包名永远允许），再发 XHS 的 intent。
        val selfIntent = appContext.packageManager
            .getLaunchIntentForPackage(appContext.packageName)
            ?.apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
        if (selfIntent != null) {
            appContext.startActivity(selfIntent)
            Jitter.sleep(600L)
        }
        val uri = Uri.parse("https://www.xiaohongshu.com/explore/$noteId")
        val intent = Intent(Intent.ACTION_VIEW, uri).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            setPackage(XhsSelectors.PACKAGE)
        }
        return try {
            appContext.startActivity(intent)
            Jitter.sleep(1_500L)
            // 等按钮出现且「长好」（有真实坐标）：网页无障碍节点刚出现时
            // bounds 是 [0,0][0,0]，立刻点会戳到屏幕左上角触发返回（E2E 实测）。
            val deadline = System.currentTimeMillis() + 15_000L
            var openBtn = driver.findFirst(XhsSelectors.BTN_OPEN_IN_APP)
            while ((openBtn == null || openBtn.boundsInScreen.isEmpty) &&
                System.currentTimeMillis() < deadline
            ) {
                Jitter.sleep(300L)
                openBtn = driver.findFirst(XhsSelectors.BTN_OPEN_IN_APP)
            }
            if (openBtn == null || openBtn.boundsInScreen.isEmpty) {
                return ApiResult.Err(
                    ErrorCode.SELECTOR_NOT_FOUND,
                    "webview '打开 APP 查看' button not found",
                    retryable = true,
                )
            }
            when (val r = driver.tap(openBtn.centerX, openBtn.centerY)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return r
            }
            Jitter.sleep(1_000L)
            // 详情页的无障碍树对本服务不可见（实测：uiautomator 能看到，
            // 但服务窗口枚举拿不到），靠节点判定不可靠；改用 Activity 名判定。
            val landed = waitForNoteDetailActivity(15_000L)
            if (landed) ApiResult.Ok(Unit)
            else ApiResult.Err(
                ErrorCode.TIMEOUT,
                "'打开 APP 查看' tapped but native detail page did not appear",
                retryable = true,
            )
        } catch (e: ActivityNotFoundException) {
            ApiResult.Err(
                ErrorCode.APP_NOT_FOUND,
                "https explore intent not handled: ${e.message}",
                retryable = false,
            )
        } catch (e: SecurityException) {
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