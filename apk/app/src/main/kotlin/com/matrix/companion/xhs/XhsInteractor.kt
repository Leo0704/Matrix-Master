package com.matrix.companion.xhs

import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.accessibility.Selector
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import kotlinx.coroutines.delay

/**
 * Like / comment / follow / collect / share via accessibility.
 * Only invoked after the master's rate scheduler confirms budget remaining.
 */
class XhsInteractor(private val actions: ActionExecutor) {

    enum class Action { LIKE, COMMENT, FOLLOW, COLLECT, SHARE }

    data class Target(
        val noteId: String? = null,
        val userId: String? = null,
    )

    suspend fun run(action: Action, target: Target, commentText: String?): ApiResult<Unit> = when (action) {
        Action.LIKE -> actions.tap(XhsSelectors.BTN_LIKE)
        Action.COLLECT -> actions.tap(XhsSelectors.BTN_COLLECT)
        Action.COMMENT -> doComment(commentText)
        Action.FOLLOW -> actions.tap(Selector.ContentDesc("关注"))
        Action.SHARE -> ApiResult.Err(ErrorCode.INTERNAL_ERROR, "share not wired yet", retryable = false)
    }

    private suspend fun doComment(commentText: String?): ApiResult<Unit> {
        val txt = commentText
            ?: return ApiResult.Err(ErrorCode.INVALID_PARAMS, "comment requires content", retryable = false)
        when (val r = actions.tap(XhsSelectors.EDIT_COMMENT)) {
            is ApiResult.Err -> return r
            is ApiResult.Ok -> Unit
        }
        delay(400)
        when (val r = actions.input(txt)) {
            is ApiResult.Err -> return r
            is ApiResult.Ok -> Unit
        }
        delay(200)
        return actions.tap(XhsSelectors.BTN_COMMENT_SEND)
    }
}
