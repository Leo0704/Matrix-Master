package com.matrix.companion.accessibility

import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * High-level actions on top of [AccessibilityDriver]. The handlers
 * (TapHandler, AppOpenHandler, …) and the XHS script layer call into here.
 *
 * Each public function is cancellable via the calling coroutine.
 */
class ActionExecutor(
    private val driver: AccessibilityDriver,
    private val appContext: Context,
) {
    suspend fun openApp(packageName: String, requestId: String): ApiResult<Unit> {
        val launch = appContext.packageManager.getLaunchIntentForPackage(packageName)
            ?: return ApiResult.Err(
                ErrorCode.APP_NOT_FOUND,
                "package $packageName not installed",
                retryable = false,
            )
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        appContext.startActivity(launch)
        // Wait up to 5 s for the app to take focus.
        val ok = withTimeoutOrNull(5_000) {
            while (true) {
                val pkg = (driver.rootNode()?.packageName ?: "").toString()
                Log.d("MatrixExec", "openApp: polling pkg=$pkg want=$packageName")
                if (pkg == packageName) return@withTimeoutOrNull true
                delay(150)
            }
            @Suppress("UNREACHABLE_CODE")
            false
        } ?: false
        return if (ok) ApiResult.Ok(Unit)
        else ApiResult.Err(ErrorCode.TIMEOUT, "focus did not switch to $packageName", retryable = true)
    }

    suspend fun tap(selector: Selector): ApiResult<Unit> = driver.tapBySelector(selector)

    /**
     * Wait up to [timeoutMs] for [selector] to appear, then tap it.
     *
     * openApp 只是"App 到前台"，首帧渲染 + 无障碍树填充还要几百 ms~几秒
     * （小红书冷启动尤其慢）。裸 [tap] 在树未就绪时直接 SELECTOR_NOT_FOUND，
     * E2E 实测 800ms Jitter 不够。等不到才报 SELECTOR_NOT_FOUND（retryable，
     * 页面慢和选择器失效是两回事，前者重试能好）。
     */
    suspend fun tapWhenReady(selector: Selector, timeoutMs: Long = 5000L): ApiResult<Unit> {
        driver.waitFor(selector, timeoutMs = timeoutMs)
            ?: return ApiResult.Err(
                ErrorCode.SELECTOR_NOT_FOUND,
                "node not ready in ${timeoutMs}ms: $selector",
                retryable = true,
            )
        return driver.tapBySelector(selector)
    }

    suspend fun input(text: String): ApiResult<Unit> = driver.inputText(text)

    suspend fun swipe(fromX: Int, fromY: Int, toX: Int, toY: Int, durationMs: Long = 300L) =
        driver.swipe(fromX, fromY, toX, toY, durationMs)
}
