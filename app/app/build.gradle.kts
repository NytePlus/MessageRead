plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.example.readreceipt"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.example.readreceipt"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        val apiBase = providers.gradleProperty("API_BASE_URL").orElse("https://10.0.2.2:4000").get()
        buildConfigField("String", "API_BASE_URL", "\"${apiBase.trimEnd('/')}\"")
    }

    signingConfigs {
        create("release") {
            storeFile = file("E:/AppKeys/MessageRead.jks") // 改成你刚才保存的真实路径
            storePassword = "1135540486ppt"
            keyAlias = "key0"
            keyPassword = "1135540486ppt"
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            // 2. 将签名应用到 release 版本
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.material3:material3:1.3.1")
    implementation("androidx.compose.ui:ui:1.7.6")
    implementation("androidx.compose.ui:ui-tooling-preview:1.7.6")
    implementation("com.google.zxing:core:3.5.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    debugImplementation("androidx.compose.ui:ui-tooling:1.7.6")
}
