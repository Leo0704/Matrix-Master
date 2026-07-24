import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}

fun gitVersionName(): String {
    return try {
        val process = ProcessBuilder("git", "describe", "--tags", "--abbrev=0")
            .directory(File(rootDir, ".."))
            .redirectErrorStream(true)
            .start()
        val exit = process.waitFor()
        if (exit == 0) {
            process.inputStream.bufferedReader().readText().trim().removePrefix("v")
        } else {
            "0.1.0"
        }
    } catch (e: Exception) {
        "0.1.0"
    }
}

fun gitVersionCode(): Int {
    return try {
        val process = ProcessBuilder("git", "rev-list", "--count", "HEAD")
            .directory(File(rootDir, ".."))
            .redirectErrorStream(true)
            .start()
        val exit = process.waitFor()
        if (exit == 0) {
            process.inputStream.bufferedReader().readText().trim().toInt()
        } else {
            1
        }
    } catch (e: Exception) {
        1
    }
}

android {
    namespace = "com.matrix.companion"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.matrix.companion"
        minSdk = 26
        targetSdk = 34
        versionCode = gitVersionCode()
        versionName = gitVersionName()
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        val masterUrl = (project.findProperty("matrixMasterUrl") as String?)
            ?: System.getenv("MATRIX_MASTER_URL")
            ?: "http://192.168.1.172:8666"
        buildConfigField("String", "MASTER_URL", "\"$masterUrl\"")
        buildConfigField("boolean", "ENABLE_HTTP_SERVER", "true")
    }

    signingConfigs {
        create("release") {
            // Filled in via -PandroidReleaseKey=... in dev / CI for real release.
            storeFile = file("release.keystore").takeIf { it.exists() }
            storePassword = (project.findProperty("androidReleaseStorePass") as String?)
                ?: System.getenv("ANDROID_RELEASE_STORE_PASS")
            keyAlias = (project.findProperty("androidReleaseKeyAlias") as String?)
                ?: System.getenv("ANDROID_RELEASE_KEY_ALIAS")
            keyPassword = (project.findProperty("androidReleaseKeyPass") as String?)
                ?: System.getenv("ANDROID_RELEASE_KEY_PASS")
        }
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs = freeCompilerArgs + listOf(
            "-opt-in=kotlinx.serialization.ExperimentalSerializationApi",
            "-opt-in=kotlin.RequiresOptIn"
        )
    }

    packaging {
        resources {
            excludes += setOf(
                "/META-INF/{AL2.0,LGPL2.1}",
                "/META-INF/INDEX.LIST",
                "/META-INF/io.netty.versions.properties"
            )
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    val ktor = "2.3.12"
    val coroutines = "1.8.1"
    val serialization = "1.6.3"
    val lifecycle = "2.8.4"
    val securityCrypto = "1.1.0-alpha06"

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-service:$lifecycle")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:$lifecycle")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.security:security-crypto:$securityCrypto")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutines")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:$coroutines")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:$serialization")

    implementation("io.ktor:ktor-server-core:$ktor")
    implementation("io.ktor:ktor-server-cio:$ktor")
    implementation("io.ktor:ktor-server-content-negotiation:$ktor")
    implementation("io.ktor:ktor-server-status-pages:$ktor")
    implementation("io.ktor:ktor-server-call-logging:$ktor")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktor")

    implementation("io.ktor:ktor-client-core:$ktor")
    implementation("io.ktor:ktor-client-cio:$ktor")
    implementation("io.ktor:ktor-client-content-negotiation:$ktor")

    implementation("com.jakewharton.timber:timber:5.0.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.13")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("androidx.test.ext:junit:1.2.1")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:$coroutines")
    testImplementation("io.ktor:ktor-server-test-host:$ktor")
    testImplementation("io.ktor:ktor-client-mock:$ktor")
    testImplementation("org.mockito:mockito-core:5.12.0")
    testImplementation("org.mockito.kotlin:mockito-kotlin:5.4.0")

    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
