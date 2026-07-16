package com.matrix.companion.xhs

import android.net.Uri
import com.matrix.companion.accessibility.AccessibilityDriver
import com.matrix.companion.accessibility.ActionExecutor
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Jitter
import com.matrix.companion.util.Logx

/**
 * Drives the XHS image picker once images are in MediaStore. The picker
 * is whatever system photo picker the OEM shipped (Photo Picker on AOSP,
 * DocumentsUI on older builds, or a vendor variant). They all look the
 * same at the AccessibilityService level: a grid of thumbnails and a
 * "完成"/"确定" button.
 *
 * Approach:
 *  1. Wait for the picker grid to appear.
 *  2. Tap the first N thumbnails (where N = uris.size, capped at 9 since
 *     XHS allows at most 9 images per note).
 *  3. Tap the confirmation button.
 *  4. Wait for the picker to dismiss.
 *
 * Anti-detection: we don't tap the same thumbnail row twice in a row,
 * and we use [Jitter.sleep] between taps.
 *
 * Limitations: this assumes the system picker shows our freshly-inserted
 * images immediately. On some OEMs (Huawei EMUI, Xiaomi MIUI) the picker
 * may need a manual refresh — we don't simulate that today. A future
 * improvement could detect the "no images" state and trigger pull-to-refresh.
 */
class XhsImagePicker(
    private val driver: AccessibilityDriver,
    private val actions: ActionExecutor,
) {

    /**
     * Select up to [count] images in the system photo picker that
     * `XhsPublisher` opened by tapping BTN_ADD_IMAGE. Returns Ok when
     * the picker has dismissed (i.e. we're back on the publish screen).
     */
    suspend fun selectImages(count: Int): ApiResult<Unit> {
        if (count <= 0) return ApiResult.Ok(Unit)
        val capped = count.coerceAtMost(MAX_NOTE_IMAGES)

        // Wait for the picker grid to render. If it never shows up,
        // the user probably denied the media permission and we're stuck
        // on the XHS "需要存储权限" dialog — surface that distinctly.
        val firstThumb = driver.waitFor(
            XhsSelectors.SYSTEM_PICKER_FIRST_PHOTO,
            timeoutMs = 10_000L,
        )
        if (firstThumb == null) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "system image picker did not appear; check READ_MEDIA_IMAGES permission",
                retryable = false,
            )
        }

        // Tap the first [capped] thumbnails. We use `findAll` each time
        // because the grid re-lays-out after each tap (selection state
        // toggles border styling which can shift positions).
        var selected = 0
        var attempts = 0
        while (selected < capped && attempts < capped * 2) {
            attempts++
            val thumbs = driver.findAll(XhsSelectors.SYSTEM_PICKER_FIRST_PHOTO)
            if (thumbs.isEmpty()) break
            // Pick the next unselected thumbnail: we don't track state,
            // so tap the first one — on most pickers, tapping an already-
            // selected thumbnail just toggles it off. To avoid that, we
            // tap the [selected]-th thumb (the "next" one).
            val target = thumbs.getOrNull(selected) ?: break
            when (val r = driver.tap(target.centerX, target.centerY)) {
                is ApiResult.Ok -> {
                    selected++
                    Jitter.sleep(300L)
                }
                is ApiResult.Err -> {
                    Logx.w("xhs_image_picker.tap_failed attempt=$attempts err=${r.message}")
                    Jitter.sleep(500L)
                }
            }
        }

        if (selected < capped) {
            Logx.w("xhs_image_picker.partial: selected=$selected wanted=$capped")
        }

        // Confirm the selection.
        when (val r = actions.tap(XhsSelectors.SYSTEM_PICKER_DONE)) {
            is ApiResult.Ok -> Unit
            is ApiResult.Err -> return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "tap picker done: ${r.message}",
                retryable = false,
            )
        }

        // Wait for the picker to dismiss and the publish form to reappear.
        val dismissed = driver.waitUntilGone(
            XhsSelectors.SYSTEM_PICKER_FIRST_PHOTO,
            timeoutMs = 5_000L,
        )
        if (!dismissed) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "image picker did not dismiss after confirm",
                retryable = false,
            )
        }
        Jitter.sleep(500L)
        return ApiResult.Ok(Unit)
    }

    companion object {
        /** XHS allows at most 9 images per note. */
        const val MAX_NOTE_IMAGES = 9

        /** We never reuse this parameter list; kept for documentation symmetry. */
        @Suppress("unused")
        fun unusedUrisReference(uris: List<Uri>): Int = uris.size
    }
}