// Copyright Citra Emulator Project / Azahar Emulator Project
// Licensed under GPLv2 or any later version
// Refer to the license.txt file included.

#include "video_core/renderer_vulkan/vk_framegen.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <optional>
#include <span>
#include <string>
#include <unordered_map>

#include "common/logging/log.h"
#include "common/settings.h"
#include "video_core/renderer_vulkan/vk_instance.h"

#ifdef __ANDROID__
#include <android/hardware_buffer.h>
#include <lsfg_3_1p.hpp>
#endif

namespace Vulkan {

#ifdef __ANDROID__
namespace {

constexpr vk::Format SharedFormat = vk::Format::eR8G8B8A8Unorm;
constexpr u32 SpirvMagic = 0x07230203;
constexpr u32 Fp16Offset = 49;
constexpr u32 Fp32Offset = 98;

constexpr std::array<u32, 26> PerformanceShaderIds = {
    255, 256,
    280, 281, 282, 283, 284, 285, 286, 287, 288, 289,
    290, 291, 292, 293,
    294, 295, 296, 297,
    298, 299, 300, 301, 302,
};

std::optional<u32> ShaderBaseId(std::string_view name) {
    static const std::unordered_map<std::string, u32> table{
        {"p_mipmaps", 255}, {"p_generate", 256},
        {"p_gamma[0]", 280}, {"p_gamma[1]", 282}, {"p_gamma[2]", 283},
        {"p_gamma[3]", 284}, {"p_gamma[4]", 285},
        {"p_delta[0]", 280}, {"p_delta[1]", 286}, {"p_delta[2]", 287},
        {"p_delta[3]", 288}, {"p_delta[4]", 289}, {"p_delta[5]", 281},
        {"p_delta[6]", 294}, {"p_delta[7]", 295}, {"p_delta[8]", 296},
        {"p_delta[9]", 297},
        {"p_alpha[0]", 290}, {"p_alpha[1]", 291}, {"p_alpha[2]", 292},
        {"p_alpha[3]", 293},
        {"p_beta[0]", 298}, {"p_beta[1]", 299}, {"p_beta[2]", 300},
        {"p_beta[3]", 301}, {"p_beta[4]", 302},
    };
    const auto it = table.find(std::string{name});
    if (it == table.end()) {
        return std::nullopt;
    }
    return it->second;
}

bool IsSpirv(std::span<const u8> bytes) {
    if (bytes.size() < sizeof(u32) || (bytes.size() & 3) != 0) {
        return false;
    }
    u32 magic{};
    std::memcpy(&magic, bytes.data(), sizeof(magic));
    return magic == SpirvMagic;
}

class PeReader {
public:
    explicit PeReader(std::span<const u8> image_) : image{image_} {}

    template <typename T>
    bool Read(size_t offset, T& value) const {
        if (offset > image.size() || image.size() - offset < sizeof(T)) {
            return false;
        }
        std::memcpy(&value, image.data() + offset, sizeof(T));
        return true;
    }

