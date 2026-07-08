package com.matrix.companion.util

/** Wall-clock abstraction so tests don't depend on real time. */
interface Clock {
    fun nowSeconds(): Long

    companion object {
        val Real: Clock = object : Clock {
            override fun nowSeconds(): Long = java.lang.System.currentTimeMillis() / 1000L
        }
    }
}
