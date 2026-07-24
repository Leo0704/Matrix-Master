package com.matrix.companion.xhs

import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.accessibility.Selector
import com.matrix.companion.util.Logx
import kotlinx.coroutines.delay

/**
 * 小红书登录状态检测器。
 *
 * 定期打开"我"页面，通过 UI 元素判断当前登录状态：
 * - success：能看到"小红书号："或"编辑主页" → 已登录
 * - failed：能看到"登录"按钮 → 未登录/掉线
 * - captcha：检测到验证码/滑块相关文本 → 需要人工验证
 * - unknown：页面结构不匹配，无法判断
 */
class LoginStateChecker(
    private val driver: AccessibilityDriver,
    private val executor: ActionExecutor,
) {

    sealed class Result {
        data class Success(val nickname: String?, val xhsId: String?) : Result()
        data class Failed(val reason: String) : Result()
        data class Captcha(val hint: String) : Result()
        data class Unknown(val hint: String) : Result()
    }

    /**
     * 执行一次检测。返回登录状态结果。
     */
    suspend fun check(): Result {
        if (!driver.isReady()) {
            return Result.Unknown("accessibility not ready")
        }

        // 1. 先把小红书带到前台
        val openResult = executor.openApp(XhsSelectors.PACKAGE, "login_state_check")
        if (openResult is com.matrix.companion.util.ApiResult.Err) {
            return Result.Unknown("open xhs failed: ${openResult.message}")
        }
        delay(1500)

        // 2. 打开"我"页面
        val tapResult = driver.tapBySelector(XhsSelectors.TAB_PROFILE)
        if (tapResult is com.matrix.companion.util.ApiResult.Err) {
            return Result.Unknown("tap profile failed: ${tapResult.message}")
        }
        delay(2000)

        // 3. 检测已登录标志
        val xhsIdNode = driver.findFirst(Selector.Text("小红书号："))
        val editProfileNode = driver.findFirst(Selector.Text("编辑主页"))

        if (xhsIdNode != null || editProfileNode != null) {
            val xhsId = xhsIdNode?.text?.removePrefix("小红书号：")?.trim()
            val nickname = extractNickname()
            Logx.i("login_state: detected logged in, xhsId=$xhsId, nickname=$nickname")
            return Result.Success(nickname, xhsId)
        }

        // 4. 检测未登录/掉线标志
        val loginBtnNode = driver.findFirst(Selector.Text("登录"))
        if (loginBtnNode != null) {
            Logx.w("login_state: detected not logged in")
            return Result.Failed("login button visible")
        }

        // 5. 检测验证码/风控标志
        val captchaKeywords = listOf("验证", "滑块", "拼图", "安全验证", "账号异常", "风险")
        for (keyword in captchaKeywords) {
            val node = driver.findFirst(Selector.Text(keyword))
            if (node != null) {
                Logx.w("login_state: detected captcha/risk control: $keyword")
                return Result.Captcha(keyword)
            }
        }

        Logx.w("login_state: unknown page structure")
        return Result.Unknown("no known indicators found")
    }

    /**
     * 从"我"页面提取用户昵称。
     * 昵称通常显示在页面顶部，"小红书号"上方。
     */
    private fun extractNickname(): String? {
        return null
    }
}
