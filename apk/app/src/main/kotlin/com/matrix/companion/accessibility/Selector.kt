package com.matrix.companion.accessibility

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Decouples selectors from the wire format. The OpenAPI's TapBySelector
// maps 1:1 onto these subtypes via @SerialName.
@Serializable
sealed class Selector {

    abstract fun matches(node: UiNode): Boolean

    @Serializable
    @SerialName("resource_id")
    data class ResourceId(val value: String) : Selector() {
        override fun matches(node: UiNode): Boolean = node.resourceId == value
    }

    @Serializable
    @SerialName("content_desc")
    data class ContentDesc(val value: String) : Selector() {
        override fun matches(node: UiNode): Boolean = node.contentDesc == value
    }

    @Serializable
    @SerialName("text")
    data class Text(val value: String, val exact: Boolean = true) : Selector() {
        override fun matches(node: UiNode): Boolean {
            val nodeText = node.text ?: return false
            return if (exact) nodeText == value else nodeText.contains(value, ignoreCase = true)
        }
    }

    @Serializable
    @SerialName("xpath")
    data class XPath(val expression: String) : Selector() {
        // Minimal XPath subset:  //*[@resource-id='x']  //*[@text='x']  //*[@content-desc='x']  //*[@class='x']
        override fun matches(node: UiNode): Boolean {
            val expr = expression.trim()
            if (!expr.startsWith("//")) return false
            val attrRegex = Regex("""@([\w-]+)=['"]([^'"]+)['"]""")
            val matches = attrRegex.findAll(expr).toList()
            if (matches.isEmpty()) return false
            return matches.all { m ->
                val attr = m.groupValues[1]
                val expected = m.groupValues[2]
                when (attr) {
                    "resource-id" -> node.resourceId == expected
                    "text" -> node.text == expected
                    "content-desc" -> node.contentDesc == expected
                    "class" -> node.className == expected
                    else -> false
                }
            }
        }
    }
}
