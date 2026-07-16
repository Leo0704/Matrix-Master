package com.matrix.companion.accessibility

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Decouples selectors from the wire format. The OpenAPI's TapBySelector
// maps 1:1 onto these subtypes via @SerialName.
//
// Important: AnyOf and orFallback are wire-incompatible (NOT @Serializable
// individually). They are constructed in code only — the API endpoint still
// receives a single typed Selector from the JSON payload.
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

/**
 * Try the [primary] selector first; fall back to [fallbacks] in order if
 * the primary doesn't match. This is the in-memory "or" composition — it
 * is NOT part of the wire format. Construct it via [Selector.orFallback]
 * or [Selector.anyOf].
 */
data class AnyOf(
    val primary: Selector,
    val fallbacks: List<Selector>,
) : Selector() {
    init {
        require(fallbacks.isNotEmpty()) { "AnyOf needs at least one fallback" }
    }

    override fun matches(node: UiNode): Boolean =
        primary.matches(node) || fallbacks.any { it.matches(node) }

    /** Sub-selector accessor for diagnostics / structured error messages. */
    fun all(): List<Selector> = listOf(primary) + fallbacks
}

/** Fluent fallback chain. `a.orFallback(b, c)` → match a, else b, else c. */
fun Selector.orFallback(vararg fallbacks: Selector): Selector =
    if (fallbacks.isEmpty()) this else AnyOf(this, fallbacks.toList())

/** Alternative explicit factory: `Selector.anyOf(a, b, c)`. */
fun Selector.Companion.anyOf(primary: Selector, vararg fallbacks: Selector): Selector =
    AnyOf(primary, fallbacks.toList())

/**
 * Convenience: a resource-id selector with optional fallbacks.
 * `Selector.fromResourceId("com.xhs:id/foo")` → bare ResourceId.
 * `Selector.fromResourceId("com.xhs:id/foo", Selector.Text("foo"))` → AnyOf.
 */
fun Selector.Companion.fromResourceId(
    id: String,
    vararg fallbacks: Selector,
): Selector {
    val primary = Selector.ResourceId(id)
    return if (fallbacks.isEmpty()) primary else AnyOf(primary, fallbacks.toList())
}