package com.matrix.companion.xhs

import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import com.matrix.companion.util.Logx
import kotlinx.serialization.Serializable

/**
 * Drives the XHS publish flow end to end:
 *
 *   Open app → tap "发布" → fill 标题 → fill 正文 → pick images
 *   → tap "发布" → wait for success → scrape note_id from "我" tab
 *
 * Failure modes (each returns a specific ErrorCode so the master can
 * decide retry vs. human escalation):
 * - INVALID_PARAMS — title/content/tags over XHS limits
 * - SELECTOR_NOT_FOUND — XHS UI changed, resource-id missing
 * - IME_ERROR — text input failed (rare; the AccessibilityDriver bug is
 *   fixed, but some OEM keyboards still reject programmatic paste)
 * - UPLOAD_FAILED — image download / MediaStore insert failed
 * - TIMEOUT — publish button tapped but success signal not seen in 30s
 * - RISK_BLOCKED — XHS shows "包含违规内容" or similar moderation toast
 * - RATE_LIMITED — XHS shows "操作过于频繁" toast
 * - DRAFT_FAILED — publish completed but note_id couldn't be parsed
 */
class XhsPublisher(
    private val actions: ActionExecutor,
    private val driver: AccessibilityDriver,
    private val imagePipeline: ImagePipeline,
    private val imagePicker: XhsImagePicker,
) {

    @Serializable
    data class PublishParams(
        val title: String,
        val content: String,
        val tags: List<String>,
        val visibility: String,
        val imagePaths: List<String>,
    )

    @Serializable
    data class PublishOutcome(
        val noteId: String?,
        val url: String?,
    )

    private inline fun err(prefix: String, e: ApiResult.Err): ApiResult<PublishOutcome> =
        ApiResult.Err(e.code, "$prefix: ${e.message}", e.retryable)

    suspend fun publish(p: PublishParams): ApiResult<PublishOutcome> {
        // ---- Param validation ----
        if (p.title.isBlank()) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "title is blank", retryable = false)
        }
        if (p.title.length > MAX_TITLE_CHARS) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "title > $MAX_TITLE_CHARS chars", retryable = false)
        }
        if (p.content.length > MAX_CONTENT_CHARS) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "content > $MAX_CONTENT_CHARS chars", retryable = false)
        }
        if (p.tags.size > MAX_TAGS) {
            return ApiResult.Err(ErrorCode.INVALID_PARAMS, "tags > $MAX_TAGS", retryable = false)
        }

        // ---- Step 1: open XHS ----
        when (val r = actions.openApp(XhsSelectors.PACKAGE, requestId = "")) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("open app", r)
        }
        Jitter.sleep(800L)

        // ---- Step 2: tap "发布" / "发布笔记" ----
        when (val r = actions.tap(XhsSelectors.BTN_CREATE_NOTE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap create-note", r)
        }
        Jitter.sleep(600L)

        // ---- Step 3: title ----
        when (val r = actions.tap(XhsSelectors.EDIT_TITLE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap title", r)
        }
        Jitter.sleep(300L)
        when (val r = actions.input(p.title)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("input title", r)
        }
        Jitter.sleep(300L)

        // ---- Step 4: content (content + space-padded tags) ----
        when (val r = actions.tap(XhsSelectors.EDIT_CONTENT)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap content", r)
        }
        Jitter.sleep(300L)
        val fullContent = buildString {
            append(p.content)
            if (p.tags.isNotEmpty()) {
                if (isNotEmpty() && last() != '\n' && last() != ' ') append(' ')
                append(p.tags.joinToString(" ") { "#$it" })
            }
        }
        when (val r = actions.input(fullContent)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("input content", r)
        }
        Jitter.sleep(300L)

        // ---- Step 5: images (real upload pipeline) ----
        if (p.imagePaths.isNotEmpty()) {
            when (val r = imagePipeline.downloadImages(p.imagePaths)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("download images", r)
            }
            // Trigger the picker.
            when (val r = actions.tap(XhsSelectors.BTN_ADD_IMAGE)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("tap add-image", r)
            }
            // Wait for the system picker to open before we start clicking.
            Jitter.sleep(800L)
            when (val r = imagePicker.selectImages(p.imagePaths.size)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("select images", r)
            }
            Jitter.sleep(500L)
        }

        // ---- Step 6: tap publish button ----
        when (val r = actions.tap(XhsSelectors.BTN_PUBLISH_FINAL)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap publish", r)
        }

        // ---- Step 7: wait for success + extract note_id ----
        return waitPublishSuccess()
    }

    /**
     * After the publish button is tapped, wait for one of:
     * - "发布成功" / "笔记已发布" toast → scrape ID
     * - Jump to the "我" tab with the new note at the top → scrape ID
     * - A moderation / rate-limit toast → return appropriate ErrorCode
     * - 30-second timeout → UPLOAD_FAILED (retryable=true; XHS may still
     *   be uploading on slow networks)
     */
    private suspend fun waitPublishSuccess(): ApiResult<PublishOutcome> {
        val deadline = System.currentTimeMillis() + PUBLISH_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            Jitter.sleep(500L)
            // 1) Check for moderation / rate-limit toast — fast bail-out.
            scanForFailureToast()?.let { (code, msg) ->
                return ApiResult.Err(code, msg, retryable = code == ErrorCode.RATE_LIMITED)
            }
            // 2) Check for success toast.
            val successText = readScreenText()
            if (successText != null) {
                NoteUrlExtractor.extractFromText(successText)?.let { id ->
                    return ApiResult.Ok(PublishOutcome(noteId = id, url = "https://www.xiaohongshu.com/explore/$id"))
                }
            }
            // 3) If we're on the "我" tab, the new note should be at the top.
            if (driver.findFirst(XhsSelectors.TAB_PROFILE) != null &&
                driver.findFirst(XhsSelectors.NOTE_CARD_FIRST) != null
            ) {
                val profileText = readScreenText()
                if (profileText != null) {
                    NoteUrlExtractor.extractFromText(profileText)?.let { id ->
                        return ApiResult.Ok(PublishOutcome(noteId = id, url = "https://www.xiaohongshu.com/explore/$id"))
                    }
                    // We're on profile but couldn't find an ID; the new note
                    // may still be uploading. Keep waiting until deadline.
                }
            }
        }
        return ApiResult.Err(
            ErrorCode.UPLOAD_FAILED,
            "publish success not detected within ${PUBLISH_TIMEOUT_MS / 1000}s",
            retryable = true,
        )
    }

    private fun scanForFailureToast(): Pair<ErrorCode, String>? {
        val text = readScreenText() ?: return null
        return when {
            RISK_BLOCKED_PHRASES.any { it in text } ->
                ErrorCode.RISK_BLOCKED to "publish blocked: ${RISK_BLOCKED_PHRASES.first { it in text }}"
            RATE_LIMITED_PHRASES.any { it in text } ->
                ErrorCode.RATE_LIMITED to "rate limited: ${RATE_LIMITED_PHRASES.first { it in text }}"
            else -> null
        }
    }

    private fun readScreenText(): String? {
        val root = driver.rootNode() ?: return null
        val blob = StringBuilder()
        walkText(root, blob)
        return blob.toString().takeIf { it.isNotBlank() }
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
        const val MAX_TITLE_CHARS = 64
        const val MAX_CONTENT_CHARS = 2000
        const val MAX_TAGS = 10
        const val PUBLISH_TIMEOUT_MS = 30_000L

        // Substrings the XHS moderation / anti-spam dialogs use.
        private val RISK_BLOCKED_PHRASES = listOf(
            "包含违规内容",
            "内容不符合规范",
            "涉及敏感信息",
            "审核未通过",
        )
        private val RATE_LIMITED_PHRASES = listOf(
            "操作过于频繁",
            "发布太频繁",
            "请稍后再试",
            "发布速度过快",
        )
    }
}