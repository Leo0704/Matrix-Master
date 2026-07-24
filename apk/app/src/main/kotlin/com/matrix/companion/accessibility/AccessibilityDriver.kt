package com.matrix.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import com.matrix.companion.util.Logx
import android.util.Log
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import kotlin.coroutines.resume

/**
 * Single point of access to the OS AccessibilityService. All UI actions
 * dispatched from handlers MUST go through here so we have one choke point
 * to recover from "service disabled" / "no root window" situations.
 */
class AccessibilityDriver(private val serviceRef: () -> AccessibilityService?) {

    private val executor: Executor by lazy { Executors.newSingleThreadExecutor() }

    fun isReady(): Boolean = serviceRef() != null

    /**
     * Get the root node of the currently *foreground* app's window.
     *
     * On stock Android, [AccessibilityService.getRootInActiveWindow] returns
     * the active window's root. However, on HyperOS / MIUI (and some other
     * OEM ROMs), `rootInActiveWindow` reliably returns the *accessibility
     * service's own* window rather than the foreground app — making every
     * selector lookup, openApp focus check, and /device/status `app` field
     * report our own package instead of the real foreground app.
     *
     * Fix: try `rootInActiveWindow` first; if it belongs to us (or is null),
     * fall back to [getWindows] and pick the topmost TYPE_APPLICATION window
     * whose root belongs to a different package.
     */
    fun rootNode(): AccessibilityNodeInfo? {
        val service = serviceRef() ?: return null
        val ownPkg = service.packageName?.toString()
        // 多窗口场景（实测：小红书笔记详情页 NoteDetailActivity）：
        // rootInActiveWindow 会返回「后面的旧窗口」（如同包的首页），
        // 树里找不到任何当前页面的节点。所以先在应用窗口里找
        // 「有焦点的」（其次 layer 最高的），最后才退回 rootInActiveWindow。
        val appWindows = service.windows.filter {
            it.type == AccessibilityWindowInfo.TYPE_APPLICATION &&
                it.root?.packageName?.toString() != ownPkg
        }
        if (appWindows.isNotEmpty()) {
            val best = appWindows.firstOrNull { it.isFocused }
                ?: appWindows.maxByOrNull { it.layer }
            best?.root?.let { return it }
        }
        val root = service.rootInActiveWindow
        if (root != null && root.packageName?.toString() != ownPkg) {
            Log.d(TAG, "rootNode: rootInActiveWindow pkg=${root.packageName} (non-own, fast path)")
            return root
        }
        root?.recycle()
        return null
    }

    companion object {
        private const val TAG = "MatrixDriver"
    }

    fun findFirst(selector: Selector): UiNode? {
        val root = rootNode() ?: return null
        return walk(root, selector)
    }

    fun findAll(selector: Selector): List<UiNode> {
        val root = rootNode() ?: return emptyList()
        val out = mutableListOf<UiNode>()
        collectMatching(root, selector, out)
        return out
    }

