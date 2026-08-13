using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace TheTower.ControlSurface;

internal sealed record BlueStacksRecoveryTarget(
    string ExecutablePath,
    string InstanceName,
    int AdbPort)
{
    public static BlueStacksRecoveryTarget Capture(ClientSettings settings) =>
        Create(
            settings.BlueStacksPlayerExecutablePath,
            settings.BlueStacksInstanceName,
            settings.WindowsBlueStacksAdbPort);

    public static BlueStacksRecoveryTarget FromAcknowledgement(
        BlueStacksHostProcessIdentity acknowledgement) =>
        Create(
            acknowledgement.ExecutablePath,
            acknowledgement.InstanceName,
            acknowledgement.AdbPort);

    public static BlueStacksRecoveryTarget Create(
        string executablePath,
        string instanceName,
        int adbPort)
    {
        if (adbPort is < 1 or > 65535)
        {
            throw new ArgumentException(
                "The Windows BlueStacks ADB port must be between 1 and 65535.");
        }
        return new BlueStacksRecoveryTarget(
            BlueStacksInstanceController.ValidateExecutablePath(executablePath),
            BlueStacksInstanceController.ValidateInstanceName(instanceName),
            adbPort);
    }
}

internal sealed record BlueStacksProcessIdentity(
    string HostId,
    int AdbPort,
    int ProcessId,
    DateTimeOffset ProcessStartedAtUtc,
    string ExecutablePath)
{
    public string ProcessStartedAtText => ProcessStartedAtUtc.ToString("O");
}

internal sealed record BlueStacksRestartResult(
    BlueStacksProcessIdentity Previous,
    BlueStacksProcessIdentity Replacement);

internal interface IBlueStacksInstanceController
{
    BlueStacksProcessIdentity Inspect(
        BlueStacksRecoveryTarget target,
        bool requireSingleActiveInstance = true);

    Task<BlueStacksRestartResult> RestartAcknowledgedAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken);

    Task<BlueStacksRestartResult> StartAfterAcknowledgedStopAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken);

    Task<BlueStacksRestartResult> ConfirmReplacementAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken);
}

internal sealed class BlueStacksListenerUnavailableException :
    InvalidOperationException
{
    public BlueStacksListenerUnavailableException(string message) : base(message)
    {
    }
}

internal sealed class BlueStacksTargetBindingException : InvalidOperationException
{
    public BlueStacksTargetBindingException(string message) : base(message)
    {
    }
}

internal sealed class BlueStacksInstanceController : IBlueStacksInstanceController
{
    private const int AddressFamilyInterNetwork = 2;
    private const int ErrorInvalidParameter = 87;
    private const int ErrorInsufficientBuffer = 122;
    private const uint StillActive = 259;
    private const int MaximumWindowsPathCharacters = 32768;
    private static readonly Regex InstanceNamePattern = new(
        @"\A[A-Za-z0-9_.-]{1,64}\z",
        RegexOptions.CultureInvariant);
    private static readonly Regex InstanceAdbPortPattern = new(
        @"\Abst\.instance\.(?<instance>[A-Za-z0-9_.-]{1,64})\.status\.adb_port\s*=\s*""?(?<port>\d{1,5})""?\s*\z",
        RegexOptions.CultureInvariant);
    private readonly string _configurationPath;

