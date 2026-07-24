package com.matrix.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import com.matrix.companion.App
import com.matrix.companion.util.Logx

/**
 * The single long-lived accessibility service. Boot order:
 *   1. User enables in Settings → Accessibility
 *   2. System binds this service
 *   3. onServiceConnected() publishes [App.accessibilityServiceInstance]
 *   4. Actions dispatched via [com.matrix.companion.accessibility.AccessibilityDriver]
 *      become available to the rest of the app.
 */
class CompanionAccessibilityService : AccessibilityService() {
    override fun onServiceConnected() {
        super.onServiceConnected()
        App.accessibilityServiceInstance = this
        Logx.i("AccessibilityService connected")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        Logx.w("AccessibilityService unbound")
        App.accessibilityServiceInstance = null
        return super.onUnbind(intent)
    }

    override fun onInterrupt() {
        Logx.w("AccessibilityService interrupted")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            currentActivity = "${event.packageName}/${event.className}"
        }
    }

    companion object {
        /**
         * 当前前台 Activity（pkg/class），由 WINDOW_STATE_CHANGED 事件维护。
         * 用途：小红书笔记详情页的无障碍树对本服务不可见（实测），
         * 「是否到达详情页」不能靠找节点，只能看 Activity 名。
         */
        @Volatile
        var currentActivity: String? = null
            private set
    }
}
