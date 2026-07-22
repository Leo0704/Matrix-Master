package com.matrix.companion.xhs

import android.content.ClipboardManager
import android.content.Context
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.Selector
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import com.matrix.companion.util.Logx
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.request.get
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
    private val appContext: Context,
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

        // ---- Step 2: tap "+" publish tab ----
        // On modern XHS the bottom bar has a centered "+" button that opens
        // a type-selector sheet (从相册选择 / 写文字 / 相机).
        when (val r = actions.tapWhenReady(XhsSelectors.TAB_PUBLISH)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap publish-tab", r)
        }
        Jitter.sleep(800L)

        if (p.imagePaths.isNotEmpty()) {
            // ---- Step 3 (image path): 先下载图片进相册，再进小红书相册选择器 ----
            // 2026 版 XHS 顺序是「先选图 → 图片编辑 → 标题/正文」，
            // 与旧版「先进编辑器再加图」相反，所以下载必须发生在点「从相册选择」之前。
            when (val r = imagePipeline.downloadImages(p.imagePaths)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("download images", r)
            }
            when (val r = actions.tapWhenReady(XhsSelectors.BTN_PICK_FROM_ALBUM)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("tap pick-from-album", r)
            }
            Jitter.sleep(1200L)

            // ---- Step 4: 相册选择器点第一张照片（刚写入的最新一张在最前） ----
            when (val r = tapFirstAlbumPhoto()) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("tap album first photo", r)
            }
            Jitter.sleep(800L)

            // 预览页 → 下一步；图片编辑页 → 下一步（部分版本没有第二页，容错跳过）
            when (val r = actions.tapWhenReady(XhsSelectors.BTN_NEXT_STEP)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return err("tap next (preview)", r)
            }
            Jitter.sleep(1000L)
            when (val r = actions.tapWhenReady(XhsSelectors.BTN_NEXT_STEP, timeoutMs = 2000L)) {
                is ApiResult.Ok -> Jitter.sleep(600L)
                is ApiResult.Err -> {
                    if (r.code != ErrorCode.SELECTOR_NOT_FOUND) {
                        return err("tap next (image-edit)", r)
                    }
                }
            }
        } else {
            // ---- Step 3 (text path): 点「写文字」；找不到则假设「+」直接进了编辑器 ----
            when (val r = actions.tapWhenReady(XhsSelectors.BTN_TEXT_NOTE, timeoutMs = 2000L)) {
                is ApiResult.Ok -> Jitter.sleep(600L)
                is ApiResult.Err -> {
                    if (r.code != ErrorCode.SELECTOR_NOT_FOUND) {
                        return err("tap text-note", r)
                    }
                }
            }
        }

        // ---- Step 3: title ----
        when (val r = actions.tapWhenReady(XhsSelectors.EDIT_TITLE)) {
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
        when (val r = actions.tapWhenReady(XhsSelectors.EDIT_CONTENT)) {
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

        // 收起输入法：正文输完键盘还开着，底部「发布笔记」按钮被盖住时
        // 按坐标点会误触键盘（实测正文被误加一个句号）。先按返回收键盘再点发布。
        when (val r = driver.pressBack()) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("dismiss keyboard", r)
        }
        Jitter.sleep(600L)

        // ---- Step 5: tap publish button ----
        // 图片已在编辑器之前的「相册选择」环节选好，无需再加图。
        when (val r = actions.tapWhenReady(XhsSelectors.BTN_PUBLISH_FINAL)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap publish", r)
        }

        // ---- Step 6: wait for success + extract note_id ----
        return waitPublishSuccess(p)
    }

    /**
     * 在小红书内置相册选择器里点第一张照片。
     *
     * 2026 版 XHS 相册选择器的照片格子是「无 content-desc 的可点击 ImageView」，
     * 没有稳定 resource-id（混淆为 0_resource_name_obfuscated），只能按
     * 「类名 + 可点击 + 无描述 + 位于顶部 tab 栏之下」圈出候选，再取最左上那格。
     * ImagePipeline 刚写入 MediaStore 的图片按时间倒序排在第一格。
     */
    private suspend fun tapFirstAlbumPhoto(): ApiResult<Unit> {
        val deadline = System.currentTimeMillis() + 5_000L
        while (System.currentTimeMillis() < deadline) {
            val first = driver
                .findAll(ALBUM_PHOTO_CANDIDATE)
                .filter {
                    it.isClickable &&
                        it.contentDesc.isNullOrBlank() &&
                        it.boundsInScreen.top > ALBUM_GRID_TOP_Y
                }
                .sortedWith(compareBy({ it.boundsInScreen.top }, { it.boundsInScreen.left }))
                .firstOrNull()
            if (first != null) {
                return driver.tap(first.centerX, first.centerY)
            }
            Jitter.sleep(200L)
        }
        return ApiResult.Err(
            ErrorCode.SELECTOR_NOT_FOUND,
            "album first photo not found in 5s",
            retryable = true,
        )
    }

    /**
     * 发布按钮点完之后的成功判定（v2，按 2026 版 XHS 实测重写）。
     *
     * 旧版只看 toast / 个人页文本里的 note_id —— 实测发布后 XHS 直接回首页，
     * 两处都没有 id，导致「发出去了却报 UPLOAD_FAILED」的误判。
     *
     * 新流程：
     *  1. 30s 内盯失败 toast（违规/限流）；编辑器界面（标题栏）消失 → 视为平台已受理
     *  2. 去「我」tab，校验第一张笔记卡片标题与刚发布的一致 → 确认成功
     *  3. 尽力取 note_id：进详情页 → 分享 → 复制链接 → 把本 App 拉到前台读剪贴板
     *     （Android 10+ 只有前台 App 能读）→ xhslink 短链走 HTTP 解析出 24 位 id
     *  4. id 取不到不判失败（笔记确实上线了），返回 noteId=null 由后端容忍
     */
    private suspend fun waitPublishSuccess(p: PublishParams): ApiResult<PublishOutcome> {
        val deadline = System.currentTimeMillis() + PUBLISH_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            Jitter.sleep(500L)
            scanForFailureToast()?.let { (code, msg) ->
                return ApiResult.Err(code, msg, retryable = code == ErrorCode.RATE_LIMITED)
            }
            // 编辑器关闭（标题输入框消失）→ 平台已受理，进入个人页确认
            if (driver.findFirst(XhsSelectors.EDIT_TITLE) == null) {
                Jitter.sleep(1_000L)
                break
            }
        }
        if (driver.findFirst(XhsSelectors.EDIT_TITLE) != null) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "editor still open ${PUBLISH_TIMEOUT_MS / 1000}s after publish tap",
                retryable = true,
            )
        }

        // ---- 个人页确认 + 取 id（尽力而为）----
        when (val r = actions.tapWhenReady(XhsSelectors.TAB_PROFILE, timeoutMs = 8_000L)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return err("tap profile tab after publish", r)
        }
        Jitter.sleep(1_500L)

        val card = findFirstProfileNoteCard()
            ?: return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "published note not found on profile",
                retryable = true,
            )
        val desc = card.contentDesc.orEmpty()
        if (!desc.contains(p.title.take(TITLE_MATCH_CHARS))) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "profile top note title mismatch: $desc",
                retryable = true,
            )
        }

        val noteId = scrapeNoteIdFromDetail(card)
        return ApiResult.Ok(
            PublishOutcome(
                noteId = noteId,
                url = noteId?.let { "https://www.xiaohongshu.com/explore/$it" },
            ),
        )
    }

    /** 「我」tab 第一张笔记卡片：content-desc 以「笔记」开头的可点击节点。 */
    private fun findFirstProfileNoteCard(): com.matrix.companion.accessibility.UiNode? =
        driver
            .findAll(PROFILE_CARD_CANDIDATE)
            .filter {
                it.isClickable &&
                    (it.contentDesc.orEmpty().startsWith("笔记") ||
                        it.contentDesc.orEmpty().startsWith("视频"))
            }
            .sortedWith(compareBy({ it.boundsInScreen.top }, { it.boundsInScreen.left }))
            .firstOrNull()

    /**
     * 进笔记详情 → 分享 → 复制链接 → 拉本 App 到前台读剪贴板 → 解析短链。
     * 任何一步失败都返回 null（不阻塞「发布成功」结论）。
     */
    private suspend fun scrapeNoteIdFromDetail(
        card: com.matrix.companion.accessibility.UiNode,
    ): String? {
        return runCatching {
            // 1) 打开详情页
            when (driver.tap(card.centerX, card.centerY)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return null
            }
            Jitter.sleep(2_000L)

            // 2) 分享按钮（无 text/desc，实测固定坐标）→ 复制链接
            when (driver.tap(SHARE_BUTTON_X, SHARE_BUTTON_Y)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return null
            }
            Jitter.sleep(1_200L)
            when (driver.tap(COPY_LINK_X, COPY_LINK_Y)) {
                is ApiResult.Ok -> Unit
                is ApiResult.Err -> return null
            }
            Jitter.sleep(800L)

            // 3) 拉本 App 到前台读剪贴板（A10+ 后台读不到）
            val home = appContext.packageManager
                .getLaunchIntentForPackage(appContext.packageName)
                ?.apply { addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK) }
                ?: return null
            appContext.startActivity(home)
            Jitter.sleep(1_000L)
            val cm = appContext.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clipText = cm.primaryClip
                ?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)
                ?.text
                ?.toString()
                ?: return null

            // 4) 剪贴板里直接有完整链接最好；否则解析 xhslink 短链
            NoteUrlExtractor.extractFromText(clipText)?.let { return it }
            val shortLink = XHS_SHORT_LINK_REGEX.find(clipText)?.value ?: return null
            resolveShortLink(shortLink)
        }.getOrElse {
            Logx.w("scrapeNoteIdFromDetail failed: ${it.message}")
            null
        }
    }

    /** xhslink 短链 → 跟随重定向 → 从最终 URL 抠 note_id。 */
    private suspend fun resolveShortLink(shortLink: String): String? =
        runCatching {
            val resp = http.get(shortLink)
            NoteUrlExtractor.extractFromText(resp.call.request.url.toString())
        }.getOrElse {
            Logx.w("resolveShortLink failed for $shortLink: ${it.message}")
            null
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

        /** 小红书相册选择器网格起始 Y（顶部「全部/视频/照片」tab 栏之下）。 */
        private const val ALBUM_GRID_TOP_Y = 350

        /** 相册照片格子候选：无稳定 id，只能按类名圈选再由调用方过滤。 */
        private val ALBUM_PHOTO_CANDIDATE =
            Selector.XPath("//*[@class='android.widget.ImageView']")

        /** 「我」tab 笔记卡片候选（FrameLayout + 自定义过滤）。 */
        private val PROFILE_CARD_CANDIDATE =
            Selector.XPath("//*[@class='android.widget.FrameLayout']")

        /** 标题校验长度：卡片 desc 含标题前缀即认为同一篇。 */
        private const val TITLE_MATCH_CHARS = 8

        /** 详情页右上角分享按钮（无 text/desc，1080x2400 实测坐标）。 */
        private const val SHARE_BUTTON_X = 993
        private const val SHARE_BUTTON_Y = 194

        /** 分享面板「复制链接」（节点 bounds 为 0，实测坐标）。 */
        private const val COPY_LINK_X = 330
        private const val COPY_LINK_Y = 2160

        private val XHS_SHORT_LINK_REGEX = Regex("""https?://xhslink\.com/\S+""")

        /** 短链解析用的轻量 client（跟随重定向）。 */
        private val http by lazy { HttpClient(CIO) }

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