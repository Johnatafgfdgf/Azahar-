#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"anchor missing in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


# Header hygiene.
replace_once(
    "src/video_core/renderer_vulkan/vk_framegen.h",
    "#include <memory>\n#include <vector>\n",
    "#include <memory>\n#include <unordered_map>\n#include <vector>\n",
)

# Enable the Android Hardware Buffer external-memory extension on Azahar's own device.
p = "src/video_core/renderer_vulkan/vk_instance.cpp"
text = read(p)
text = text.replace(
    "boost::container::static_vector<const char*, 13> enabled_extensions;",
    "boost::container::static_vector<const char*, 20> enabled_extensions;",
)
anchor = "    add_extension(VK_KHR_SWAPCHAIN_EXTENSION_NAME);\n"
block = """    add_extension(VK_KHR_SWAPCHAIN_EXTENSION_NAME);
#ifdef __ANDROID__
    // Frame generation shares the final composed image with its compute device through
    // AHardwareBuffer. These are device extensions only; the normal renderer remains
    // unchanged when frame generation is disabled.
    add_extension(VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME);
    add_extension(VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME);
    add_extension(VK_KHR_DEDICATED_ALLOCATION_EXTENSION_NAME);
    add_extension(VK_KHR_GET_MEMORY_REQUIREMENTS_2_EXTENSION_NAME);
    add_extension(VK_KHR_BIND_MEMORY_2_EXTENSION_NAME);
    add_extension(VK_KHR_SAMPLER_YCBCR_CONVERSION_EXTENSION_NAME);
#endif
"""
if "VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME" not in text:
    if anchor not in text:
        raise RuntimeError("vk_instance extension anchor missing")
    text = text.replace(anchor, block, 1)
write(p, text)

# Build the upstream Android LSFG compute backend only for Android builds. The build workflow
# materializes the source at this path, keeping third-party code out of this repository.
p = "externals/CMakeLists.txt"
text = read(p)
lsfg_cmake = """

# Integrated LSFG frame-generation backend (Android only).
if(ANDROID AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/lsfg-vk-android/framegen/CMakeLists.txt")
    set(VOLK_INSTALL OFF CACHE BOOL "" FORCE)
    add_subdirectory(lsfg-vk-android/thirdparty/volk EXCLUDE_FROM_ALL)
    add_subdirectory(lsfg-vk-android/framegen EXCLUDE_FROM_ALL)
endif()
"""
if "Integrated LSFG frame-generation backend" not in text:
    text += lsfg_cmake
write(p, text)

p = "src/video_core/CMakeLists.txt"
text = read(p)
anchor = "        renderer_vulkan/vk_graphics_pipeline.cpp\n        renderer_vulkan/vk_graphics_pipeline.h\n"
insert = anchor + "        renderer_vulkan/vk_framegen.cpp\n        renderer_vulkan/vk_framegen.h\n"
if "renderer_vulkan/vk_framegen.cpp" not in text:
    if anchor not in text:
        raise RuntimeError("video_core source anchor missing")
    text = text.replace(anchor, insert, 1)
link_anchor = "    target_link_libraries(video_core PRIVATE vulkan-headers vma sirit SPIRV glslang)\n"
link_new = link_anchor + "    if(ANDROID AND TARGET lsfg-vk-framegen)\n        target_link_libraries(video_core PRIVATE lsfg-vk-framegen android)\n    endif()\n"
if "TARGET lsfg-vk-framegen" not in text:
    if link_anchor not in text:
        raise RuntimeError("video_core link anchor missing")
    text = text.replace(link_anchor, link_new, 1)
write(p, text)

# Presenter owns one framegen bridge. It is dormant unless frame_gen is enabled.
p = "src/video_core/renderer_vulkan/vk_present_window.h"
text = read(p)
if "class FrameGenerator;" not in text:
    text = text.replace("class RenderManager;\n", "class RenderManager;\nclass FrameGenerator;\n", 1)
if "PresentImage(Frame* frame" not in text:
    text = text.replace(
        "    void CopyToSwapchain(Frame* frame);\n",
        "    void CopyToSwapchain(Frame* frame);\n\n"
        "    void PresentImage(Frame* frame, vk::Image source_image, bool generated,\n"
        "                      size_t generated_index, bool final_real);\n",
        1,
    )
if "std::unique_ptr<FrameGenerator> frame_generator;" not in text:
    text = text.replace(
        "    std::jthread present_thread;\n",
        "    std::jthread present_thread;\n    std::unique_ptr<FrameGenerator> frame_generator;\n",
        1,
    )
write(p, text)

