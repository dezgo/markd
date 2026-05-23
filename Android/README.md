# Markd — Android

Native Android client for Markd. Talks to the API in `../Backend/`.

Not yet initialized. To start the project:

1. Open Android Studio → New Project → Empty Activity (Kotlin, Jetpack Compose).
2. Set the project location to this directory (`Android/`).
3. Use the package `cc.appfoundry.markd` (or whatever matches your iOS bundle id).
4. Commit `app/`, `build.gradle.kts`, `gradle/`, `gradlew`, `gradlew.bat`, `settings.gradle.kts`. The root `.gitignore` already excludes Android build output, `local.properties`, and keystores.

See `../Web/` for the existing web app and `../Backend/` for the shared API.
