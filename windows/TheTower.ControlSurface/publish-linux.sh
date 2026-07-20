#!/usr/bin/env bash

set -euo pipefail

client_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_path="${client_dir}/TheTower.ControlSurface.csproj"
publish_dir="${client_dir}/publish/win-x64"
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

"${dotnet_bin}" publish "${project_path}" \
    --configuration Release \
    --runtime win-x64 \
    --self-contained true \
    --output "${publish_dir}" \
    -p:PublishSingleFile=true \
    -p:IncludeNativeLibrariesForSelfExtract=true \
    -p:EnableCompressionInSingleFile=true

executable="${publish_dir}/TheTower.ControlSurface.exe"
if [[ ! -f "${executable}" ]]; then
    echo "Publish completed without creating ${executable}" >&2
    exit 1
fi

echo "Published standalone Windows application to:"
echo "${executable}"