p = "src/video_core/renderer_vulkan/vk_present_window.cpp"
text = read(p)
if '#include "video_core/renderer_vulkan/vk_framegen.h"' not in text:
    text = text.replace(
        '#include "video_core/renderer_vulkan/vk_instance.h"\n',
        '#include "video_core/renderer_vulkan/vk_framegen.h"\n#include "video_core/renderer_vulkan/vk_instance.h"\n',
        1,
    )

# Construct after the command pool / per-frame resources exist.
ctor_anchor = "    if (use_present_thread) {\n        present_thread = std::jthread([this](std::stop_token token) { PresentThread(token); });\n    }\n"
ctor_new = """#ifdef __ANDROID__
    frame_generator = std::make_unique<FrameGenerator>(instance);
#endif

    if (use_present_thread) {
        present_thread = std::jthread([this](std::stop_token token) { PresentThread(token); });
    }
"""
if "frame_generator = std::make_unique<FrameGenerator>" not in text:
    if ctor_anchor not in text:
        raise RuntimeError("PresentWindow constructor anchor missing")
    text = text.replace(ctor_anchor, ctor_new, 1)

start = text.find("void PresentWindow::CopyToSwapchain(Frame* frame) {")
end = text.find("vk::RenderPass PresentWindow::CreateRenderpass()", start)
if start < 0 or end < 0:
    raise RuntimeError("CopyToSwapchain function bounds missing")

