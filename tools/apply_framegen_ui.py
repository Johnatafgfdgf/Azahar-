#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before_close(path: str, block: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    marker = block.strip().splitlines()[0].strip()
    if marker in text:
        return
    close = "</resources>"
    if close not in text:
        raise RuntimeError(f"resources close tag missing in {path}")
    p.write_text(text.replace(close, block.rstrip() + "\n\n" + close, 1), encoding="utf-8")


# Native settings shared by desktop and Android.
replace_once(
    "src/common/settings.h",
    '    SwitchableSetting<bool> async_presentation{true, "async_presentation"};\n',
    '    SwitchableSetting<bool> async_presentation{true, "async_presentation"};\n'
    '    // Integrated Vulkan frame generation. The backend consumes the final composed 3DS image\n'
    '    // immediately before swapchain presentation; it never uses Android screen capture.\n'
    '    SwitchableSetting<bool> frame_gen{false, "frame_gen"};\n'
    '    SwitchableSetting<u32, true> frame_gen_multiplier{2, 2, 4, "frame_gen_multiplier"};\n'
    '    SwitchableSetting<u32, true> frame_gen_target_rate{120, 0, 240, "frame_gen_target_rate"};\n'
    '    SwitchableSetting<u32, true> frame_gen_queue_target{0, 0, 2, "frame_gen_queue_target"};\n'
    '    SwitchableSetting<bool> frame_gen_flow_scale_auto{true, "frame_gen_flow_scale_auto"};\n'
    '    SwitchableSetting<u32, true> frame_gen_flow_scale{100, 25, 100, "frame_gen_flow_scale"};\n'
    '    SwitchableSetting<bool> frame_gen_fp16{true, "frame_gen_fp16"};\n'
)

replace_once(
    "src/common/settings.cpp",
    '    log_setting("Renderer_AsyncPresentation", values.async_presentation.GetValue());\n',
    '    log_setting("Renderer_AsyncPresentation", values.async_presentation.GetValue());\n'
    '    log_setting("Renderer_FrameGen", values.frame_gen.GetValue());\n'
    '    log_setting("Renderer_FrameGenMultiplier", values.frame_gen_multiplier.GetValue());\n'
    '    log_setting("Renderer_FrameGenTargetRate", values.frame_gen_target_rate.GetValue());\n'
    '    log_setting("Renderer_FrameGenQueueTarget", values.frame_gen_queue_target.GetValue());\n'
    '    log_setting("Renderer_FrameGenFlowScaleAuto", values.frame_gen_flow_scale_auto.GetValue());\n'
    '    log_setting("Renderer_FrameGenFlowScale", values.frame_gen_flow_scale.GetValue());\n'
    '    log_setting("Renderer_FrameGenFp16", values.frame_gen_fp16.GetValue());\n'
)

replace_once(
    "src/common/settings.cpp",
    '    values.async_presentation.SetGlobal(true);\n',
    '    values.async_presentation.SetGlobal(true);\n'
    '    values.frame_gen.SetGlobal(true);\n'
    '    values.frame_gen_multiplier.SetGlobal(true);\n'
    '    values.frame_gen_target_rate.SetGlobal(true);\n'
    '    values.frame_gen_queue_target.SetGlobal(true);\n'
    '    values.frame_gen_flow_scale_auto.SetGlobal(true);\n'
    '    values.frame_gen_flow_scale.SetGlobal(true);\n'
    '    values.frame_gen_fp16.SetGlobal(true);\n'
)

# Android setting models.
replace_once(
    "src/android/app/src/main/java/org/citra/citra_emu/features/settings/model/BooleanSetting.kt",
    '    ASYNC_SHADERS("async_shader_compilation", Settings.SECTION_RENDERER, false),\n',
    '    ASYNC_SHADERS("async_shader_compilation", Settings.SECTION_RENDERER, false),\n'
    '    FRAME_GEN("frame_gen", Settings.SECTION_RENDERER, false),\n'
    '    FRAME_GEN_FLOW_SCALE_AUTO("frame_gen_flow_scale_auto", Settings.SECTION_RENDERER, true),\n'
    '    FRAME_GEN_FP16("frame_gen_fp16", Settings.SECTION_RENDERER, true),\n'
)

replace_once(
    "src/android/app/src/main/java/org/citra/citra_emu/features/settings/model/IntSetting.kt",
    '    RESOLUTION_FACTOR("resolution_factor", Settings.SECTION_RENDERER, 1),\n',
    '    RESOLUTION_FACTOR("resolution_factor", Settings.SECTION_RENDERER, 1),\n'
    '    FRAME_GEN_MULTIPLIER("frame_gen_multiplier", Settings.SECTION_RENDERER, 2),\n'
    '    FRAME_GEN_TARGET_RATE("frame_gen_target_rate", Settings.SECTION_RENDERER, 120),\n'
    '    FRAME_GEN_QUEUE_TARGET("frame_gen_queue_target", Settings.SECTION_RENDERER, 0),\n'
    '    FRAME_GEN_FLOW_SCALE("frame_gen_flow_scale", Settings.SECTION_RENDERER, 100),\n'
)

