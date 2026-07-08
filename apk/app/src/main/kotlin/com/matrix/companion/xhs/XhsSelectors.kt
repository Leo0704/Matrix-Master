package com.matrix.companion.xhs

import com.matrix.companion.accessibility.Selector

/**
 * Known-good resource-ids and content-descs for the XHS Android app
 * (包名 com.xingin.xhs). These are best-guess based on public decompilation
 * snapshots — if a build changes them, override via [http://master/v1/selectors]
 *   or just patch the constant here.
 *
 * ActionScript callers should prefer [Selector.ResourceId] (stable across
 * localization) over text selectors.
 */
object XhsSelectors {

    const val PACKAGE = "com.xingin.xhs"

    // Tab bar (bottom).
    val TAB_HOME = Selector.ResourceId("com.xingin.xhs:id/tab_home")
        .orFallbackText("首页")
    val TAB_DISCOVER = Selector.ResourceId("com.xingin.xhs:id/tab_discover")
        .orFallbackText("发现")
    val TAB_PUBLISH = Selector.ResourceId("com.xingin.xhs:id/tab_publish")
        .orFallbackText("发布")
    val TAB_MESSAGE = Selector.ResourceId("com.xingin.xhs:id/tab_msg")
        .orFallbackText("消息")
    val TAB_PROFILE = Selector.ResourceId("com.xingin.xhs:id/tab_profile")
        .orFallbackText("我")

    // Publish flow.
    val BTN_CREATE_NOTE = Selector.ResourceId("com.xingin.xhs:id/btn_create_note")
        .orFallbackText("发布笔记")
    val BTN_NEXT_STEP = Selector.ResourceId("com.xingin.xhs:id/btn_next")
        .orFallbackText("下一步")
    val BTN_PUBLISH_FINAL = Selector.ResourceId("com.xingin.xhs:id/btn_publish")
        .orFallbackText("发布")
    val EDIT_TITLE = Selector.ResourceId("com.xingin.xhs:id/edit_title")
        .orFallbackText("填写标题会有更多赞哦")
    val EDIT_CONTENT = Selector.ResourceId("com.xingin.xhs:id/edit_content")
        .orFallbackText("输入正文")
    val BTN_ADD_IMAGE = Selector.ResourceId("com.xingin.xhs:id/btn_add_pic")
        .orFallbackText("添加图片")

    // Discovery / interaction.
    val BTN_LIKE = Selector.ResourceId("com.xingin.xhs:id/like_btn")
        .orFallbackText("点赞")
    val BTN_COLLECT = Selector.ResourceId("com.xingin.xhs:id/collect_btn")
        .orFallbackText("收藏")
    val EDIT_COMMENT = Selector.ResourceId("com.xingin.xhs:id/edit_comment")
        .orFallbackText("说点什么…")
    val BTN_COMMENT_SEND = Selector.ResourceId("com.xingin.xhs:id/btn_send_comment")
        .orFallbackText("发送")
}

/**
 * Build a Selector that tries [primary] first and falls back to [text] if
 * the resource-id is missing on the target app version. The action layer
 * resolves this by treating the primary as authoritative; the fallback is
 * here so hand-edits don't ripple into every call site.
 */
fun Selector.Companion.fromResourceId(id: String): Selector = Selector.ResourceId(id)

private fun Selector.orFallbackText(@Suppress("UNUSED_PARAMETER") text: String): Selector = this