new_present = r'''void PresentWindow::CopyToSwapchain(Frame* frame) {
    size_t generated_count = 0;
#ifdef __ANDROID__
    if (frame_generator && Settings::values.frame_gen.GetValue()) {
        generated_count = frame_generator->Process(frame->image, frame->width, frame->height,
                                                   swapchain.GetSurfaceFormat().format);
    }
#endif

    // LSFG outputs represent the temporal positions between the previous real frame and this
    // one, so display them first and then the actual game frame.
    for (size_t i = 0; i < generated_count; ++i) {
        PresentImage(frame, frame_generator->GeneratedImage(i), true, i, false);
    }
    PresentImage(frame, frame->image, false, 0, true);
}

void PresentWindow::PresentImage(Frame* frame, vk::Image source_image, bool generated,
                                 size_t generated_index, bool final_real) {
    const auto recreate_swapchain = [&] {
#ifdef ANDROID
        {
            std::unique_lock lock{recreate_surface_mutex};
            recreate_surface_cv.wait(lock, [this]() { return surface != next_surface; });
            surface = next_surface;
        }
#endif
        std::scoped_lock submit_lock{scheduler.submit_mutex};
        graphics_queue.waitIdle();
        swapchain.Create(frame->width, frame->height, surface, low_refresh_rate);
    };

#ifndef ANDROID
    const bool use_vsync = Settings::values.use_vsync_new.GetValue();
    const bool size_changed =
        swapchain.GetWidth() != frame->width || swapchain.GetHeight() != frame->height;
    const bool vsync_changed = vsync_enabled != use_vsync;
    if (vsync_changed || size_changed) [[unlikely]] {
        vsync_enabled = use_vsync;
        recreate_swapchain();
    }
#endif

    while (!swapchain.AcquireNextImage()) {
        recreate_swapchain();
    }

    const vk::Image swapchain_image = swapchain.Image();
    const vk::CommandBufferBeginInfo begin_info = {
        .flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit,
    };
    const vk::CommandBuffer cmdbuf = frame->cmdbuf;
    cmdbuf.begin(begin_info);

#ifdef __ANDROID__
    if (generated && frame_generator) {
        frame_generator->RecordAcquireGenerated(cmdbuf, generated_index);
    }
#endif

    const vk::Extent2D extent = swapchain.GetExtent();
    const vk::ImageMemoryBarrier swapchain_barrier{
        .srcAccessMask = vk::AccessFlagBits::eNone,
        .dstAccessMask = vk::AccessFlagBits::eTransferWrite,
        .oldLayout = vk::ImageLayout::eUndefined,
        .newLayout = vk::ImageLayout::eTransferDstOptimal,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = swapchain_image,
        .subresourceRange{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = VK_REMAINING_ARRAY_LAYERS,
        },
    };
    cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe,
                           vk::PipelineStageFlagBits::eTransfer,
                           vk::DependencyFlagBits::eByRegion, {}, {}, swapchain_barrier);

    if (!generated) {
        const vk::ImageMemoryBarrier real_frame_barrier{
            .srcAccessMask = vk::AccessFlagBits::eColorAttachmentWrite,
            .dstAccessMask = vk::AccessFlagBits::eTransferRead,
            .oldLayout = vk::ImageLayout::eTransferSrcOptimal,
            .newLayout = vk::ImageLayout::eTransferSrcOptimal,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = source_image,
            .subresourceRange{
                .aspectMask = vk::ImageAspectFlagBits::eColor,
                .baseMipLevel = 0,
                .levelCount = 1,
                .baseArrayLayer = 0,
                .layerCount = VK_REMAINING_ARRAY_LAYERS,
            },
        };
        cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eColorAttachmentOutput,
                               vk::PipelineStageFlagBits::eTransfer,
                               vk::DependencyFlagBits::eByRegion, {}, {}, real_frame_barrier);
    }

    if (blit_supported || generated) {
        cmdbuf.blitImage(source_image, vk::ImageLayout::eTransferSrcOptimal, swapchain_image,
                         vk::ImageLayout::eTransferDstOptimal,
                         MakeImageBlit(frame->width, frame->height, extent.width, extent.height),
                         vk::Filter::eLinear);
    } else {
        cmdbuf.copyImage(source_image, vk::ImageLayout::eTransferSrcOptimal, swapchain_image,
                         vk::ImageLayout::eTransferDstOptimal,
                         MakeImageCopy(frame->width, frame->height, extent.width, extent.height));
    }

#ifdef __ANDROID__
    if (generated && frame_generator) {
        frame_generator->RecordReleaseGenerated(cmdbuf, generated_index);
    }
#endif

    const vk::ImageMemoryBarrier post_barrier{
        .srcAccessMask = vk::AccessFlagBits::eTransferWrite,
        .dstAccessMask = vk::AccessFlagBits::eMemoryRead,
        .oldLayout = vk::ImageLayout::eTransferDstOptimal,
        .newLayout = vk::ImageLayout::ePresentSrcKHR,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = swapchain_image,
        .subresourceRange{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = VK_REMAINING_ARRAY_LAYERS,
        },
    };
    cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,
                           vk::PipelineStageFlagBits::eBottomOfPipe,
                           vk::DependencyFlagBits::eByRegion, {}, {}, post_barrier);
    cmdbuf.end();

    static constexpr vk::PipelineStageFlags acquire_stage = vk::PipelineStageFlagBits::eTransfer;
    static constexpr std::array<vk::PipelineStageFlags, 2> real_wait_stages = {
        vk::PipelineStageFlagBits::eTransfer,
        vk::PipelineStageFlagBits::eAllGraphics,
    };

    const vk::Semaphore present_ready = swapchain.GetPresentReadySemaphore();
    const vk::Semaphore image_acquired = swapchain.GetImageAcquiredSemaphore();
    const std::array real_waits = {image_acquired, frame->render_ready};

    vk::SubmitInfo submit_info{
        .waitSemaphoreCount = generated ? 1u : 2u,
        .pWaitSemaphores = generated ? &image_acquired : real_waits.data(),
        .pWaitDstStageMask = generated ? &acquire_stage : real_wait_stages.data(),
        .commandBufferCount = 1u,
        .pCommandBuffers = &cmdbuf,
        .signalSemaphoreCount = 1u,
        .pSignalSemaphores = &present_ready,
    };

    std::scoped_lock submit_lock{scheduler.submit_mutex, recreate_surface_mutex};
    try {
        graphics_queue.submit(submit_info, final_real ? frame->present_done : vk::Fence{});
    } catch (vk::DeviceLostError& err) {
        LOG_CRITICAL(Render_Vulkan, "Device lost during framegen present submit: {}", err.what());
        UNREACHABLE();
    }
    swapchain.Present();

    // The current Azahar presenter owns one command buffer per real frame. Generated frames are
    // therefore drained before re-recording that command buffer. This is deliberately conservative
    // and can later be replaced by a small command-buffer/fence ring without changing LSFG itself.
    if (generated) {
        graphics_queue.waitIdle();
        cmdbuf.reset();
    }
}

'''
text = text[:start] + new_present + text[end:]
write(p, text)

# Fix the AAPT apostrophe issue in the generated English resource.
p = "src/android/app/src/main/res/values/strings.xml"
text = read(p).replace(
    "Generate intermediate frames inside Azahar's Vulkan renderer after the final 3DS screen composition. Vulkan only.",
    "Generate intermediate frames inside the Azahar Vulkan renderer after the final 3DS screen composition. Vulkan only.",
)
write(p, text)

# Keep the UI generator idempotent with the fixed text too.
p = "tools/apply_framegen_ui.py"
text = read(p).replace(
    "Generate intermediate frames inside Azahar's Vulkan renderer after the final 3DS screen composition. Vulkan only.",
    "Generate intermediate frames inside the Azahar Vulkan renderer after the final 3DS screen composition. Vulkan only.",
)
write(p, text)

print("Azahar integrated LSFG backend patches applied")
