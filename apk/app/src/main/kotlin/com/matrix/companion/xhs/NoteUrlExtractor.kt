package com.matrix.companion.xhs

/**
 * Extracts a 24-hex XHS note ID from various places it shows up in the
 * UI: URL fragments, share-link text, accessibility node text/content-desc.
 *
 * XHS note IDs are 24-char lowercase hex (`[a-f0-9]{24}`). They appear in:
 * - `https://www.xiaohongshu.com/explore/{id}`
 * - `https://www.xiaohongshu.com/discovery/item/{id}`
 * - `xhsdiscover://note/{id}` deep link
 * - `http://xhslink.com/{short}` share links (we can't resolve these
 *   without a network round-trip, so we ignore them)
 *
 * Used by:
 * - [XhsPublisher.waitPublishSuccess] — after publish, scrape the active
 *   window text and pull the ID for the just-created note.
 * - [XhsInteractor.run] — when the master passes a note_id, we still
 *   verify it matches the note currently open before performing the action.
 */
object NoteUrlExtractor {

    /** Match 24 lowercase hex chars. Anchored to a non-hex boundary via
     *  capture-group boundary inspection in [extractFromText]. */
    private val NOTE_ID_REGEX = Regex("[a-f0-9]{24}")

    /** Common XHS URL prefixes that contain a note ID. */
    private val URL_PATTERNS = listOf(
        // xiaohongshu.com/explore/{id}
        Regex("""xiaohongshu\.com/explore/([a-f0-9]{24})"""),
        // xiaohongshu.com/discovery/item/{id}
        Regex("""xiaohongshu\.com/discovery/item/([a-f0-9]{24})"""),
        // xhsdiscover://note/{id}
        Regex("""xhsdiscover://note/([a-f0-9]{24})"""),
    )

    /**
     * Pull the first plausible note ID out of [text]. Returns null if no
     * 24-hex candidate is found.
     *
     * Order:
     *  1. URL patterns first (less ambiguous)
     *  2. Bare 24-hex fallback
     */
    fun extractFromText(text: String): String? {
        if (text.isBlank()) return null
        for (re in URL_PATTERNS) {
            re.find(text)?.groupValues?.get(1)?.let { return it }
        }
        // Bare 24-hex. Be conservative: require a non-hex boundary on both sides
        // to avoid matching inside longer hex strings (rare in XHS UI text but
        // it can happen in v1/v2 token strings).
        val matches = NOTE_ID_REGEX.findAll(text)
        for (m in matches) {
            val startOk = m.range.first == 0 ||
                !text[m.range.first - 1].isHex()
            val endOk = m.range.last + 1 == text.length ||
                !text[m.range.last + 1].isHex()
            if (startOk && endOk) return m.value
        }
        return null
    }

    private fun Char.isHex(): Boolean = this in '0'..'9' || this in 'a'..'f' || this in 'A'..'F'
}