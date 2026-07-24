package com.matrix.companion.xhs

import com.matrix.companion.accessibility.Selector
import com.matrix.companion.accessibility.fromResourceId

/**
 * Known-good resource-ids, text and content-desc selectors for the XHS
 * Android app (`com.xingin.xhs`).
 *
 * Sourcing:
 * - Public decompilation snapshots — best-guess, may shift across XHS
 *   builds. When a resource-id disappears, the chained fallback (text /
 *   content-desc) keeps things working until a human can patch the
 *   primary.
 * - All `fromResourceId(...)` calls below use the vararg overload from
 *   `Selector.Companion`, which composes into [AnyOf] for genuine
 *   primary-then-fallback matching. The old `orFallbackText` shim that
 *   silently no-op'd has been removed — see git history for the dead
 *   implementation.
 *
 * To override selectors remotely without an app release, the master
 * controller can serve an updated selector set over a future
 * `/v1/selectors` endpoint (not yet implemented — Phase 2 roadmap).
 */
object XhsSelectors {

    const val PACKAGE = "com.xingin.xhs"

    // Tab bar (bottom).
    // XHS obfuscates resource-IDs to 0_resource_name_obfuscated, so
    // ContentDesc is the most reliable fallback.
    val TAB_HOME = Selector.fromResourceId(
        "com.xingin.xhs:id/tab_home",
        Selector.Text("首页"),
        Selector.ContentDesc("首页"),
    )
    val TAB_DISCOVER = Selector.fromResourceId(
        "com.xingin.xhs:id/tab_discover",
        Selector.Text("发现"),
        Selector.ContentDesc("发现"),
    )
    val TAB_PUBLISH = Selector.fromResourceId(
        "com.xingin.xhs:id/tab_publish",
        Selector.Text("发布"),
        Selector.ContentDesc("发布"),
    )
    val TAB_MESSAGE = Selector.fromResourceId(
        "com.xingin.xhs:id/tab_msg",
        Selector.Text("消息"),
        Selector.ContentDesc("消息"),
    )
    val TAB_PROFILE = Selector.fromResourceId(
        "com.xingin.xhs:id/tab_profile",
        Selector.Text("我"),
        Selector.ContentDesc("我"),
    )

    // Publish flow.
    val BTN_CREATE_NOTE = Selector.fromResourceId(
        "com.xingin.xhs:id/btn_create_note",
        Selector.Text("发布笔记"),
        Selector.ContentDesc("发布笔记"),
    )
    // 2026 版小红书：点「+」后弹出类型选择底部弹窗（从相册选择 / 写文字 / 相机）
    val BTN_PICK_FROM_ALBUM = Selector.fromResourceId(
        "com.xingin.xhs:id/tv_pick_from_album",
        Selector.Text("从相册选择"),
        Selector.ContentDesc("从相册选择"),
    )
    val BTN_TEXT_NOTE = Selector.fromResourceId(
        "com.xingin.xhs:id/tv_text_note",
        Selector.Text("写文字"),
        Selector.ContentDesc("写文字"),
    )
    val BTN_NEXT_STEP = Selector.fromResourceId(
        "com.xingin.xhs:id/btn_next",
        Selector.Text("下一步"),
        Selector.ContentDesc("下一步"),
    )
    val BTN_PUBLISH_FINAL = Selector.fromResourceId(
        "com.xingin.xhs:id/btn_publish",
        Selector.Text("发布"),
        Selector.Text("发布笔记"),
        Selector.ContentDesc("发布"),
    )
    val EDIT_TITLE = Selector.fromResourceId(
        "com.xingin.xhs:id/edit_title",
        Selector.Text("添加标题"),
        Selector.Text("填写标题会有更多赞哦"),
        Selector.ContentDesc("添加标题"),
        Selector.ContentDesc("填写标题会有更多赞哦"),
    )
    val EDIT_CONTENT = Selector.fromResourceId(
        "com.xingin.xhs:id/edit_content",
        Selector.Text("添加正文"),
        Selector.Text("输入正文"),
        Selector.ContentDesc("添加正文"),
        Selector.ContentDesc("输入正文"),
    )
    val BTN_ADD_IMAGE = Selector.fromResourceId(
        "com.xingin.xhs:id/btn_add_pic",
        Selector.Text("图片"),
        Selector.ContentDesc("添加图片"),
    )

    // Note-detail page (used by XhsNoteOpener + interact + metrics).
    // The detail page's like button is the canonical "we're on a note page" indicator.
    val NOTE_DETAIL_LIKE_BTN = Selector.fromResourceId(
        "com.xingin.xhs:id/like_btn",
        Selector.Text("点赞"),
        Selector.ContentDesc("点赞"),
    )
    // WebView 落地页上的「打开 APP 查看」按钮（https explore 链接 → 原生详情页的中转）
    val BTN_OPEN_IN_APP = Selector.fromResourceId(
        "com.xingin.xhs:id/btn_open_in_app",
        Selector.Text("打开 APP 查看"),
        Selector.Text("App内打开"),
        Selector.ContentDesc("打开 APP 查看"),
    )
    val BTN_LIKE = NOTE_DETAIL_LIKE_BTN
    val BTN_COLLECT = Selector.fromResourceId(
        "com.xingin.xhs:id/collect_btn",
        Selector.Text("收藏"),
        Selector.ContentDesc("收藏"),
    )
    val EDIT_COMMENT = Selector.fromResourceId(
        "com.xingin.xhs:id/edit_comment",
        Selector.Text("让大家听到你的声音"),
        Selector.Text("说点什么…"),
    )
    val BTN_COMMENT_SEND = Selector.fromResourceId(
        "com.xingin.xhs:id/btn_send_comment",
        Selector.Text("发送"),
    )
    val BTN_FOLLOW = Selector.fromResourceId(
        "com.xingin.xhs:id/follow_btn",
        Selector.Text("关注"),
        Selector.ContentDesc("关注"),
    )

    // Post-publish: snackbar / toast + "我" tab indicators.
    val TOAST_PUBLISH_SUCCESS = Selector.fromResourceId(
        "com.xingin.xhs:id/toast_text",
        Selector.Text("发布成功"),
        Selector.Text("笔记已发布"),
    )
    val NOTE_CARD_FIRST = Selector.fromResourceId(
        "com.xingin.xhs:id/note_item",
        Selector.Text("笔记"),
    )

    // System image picker — vendor packages differ across OEMs.
    // Try the open-source photo picker first, then legacy Documents UI, then
    // intent resolver's "Just once" button (covers Chinese OEMs).
    val SYSTEM_PICKER_FIRST_PHOTO = Selector.fromResourceId(
        "com.android.providers.media.module:id/icon_thumbnail",
        Selector.fromResourceId(
            "com.google.android.providers.media.module:id/icon_thumbnail",
        ),
        Selector.fromResourceId(
            "com.android.documentsui:id/icon_thumbnail",
        ),
    )
    val SYSTEM_PICKER_DONE = Selector.fromResourceId(
        "com.android.intentresolver:id/button_once",
        Selector.Text("完成"),
        Selector.Text("确定"),
        Selector.Text("添加"),
    )
    val SYSTEM_PICKER_ALBUM_TAB = Selector.fromResourceId(
        "com.android.providers.media.module:id/tab_photos",
        Selector.Text("相册"),
        Selector.Text("图库"),
    )
}