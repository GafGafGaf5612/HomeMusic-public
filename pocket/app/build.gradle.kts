import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// Machine-specific paths: pocket/local.properties (see pocket/README.md) or -P on the command line.
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.isFile }?.inputStream()?.use { load(it) }
}
fun prop(name: String, default: String? = null): String? =
    (project.findProperty(name) as String?) ?: localProps.getProperty(name) ?: default

/** Python 3.12 on this machine: Chaquopy's pip and the Hub's bytecode need the app's version. */
val hostPython = prop("pocket.python", "python3.12")!!
/** native/build-wheels.sh output: pydantic-core, jiter, selectolax, rapidfuzz for Android. */
val wheelsDir = file(prop("pocket.wheels", rootProject.file("wheels").path)!!)
val repoRoot = rootProject.projectDir.parentFile

android {
    namespace = "app.homemusic.pocket"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.homemusic.pocket"
        minSdk = 29
        targetSdk = 35
        versionCode = 4
        versionName = "0.1.3"
        ndk { abiFilters += "arm64-v8a" }
    }

    signingConfigs {
        // a release key of your own: pocket.keystore, pocket.keystore.password, pocket.key.alias in local.properties
        prop("pocket.keystore")?.let { path ->
            create("release") {
                storeFile = file(path)
                storePassword = prop("pocket.keystore.password")
                keyAlias = prop("pocket.key.alias")
                keyPassword = prop("pocket.key.password", prop("pocket.keystore.password"))
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    packaging {
        // ffmpeg, Navidrome, QuickJS are executables packed as lib*.so: Android must unpack them to run them
        jniLibs { useLegacyPackaging = true }
    }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/hub-assets"))
    // native/build-binaries.sh output: libffmpeg.so, libffprobe.so, libqjs.so, libnavidrome.so in arm64-v8a/
    prop("pocket.jniLibs")?.let { sourceSets["main"].jniLibs.srcDir(it) }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        buildPython(hostPython)
        pip {
            options("--find-links", wheelsDir.path)
            install("-r", "requirements.txt")
        }
        // packages that read their own data files through the file system
        extractPackages("certifi", "tzdata", "yt_dlp_ejs")
    }
}

/** The Hub (code, migrations, the built PWA) as assets/hub.zip; PocketService unpacks it on the first start. */
val stageHub by tasks.registering(Exec::class) {
    val out = layout.buildDirectory.file("generated/hub-assets/hub.zip")
    inputs.dir(File(repoRoot, "hub/app"))
    inputs.dir(File(repoRoot, "hub/migrations"))
    outputs.file(out)
    commandLine(hostPython, rootProject.file("tools/stage_hub.py").path, repoRoot.path, out.get().asFile.path)
}
tasks.named("preBuild") { dependsOn(stageHub) }

dependencies {
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("androidx.media:media:1.7.0")
    implementation("org.libtorrent4j:libtorrent4j:2.1.0-39")
    implementation("org.libtorrent4j:libtorrent4j-android-arm64:2.1.0-39")
    implementation("org.nanohttpd:nanohttpd:2.3.1")
}
