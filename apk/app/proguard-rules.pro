# Keep Kotlinx Serialization metadata
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

# Keep Ktor + CIO
-keep class io.ktor.** { *; }
-keep class kotlinx.coroutines.** { *; }

# Keep our companion package public API
-keep class com.matrix.companion.** { *; }

# Timber (defensive — Tree subclasses are reflectively planted)
-keep class timber.log.** { *; }
-dontwarn timber.log.**
