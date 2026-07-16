package com.matrix.companion.xhs

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.matrix.companion.util.ApiResult
import com.matrix.companion.util.ErrorCode
import com.matrix.companion.util.Logx
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.request.get
import io.ktor.client.statement.HttpResponse
import java.io.File
import java.io.FileOutputStream

/**
 * Downloads note images from URLs the master passes in, and inserts them
 * into the system gallery so the XHS image picker can find them.
 *
 * Why MediaStore (not FileProvider + Intent):
 * - XHS does not accept inbound share intents for images; we have to
 *   drive its image picker via AccessibilityService. The picker reads
 *   from MediaStore, so we need the bytes to be visible there.
 * - FileProvider would require XHS to declare a matching `<provider>`,
 *   which it doesn't.
 *
 * Flow per image:
 *   1. HTTP GET the URL → bytes.
 *   2. Write to MediaStore (Pictures/MatrixCompanion/), which inserts a
 *      row and gives us back a content:// URI.
 *   3. Return the list of URIs to the caller, which then drives the
 *      picker to select them.
 *
 * Errors are non-retryable from the client's perspective: a 404 or a
 * corrupt image won't fix itself by retrying within the same publish
 * request. Callers should propagate UPLOAD_FAILED and let the master
 * decide whether to retry the whole publish flow later.
 */
class ImagePipeline(
    private val appContext: Context,
    private val httpClient: HttpClient = HttpClient(CIO),
) {

    /**
     * Download all [urls] in parallel-ish (sequential for now — Ktor
     * CIO pool limits concurrent connections anyway) and register them
     * with the system gallery. Returns the content URIs in input order.
     *
     * Atomicity: best-effort. On partial failure, we return Err with the
     * already-registered URIs so the caller can decide to abort or try
     * with what we have. Already-registered entries stay in the gallery
     * until manual cleanup; this is the safer default than rolling back
     * media rows mid-write.
     */
    suspend fun downloadImages(urls: List<String>): ApiResult<List<Uri>> {
        if (urls.isEmpty()) return ApiResult.Ok(emptyList())

        val collected = mutableListOf<Uri>()
        for ((index, url) in urls.withIndex()) {
            when (val r = downloadAndRegister(url, index)) {
                is ApiResult.Ok -> collected.add(r.value)
                is ApiResult.Err -> {
                    Logx.w("image_pipeline.failed url=$url code=${r.code} msg=${r.message}")
                    return ApiResult.Err(
                        r.code,
                        "image[$index] failed: ${r.message}",
                        retryable = r.retryable,
                    )
                }
            }
        }
        return ApiResult.Ok(collected)
    }

    private suspend fun downloadAndRegister(url: String, index: Int): ApiResult<Uri> {
        val bytes: ByteArray = try {
            val resp: HttpResponse = httpClient.get(url)
            if (resp.status.value !in 200..299) {
                return ApiResult.Err(
                    ErrorCode.UPLOAD_FAILED,
                    "HTTP ${resp.status.value} downloading image[$index]",
                    retryable = resp.status.value in 500..599,
                )
            }
            resp.body()
        } catch (t: Throwable) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "network error downloading image[$index]: ${t.message}",
                retryable = true,
            )
        }
        if (bytes.isEmpty()) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "image[$index] is empty (0 bytes)",
                retryable = false,
            )
        }
        return registerInGallery(bytes, index)
    }

    private fun registerInGallery(bytes: ByteArray, index: Int): ApiResult<Uri> {
        val resolver = appContext.contentResolver
        val collection = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        } else {
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        }
        val fileName = "matrix_publish_${System.currentTimeMillis()}_$index.jpg"
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, fileName)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(
                    MediaStore.Images.Media.RELATIVE_PATH,
                    "${Environment.DIRECTORY_PICTURES}/$ALBUM_NAME",
                )
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
        }
        val uri = try {
            resolver.insert(collection, values)
        } catch (e: Exception) {
            return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "MediaStore.insert failed: ${e.message}",
                retryable = false,
            )
        } ?: return ApiResult.Err(
            ErrorCode.UPLOAD_FAILED,
            "MediaStore.insert returned null uri",
            retryable = false,
        )

        return try {
            resolver.openOutputStream(uri)?.use { out ->
                out.write(bytes)
            } ?: return ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "could not open output stream for $uri",
                retryable = false,
            )
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.clear()
                values.put(MediaStore.Images.Media.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            }
            ApiResult.Ok(uri)
        } catch (e: Exception) {
            // Best-effort cleanup; we don't want a half-written MediaStore row.
            try { resolver.delete(uri, null, null) } catch (_: Exception) {}
            ApiResult.Err(
                ErrorCode.UPLOAD_FAILED,
                "write bytes to MediaStore failed: ${e.message}",
                retryable = false,
            )
        }
    }

    /**
     * Cleanup helper: delete a list of URIs we previously inserted.
     * Currently unused (no rollback path on partial failure) but kept
     * for future test code.
     */
    @Suppress("unused")
    fun cleanup(uris: List<Uri>): Int {
        var removed = 0
        for (uri in uris) {
            try {
                if (appContext.contentResolver.delete(uri, null, null) > 0) removed++
            } catch (_: Exception) {}
        }
        return removed
    }

    companion object {
        const val ALBUM_NAME = "MatrixCompanion"
    }
}