    bool Slice(size_t offset, size_t size, std::span<const u8>& out) const {
        if (offset > image.size() || image.size() - offset < size) {
            return false;
        }
        out = image.subspan(offset, size);
        return true;
    }

private:
    std::span<const u8> image;
};

struct PeSection {
    u32 virtual_address{};
    u32 virtual_size{};
    u32 raw_address{};
    u32 raw_size{};
};

struct ResourceEntry {
    u32 id{};
    u32 offset{};
    bool directory{};
    bool named{};
};

std::optional<size_t> RvaToFile(std::span<const PeSection> sections, u32 rva) {
    for (const auto& section : sections) {
        const u32 span = std::max(section.virtual_size, section.raw_size);
        if (span == 0 || rva < section.virtual_address) {
            continue;
        }
        const u32 relative = rva - section.virtual_address;
        if (relative < span) {
            return static_cast<size_t>(section.raw_address) + relative;
        }
    }
    return std::nullopt;
}

bool ReadDirectory(const PeReader& reader, size_t offset, std::vector<ResourceEntry>& entries) {
    constexpr size_t DirectoryHeaderSize = 16;
    constexpr size_t EntrySize = 8;
    constexpr u32 HighBit = 0x80000000u;
    u16 named{};
    u16 ids{};
    if (!reader.Read(offset + 12, named) || !reader.Read(offset + 14, ids)) {
        return false;
    }
    const size_t count = static_cast<size_t>(named) + ids;
    entries.clear();
    entries.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        u32 name{};
        u32 data{};
        const size_t e = offset + DirectoryHeaderSize + i * EntrySize;
        if (!reader.Read(e, name) || !reader.Read(e + 4, data)) {
            return false;
        }
        entries.push_back({
            .id = name & ~HighBit,
            .offset = data & ~HighBit,
            .directory = (data & HighBit) != 0,
            .named = (name & HighBit) != 0,
        });
    }
    return true;
}

bool ParseRcData(std::span<const u8> bytes, std::unordered_map<u32, std::vector<u8>>& out) {
    constexpr u16 DosMagic = 0x5A4D;
    constexpr u32 PeSignature = 0x00004550;
    constexpr u16 Pe32Magic = 0x010B;
    constexpr u16 Pe32PlusMagic = 0x020B;
    constexpr u32 RcDataType = 10;
    constexpr u32 DirectoryIndexResources = 2;

    PeReader reader{bytes};
    u16 dos{};
    u32 pe_offset{};
    u32 pe_signature{};
    if (!reader.Read(0, dos) || dos != DosMagic || !reader.Read(0x3c, pe_offset) ||
        !reader.Read(pe_offset, pe_signature) || pe_signature != PeSignature) {
        return false;
    }

    u16 section_count{};
    u16 optional_size{};
    if (!reader.Read(pe_offset + 6, section_count) || !reader.Read(pe_offset + 20, optional_size)) {
        return false;
    }
    const size_t optional = static_cast<size_t>(pe_offset) + 24;
    u16 optional_magic{};
    if (!reader.Read(optional, optional_magic)) {
        return false;
    }
    size_t data_directory{};
    if (optional_magic == Pe32Magic) {
        data_directory = optional + 96;
    } else if (optional_magic == Pe32PlusMagic) {
        data_directory = optional + 112;
    } else {
        return false;
    }

    std::vector<PeSection> sections;
    sections.reserve(section_count);
    const size_t section_table = optional + optional_size;
    for (u16 i = 0; i < section_count; ++i) {
        const size_t s = section_table + static_cast<size_t>(i) * 40;
        PeSection section{};
        if (!reader.Read(s + 8, section.virtual_size) || !reader.Read(s + 12, section.virtual_address) ||
            !reader.Read(s + 16, section.raw_size) || !reader.Read(s + 20, section.raw_address)) {
            return false;
        }
        sections.push_back(section);
    }

    u32 resources_rva{};
    if (!reader.Read(data_directory + DirectoryIndexResources * 8, resources_rva) || resources_rva == 0) {
        return false;
    }
    const auto resource_base_opt = RvaToFile(sections, resources_rva);
    if (!resource_base_opt) {
        return false;
    }
    const size_t resource_base = *resource_base_opt;

    std::vector<ResourceEntry> type_entries;
    if (!ReadDirectory(reader, resource_base, type_entries)) {
        return false;
    }
    for (const auto& type : type_entries) {
        if (type.named || type.id != RcDataType || !type.directory) {
            continue;
        }
        std::vector<ResourceEntry> name_entries;
        if (!ReadDirectory(reader, resource_base + type.offset, name_entries)) {
            return false;
        }
        for (const auto& name : name_entries) {
            if (name.named || !name.directory) {
                continue;
            }
            std::vector<ResourceEntry> language_entries;
            if (!ReadDirectory(reader, resource_base + name.offset, language_entries)) {
                return false;
            }
            for (const auto& language : language_entries) {
                if (language.directory) {
                    continue;
                }
                u32 data_rva{};
                u32 data_size{};
                const size_t leaf = resource_base + language.offset;
                if (!reader.Read(leaf, data_rva) || !reader.Read(leaf + 4, data_size) || data_size == 0) {
                    continue;
                }
                const auto file_offset = RvaToFile(sections, data_rva);
                std::span<const u8> data;
                if (!file_offset || !reader.Slice(*file_offset, data_size, data)) {
                    continue;
                }
                out[name.id] = std::vector<u8>{data.begin(), data.end()};
                break;
            }
        }
    }
    return !out.empty();
}

bool VariantComplete(const std::unordered_map<u32, std::vector<u8>>& resources, u32 offset) {
    for (const u32 id : PerformanceShaderIds) {
        const auto it = resources.find(id + offset);
        if (it == resources.end() || !IsSpirv(it->second)) {
            return false;
        }
    }
    return true;
}

u32 FindMemoryType(vk::PhysicalDevice physical_device, u32 bits) {
    const vk::PhysicalDeviceMemoryProperties props = physical_device.getMemoryProperties();
    for (u32 i = 0; i < props.memoryTypeCount; ++i) {
        if ((bits & (1u << i)) != 0) {
            return i;
        }
    }
    return std::numeric_limits<u32>::max();
}

} // Anonymous namespace
#endif

