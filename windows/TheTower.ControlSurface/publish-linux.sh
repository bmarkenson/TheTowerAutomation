#!/usr/bin/env bash

set -euo pipefail

client_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
gui_project="${client_dir}/TheTower.ControlSurface.csproj"
host_project="${client_dir}/../TheTower.TunnelHost/TheTower.TunnelHost.csproj"
publish_root="${client_dir}/publish"
publish_dir="${publish_root}/win-x64"
history_dir="${publish_root}/previous"
staging_dir=""
next_history_dir=""
current_backup=""
history_backup=""
had_current=0
had_history=0
installed_current=0
installed_history=0
swap_complete=0
data_root="${XDG_DATA_HOME:-${HOME}/.local/share}"
default_dotnet="${data_root}/thetower-dotnet/dotnet"
dotnet_bin="${THETOWER_DOTNET:-${default_dotnet}}"
required_files=(
    "TheTower.ControlSurface.exe"
    "TheTower.TunnelHost.exe"
)

validate_package() {
    local package_dir="$1"
    local description="$2"
    local file_name

    if [[ ! -d "${package_dir}" ]]; then
        echo "${description} is not a package directory: ${package_dir}" >&2
        return 1
    fi
    for file_name in "${required_files[@]}"; do
        if [[ ! -s "${package_dir}/${file_name}" ]]; then
            echo "${description} is missing nonempty ${file_name}:" >&2
            echo "  ${package_dir}" >&2
            return 1
        fi
    done
}

finish() {
    local status=$?
    trap - EXIT
    set +e

    if [[ "${swap_complete}" -eq 0 ]]; then
        if [[ "${installed_history}" -eq 1 && -n "${history_dir}" ]]; then
            rm -rf -- "${history_dir}"
        fi
        if [[ "${had_history}" -eq 1 && -n "${history_backup}" \
            && -d "${history_backup}" ]]; then
            mv -- "${history_backup}" "${history_dir}"
        fi
        if [[ "${installed_current}" -eq 1 && -n "${publish_dir}" ]]; then
            rm -rf -- "${publish_dir}"
        fi
        if [[ "${had_current}" -eq 1 && -n "${current_backup}" \
            && -d "${current_backup}" ]]; then
            mv -- "${current_backup}" "${publish_dir}"
        fi
    fi

    for path in "${staging_dir}" "${next_history_dir}"; do
        if [[ -n "${path}" && -e "${path}" ]]; then
            rm -rf -- "${path}"
        fi
    done
    if [[ "${swap_complete}" -eq 1 ]]; then
        for path in "${current_backup}" "${history_backup}"; do
            if [[ -n "${path}" && -e "${path}" ]]; then
                rm -rf -- "${path}"
            fi
        done
    fi
    exit "${status}"
}

trap finish EXIT

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

mkdir -p -- "${publish_root}"
exec 9>"${publish_root}/.publish.lock"
if ! flock -n 9; then
    echo "Another Windows package publication is already in progress." >&2
    exit 1
fi
staging_dir="$(mktemp -d "${publish_root}/.win-x64-staging.XXXXXX")"
next_history_dir="$(mktemp -d "${publish_root}/.previous-staging.XXXXXX")"

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

validate_package "${staging_dir}" "Newly built package"

if [[ -e "${publish_dir}" && ! -d "${publish_dir}" ]]; then
    echo "The current package path is not a directory: ${publish_dir}" >&2
    exit 1
fi
if [[ -e "${history_dir}" && ! -d "${history_dir}" ]]; then
    echo "The prior-package path is not a directory: ${history_dir}" >&2
    exit 1
fi
if [[ -d "${history_dir}" ]]; then
    unexpected_history_entry="$(
        find "${history_dir}" -mindepth 1 -maxdepth 1 \
            ! -name 1 ! -name 2 -print -quit
    )"
    if [[ -n "${unexpected_history_entry}" ]]; then
        echo "Unexpected entry in managed prior-package directory:" >&2
        echo "  ${unexpected_history_entry}" >&2
        exit 1
    fi
fi
if [[ -d "${history_dir}" && ! -d "${publish_dir}" ]]; then
    echo "Prior packages exist without a current package; refusing rotation." >&2
    exit 1
fi
for slot in 1 2; do
    if [[ -e "${history_dir}/${slot}" && ! -d "${history_dir}/${slot}" ]]; then
        echo "Prior-package slot ${slot} is not a directory; refusing rotation." >&2
        exit 1
    fi
done
if [[ -e "${history_dir}/2" && ! -d "${history_dir}/1" ]]; then
    echo "Prior-package slot 2 exists without slot 1; refusing rotation." >&2
    exit 1
fi

if [[ -d "${publish_dir}" ]]; then
    validate_package "${publish_dir}" "Current package"
    cp -a -- "${publish_dir}" "${next_history_dir}/1"
    validate_package "${next_history_dir}/1" "Staged prior package 1"
fi
if [[ -d "${history_dir}/1" ]]; then
    validate_package "${history_dir}/1" "Prior package 1"
    cp -a -- "${history_dir}/1" "${next_history_dir}/2"
    validate_package "${next_history_dir}/2" "Staged prior package 2"
fi
if [[ -d "${history_dir}/2" ]]; then
    validate_package "${history_dir}/2" "Prior package 2"
fi

current_backup="${publish_root}/.win-x64-backup-${BASHPID}"
history_backup="${publish_root}/.previous-backup-${BASHPID}"
if [[ -e "${current_backup}" || -e "${history_backup}" ]]; then
    echo "A reserved publication backup path already exists; retry." >&2
    exit 1
fi

if [[ -d "${publish_dir}" ]]; then
    had_current=1
    mv -- "${publish_dir}" "${current_backup}"
fi
if [[ -d "${history_dir}" ]]; then
    had_history=1
    mv -- "${history_dir}" "${history_backup}"
fi

installed_current=1
mv -- "${staging_dir}" "${publish_dir}"
if [[ "${had_current}" -eq 1 ]]; then
    installed_history=1
    mv -- "${next_history_dir}" "${history_dir}"
fi
validate_package "${publish_dir}" "Installed current package"
if [[ "${installed_history}" -eq 1 ]]; then
    validate_package "${history_dir}/1" "Installed prior package 1"
    if [[ -d "${history_dir}/2" ]]; then
        validate_package "${history_dir}/2" "Installed prior package 2"
    fi
fi
swap_complete=1

echo "Published complete standalone Control Surface package to:"
echo "${publish_dir}"
for file_name in "${required_files[@]}"; do
    echo "  ${file_name}"
done
if [[ -d "${history_dir}/1" ]]; then
    echo "Retained prior complete packages (newest first):"
    echo "  ${history_dir}/1"
    if [[ -d "${history_dir}/2" ]]; then
        echo "  ${history_dir}/2"
    fi
fi
