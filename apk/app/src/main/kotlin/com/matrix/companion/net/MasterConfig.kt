package com.matrix.companion.net

import android.content.Context
import android.content.SharedPreferences
import com.matrix.companion.BuildConfig
import com.matrix.companion.util.Logx

/**
 * 运行时 Master URL 配置。
 *
 * 优先级：
 * 1. 本地 SharedPreferences 里用户/配对流程写入的值（支持服务端下发或手动切换）
 * 2. BuildConfig.MASTER_URL（编译时注入，如 -PmatrixMasterUrl=...）
 *
 * 这样生产环境不需要为每个域名重编 APK，调试时也可以临时切换。
 */
object MasterConfig {

    private const val PREFS_NAME = "matrix_companion_config"
    private const val KEY_MASTER_URL = "master_url"

    @Volatile
    private var cached: String? = null

    fun get(context: Context): String {
        cached?.let { return it }
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val stored = prefs.getString(KEY_MASTER_URL, null)
        val url = normalize(stored ?: BuildConfig.MASTER_URL)
        cached = url
        return url
    }

    fun set(context: Context, url: String) {
        val normalized = normalize(url)
        cached = normalized
        prefs(context).edit().putString(KEY_MASTER_URL, normalized).apply()
        Logx.i("MasterConfig.set: $normalized")
    }

    fun clear(context: Context) {
        cached = null
        prefs(context).edit().remove(KEY_MASTER_URL).apply()
        Logx.i("MasterConfig.cleared")
    }

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private fun normalize(url: String): String {
        var out = url.trim()
        while (out.endsWith("/")) {
            out = out.dropLast(1)
        }
        return out
    }
}
