[app]
# (str) Title of your application
title = ManaVault

# (str) Package name
package.name = manavault

# (str) Package domain (needed for android/ios packaging)
package.domain = org.manavault

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,json

# (str) Application version
version = 1.10.0

# (list) Application requirements
requirements = python3,kivy,androidstorage4kivy

# (list) Gradle dependencies to add
android.gradle_dependencies = com.google.mlkit:text-recognition:16.0.1

# (bool) Enable AndroidX support
android.enable_androidx = True

# (str) Supported orientation (one of landscape, portrait or all)
orientation = portrait

# (list) Android permissions
android.permissions = INTERNET,CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# (int) Android API to target
android.api = 34

# (int) Minimum supported Android API
android.minapi = 24

# (str) Android NDK version
android.ndk = 26b

# (list) Android architectures
android.archs = arm64-v8a

# (bool) Accept Android SDK license
android.accept_sdk_license = True

# (bool) Keep application data when updating
android.allow_backup = True

# (str) Android entry point
p4a.bootstrap = sdl2

# (str) python-for-android branch to use
p4a.branch = develop

[buildozer]
# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 0
