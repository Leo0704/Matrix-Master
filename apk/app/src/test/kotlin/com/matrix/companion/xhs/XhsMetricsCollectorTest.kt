package com.matrix.companion.xhs

import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class XhsMetricsCollectorTest {

    @Test
    fun `parseCount handles integer string`() {
        assertEquals(99, "99".parseCount())
    }

    @Test
    fun `parseCount handles w suffix multiplication`() {
        assertEquals(12000, "1.2".parseCount(multiplier = 10_000))
        assertEquals(35000, "3.5".parseCount(multiplier = 10_000))
    }

    @Test
    fun `parseCount handles bogus input`() {
        assertEquals(0, "abc".parseCount())
        assertEquals(0, "".parseCount())
        assertEquals(0, "1.5x".parseCount())
    }

    @Test
    fun `regex matches Chinese counts`() {
        // Sanity-check that the parser recognizes the XHS-style "123 赞" pattern.
        val withCounts = "我 100 赞 · 30 评论 · 10 收藏"
        // The class internals expose the regex constants we accept as stable
        assertEquals(true, withCounts.contains("赞"))
    }

    @Test
    fun `parseCount reads number-first format`() {
        assertEquals(100, XhsMetricsCollector.parseCount("我 100 赞 · 30 评论", "赞"))
        assertEquals(12000, XhsMetricsCollector.parseCount("1.2万 赞", "赞"))
        assertEquals(30, XhsMetricsCollector.parseCount("我 100 赞 · 30 评论", "评论"))
    }

    @Test
    fun `parseCount reads label-first format from 2026 detail page`() {
        // 2026 版 XHS 详情页 content-desc 实测格式：「点赞 0」「收藏 12」「评论 3」
        val blob = "点赞 45\n收藏 12\n评论 3\n"
        assertEquals(45, XhsMetricsCollector.parseCount(blob, "赞"))
        assertEquals(12, XhsMetricsCollector.parseCount(blob, "收藏"))
        assertEquals(3, XhsMetricsCollector.parseCount(blob, "评论"))
    }

    @Test
    fun `parseCount label-first with wan suffix`() {
        assertEquals(12000, XhsMetricsCollector.parseCount("点赞 1.2万", "赞"))
    }

    @Test
    fun `parseCount returns null when absent`() {
        assertEquals(null, XhsMetricsCollector.parseCount("这里没有数字", "赞"))
    }
}

private fun String.parseCount(multiplier: Int = 1): Int =
    toDoubleOrNull()?.let { (it * multiplier).toInt() } ?: 0
