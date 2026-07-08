package com.matrix.companion.accessibility

import android.graphics.Rect

/**
 * A simplified, snapshot view of a single accessibility node. We strip
 * down the platform AccessibilityNodeInfo into something we can pass
 * between coroutine boundaries without holding stale references.
 */
data class UiNode(
    val resourceId: String?,
    val className: String?,
    val contentDesc: String?,
    val text: String?,
    val packageName: String?,
    val isClickable: Boolean,
    val isFocusable: Boolean,
    val isEditable: Boolean,
    val isScrollable: Boolean,
    val boundsInScreen: Rect,
) {
    val centerX: Int get() = boundsInScreen.centerX()
    val centerY: Int get() = boundsInScreen.centerY()
}
