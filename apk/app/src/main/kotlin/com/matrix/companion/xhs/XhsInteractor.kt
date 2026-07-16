package com.matrix.companion.xhs

import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import com.matrix.companion.util.Logx

/**
 * Like / comment / follow / collect / share via accessibility.
 *
 * Only invoked after the master's rate scheduler confirms budget remaining.
 *
 * Navigation:
 *   The previous version ignored [Target.noteId] / [Target.userId] and
 *   operated on whatever happened to be on screen — which silently
 *   liked random notes when the master intended a specific one.
 *
 *   Now, when a target note is provided, we first open it via deep link
 *   ([XhsNoteOpener]) so the like/comment lands on the right note. If
 *   no target is provided, we operate on the current page (useful for
 *   "interact with what's already open" workflows).
 *
 *   User-level follows still accept a user_id in the API contract but
 *   XHS doesn't expose a public deep link for arbitrary user profiles,
 *   so we currently can't navigate to a specific user. We surface that
 *   as SELECTOR_NOT_FOUND rather than silently following whoever is on
 *   screen.
 */
class XhsInteractor(
    private val actions: ActionExecutor,
    private val noteOpener: XhsNoteOpener,
) {

    enum class Action { LIKE, COMMENT, FOLLOW, COLLECT, SHARE }

    data class Target(
        val noteId: String? = null,
        val userId: String? = null,
    )

    suspend fun run(action: Action, target: Target, commentText: String?): ApiResult<Unit> {
        // ---- Step 0: navigate to the target ----
        if (target.noteId != null) {
            when (val r = noteOpener.openNote(target.noteId)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return r
            }
            Jitter.sleep(500L)
        } else if (action.requiresNavigation && target.userId == null) {
            // No target at all — refuse to blindly operate on whatever is
            // currently on screen. The master should always pass a
            // platform_note_id for like / comment / collect.
            return ApiResult.Err(
                ErrorCode.INVALID_PARAMS,
                "action ${action.name} requires target.note_id or target.user_id",
                retryable = false,
            )
        }

        // ---- Step 1: perform the action ----
        return when (action) {
            Action.LIKE -> doLike()
            Action.COLLECT -> doCollect()
            Action.COMMENT -> doComment(commentText)
            Action.FOLLOW -> doFollow(target)
            Action.SHARE -> ApiResult.Err(
                ErrorCode.INTERNAL_ERROR,
                "share not wired yet (Phase 2 roadmap)",
                retryable = false,
            )
        }
    }

    private suspend fun doLike(): ApiResult<Unit> {
        // Idempotency-lite: detect "已点赞" state via the like button's
        // selected state. We don't currently have a reliable accessor
        // for AccessibilityNodeInfo.isSelected across XHS versions, so
        // we just tap and rely on the master scheduler for rate control.
        val r = actions.tap(XhsSelectors.BTN_LIKE)
        if (r is ApiResult.Ok) Jitter.sleep(400L)
        return r
    }

    private suspend fun doCollect(): ApiResult<Unit> {
        val r = actions.tap(XhsSelectors.BTN_COLLECT)
        if (r is ApiResult.Ok) Jitter.sleep(400L)
        return r
    }

    private suspend fun doComment(commentText: String?): ApiResult<Unit> {
        val txt = commentText
            ?: return ApiResult.Err(
                ErrorCode.INVALID_PARAMS,
                "comment requires content",
                retryable = false,
            )
        when (val r = actions.tap(XhsSelectors.EDIT_COMMENT)) {
            is ApiResult.Err -> return r
            is ApiResult.Ok -> Unit
        }
        Jitter.sleep(400L)
        when (val r = actions.input(txt)) {
            is ApiResult.Err -> return r
            is ApiResult.Ok -> Unit
        }
        Jitter.sleep(200L)
        return actions.tap(XhsSelectors.BTN_COMMENT_SEND)
    }

    private suspend fun doFollow(target: Target): ApiResult<Unit> {
        if (target.userId != null) {
            // We have a user_id but no deep-link path to a user profile.
            // Don't tap "关注" blindly on the current page — it's almost
            // certainly the wrong target.
            Logx.w("xhs_interactor.doFollow: user_id provided but no user-profile deep link; refusing")
            return ApiResult.Err(
                ErrorCode.SELECTOR_NOT_FOUND,
                "cannot navigate to user ${target.userId}",
                retryable = false,
            )
        }
        return actions.tap(XhsSelectors.BTN_FOLLOW)
    }

    /** Actions that MUST have a navigation target to be safe. */
    private val Action.requiresNavigation: Boolean
        get() = this == Action.LIKE || this == Action.COLLECT || this == Action.COMMENT
}