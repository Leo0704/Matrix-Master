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
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
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

    @Volatile private var lastEventAt: Long = 0L
    private val executor: Executor by lazy { Executors.newSingleThreadExecutor() }

    fun isReady(): Boolean = serviceRef() != null

    fun markEventSeen() {
        lastEventAt = System.currentTimeMillis()
    }

    fun secondsSinceLastEvent(): Long {
        val now = System.currentTimeMillis()
        return if (lastEventAt == 0L) Long.MAX_VALUE else (now - lastEventAt) / 1000L
    }

    fun rootNode(): AccessibilityNodeInfo? = serviceRef()?.rootInActiveWindow

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

    suspend fun tapBySelector(selector: Selector): ApiResult<Unit> {
        val node = findFirst(selector)
            ?: return ApiResult.Err(ErrorCode.SELECTOR_NOT_FOUND, "no node matched $selector", retryable = false)
        return tap(node.centerX, node.centerY)
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

    suspend fun inputText(text: String): ApiResult<Unit> {
        val service = serviceRef()
            ?: return ApiResult.Err(ErrorCode.DEVICE_OFFLINE, "accessibility not bound", retryable = false)
        val focused = service.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: return ApiResult.Err(ErrorCode.IME_ERROR, "no focused input field", retryable = true)
        val clipboard = service.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("matrix_paste", text))
        val args = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            Bundle().apply {
                putBoolean(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, true)
            }
        } else null
        val setText = if (args != null) {
            focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        } else {
            focused.performAction(AccessibilityNodeInfo.ACTION_PASTE)
        }
        return if (setText) ApiResult.Ok(Unit)
        else ApiResult.Err(ErrorCode.IME_ERROR, "setText/paste returned false", retryable = true)
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
