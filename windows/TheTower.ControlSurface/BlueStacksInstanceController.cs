using System.ComponentModel;
using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

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
    BlueStacksProcessIdentity Inspect(BlueStacksRecoveryTarget target);

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
    private const int ErrorInsufficientBuffer = 122;
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

    public BlueStacksProcessIdentity Inspect(BlueStacksRecoveryTarget target)
    {
        ValidateConfiguredInstanceBinding(target, requireExactPort: true);
        return InspectListener(target);
    }

    public async Task<BlueStacksRestartResult> RestartAcknowledgedAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        ValidateAcknowledgedTarget(previous, target);
        ValidateConfiguredInstanceBinding(target, requireExactPort: true);
        await StopExactProcessAsync(previous, cancellationToken);
        return await StartReplacementAsync(
            previous,
            target,
            cancellationToken);
    }

    public Task<BlueStacksRestartResult> StartAfterAcknowledgedStopAsync(
        BlueStacksProcessIdentity previous,
        BlueStacksRecoveryTarget target,
        CancellationToken cancellationToken)
    {
        ValidateAcknowledgedTarget(previous, target);
        ValidateConfiguredInstanceBinding(target, requireExactPort: false);
        try
        {
            _ = InspectListener(target);
            throw new InvalidOperationException(
                "A process already owns the acknowledged BlueStacks listener.");
        }
        catch (BlueStacksListenerUnavailableException)
        {
            return StartReplacementAsync(previous, target, cancellationToken);
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
                var candidate = Inspect(target);
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
        using var process = Process.GetProcessById(owners[0]);
        return IdentityFromVerifiedHandle(process, target, requireListener: true);
    }

    private BlueStacksProcessIdentity IdentityFromVerifiedHandle(
        Process process,
        BlueStacksRecoveryTarget target,
        bool requireListener)
    {
        process.Refresh();
        if (process.HasExited)
        {
            throw new InvalidOperationException(
                "The acknowledged BlueStacks process already exited.");
        }
        var actualPath = process.MainModule?.FileName;
        if (string.IsNullOrWhiteSpace(actualPath)
            || !string.Equals(
                Path.GetFullPath(actualPath),
                target.ExecutablePath,
                StringComparison.OrdinalIgnoreCase))
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
            if (owners.Length != 1 || owners[0] != process.Id)
            {
                throw new InvalidOperationException(
                    "The exact BlueStacks process no longer owns the configured "
                        + "listener.");
            }
        }
        return new BlueStacksProcessIdentity(
            Environment.MachineName,
            target.AdbPort,
            process.Id,
            new DateTimeOffset(process.StartTime.ToUniversalTime(), TimeSpan.Zero),
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
        using var process = Process.GetProcessById(expected.ProcessId);
        var current = IdentityFromVerifiedHandle(
            process,
            target,
            requireListener: true);
        if (current.ProcessStartedAtUtc != expected.ProcessStartedAtUtc)
        {
            throw new InvalidOperationException(
                "The BlueStacks listener owner changed before the stop boundary.");
        }

        var closeRequested = process.CloseMainWindow();
        if (closeRequested
            && await WaitForExitAsync(
                process,
                TimeSpan.FromSeconds(30),
                cancellationToken))
        {
            return;
        }

        // Keep the original process HANDLE from verification through force.
        // If that process exited and Windows reused its PID, this handle cannot
        // mutate the replacement process.
        current = IdentityFromVerifiedHandle(
            process,
            target,
            requireListener: true);
        if (current.ProcessStartedAtUtc != expected.ProcessStartedAtUtc)
        {
            throw new InvalidOperationException(
                "The BlueStacks listener owner changed before forced stop.");
        }
        process.Kill(entireProcessTree: true);
        if (!await WaitForExitAsync(
            process,
            TimeSpan.FromSeconds(30),
            cancellationToken))
        {
            throw new TimeoutException(
                "The exact BlueStacks process did not stop within 30 seconds.");
        }
    }

    private void ValidateConfiguredInstanceBinding(
        BlueStacksRecoveryTarget target,
        bool requireExactPort)
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
        Process process,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        if (process.HasExited)
        {
            return true;
        }
        using var timeoutCancellation = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken);
        timeoutCancellation.CancelAfter(timeout);
        try
        {
            await process.WaitForExitAsync(timeoutCancellation.Token);
            return true;
        }
        catch (OperationCanceledException)
            when (!cancellationToken.IsCancellationRequested)
        {
            return process.HasExited;
        }
    }

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
}
