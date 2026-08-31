#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(p):
    return (ROOT / p).read_text(encoding="utf-8")


def write(p, s):
    (ROOT / p).write_text(s, encoding="utf-8")


def replace_once(p, old, new):
    s = read(p)
    if new in s:
        return
    if old not in s:
        raise RuntimeError(f"anchor missing: {p}: {old[:100]!r}")
    write(p, s.replace(old, new, 1))


# NativeLibrary entry used at app startup and after the user imports a DLL.
p = "src/android/app/src/main/java/org/citra/citra_emu/NativeLibrary.kt"
s = read(p)
anchor = "    external fun reloadSettings()\n"
insert = anchor + "\n    /** Path to the user's own Lossless Scaling DLL used only for shader resources. */\n    external fun setFrameGenDllPath(path: String)\n"
if "external fun setFrameGenDllPath" not in s:
    if anchor not in s:
        raise RuntimeError("NativeLibrary reloadSettings anchor missing")
    s = s.replace(anchor, insert, 1)
write(p, s)

# Always publish the persistent private path to native code when the app process starts.
p = "src/android/app/src/main/java/org/citra/citra_emu/CitraApplication.kt"
s = read(p)
if "java.io.File" not in s:
    s = s.replace("import android.os.Build\n", "import android.os.Build\nimport java.io.File\n", 1)
anchor = "        NativeLibrary.logDeviceInfo()\n"
insert = """        val frameGenDll = File(filesDir, "framegen/Lossless.dll")
        NativeLibrary.setFrameGenDllPath(frameGenDll.absolutePath)

        NativeLibrary.logDeviceInfo()
"""
if "frameGenDll = File(filesDir" not in s:
    if anchor not in s:
        raise RuntimeError("CitraApplication onCreate anchor missing")
    s = s.replace(anchor, insert, 1)
write(p, s)

# Document picker + private copy. This avoids Android scoped-storage path issues.
p = "src/android/app/src/main/java/org/citra/citra_emu/features/settings/ui/SettingsActivity.kt"
s = read(p)
if "ActivityResultContracts" not in s:
    s = s.replace(
        "import androidx.activity.result.ActivityResultLauncher\n",
        "import androidx.activity.result.ActivityResultLauncher\nimport androidx.activity.result.contract.ActivityResultContracts\n",
        1,
    )
if "java.io.File\n" not in s:
    s = s.replace("import java.io.IOException\n", "import java.io.File\nimport java.io.FileOutputStream\nimport java.io.IOException\n", 1)

class_anchor = "class SettingsActivity : AppCompatActivity(), SettingsActivityView {\n    private val presenter = SettingsActivityPresenter(this)\n"
class_insert = """class SettingsActivity : AppCompatActivity(), SettingsActivityView {
    private val presenter = SettingsActivityPresenter(this)

    private val losslessDllPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@registerForActivityResult
        try {
            val dir = File(filesDir, "framegen")
            if (!dir.exists() && !dir.mkdirs()) {
                throw IOException("Could not create frame generation directory")
            }
            val destination = File(dir, "Lossless.dll")
            contentResolver.openInputStream(uri).use { input ->
                if (input == null) throw IOException("Could not open selected file")
                FileOutputStream(destination, false).use { output -> input.copyTo(output) }
            }
            if (destination.length() < 1024L) {
                destination.delete()
                throw IOException("Selected file is too small to be Lossless.dll")
            }
            NativeLibrary.setFrameGenDllPath(destination.absolutePath)
            showToastMessage(getString(R.string.frame_gen_dll_imported), false)
            settingsFragment?.loadSettingsList()
        } catch (e: Exception) {
            showToastMessage(getString(R.string.frame_gen_dll_import_failed, e.message ?: "unknown error"), true)
        }
    }
"""
if "private val losslessDllPicker" not in s:
    if class_anchor not in s:
        raise RuntimeError("SettingsActivity class anchor missing")
    s = s.replace(class_anchor, class_insert, 1)

method_anchor = "    fun setToolbarTitle(title: String) {\n"
method_insert = """    fun pickLosslessDll() {
        losslessDllPicker.launch(arrayOf("application/octet-stream", "application/x-msdownload", "*/*"))
    }

    fun losslessDllStatus(): String {
        val file = File(filesDir, "framegen/Lossless.dll")
        return if (file.isFile && file.length() > 1024L) {
            getString(R.string.frame_gen_dll_installed)
        } else {
            getString(R.string.frame_gen_dll_not_installed)
        }
    }

    fun setToolbarTitle(title: String) {
"""
if "fun pickLosslessDll()" not in s:
    if method_anchor not in s:
        raise RuntimeError("SettingsActivity toolbar anchor missing")
    s = s.replace(method_anchor, method_insert, 1)
write(p, s)

# Add the import action directly above the framegen toggle.
p = "src/android/app/src/main/java/org/citra/citra_emu/features/settings/ui/SettingsFragmentPresenter.kt"
s = read(p)
anchor = "            add(HeaderSetting(R.string.frame_gen))\n"
insert = """            add(HeaderSetting(R.string.frame_gen))
            add(
                RunnableSetting(
                    R.string.frame_gen_dll,
                    R.string.frame_gen_dll_description,
                    false,
                    runnable = { settingsActivity.pickLosslessDll() },
                    value = { settingsActivity.losslessDllStatus() }
                )
            )
"""
if "R.string.frame_gen_dll_description" not in s:
    if anchor not in s:
        raise RuntimeError("framegen UI header anchor missing")
    s = s.replace(anchor, insert, 1)
write(p, s)

# JNI setter simply exports a process-local path. No proprietary bytes are compiled into the APK.
p = "src/android/app/src/main/jni/native.cpp"
s = read(p)
anchor = 'extern "C" {\n\n'
insert = '''extern "C" {\n\nvoid Java_org_citra_citra_1emu_NativeLibrary_setFrameGenDllPath(JNIEnv* env,\n                                                               [[maybe_unused]] jobject obj,\n                                                               jstring jpath) {\n    const std::string path = GetJString(env, jpath);\n    if (path.empty()) {\n        unsetenv("AZAHAR_LOSSLESS_DLL");\n    } else {\n        setenv("AZAHAR_LOSSLESS_DLL", path.c_str(), 1);\n    }\n}\n\n'''
if "NativeLibrary_setFrameGenDllPath" not in s:
    if anchor not in s:
        raise RuntimeError("native.cpp extern C anchor missing")
    s = s.replace(anchor, insert, 1)
write(p, s)

# English resources.
p = "src/android/app/src/main/res/values/strings.xml"
s = read(p)
block = '''    <string name="frame_gen_dll">Lossless Scaling library</string>\n    <string name="frame_gen_dll_description">Select your own Lossless.dll. Azahar reads only the LSFG shader resources and stores the selected file in private app storage.</string>\n    <string name="frame_gen_dll_installed">Installed</string>\n    <string name="frame_gen_dll_not_installed">Not installed</string>\n    <string name="frame_gen_dll_imported">Lossless.dll imported successfully</string>\n    <string name="frame_gen_dll_import_failed">Could not import Lossless.dll: %1$s</string>\n'''
if 'name="frame_gen_dll"' not in s:
    s = s.replace("</resources>", block + "</resources>", 1)
write(p, s)

print("Lossless.dll picker patches applied")