FrameGenerator::FrameGenerator(const Instance& instance_)
#ifdef __ANDROID__
    : instance{instance_}, device{instance.GetDevice()}, physical_device{instance.GetPhysicalDevice()},
      queue{instance.GetGraphicsQueue()}, queue_family{instance.GetGraphicsQueueFamilyIndex()}
#endif
{
#ifdef __ANDROID__
    const vk::CommandPoolCreateInfo pool_info{
        .flags = vk::CommandPoolCreateFlagBits::eResetCommandBuffer |
                 vk::CommandPoolCreateFlagBits::eTransient,
        .queueFamilyIndex = queue_family,
    };
    try {
        command_pool = device.createCommandPool(pool_info);
    } catch (const std::exception& e) {
        LOG_ERROR(Render_Vulkan, "FrameGen: command pool creation failed: {}", e.what());
        unavailable = true;
    }
#else
    (void)instance_;
#endif
}

FrameGenerator::~FrameGenerator() {
#ifdef __ANDROID__
    Reset();
    if (command_pool) {
        device.destroyCommandPool(command_pool);
    }
#endif
}

void FrameGenerator::Reset() {
#ifdef __ANDROID__
    if (queue) {
        queue.waitIdle();
    }
    if (context_id >= 0) {
        try {
            LSFG_3_1P::waitIdle();
            LSFG_3_1P::deleteContext(context_id);
        } catch (const std::exception& e) {
            LOG_WARNING(Render_Vulkan, "FrameGen: context cleanup failed: {}", e.what());
        }
        context_id = -1;
    }
    if (backend_initialized) {
        try {
            LSFG_3_1P::finalize();
        } catch (...) {
        }
        backend_initialized = false;
    }
    for (auto& image : inputs) {
        DestroySharedImage(image);
    }
    for (auto& image : outputs) {
        DestroySharedImage(image);
    }
    inputs.clear();
    outputs.clear();
    built_width = 0;
    built_height = 0;
    built_generations = 0;
    frame_count = 0;
#endif
}

