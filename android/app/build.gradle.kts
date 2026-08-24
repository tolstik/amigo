plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "ru.tolstik.amigo.sync"
    compileSdk = 36

    defaultConfig {
        applicationId = "ru.tolstik.amigo.sync"
        minSdk = 28
        targetSdk = 36
        versionCode = 14
        versionName = "1.3.4"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables.useSupportLibrary = true
    }

    val keystorePath = providers.environmentVariable("AMIGO_ANDROID_KEYSTORE")
    val keystorePassword = providers.environmentVariable("AMIGO_ANDROID_KEYSTORE_PASSWORD").orElse(
        providers.environmentVariable("AMIGO_ANDROID_KEYSTORE_PASSWORD_FILE")
            .map { file(it).readText().trimEnd('\r', '\n') },
    )
    val keyAliasValue = providers.environmentVariable("AMIGO_ANDROID_KEY_ALIAS")
    val keyPasswordValue = providers.environmentVariable("AMIGO_ANDROID_KEY_PASSWORD").orElse(
        providers.environmentVariable("AMIGO_ANDROID_KEY_PASSWORD_FILE")
            .map { file(it).readText().trimEnd('\r', '\n') },
    )
    val hasReleaseSigning = listOf(
        keystorePath,
        keystorePassword,
        keyAliasValue,
        keyPasswordValue,
    ).all { it.isPresent }

    if (hasReleaseSigning) {
        signingConfigs {
            create("releaseFromEnvironment") {
                storeFile = file(keystorePath.get())
                storePassword = keystorePassword.get()
                keyAlias = keyAliasValue.get()
                keyPassword = keyPasswordValue.get()
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("releaseFromEnvironment")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions.jvmTarget = "17"
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging.resources.excludes += setOf(
        "/META-INF/{AL2.0,LGPL2.1}",
        "META-INF/DEPENDENCIES",
    )
    lint {
        // Versions are deliberately pinned to the newest Compose line compatible with
        // compileSdk 36 / AGP 8.x. Direct SharedPreferences editors are used for atomic
        // multi-key state transitions in the resumable sync state machine.
        disable += setOf(
            "AndroidGradlePluginVersion",
            "GradleDependency",
            "NewerVersionAvailable",
            "UseKtx",
        )
    }
}

dependencies {
    // Compose 1.11 is the newest line compatible with compileSdk 36 / AGP 8.x.
    implementation(platform("androidx.compose:compose-bom:2026.06.01"))
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.health.connect:connect-client:1.1.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.10.0")
    implementation("androidx.work:work-runtime-ktx:2.11.2")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}
