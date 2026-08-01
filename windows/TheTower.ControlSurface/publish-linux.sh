#!/usr/bin/env bash

set -euo pipefail

client_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
gui_project="${client_dir}/TheTower.ControlSurface.csproj"
host_project="${client_dir}/../TheTower.TunnelHost/TheTower.TunnelHost.csproj"
publish_dir="${client_dir}/publish/win-x64"
staging_dir="${client_dir}/publish/.win-x64-staging-$$"
data_root="${XDG_DATA_HOME:-${HOME}/.local/share}"
default_dotnet="${data_root}/thetower-dotnet/dotnet"
dotnet_bin="${THETOWER_DOTNET:-${default_dotnet}}"

if [[ ! -x "${dotnet_bin}" ]]; then
    dotnet_bin="$(command -v dotnet || true)"
fi

if [[ -z "${dotnet_bin}" || ! -x "${dotnet_bin}" ]]; then
    echo "No usable .NET SDK executable was found." >&2
    echo "Follow the Linux SDK instructions in README.md, then retry." >&2
    exit 1
fi

sdk_base="$("${dotnet_bin}" --info | sed -n 's/^ Base Path:[[:space:]]*//p')"
windows_desktop_targets="${sdk_base}/Sdks/Microsoft.NET.Sdk.WindowsDesktop/targets/Microsoft.NET.Sdk.WindowsDesktop.targets"
if [[ -z "${sdk_base}" || ! -f "${windows_desktop_targets}" ]]; then
    echo "The selected SDK does not include Microsoft.NET.Sdk.WindowsDesktop:" >&2
    echo "  ${dotnet_bin}" >&2
    echo "Ubuntu's Canonical dotnet-sdk-8.0 package omits these cross-build files." >&2
    echo "Install Microsoft's SDK side-by-side as described in README.md." >&2
    exit 1
fi

cleanup_staging() {
    rm -rf -- "${staging_dir}"
}
trap cleanup_staging EXIT
mkdir -p -- "${staging_dir}"

for project_path in "${host_project}" "${gui_project}"; do
    "${dotnet_bin}" publish "${project_path}" \
        --configuration Release \
        --runtime win-x64 \
        --self-contained true \
        --output "${staging_dir}" \
        -p:PublishSingleFile=true \
        -p:IncludeNativeLibrariesForSelfExtract=true \
        -p:EnableCompressionInSingleFile=true
done

required_files=(
    "TheTower.ControlSurface.exe"
    "TheTower.TunnelHost.exe"
)
for file_name in "${required_files[@]}"; do
    if [[ ! -f "${staging_dir}/${file_name}" ]]; then
        echo "Publish completed without creating ${staging_dir}/${file_name}" >&2
        exit 1
    fi
done

rm -rf -- "${publish_dir}"
mv -- "${staging_dir}" "${publish_dir}"
trap - EXIT

echo "Published complete standalone Control Surface package to:"
echo "${publish_dir}"
for file_name in "${required_files[@]}"; do
    echo "  ${file_name}"
done
