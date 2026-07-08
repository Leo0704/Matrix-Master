package com.matrix.companion.accessibility

import android.graphics.Rect
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class SelectorTest {

    private fun node(
        rid: String? = null,
        cd: String? = null,
        text: String? = null,
        cls: String? = null,
        pkg: String? = "com.xingin.xhs",
    ) = UiNode(
        resourceId = rid,
        className = cls,
        contentDesc = cd,
        text = text,
        packageName = pkg,
        isClickable = false,
        isFocusable = false,
        isEditable = false,
        isScrollable = false,
        boundsInScreen = Rect(0, 0, 100, 100),
    )

    @Test
    fun `resource_id selector matches exact id`() {
        assertTrue(Selector.ResourceId("com.xhs:id/btn").matches(node(rid = "com.xhs:id/btn")))
        assertFalse(Selector.ResourceId("com.xhs:id/btn").matches(node(rid = "com.xhs:id/other")))
    }

    @Test
    fun `content_desc selector matches text`() {
        assertTrue(Selector.ContentDesc("关闭").matches(node(cd = "关闭")))
        assertFalse(Selector.ContentDesc("关闭").matches(node(cd = "开启")))
    }

    @Test
    fun `text selector exact and contains`() {
        assertTrue(Selector.Text("发布", exact = true).matches(node(text = "发布")))
        assertFalse(Selector.Text("发布", exact = true).matches(node(text = "发布笔记")))
        assertTrue(Selector.Text("发布", exact = false).matches(node(text = "发布笔记")))
    }

    @Test
    fun `xpath selector handles 3 attribute forms`() {
        val x = Selector.XPath("//*[@resource-id='com.xhs:id/x']")
        assertTrue(x.matches(node(rid = "com.xhs:id/x")))
        assertFalse(x.matches(node(rid = "com.xhs:id/y")))

        val xc = Selector.XPath("//*[@content-desc='Close']")
        assertTrue(xc.matches(node(cd = "Close")))

        val xt = Selector.XPath("//*[@text='Hi']")
        assertTrue(xt.matches(node(text = "Hi")))
        assertFalse(xt.matches(node(text = "No")))
    }

    @Test
    fun `nulls never throw and return false`() {
        val sel = Selector.ResourceId("a")
        assertFalse(sel.matches(node(rid = null)))
    }
}