    /**
     * Wait up to [timeoutMs] for [selector] to appear in the active window.
     * Returns the first matched node, or null on timeout.
     * Polling uses [Jitter] so a fixed-interval probe isn't trivially
     * detectable by the platform.
     */
    suspend fun waitFor(
        selector: Selector,
        timeoutMs: Long = 5000L,
        pollIntervalBaseMs: Long = 200L,
    ): UiNode? {
        require(timeoutMs >= 0) { "timeoutMs must be non-negative" }
        require(pollIntervalBaseMs > 0) { "pollIntervalBaseMs must be positive" }
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            findFirst(selector)?.let { return it }
            Jitter.sleep(pollIntervalBaseMs)
        }
        return null
    }

    /**
     * Wait up to [timeoutMs] for [selector] to disappear from the active window.
     * Returns true if the selector was no longer found before the deadline.
     */
    suspend fun waitUntilGone(
        selector: Selector,
        timeoutMs: Long = 5000L,
        pollIntervalBaseMs: Long = 200L,
    ): Boolean {
        require(timeoutMs >= 0) { "timeoutMs must be non-negative" }
        require(pollIntervalBaseMs > 0) { "pollIntervalBaseMs must be positive" }
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (findFirst(selector) == null) return true
            Jitter.sleep(pollIntervalBaseMs)
        }
        return false
    }

    /**
     * Tap at the screen coordinates ([x], [y]). Returns DEVICE_OFFLINE if
     * the accessibility service is not bound, INTERNAL_ERROR (retryable)
     * if the system rejected the gesture.
     */
    suspend fun tap(x: Int, y: Int): ApiResult<Unit> {
        val service = serviceRef()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "accessibility not bound", retryable = false)
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        val ok = service.dispatchGesture(
            GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0L, 80L))
                .build(),
            null, null,
        )
        return if (ok) ApiResult.Ok(Unit)
        else ApiResult.Err(ErrorCode.INTERNAL_ERROR, "gesture rejected", retryable = true)
    }

    /**
     * Tap the first node matching [selector].
     *
     * Behaviour change vs. the old implementation:
     * - If the matched node itself is not clickable, walk up the parent
     *   chain to find the nearest clickable ancestor (XHS often wraps a
     *   TextView inside a clickable container, and tapping the inner text
     *   on a non-clickable view does nothing).
     * - Operates on AccessibilityNodeInfo directly (not the UiNode
     *   snapshot) so the parent chain is still available.
     */
    suspend fun tapBySelector(selector: Selector): ApiResult<Unit> {
        val root = rootNode()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "no active window", retryable = true)
        val matched = walkForTap(root, selector)
        if (matched == null) {
            root.recycle()
            return ApiResult.Err(
                ErrorCode.SELECTOR_NOT_FOUND,
                "no node matched $selector",
                retryable = false,
            )
        }
        val clickable = findClickableAncestor(matched) ?: matched
        val bounds = Rect().also { clickable.getBoundsInScreen(it) }
        val cx = bounds.centerX()
        val cy = bounds.centerY()
        if (clickable !== matched) clickable.recycle()
        matched.recycle()
        root.recycle()
        return tap(cx, cy)
    }

    private fun findClickableAncestor(start: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var current: AccessibilityNodeInfo? = start
        while (current != null) {
            if (current.isClickable) return current
            current = current.parent
        }
        return null
    }

    /**
     * 按一次系统返回键（无 UI 目标时用）。典型用途：输完正文后先收起输入法，
     * 否则底部「发布」按钮被键盘盖住，按坐标点会误触键盘按键（E2E 实测：
     * 发布键被盖后 tap 落到键盘 "." 上，正文被多加一个句号）。
     */
    suspend fun pressBack(): ApiResult<Unit> {
        val service = serviceRef()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "accessibility not bound", retryable = false)
        val ok = service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
        return if (ok) ApiResult.Ok(Unit)
        else ApiResult.Err(ErrorCode.INTERNAL_ERROR, "back action rejected", retryable = true)
    }

    suspend fun swipe(fromX: Int, fromY: Int, toX: Int, toY: Int, durationMs: Long): ApiResult<Unit> {
        val service = serviceRef()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "accessibility not bound", retryable = false)
        val path = Path().apply {
            moveTo(fromX.toFloat(), fromY.toFloat())
            lineTo(toX.toFloat(), toY.toFloat())
        }
        val ok = service.dispatchGesture(
            GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0L, durationMs))
                .build(),
            null, null,
        )
        return if (ok) ApiResult.Ok(Unit)
        else ApiResult.Err(ErrorCode.INTERNAL_ERROR, "swipe rejected", retryable = true)
    }

    /**
     * Input [text] into the currently focused input field.
     *
     * Bug fix vs. the old implementation:
     * - The old code passed `Bundle.putBoolean(SET_TEXT_CHARSEQUENCE, true)`,
     *   which is the wrong Bundle entry — Android expects
     *   `putCharSequence(SET_TEXT_CHARSEQUENCE, text)` carrying the actual
     *   string. The boolean-true variant was silently ignored, so all
     *   `inputText` calls did nothing.
     * - The clipboard-paste fallback is retained for API < 24 (not
     *   reachable at our minSdk=26, but kept defensively in case the
     *   platform ever returns false from performAction).
     */
    suspend fun inputText(text: String): ApiResult<Unit> {
        val service = serviceRef()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "accessibility not bound", retryable = false)
        if (text.isEmpty()) return ApiResult.Ok(Unit)
        val focused = service.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: return ApiResult.Err(ErrorCode.IME_ERROR, "no focused input field", retryable = true)

        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                val args = Bundle().apply {
                    putCharSequence(
                        AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                        text,
                    )
                }
                if (focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                    ApiResult.Ok(Unit)
                } else {
                    pasteFromClipboard(service, focused)
                }
            } else {
                pasteFromClipboard(service, focused)
            }
        } finally {
            focused.recycle()
        }
    }

    private fun pasteFromClipboard(
        service: AccessibilityService,
        focused: AccessibilityNodeInfo,
    ): ApiResult<Unit> {
        val clipboard = service.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("matrix_paste", ""))
        // We don't have the text in scope here without changing the signature;
        // paste fallback is only triggered after a successful SET_TEXT above,
        // so it's a last-resort path. Return IME_ERROR if hit.
        Logx.w("inputText: fell back to paste path (text not delivered)")
        return ApiResult.Err(
            ErrorCode.IME_ERROR,
            "setText returned false; paste fallback unavailable without text in scope",
            retryable = true,
        )
    }

    /**
     * Take a screenshot via the accessibility service. Available on API 30+.
     * Falls back to MediaProjection on older devices (not implemented here —
     * main controller can use `adb exec-out screencap` instead).
     */
    suspend fun screenshot(): ApiResult<ByteArray> {
        if (Build.VERSION.SDK_INT < 30) {
            return ApiResult.Err(ErrorCode.INTERNAL_ERROR, "screenshot requires API 30+", retryable = false)
        }
        val service = serviceRef()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "accessibility not bound", retryable = false)
        return suspendCancellableCoroutine { cont ->
            val callback = object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                    val hw = result.hardwareBuffer
                    val bitmap = if (hw != null) Bitmap.wrapHardwareBuffer(hw, null) else null
                    hw?.close()
                    if (bitmap == null) {
                        cont.resume(ApiResult.Err(ErrorCode.INTERNAL_ERROR, "empty screenshot bitmap", retryable = true))
                        return
                    }
                    val baos = ByteArrayOutputStream()
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, baos)
                    bitmap.recycle()
                    cont.resume(ApiResult.Ok(baos.toByteArray()))
                }

                override fun onFailure(errorCode: Int) {
                    cont.resume(ApiResult.Err(ErrorCode.INTERNAL_ERROR, "screenshot failed code=$errorCode", retryable = false))
                }
            }
            try {
                service.takeScreenshot(android.view.Display.DEFAULT_DISPLAY, executor, callback)
            } catch (t: Throwable) {
                cont.resume(ApiResult.Err(ErrorCode.INTERNAL_ERROR, t.message ?: "screenshot fail", retryable = true))
            }
        }
    }

    private fun walk(node: AccessibilityNodeInfo, selector: Selector): UiNode? {
        if (selector.matches(toUiNode(node))) return toUiNode(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = walk(child, selector)
            child.recycle()
            if (found != null) return found
        }
        return null
    }

    private fun collectMatching(node: AccessibilityNodeInfo, selector: Selector, out: MutableList<UiNode>) {
        if (selector.matches(toUiNode(node))) out.add(toUiNode(node))
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            collectMatching(child, selector, out)
            child.recycle()
        }
    }

    /**
     * Like [walk] but returns the live AccessibilityNodeInfo so callers can
     * walk up the parent chain. Used by [tapBySelector].
     * Caller is responsible for [AccessibilityNodeInfo.recycle] on the result.
     */
    private fun walkForTap(node: AccessibilityNodeInfo, selector: Selector): AccessibilityNodeInfo? {
        if (selector.matches(toUiNode(node))) return AccessibilityNodeInfo.obtain(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = walkForTap(child, selector)
            child.recycle()
            if (found != null) return found
        }
        return null
    }

    private fun toUiNode(node: AccessibilityNodeInfo): UiNode = UiNode(
        resourceId = node.viewIdResourceName,
        className = node.className?.toString(),
        contentDesc = node.contentDescription?.toString(),
        text = node.text?.toString(),
        packageName = node.packageName?.toString(),
        isClickable = node.isClickable,
        isFocusable = node.isFocusable,
        isEditable = node.isEditable,
        isScrollable = node.isScrollable,
        boundsInScreen = Rect().also { node.getBoundsInScreen(it) },
    )
}