    public BlueStacksInstanceController(string? configurationPath = null)
    {
        _configurationPath = configurationPath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "BlueStacks_nxt",
            "bluestacks.conf");
    }

    public BlueStacksProcessIdentity Inspect(
        BlueStacksRecoveryTarget target,
        bool requireSingleActiveInstance = true)
    {
        ValidateConfiguredInstanceBinding(
            target,
            requireExactPort: true,
            requireSingleActiveInstance: requireSingleActiveInstance);
        return InspectListener(target);
    }

    public async Task<BlueStacksRestartResult> RestartAcknowledgedAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        ValidateAcknowledgedTarget(previous, target);
        ValidateConfiguredInstanceBinding(
            target,
            requireExactPort: true,
            requireSingleActiveInstance: false);
        await StopExactProcessAsync(previous, cancellationToken);
        return await StartReplacementAsync(
            previous,
            target,
            cancellationToken);
    }

    public async Task<BlueStacksRestartResult> StartAfterAcknowledgedStopAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        ValidateAcknowledgedTarget(previous, target);
        ValidateConfiguredInstanceBinding(
            target,
            requireExactPort: false,
            requireSingleActiveInstance: false);
        try
        {
            _ = InspectListener(target);
            throw new InvalidOperationException(
                "A process already owns the acknowledged BlueStacks listener.");
        }
        catch (BlueStacksListenerUnavailableException)
        {
            await StopAcknowledgedProcessIfStillAliveAsync(
                previous,
                target,
                cancellationToken);
            return await StartReplacementAsync(
                previous,
                target,
                cancellationToken);
        }
    }

    public async Task<BlueStacksRestartResult> ConfirmReplacementAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        ValidateAcknowledgedTarget(previous, target);
        return await AwaitReplacementAsync(
            previous,
            target,
            previous.ProcessStartedAtUtc.AddTicks(1),
            TimeSpan.FromSeconds(15),
            cancellationToken);
    }

    private async Task<BlueStacksRestartResult> StartReplacementAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        try
        {
            _ = InspectListener(target);
            throw new InvalidOperationException(
                "A process already owns the acknowledged BlueStacks listener.");
        }
        catch (BlueStacksListenerUnavailableException)
        {
            // Expected exact start boundary.
        }

        var launchBoundary = DateTimeOffset.UtcNow;
        var start = new ProcessStartInfo
        {
            FileName = target.ExecutablePath,
            UseShellExecute = false,
            WorkingDirectory = Path.GetDirectoryName(target.ExecutablePath) ?? "",
        };
        start.ArgumentList.Add("--instance");
        start.ArgumentList.Add(target.InstanceName);
        using var launched = Process.Start(start)
            ?? throw new InvalidOperationException(
                "Windows did not start the configured BlueStacks player.");

        return await AwaitReplacementAsync(
            previous,
            target,
            launchBoundary - TimeSpan.FromSeconds(2),
            TimeSpan.FromMinutes(2),
            cancellationToken);
    }

    private async Task<BlueStacksRestartResult> AwaitReplacementAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        DateTimeOffset minimumStartedAt,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        BlueStacksProcessIdentity? last = null;
        var stablePolls = 0;
        var deadline = DateTimeOffset.UtcNow + timeout;
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            await Task.Delay(TimeSpan.FromSeconds(2), cancellationToken);
            try
            {
                var configuredPort = ValidateConfiguredInstanceBinding(
                    target,
                    requireExactPort: false,
                    requireSingleActiveInstance: false);
                if (!ReplacementMappingReady(target, configuredPort))
                {
                    stablePolls = 0;
                    last = null;
                    continue;
                }
                var candidate = InspectListener(target);
                // A listener alone does not prove the configured instance owns
                // it while BlueStacks still reports the target instance at 0.
                // Count stability only after the mapping reaches the exact
                // durable port; a wrong nonzero mapping fails immediately.
                ValidateConfiguredInstanceBinding(
                    target,
                    requireExactPort: true,
                    requireSingleActiveInstance: false);
                if ((candidate.ProcessId == previous.ProcessId
                        && candidate.ProcessStartedAtUtc
                        == previous.ProcessStartedAtUtc)
                    || candidate.ProcessStartedAtUtc < minimumStartedAt)
                {
                    stablePolls = 0;
                    last = null;
                    continue;
                }
                if (last is not null
                    && candidate.ProcessId == last.ProcessId
                    && candidate.ProcessStartedAtUtc == last.ProcessStartedAtUtc)
                {
                    stablePolls++;
                }
                else
                {
                    last = candidate;
                    stablePolls = 1;
                }
                if (stablePolls >= 2)
                {
                    return new BlueStacksRestartResult(previous, candidate);
                }
            }
            catch (BlueStacksListenerUnavailableException)
            {
                stablePolls = 0;
                last = null;
            }
            catch (ArgumentException)
            {
                stablePolls = 0;
                last = null;
            }
        }
        throw new TimeoutException(
            $"BlueStacks did not restore stable exact listener {target.AdbPort} "
                + $"within {timeout.TotalSeconds:F0} seconds.");
    }

    private BlueStacksProcessIdentity InspectListener(
        BlueStacksRecoveryTarget target)
    {
        var owners = ListenerRows(target.AdbPort)
            .Where(row => IsAllowedListenerAddress(row.LocalAddress))
            .Select(row => unchecked((int)row.OwningPid))
            .Where(processId => processId > 0)
            .Distinct()
            .ToArray();
        if (owners.Length != 1)
        {
            if (owners.Length == 0)
            {
                throw new BlueStacksListenerUnavailableException(
                    $"No loopback/any-address Windows process owns TCP listener "
                        + $"{target.AdbPort}.");
            }
            throw new InvalidOperationException(
                $"TCP listener {target.AdbPort} has ambiguous process ownership.");
        }
        return IdentityFromVerifiedProcess(
            owners[0],
            target,
            requireListener: true);
    }

    private BlueStacksProcessIdentity IdentityFromVerifiedProcess(
        int processId,
        BlueStacksRecoveryTarget target,
        bool requireListener)
    {
        using var processHandle = OpenProcessHandle(
            processId,
            ProcessAccessRights.QueryLimitedInformation,
            "BlueStacks listener owner inspection failed");
        return IdentityFromVerifiedHandle(
            processHandle,
            processId,
            target,
            requireListener);
    }

    private BlueStacksProcessIdentity IdentityFromVerifiedHandle(
        SafeProcessHandle processHandle,
        int processId,
        BlueStacksRecoveryTarget target,
        bool requireListener)
    {
        var snapshot = ReadProcessSnapshot(processHandle, processId);
        if (!PathsEqual(snapshot.ExecutablePath, target.ExecutablePath))
        {
            throw new InvalidOperationException(
                $"TCP listener {target.AdbPort} is not owned by the configured "
                    + "BlueStacks player executable.");
        }
        if (requireListener)
        {
            var owners = ListenerRows(target.AdbPort)
                .Where(row => IsAllowedListenerAddress(row.LocalAddress))
                .Select(row => unchecked((int)row.OwningPid))
                .Where(processId => processId > 0)
                .Distinct()
                .ToArray();
            if (owners.Length != 1 || owners[0] != processId)
            {
                throw new InvalidOperationException(
                    "The exact BlueStacks process no longer owns the configured "
                        + "listener.");
            }
        }
        return new BlueStacksProcessIdentity(
            Environment.MachineName,
            target.AdbPort,
            processId,
            snapshot.ProcessStartedAtUtc,
            target.ExecutablePath);
    }

    private static void ValidateAcknowledgedTarget(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target)
    {
        if (previous.AdbPort != target.AdbPort
            || !string.Equals(
                previous.HostId,
                Environment.MachineName,
                StringComparison.OrdinalIgnoreCase)
            || !string.Equals(
                previous.ExecutablePath,
                target.ExecutablePath,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "The acknowledged BlueStacks identity does not match the "
                    + "durably bound target.");
        }
    }

    private async Task StopExactProcessAsync(
        BlueStacksProcessIdentity expected,
        CancellationToken cancellationToken)
    {
        var target = new BlueStacksRecoveryTarget(
            expected.ExecutablePath,
            "acknowledged-instance",
            expected.AdbPort);
        using var processHandle = OpenProcessHandle(
            expected.ProcessId,
            ProcessAccessRights.QueryLimitedInformation,
            "The acknowledged BlueStacks process could not be inspected before stop");
        var current = IdentityFromVerifiedHandle(
            processHandle,
            expected.ProcessId,
            target,
            requireListener: true);
        if (current.ProcessStartedAtUtc != expected.ProcessStartedAtUtc)
        {
            throw new InvalidOperationException(
                "The BlueStacks listener owner changed before the stop boundary.");
        }

        var closeRequested = TryCloseMainWindow(expected.ProcessId);
        if (closeRequested
            && await WaitForExitAsync(
                processHandle,
                TimeSpan.FromSeconds(30),
                cancellationToken))
        {
            return;
        }

        if (HasExited(processHandle))
        {
            return;
        }

        using var forceHandle = OpenProcessHandle(
            expected.ProcessId,
            ProcessAccessRights.QueryLimitedInformation
                | ProcessAccessRights.Terminate,
            "The acknowledged BlueStacks process could not be opened for forced stop");
        // Revalidate through the exact handle used for force. If Windows
        // reused the PID after the graceful-close boundary, the start-time
        // comparison fails before TerminateProcess can mutate it.
        current = IdentityFromVerifiedHandle(
            forceHandle,
            expected.ProcessId,
            target,
            requireListener: false);
        if (current.ProcessStartedAtUtc != expected.ProcessStartedAtUtc)
        {
            throw new InvalidOperationException(
                "The BlueStacks listener owner changed before forced stop.");
        }
        if (!TerminateProcess(forceHandle, 1))
        {
            var error = Marshal.GetLastWin32Error();
            if (!HasExited(forceHandle))
            {
                throw Win32Failure(
                    error,
                    "The exact BlueStacks process could not be terminated");
            }
        }
        if (!await WaitForExitAsync(
            forceHandle,
            TimeSpan.FromSeconds(30),
            cancellationToken))
        {
            throw new TimeoutException(
                "The exact BlueStacks process did not stop within 30 seconds.");
        }
    }

    private static async Task StopAcknowledgedProcessIfStillAliveAsync(
        BlueStacksProcessIdentity expected,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        SafeProcessHandle processHandle;
        try
        {
            processHandle = OpenProcessHandle(
                expected.ProcessId,
                ProcessAccessRights.QueryLimitedInformation
                    | ProcessAccessRights.Terminate,
                "The acknowledged BlueStacks process could not be reopened for reconciliation");
        }
        catch (Win32Exception exception)
            when (exception.NativeErrorCode == ErrorInvalidParameter)
        {
            return;
        }
        using (processHandle)
        {
            if (HasExited(processHandle))
            {
                return;
            }
            var snapshot = ReadProcessSnapshot(
                processHandle,
                expected.ProcessId);
            if (snapshot.ProcessStartedAtUtc != expected.ProcessStartedAtUtc)
            {
                // The acknowledged process is gone and Windows reused its PID.
                return;
            }
            if (!PathsEqual(snapshot.ExecutablePath, target.ExecutablePath))
            {
                throw new InvalidOperationException(
                    "The acknowledged BlueStacks PID still exists with an "
                        + "unexpected executable path; replacement start is "
                        + "deferred.");
            }
            if (!TerminateProcess(processHandle, 1))
            {
                var error = Marshal.GetLastWin32Error();
                if (!HasExited(processHandle))
                {
                    throw Win32Failure(
                        error,
                        "The exact acknowledged BlueStacks process could not be terminated");
                }
            }
            if (!await WaitForExitAsync(
                processHandle,
                TimeSpan.FromSeconds(30),
                cancellationToken))
            {
                throw new TimeoutException(
                    "The acknowledged BlueStacks process did not stop within "
                        + "30 seconds.");
            }
        }
    }

    private int ValidateConfiguredInstanceBinding(
        BlueStacksRecoveryTarget target,
        bool requireExactPort,
        bool requireSingleActiveInstance)
    {
        Dictionary<string, int> mappings;
        try
        {
            mappings = new Dictionary<string, int>(
                ParseInstanceAdbPortMappings(File.ReadLines(_configurationPath)),
                StringComparer.OrdinalIgnoreCase);
        }
        catch (Exception exception) when (
            exception is IOException
            or UnauthorizedAccessException
            or ArgumentException)
        {
            throw new BlueStacksTargetBindingException(
                "BlueStacks instance-to-ADB mapping could not be read from "
                    + $"{_configurationPath}: {exception.Message}");
        }
        if (!mappings.TryGetValue(target.InstanceName, out var configuredPort))
        {
            throw new BlueStacksTargetBindingException(
                $"BlueStacks configuration has no instance named "
                    + $"{target.InstanceName}.");
        }
        ValidateConfiguredPortBinding(
            target,
            configuredPort,
            allowStoppedPort: !requireExactPort);
        ValidateUniqueConfiguredPortBinding(target, mappings, configuredPort);
        if (!requireSingleActiveInstance)
        {
            return configuredPort;
        }
        var activeInstances = mappings
            .Where(item => item.Value is >= 1 and <= 65535)
            .Where(item => ListenerRows(item.Value).Any(row =>
                IsAllowedListenerAddress(row.LocalAddress)))
            .Select(item => item.Key)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Take(2)
            .ToArray();
        if (activeInstances.Length > 1)
        {
            throw new BlueStacksTargetBindingException(
                "Automatic recovery is disabled while multiple BlueStacks "
                    + "instances have active ADB listeners because host-wide "
                    + "aging evidence is ambiguous.");
        }
        return configuredPort;
    }

    internal static void ValidateConfiguredPortBinding(
        BlueStacksRecoveryTarget target,
        int configuredPort,
        bool allowStoppedPort)
    {
        if (configuredPort != target.AdbPort
            && !(allowStoppedPort && configuredPort == 0))
        {
            throw new BlueStacksTargetBindingException(
                $"BlueStacks instance {target.InstanceName} maps to ADB port "
                    + $"{configuredPort}, not configured port {target.AdbPort}.");
        }
    }

    internal static bool ReplacementMappingReady(
        BlueStacksRecoveryTarget target,
        int configuredPort)
    {
        ValidateConfiguredPortBinding(
            target,
            configuredPort,
            allowStoppedPort: true);
        return configuredPort == target.AdbPort;
    }

    internal static void ValidateUniqueConfiguredPortBinding(
        BlueStacksRecoveryTarget target,
        IReadOnlyDictionary<string, int> mappings,
        int configuredPort)
    {
        if (configuredPort == 0)
        {
            return;
        }
        var conflictingInstance = mappings
            .Where(item => !string.Equals(
                item.Key,
                target.InstanceName,
                StringComparison.OrdinalIgnoreCase))
            .FirstOrDefault(item => item.Value == configuredPort)
            .Key;
        if (!string.IsNullOrWhiteSpace(conflictingInstance))
        {
            throw new BlueStacksTargetBindingException(
                $"BlueStacks ADB port {configuredPort} is mapped to both "
                    + $"{target.InstanceName} and {conflictingInstance}; exact "
                    + "instance ownership is ambiguous.");
        }
    }

    internal static IReadOnlyDictionary<string, int>
        ParseInstanceAdbPortMappings(IEnumerable<string> lines) =>
        lines
            .Select(line => InstanceAdbPortPattern.Match(line.Trim()))
            .Where(match => match.Success)
            .Select(match => new
            {
                Instance = match.Groups["instance"].Value,
                Port = int.Parse(match.Groups["port"].Value),
            })
            .Where(item => item.Port is >= 0 and <= 65535)
            .GroupBy(item => item.Instance, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                group => group.Key,
                group => group.Last().Port,
                StringComparer.OrdinalIgnoreCase);

    private static async Task<bool> WaitForExitAsync(
        SafeProcessHandle processHandle,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        if (HasExited(processHandle))
        {
            return true;
        }
        var deadline = DateTimeOffset.UtcNow + timeout;
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            await Task.Delay(TimeSpan.FromMilliseconds(100), cancellationToken);
            if (HasExited(processHandle))
            {
                return true;
            }
        }
        return HasExited(processHandle);
    }

    private static bool TryCloseMainWindow(int processId)
    {
        try
        {
            using var process = Process.GetProcessById(processId);
            return process.CloseMainWindow();
        }
        catch (Exception exception) when (
            exception is ArgumentException
                or InvalidOperationException
                or Win32Exception)
        {
            return false;
        }
    }

    private static SafeProcessHandle OpenProcessHandle(
        int processId,
        ProcessAccessRights access,
        string operation)
    {
        var processHandle = OpenProcess(access, false, processId);
        if (!processHandle.IsInvalid)
        {
            return processHandle;
        }
        var error = Marshal.GetLastWin32Error();
        processHandle.Dispose();
        throw Win32Failure(error, operation);
    }

    private static NativeProcessSnapshot ReadProcessSnapshot(
        SafeProcessHandle processHandle,
        int processId)
    {
        var path = new StringBuilder(MaximumWindowsPathCharacters);
        var pathLength = path.Capacity;
        if (!QueryFullProcessImageName(
                processHandle,
                0,
                path,
                ref pathLength))
        {
            throw Win32Failure(
                Marshal.GetLastWin32Error(),
                $"The executable path for BlueStacks PID {processId} could not be read");
        }
        if (!GetProcessTimes(
                processHandle,
                out var createdAt,
                out _,
                out _,
                out _))
        {
            throw Win32Failure(
                Marshal.GetLastWin32Error(),
                $"The start time for BlueStacks PID {processId} could not be read");
        }
        return new NativeProcessSnapshot(
            path.ToString(),
            FileTimeToUtc(createdAt));
    }

    internal static DateTimeOffset FileTimeToUtc(NativeFileTime value)
    {
        var fileTime = unchecked(
            (long)(((ulong)value.HighDateTime << 32) | value.LowDateTime));
        return new DateTimeOffset(
            DateTime.FromFileTimeUtc(fileTime),
            TimeSpan.Zero);
    }

    internal static bool PathsEqual(string left, string right) =>
        string.Equals(
            Path.GetFullPath(left),
            Path.GetFullPath(right),
            StringComparison.OrdinalIgnoreCase);

    private static bool HasExited(SafeProcessHandle processHandle)
    {
        if (!GetExitCodeProcess(processHandle, out var exitCode))
        {
            throw Win32Failure(
                Marshal.GetLastWin32Error(),
                "The exact BlueStacks process state could not be read");
        }
        return exitCode != StillActive;
    }

    private static Win32Exception Win32Failure(int error, string operation) =>
        new(error, $"{operation}: {new Win32Exception(error).Message}");

    internal static string ValidateExecutablePath(string value)
    {
        var trimmed = value?.Trim() ?? "";
        if (string.IsNullOrWhiteSpace(trimmed) || !Path.IsPathFullyQualified(trimmed))
        {
            throw new ArgumentException(
                "BlueStacks player path must be an absolute path to HD-Player.exe.");
        }
        var normalized = Path.GetFullPath(trimmed);
        if (!string.Equals(
            Path.GetFileName(normalized),
            "HD-Player.exe",
            StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException(
                "BlueStacks player path must name HD-Player.exe.");
        }
        if (!File.Exists(normalized))
        {
            throw new ArgumentException(
                "The configured BlueStacks player executable does not exist.");
        }
        return normalized;
    }

    internal static string ValidateInstanceName(string value)
    {
        var normalized = value?.Trim() ?? "";
        if (!InstanceNamePattern.IsMatch(normalized))
        {
            throw new ArgumentException(
                "BlueStacks instance name must contain 1-64 letters, digits, "
                    + "dots, underscores, or hyphens.");
        }
        return normalized;
    }

    private static bool IsAllowedListenerAddress(uint rawAddress)
    {
        var address = new IPAddress(BitConverter.GetBytes(rawAddress));
        return IPAddress.Any.Equals(address) || IPAddress.Loopback.Equals(address);
    }

    private static IReadOnlyList<MibTcpRowOwnerPid> ListenerRows(int port)
    {
        var size = 0;
        var result = GetExtendedTcpTable(
            IntPtr.Zero,
            ref size,
            true,
            AddressFamilyInterNetwork,
            TcpTableClass.OwnerPidListener,
            0);
        if (result != ErrorInsufficientBuffer)
        {
            throw new Win32Exception(result);
        }
        var buffer = Marshal.AllocHGlobal(size);
        try
        {
            result = GetExtendedTcpTable(
                buffer,
                ref size,
                true,
                AddressFamilyInterNetwork,
                TcpTableClass.OwnerPidListener,
                0);
            if (result != 0)
            {
                throw new Win32Exception(result);
            }
            var count = Marshal.ReadInt32(buffer);
            var rowSize = Marshal.SizeOf<MibTcpRowOwnerPid>();
            var rows = new List<MibTcpRowOwnerPid>();
            var offset = sizeof(uint);
            for (var index = 0; index < count; index++)
            {
                var row = Marshal.PtrToStructure<MibTcpRowOwnerPid>(
                    IntPtr.Add(buffer, offset + index * rowSize));
                var localPort = (ushort)IPAddress.NetworkToHostOrder(
                    unchecked((short)(row.LocalPort & 0xffff)));
                if (localPort == port && row.OwningPid > 0)
                {
                    rows.Add(row);
                }
            }
            return rows;
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    private enum TcpTableClass
    {
        OwnerPidListener = 3,
    }

    [Flags]
    private enum ProcessAccessRights : uint
    {
        Terminate = 0x0001,
        QueryLimitedInformation = 0x1000,
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct NativeFileTime
    {
        public uint LowDateTime;
        public uint HighDateTime;

        public NativeFileTime(uint lowDateTime, uint highDateTime)
        {
            LowDateTime = lowDateTime;
            HighDateTime = highDateTime;
        }
    }

    private sealed record NativeProcessSnapshot(
        string ExecutablePath,
        DateTimeOffset ProcessStartedAtUtc);

    [StructLayout(LayoutKind.Sequential)]
    private struct MibTcpRowOwnerPid
    {
        public uint State;
        public uint LocalAddress;
        public uint LocalPort;
        public uint RemoteAddress;
        public uint RemotePort;
        public uint OwningPid;
    }

    [DllImport("iphlpapi.dll", SetLastError = true)]
    private static extern int GetExtendedTcpTable(
        IntPtr tcpTable,
        ref int size,
        [MarshalAs(UnmanagedType.Bool)] bool order,
        int addressFamily,
        TcpTableClass tableClass,
        uint reserved);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern SafeProcessHandle OpenProcess(
        ProcessAccessRights desiredAccess,
        [MarshalAs(UnmanagedType.Bool)] bool inheritHandle,
        int processId);

    [DllImport(
        "kernel32.dll",
        EntryPoint = "QueryFullProcessImageNameW",
        CharSet = CharSet.Unicode,
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool QueryFullProcessImageName(
        SafeProcessHandle processHandle,
        uint flags,
        StringBuilder executablePath,
        ref int size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetProcessTimes(
        SafeProcessHandle processHandle,
        out NativeFileTime creationTime,
        out NativeFileTime exitTime,
        out NativeFileTime kernelTime,
        out NativeFileTime userTime);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TerminateProcess(
        SafeProcessHandle processHandle,
        uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetExitCodeProcess(
        SafeProcessHandle processHandle,
        out uint exitCode);
}
