// Copyright Citra Emulator Project / Azahar Emulator Project
// Licensed under GPLv2 or any later version
// Refer to the license.txt file included.

#pragma once

#include <chrono>
#include <memory>
#include <vector>

#include "video_core/renderer_vulkan/vk_common.h"

#ifdef __ANDROID__
struct AHardwareBuffer;
#endif

namespace Vulkan {

class Instance;

/// In-process LSFG bridge used by Azahar's Vulkan presenter.
///
/// The real 3DS frame is copied directly from the final compositor image into
/// an AHardwareBuffer shared with the LSFG compute device. No MediaProjection,
/// SurfaceView capture or CPU readback is involved.
class FrameGenerator final {
public:
    explicit FrameGenerator(const Instance& instance);
    ~FrameGenerator();

    FrameGenerator(const FrameGenerator&) = delete;
    FrameGenerator& operator=(const FrameGenerator&) = delete;

    /// Feed one final composed frame to LSFG. Returns the number of generated
    /// frames that are ready to be inserted before the current real frame.
    size_t Process(vk::Image source, u32 width, u32 height, vk::Format source_format);

    [[nodiscard]] vk::Image GeneratedImage(size_t index) const;

    /// Transfer queue ownership of a generated AHB image from LSFG to Azahar
    /// and put it in TRANSFER_SRC_OPTIMAL.
    void RecordAcquireGenerated(vk::CommandBuffer cmdbuf, size_t index);

    /// Return a generated AHB image to external ownership so LSFG can safely
    /// overwrite it during a later generation pass.
    void RecordReleaseGenerated(vk::CommandBuffer cmdbuf, size_t index);

    void Reset();

private:
#ifdef __ANDROID__
    struct SharedImage {
        AHardwareBuffer* ahb{};
        vk::Image image{};
        vk::DeviceMemory memory{};
        vk::ImageLayout layout{vk::ImageLayout::eUndefined};
    };

    bool Rebuild(u32 width, u32 height, u32 generations, float flow_scale);
    bool CreateSharedImage(SharedImage& out, u32 width, u32 height);
    void DestroySharedImage(SharedImage& image);
    bool CopyInput(vk::Image source, SharedImage& destination, u32 width, u32 height,
                   vk::Format source_format);
    bool LoadShaders();
    u32 DesiredGenerations();

    const Instance& instance;
    vk::Device device{};
    vk::PhysicalDevice physical_device{};
    vk::Queue queue{};
    u32 queue_family{};
    vk::CommandPool command_pool{};

    std::vector<SharedImage> inputs;
    std::vector<SharedImage> outputs;
    std::unordered_map<u32, std::vector<u8>> shader_resources;

    s32 context_id{-1};
    u32 built_width{};
    u32 built_height{};
    u32 built_generations{};
    float built_flow_scale{};
    u64 frame_count{};
    bool backend_initialized{};
    bool unavailable{};
    u32 native_shader_offset{};

    std::chrono::steady_clock::time_point last_real_frame{};
    double smoothed_fps{};
    u32 desired_streak{};
    u32 pending_generations{};
#endif
};

} // namespace Vulkan