#ifdef __ANDROID__
bool FrameGenerator::LoadShaders() {
    const char* path_env = std::getenv("AZAHAR_LOSSLESS_DLL");
    if (!path_env || *path_env == '\0') {
        LOG_WARNING(Render_Vulkan, "FrameGen: Lossless.dll path is not configured");
        return false;
    }
    const std::filesystem::path path{path_env};
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) {
        LOG_WARNING(Render_Vulkan, "FrameGen: cannot open {}", path.string());
        return false;
    }
    const auto end = file.tellg();
    if (end <= 0) {
        return false;
    }
    std::vector<u8> image(static_cast<size_t>(end));
    file.seekg(0, std::ios::beg);
    if (!file.read(reinterpret_cast<char*>(image.data()), static_cast<std::streamsize>(image.size()))) {
        return false;
    }

    shader_resources.clear();
    if (!ParseRcData(image, shader_resources)) {
        LOG_ERROR(Render_Vulkan, "FrameGen: Lossless.dll is not a supported PE resource image");
        return false;
    }

    const bool prefer_fp16 = Settings::values.frame_gen_fp16.GetValue();
    if (prefer_fp16 && VariantComplete(shader_resources, Fp16Offset)) {
        native_shader_offset = Fp16Offset;
        LOG_INFO(Render_Vulkan, "FrameGen: using native FP16 LSFG 3.1P shaders");
        return true;
    }
    if (VariantComplete(shader_resources, Fp32Offset)) {
        native_shader_offset = Fp32Offset;
        LOG_INFO(Render_Vulkan, "FrameGen: using native FP32 LSFG 3.1P shaders");
        return true;
    }
    if (VariantComplete(shader_resources, Fp16Offset)) {
        native_shader_offset = Fp16Offset;
        LOG_INFO(Render_Vulkan, "FrameGen: FP32 unavailable, falling back to FP16 LSFG shaders");
        return true;
    }

    LOG_ERROR(Render_Vulkan, "FrameGen: compatible native SPIR-V shaders are missing from Lossless.dll");
    return false;
}