# Frame-generation subsection inside Graphics. Keeping it in the native Azahar settings screen
# means global and per-game configs both use the same renderer section.
ui_block = '''            add(HeaderSetting(R.string.frame_gen))
            add(
                SwitchSetting(
                    BooleanSetting.FRAME_GEN,
                    R.string.frame_gen,
                    R.string.frame_gen_description,
                    BooleanSetting.FRAME_GEN.key,
                    BooleanSetting.FRAME_GEN.defaultValue
                )
            )
            add(
                SingleChoiceSetting(
                    IntSetting.FRAME_GEN_TARGET_RATE,
                    R.string.frame_gen_target_rate,
                    R.string.frame_gen_target_rate_description,
                    R.array.frameGenTargetRateNames,
                    R.array.frameGenTargetRateValues,
                    IntSetting.FRAME_GEN_TARGET_RATE.key,
                    IntSetting.FRAME_GEN_TARGET_RATE.defaultValue
                )
            )
            add(
                SingleChoiceSetting(
                    IntSetting.FRAME_GEN_MULTIPLIER,
                    R.string.frame_gen_multiplier,
                    R.string.frame_gen_multiplier_description,
                    R.array.frameGenMultiplierNames,
                    R.array.frameGenMultiplierValues,
                    IntSetting.FRAME_GEN_MULTIPLIER.key,
                    IntSetting.FRAME_GEN_MULTIPLIER.defaultValue
                )
            )
            add(
                SingleChoiceSetting(
                    IntSetting.FRAME_GEN_QUEUE_TARGET,
                    R.string.frame_gen_queue_target,
                    R.string.frame_gen_queue_target_description,
                    R.array.frameGenQueueTargetNames,
                    R.array.frameGenQueueTargetValues,
                    IntSetting.FRAME_GEN_QUEUE_TARGET.key,
                    IntSetting.FRAME_GEN_QUEUE_TARGET.defaultValue
                )
            )
            add(
                SwitchSetting(
                    BooleanSetting.FRAME_GEN_FLOW_SCALE_AUTO,
                    R.string.frame_gen_flow_scale_auto,
                    R.string.frame_gen_flow_scale_auto_description,
                    BooleanSetting.FRAME_GEN_FLOW_SCALE_AUTO.key,
                    BooleanSetting.FRAME_GEN_FLOW_SCALE_AUTO.defaultValue
                )
            )
            add(
                SliderSetting(
                    IntSetting.FRAME_GEN_FLOW_SCALE,
                    R.string.frame_gen_flow_scale,
                    R.string.frame_gen_flow_scale_description,
                    25,
                    100,
                    "%",
                    IntSetting.FRAME_GEN_FLOW_SCALE.key,
                    IntSetting.FRAME_GEN_FLOW_SCALE.defaultValue.toFloat()
                )
            )
            add(
                SwitchSetting(
                    BooleanSetting.FRAME_GEN_FP16,
                    R.string.frame_gen_fp16,
                    R.string.frame_gen_fp16_description,
                    BooleanSetting.FRAME_GEN_FP16.key,
                    BooleanSetting.FRAME_GEN_FP16.defaultValue
                )
            )

'''
replace_once(
    "src/android/app/src/main/java/org/citra/citra_emu/features/settings/ui/SettingsFragmentPresenter.kt",
    '            add(HeaderSetting(R.string.stereoscopy))\n',
    ui_block + '            add(HeaderSetting(R.string.stereoscopy))\n',
)

insert_before_close(
    "src/android/app/src/main/res/values/strings.xml",
    '''    <!-- Integrated Vulkan frame generation -->
    <string name="frame_gen">Frame generation</string>
    <string name="frame_gen_description">Generate intermediate frames inside Azahar's Vulkan renderer after the final 3DS screen composition. Vulkan only.</string>
    <string name="frame_gen_target_rate">Target frame rate</string>
    <string name="frame_gen_target_rate_description">Automatically choose how many intermediate frames to generate for the selected display target. Choose Disabled to use the fixed multiplier.</string>
    <string name="frame_gen_multiplier">Frame multiplier</string>
    <string name="frame_gen_multiplier_description">Fixed number of displayed frames per rendered game frame when target frame rate is disabled.</string>
    <string name="frame_gen_queue_target">Frame queue target</string>
    <string name="frame_gen_queue_target_description">Lower buffering reduces latency; extra buffering can improve pacing on unstable devices.</string>
    <string name="frame_gen_flow_scale_auto">Match motion estimation to the game</string>
    <string name="frame_gen_flow_scale_auto_description">Automatically scale motion estimation to the game's rendered resolution.</string>
    <string name="frame_gen_flow_scale">Motion estimation scale</string>
    <string name="frame_gen_flow_scale_description">Manual optical-flow resolution. Lower values reduce GPU cost but may increase artifacts.</string>
    <string name="frame_gen_fp16">Half precision shaders</string>
    <string name="frame_gen_fp16_description">Prefer FP16 frame-generation shaders when the GPU and imported Lossless Scaling shaders support them.</string>'''
)

insert_before_close(
    "src/android/app/src/main/res/values/arrays.xml",
    '''    <!-- Integrated Vulkan frame generation -->
    <string-array name="frameGenMultiplierNames">
        <item>2×</item>
        <item>3×</item>
        <item>4×</item>
    </string-array>
    <integer-array name="frameGenMultiplierValues">
        <item>2</item>
        <item>3</item>
        <item>4</item>
    </integer-array>

    <string-array name="frameGenTargetRateNames">
        <item>Disabled (use multiplier)</item>
        <item>60 FPS</item>
        <item>90 FPS</item>
        <item>120 FPS</item>
        <item>144 FPS</item>
    </string-array>
    <integer-array name="frameGenTargetRateValues">
        <item>0</item>
        <item>60</item>
        <item>90</item>
        <item>120</item>
        <item>144</item>
    </integer-array>

    <string-array name="frameGenQueueTargetNames">
        <item>Lowest latency (Unbuffered)</item>
        <item>Balanced (1 frame)</item>
        <item>Smoothest (2 frames)</item>
    </string-array>
    <integer-array name="frameGenQueueTargetValues">
        <item>0</item>
        <item>1</item>
        <item>2</item>
    </integer-array>'''
)

print("Azahar frame-generation settings/UI patches applied.")
