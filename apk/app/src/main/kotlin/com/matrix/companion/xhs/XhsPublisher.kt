package com.matrix.companion.xhs

import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Logx
import kotlinx.coroutines.delay

/**
 * Drives the XHS publish flow:
 *
 *   Open app → tap "发布" tab / btn_create_note
 *   → fill 标题 (max 64 chars)
 *   → fill 正文 (max 2000 chars)
 *   → upload N images (placeholders via local URI list)
 *   → tap "发布"
 *   → wait for "已发布" toast / new-note id
 *
 * Failures return specific ErrorCodes so the master can decide whether to
 * retry (TIMEOUT, IME_ERROR) or escalate to a human (SELECTOR_NOT_FOUND,
 * RISK_BLOCKED, DRAFT_FAILED).
 */
class XhsPublisher(private val actions: ActionExecutor) {

    data class PublishParams(
        val title: String,
        val content: String,
        val tags: List<String>,
        val visibility: String,
        val imagePaths: List<String>,
    )

    data class PublishOutcome(
        val noteId: String?,
        val url: String?,
    )

    private inline fun err(prefix: String, e: ApiResult.Err): ApiResult<PublishOutcome> =
        ApiResult.Err(e.code, "$prefix: ${e.message}", e.retryable)

    suspend fun publish(p: PublishParams): ApiResult<PublishOutcome> {
        if (p.title.length > 64) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "title > 64 chars", retryable = false)
        }
        if (p.content.length > 2000) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "content > 2000 chars", retryable = false)
        }
        if (p.tags.size > 10) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "tags > 10", retryable = false)
        }

        // Step 1: ensure app on home.
        when (val r = actions.openApp(XhsSelectors.PACKAGE, requestId = "")) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("open app", r)
        }
        delay(800)

        // Step 2: tap "发布" tab.
        when (val r = actions.tap(XhsSelectors.BTN_CREATE_NOTE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap create-note", r)
        }
        delay(600)

        // Step 3: title.
        when (val r = actions.tap(XhsSelectors.EDIT_TITLE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap title", r)
        }
        delay(300)
        when (val r = actions.input(p.title)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("input title", r)
        }
        delay(300)

        // Step 4: content.
        when (val r = actions.tap(XhsSelectors.EDIT_CONTENT)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap content", r)
        }
        delay(300)
        when (val r = actions.input(p.content + p.tags.joinToString(" ") { "#$it" })) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("input content", r)
        }
        delay(300)

        // Step 5: images. Real picker integration lives outside this MVP.
        p.imagePaths.forEachIndexed { idx, path ->
            Logx.i("placeholder image[$idx]: $path — UI selection not implemented yet")
        }

        // Step 6: publish.
        when (val r = actions.tap(XhsSelectors.BTN_PUBLISH_FINAL)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap publish", r)
        }

        delay(1500)
        // We don't yet parse "发布成功" toast back to a note_id; the master
        // collects the new ID via /xhs/collect_metrics later.
        return ApiResult.Ok(PublishOutcome(noteId = null, url = null))
    }
}