bool FrameGenerator::CreateSharedImage(SharedImage& out, u32 width, u32 height) {
    AHardwareBuffer_Desc desc{
        .width = width,
        .height = height,
        .layers = 1,
        .format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM,
        .usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT,
        .stride = 0,
        .rfu0 = 0,
        .rfu1 = 0,
    };
    if (AHardwareBuffer_allocate(&desc, &out.ahb) != 0 || !out.ahb) {
        LOG_ERROR(Render_Vulkan, "FrameGen: AHardwareBuffer_allocate failed for {}x{}", width, height);
        return false;
    }

    vk::AndroidHardwareBufferFormatPropertiesANDROID format_props{};
    vk::AndroidHardwareBufferPropertiesANDROID ahb_props{};
    ahb_props.pNext = &format_props;
    const vk::Result properties_result = device.getAndroidHardwareBufferPropertiesANDROID(out.ahb, &ahb_props);
    if (properties_result != vk::Result::eSuccess) {
        LOG_ERROR(Render_Vulkan, "FrameGen: getAndroidHardwareBufferProperties failed: {}",
                  vk::to_string(properties_result));
        AHardwareBuffer_release(out.ahb);
        out.ahb = nullptr;
        return false;
    }

    vk::ExternalMemoryImageCreateInfo external_info{
        .handleTypes = vk::ExternalMemoryHandleTypeFlagBits::eAndroidHardwareBufferANDROID,
    };
    vk::ImageCreateInfo image_info{
        .pNext = &external_info,
        .imageType = vk::ImageType::e2D,
        .format = SharedFormat,
        .extent = {width, height, 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = vk::SampleCountFlagBits::e1,
        .tiling = vk::ImageTiling::eOptimal,
        .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eStorage |
                 vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst,
        .sharingMode = vk::SharingMode::eExclusive,
        .initialLayout = vk::ImageLayout::eUndefined,
    };
    const auto image_result = device.createImage(image_info);
    if (image_result.result != vk::Result::eSuccess) {
        LOG_ERROR(Render_Vulkan, "FrameGen: createImage(AHB) failed: {}", vk::to_string(image_result.result));
        AHardwareBuffer_release(out.ahb);
        out.ahb = nullptr;
        return false;
    }
    out.image = image_result.value;

    const u32 memory_type = FindMemoryType(physical_device, ahb_props.memoryTypeBits);
    if (memory_type == std::numeric_limits<u32>::max()) {
        DestroySharedImage(out);
        return false;
    }

    vk::MemoryDedicatedAllocateInfo dedicated{
        .image = out.image,
    };
    vk::ImportAndroidHardwareBufferInfoANDROID import_info{
        .pNext = &dedicated,
        .buffer = out.ahb,
    };
    vk::MemoryAllocateInfo allocation_info{
        .pNext = &import_info,
        .allocationSize = ahb_props.allocationSize,
        .memoryTypeIndex = memory_type,
    };
    const auto memory_result = device.allocateMemory(allocation_info);
    if (memory_result.result != vk::Result::eSuccess) {
        LOG_ERROR(Render_Vulkan, "FrameGen: allocateMemory(AHB) failed: {}",
                  vk::to_string(memory_result.result));
        DestroySharedImage(out);
        return false;
    }
    out.memory = memory_result.value;
    if (device.bindImageMemory(out.image, out.memory, 0) != vk::Result::eSuccess) {
        LOG_ERROR(Render_Vulkan, "FrameGen: bindImageMemory(AHB) failed");
        DestroySharedImage(out);
        return false;
    }
    out.layout = vk::ImageLayout::eUndefined;
    return true;
}

void FrameGenerator::DestroySharedImage(SharedImage& image) {
    if (image.image) {
        device.destroyImage(image.image);
        image.image = {};
    }
    if (image.memory) {
        device.freeMemory(image.memory);
        image.memory = {};
    }
    if (image.ahb) {
        AHardwareBuffer_release(image.ahb);
        image.ahb = nullptr;
    }
    image.layout = vk::ImageLayout::eUndefined;
}

bool FrameGenerator::Rebuild(u32 width, u32 height, u32 generations, float flow_scale) {
    Reset();
    if (!LoadShaders()) {
        return false;
    }

    inputs.resize(2);
    outputs.resize(generations);
    for (auto& image : inputs) {
        if (!CreateSharedImage(image, width, height)) {
            Reset();
            return false;
        }
    }
    for (auto& image : outputs) {
        if (!CreateSharedImage(image, width, height)) {
            Reset();
            return false;
        }
    }

    try {
        const u64 uuid = (static_cast<u64>(instance.GetVendorID()) << 32) | instance.GetDeviceID();
        const auto loader = [this](const std::string& name) -> std::vector<u8> {
            const auto base = ShaderBaseId(name);
            if (!base) {
                LOG_ERROR(Render_Vulkan, "FrameGen: unknown shader requested: {}", name);
                return {};
            }
            const auto it = shader_resources.find(*base + native_shader_offset);
            return it == shader_resources.end() ? std::vector<u8>{} : it->second;
        };
        LSFG_3_1P::initialize(uuid, false, flow_scale, generations, loader);
        backend_initialized = true;
        std::vector<AHardwareBuffer*> out_ahbs;
        out_ahbs.reserve(outputs.size());
        for (auto& image : outputs) {
            out_ahbs.push_back(image.ahb);
        }
        context_id = LSFG_3_1P::createContextFromAHB(
            inputs[0].ahb, inputs[1].ahb, out_ahbs,
            VkExtent2D{width, height}, static_cast<VkFormat>(SharedFormat));
    } catch (const std::exception& e) {
        LOG_ERROR(Render_Vulkan, "FrameGen: LSFG initialization failed: {}", e.what());
        Reset();
        return false;
    }

    built_width = width;
    built_height = height;
    built_generations = generations;
    built_flow_scale = flow_scale;
    frame_count = 0;
    LOG_INFO(Render_Vulkan, "FrameGen: pipeline ready {}x{}, {} generated frame(s), flow scale {:.2f}",
             width, height, generations, flow_scale);
    return true;
}

bool FrameGenerator::CopyInput(vk::Image source, SharedImage& destination, u32 width, u32 height,
                               vk::Format source_format) {
    const vk::CommandBufferAllocateInfo alloc_info{
        .commandPool = command_pool,
        .level = vk::CommandBufferLevel::ePrimary,
        .commandBufferCount = 1,
    };
    const auto command_buffers = device.allocateCommandBuffers(alloc_info);
    if (command_buffers.empty()) {
        return false;
    }
    const vk::CommandBuffer cmdbuf = command_buffers[0];
    cmdbuf.begin({.flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit});

    const bool previously_external = destination.layout != vk::ImageLayout::eUndefined;
    const vk::ImageMemoryBarrier acquire{
        .srcAccessMask = {},
        .dstAccessMask = vk::AccessFlagBits::eTransferWrite,
        .oldLayout = destination.layout,
        .newLayout = vk::ImageLayout::eTransferDstOptimal,
        .srcQueueFamilyIndex = previously_external ? VK_QUEUE_FAMILY_EXTERNAL : VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = previously_external ? queue_family : VK_QUEUE_FAMILY_IGNORED,
        .image = destination.image,
        .subresourceRange{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe,
                           vk::PipelineStageFlagBits::eTransfer, {}, {}, {}, acquire);

    const vk::ImageSubresourceLayers layers{
        .aspectMask = vk::ImageAspectFlagBits::eColor,
        .mipLevel = 0,
        .baseArrayLayer = 0,
        .layerCount = 1,
    };
    if (source_format == SharedFormat) {
        const vk::ImageCopy copy{
            .srcSubresource = layers,
            .srcOffset = {},
            .dstSubresource = layers,
            .dstOffset = {},
            .extent = {width, height, 1},
        };
        cmdbuf.copyImage(source, vk::ImageLayout::eTransferSrcOptimal, destination.image,
                         vk::ImageLayout::eTransferDstOptimal, copy);
    } else {
        const vk::ImageBlit blit{
            .srcSubresource = layers,
            .srcOffsets = std::array{vk::Offset3D{0, 0, 0},
                                     vk::Offset3D{static_cast<s32>(width), static_cast<s32>(height), 1}},
            .dstSubresource = layers,
            .dstOffsets = std::array{vk::Offset3D{0, 0, 0},
                                     vk::Offset3D{static_cast<s32>(width), static_cast<s32>(height), 1}},
        };
        cmdbuf.blitImage(source, vk::ImageLayout::eTransferSrcOptimal, destination.image,
                         vk::ImageLayout::eTransferDstOptimal, blit, vk::Filter::eNearest);
    }

    const vk::ImageMemoryBarrier release{
        .srcAccessMask = vk::AccessFlagBits::eTransferWrite,
        .dstAccessMask = {},
        .oldLayout = vk::ImageLayout::eTransferDstOptimal,
        .newLayout = vk::ImageLayout::eGeneral,
        .srcQueueFamilyIndex = queue_family,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_EXTERNAL,
        .image = destination.image,
        .subresourceRange = acquire.subresourceRange,
    };
    cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,
                           vk::PipelineStageFlagBits::eBottomOfPipe, {}, {}, {}, release);
    cmdbuf.end();

    const vk::SubmitInfo submit{
        .commandBufferCount = 1,
        .pCommandBuffers = &cmdbuf,
    };
    queue.submit(submit);
    queue.waitIdle();
    device.freeCommandBuffers(command_pool, cmdbuf);
    destination.layout = vk::ImageLayout::eGeneral;
    return true;
}

u32 FrameGenerator::DesiredGenerations() {
    const u32 fixed = std::clamp(Settings::values.frame_gen_multiplier.GetValue(), 2u, 4u) - 1u;
    const u32 target = Settings::values.frame_gen_target_rate.GetValue();
    if (target == 0 || smoothed_fps < 10.0) {
        return fixed;
    }
    const long multiplier = std::lround(static_cast<double>(target) / smoothed_fps);
    return static_cast<u32>(std::clamp(multiplier, 2l, 4l) - 1l);
}
#endif

size_t FrameGenerator::Process(vk::Image source, u32 width, u32 height, vk::Format source_format) {
#ifdef __ANDROID__
    if (unavailable || !Settings::values.frame_gen.GetValue()) {
        return 0;
    }

    const auto now = std::chrono::steady_clock::now();
    if (last_real_frame.time_since_epoch().count() != 0) {
        const double seconds = std::chrono::duration<double>(now - last_real_frame).count();
        if (seconds > 0.001 && seconds < 0.2) {
            const double fps = 1.0 / seconds;
            smoothed_fps = smoothed_fps == 0.0 ? fps : (smoothed_fps * 0.90 + fps * 0.10);
        }
    }
    last_real_frame = now;

    const u32 desired = DesiredGenerations();
    if (pending_generations == desired) {
        ++desired_streak;
    } else {
        pending_generations = desired;
        desired_streak = 0;
    }
    const u32 generations = built_generations == 0 || desired_streak >= 12 ? desired : built_generations;
    const float flow_scale = Settings::values.frame_gen_flow_scale_auto.GetValue()
                                 ? std::clamp(1.0f / static_cast<float>(std::max(1u, Settings::values.resolution_factor.GetValue())), 0.25f, 1.0f)
                                 : static_cast<float>(Settings::values.frame_gen_flow_scale.GetValue()) / 100.0f;

    if (built_width != width || built_height != height || built_generations != generations ||
        std::abs(built_flow_scale - flow_scale) > 0.001f) {
        if (!Rebuild(width, height, generations, flow_scale)) {
            unavailable = true;
            return 0;
        }
        desired_streak = 0;
    }

    SharedImage& input = inputs[frame_count & 1u];
    if (!CopyInput(source, input, width, height, source_format)) {
        LOG_ERROR(Render_Vulkan, "FrameGen: failed to copy composed frame into AHB input");
        return 0;
    }

    const bool warm = frame_count > 0;
    if (warm) {
        try {
            // Cross-device correctness barrier. The presenter can replace these full waits with
            // exported SYNC_FD semaphores later without changing the framegen pipeline itself.
            queue.waitIdle();
            LSFG_3_1P::presentContext(context_id, -1, {});
            LSFG_3_1P::waitIdle();
        } catch (const std::exception& e) {
            LOG_ERROR(Render_Vulkan, "FrameGen: generation failed: {}", e.what());
            unavailable = true;
            return 0;
        }
    }
    ++frame_count;
    return warm ? outputs.size() : 0;
#else
    (void)source;
    (void)width;
    (void)height;
    (void)source_format;
    return 0;
#endif
}

vk::Image FrameGenerator::GeneratedImage(size_t index) const {
#ifdef __ANDROID__
    return index < outputs.size() ? outputs[index].image : vk::Image{};
#else
    (void)index;
    return {};
#endif
}

void FrameGenerator::RecordAcquireGenerated(vk::CommandBuffer cmdbuf, size_t index) {
#ifdef __ANDROID__
    if (index >= outputs.size()) {
        return;
    }
    auto& image = outputs[index];
    const vk::ImageMemoryBarrier barrier{
        .srcAccessMask = {},
        .dstAccessMask = vk::AccessFlagBits::eTransferRead,
        .oldLayout = vk::ImageLayout::eGeneral,
        .newLayout = vk::ImageLayout::eTransferSrcOptimal,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_EXTERNAL,
        .dstQueueFamilyIndex = queue_family,
        .image = image.image,
        .subresourceRange{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe,
                           vk::PipelineStageFlagBits::eTransfer, {}, {}, {}, barrier);
    image.layout = vk::ImageLayout::eTransferSrcOptimal;
#else
    (void)cmdbuf;
    (void)index;
#endif
}

void FrameGenerator::RecordReleaseGenerated(vk::CommandBuffer cmdbuf, size_t index) {
#ifdef __ANDROID__
    if (index >= outputs.size()) {
        return;
    }
    auto& image = outputs[index];
    const vk::ImageMemoryBarrier barrier{
        .srcAccessMask = vk::AccessFlagBits::eTransferRead,
        .dstAccessMask = {},
        .oldLayout = vk::ImageLayout::eTransferSrcOptimal,
        .newLayout = vk::ImageLayout::eGeneral,
        .srcQueueFamilyIndex = queue_family,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_EXTERNAL,
        .image = image.image,
        .subresourceRange{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,
                           vk::PipelineStageFlagBits::eBottomOfPipe, {}, {}, {}, barrier);
    image.layout = vk::ImageLayout::eGeneral;
#else
    (void)cmdbuf;
    (void)index;
#endif
}

} // namespace Vulkan
