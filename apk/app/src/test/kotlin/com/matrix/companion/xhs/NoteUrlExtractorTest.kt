package com.matrix.companion.xhs

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Pin down the URL → note-id extractor. If this regresses, [XhsPublisher]
 * publishes succeed but the master sees platform_note_id=null and the
 * whole publish is rejected downstream.
 */
class NoteUrlExtractorTest {

    private val sampleId = "5f8b9c2e1a0b4d0001234567"

    @Test
    fun `extracts id from explore URL`() {
        val text = "【小红书】救命！这杯奶茶太好喝了 http://www.xiaohongshu.com/explore/$sampleId"
        assertEquals(sampleId, NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `extracts id from discovery URL`() {
        val text = "https://www.xiaohongshu.com/discovery/item/$sampleId?xsec_token=abc"
        assertEquals(sampleId, NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `extracts id from deep link`() {
        val text = "xhsdiscover://note/$sampleId"
        assertEquals(sampleId, NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `extracts id from mobile share link variant`() {
        // Some XHS share URLs use m.xiaohongshu.com
        val text = "http://m.xiaohongshu.com/explore/$sampleId"
        // Our pattern is xiaohongshu.com/explore/{id} — "m." prefix
        // doesn't match because we anchor on xiaohongshu.com exactly.
        // Falls back to bare 24-hex extraction.
        assertEquals(sampleId, NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `extracts bare hex when no URL pattern matches`() {
        val text = "Note ID: $sampleId published at 14:32"
        assertEquals(sampleId, NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `returns null on empty text`() {
        assertNull(NoteUrlExtractor.extractFromText(""))
        assertNull(NoteUrlExtractor.extractFromText("   "))
    }

    @Test
    fun `returns null when no 24-hex candidate exists`() {
        val text = "发布了一篇新笔记，快来围观"
        assertNull(NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `does not match inside longer hex strings`() {
        // 25 hex chars — too long, should not extract anything
        val text = "abc${sampleId}def" // 24-hex flanked by 'a','d'
        // The first 24-hex substring would be 24 chars starting from a —
        // but 'a' is hex, so we need to check the boundary logic.
        // Our matcher requires non-hex boundaries on both sides.
        // 'abc' = 3 hex chars, then 24 more = could match starting at index 3.
        // Boundary check: text[2]='c' (hex) so start boundary fails.
        assertNull(NoteUrlExtractor.extractFromText(text))
    }

    @Test
    fun `URL pattern takes priority over bare hex`() {
        val text = "URL: xiaohongshu.com/explore/$sampleId also random $sampleId extra"
        assertEquals(sampleId, NoteUrlExtractor.extractFromText(text))
    }
}