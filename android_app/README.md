# ManaVault for Android

This is a separate, offline Android app. The existing Windows/Tkinter program is not changed. The Android app reads and writes the same `magic_data.json` structure and keeps its working copy in Android app-private storage.

## Build without installing tools on the Windows PC

The APK is built by GitHub Actions on a hosted Ubuntu runner. The repository only needs `.github/workflows/build-android.yml` and the contents of `android_app/`. This folder is not currently connected to GitHub, so no upload or build has been started.

If uploading through GitHub in a browser, select only those Android files. Browser uploads may not apply `.gitignore`; do not upload the root `magic_data.json`, `.venv/`, `build/`, or `dist/`. The JSON can contain personal match notes and is not needed to build the APK.

After the repository is on GitHub:

1. Open the repository's **Actions** tab.
2. Select **Build ManaVault Android APK** and choose **Run workflow**.
3. Open the completed run and download the `ManaVault-Android-debug` artifact.
4. Extract the APK and install it on the phone. Android may ask you to allow installation from the app used to open the APK.

The APK does not need internet access to manage decks, cards, matches, results, or rules. Internet is only needed by GitHub Actions to build it and by the phone to download the APK. Use **Importar backup** and **Exportar backup** in the results screen to move JSON data between the PC and phone; the two copies do not synchronize automatically.

## Local tests

The JSON storage tests use only the Python standard library:

```powershell
python -m unittest discover -s android_app -p "test_*.py" -v
```

The Android UI and file chooser still need a successful hosted APK build and a check on a real Android device. No Android SDK, Android Studio, or extra package is installed on the Windows PC by this project.
