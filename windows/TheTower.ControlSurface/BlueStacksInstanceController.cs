using System.ComponentModel;
using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

namespace TheTower.ControlSurface;

internal sealed record BlueStacksProcessIdentity(
    string HostId,
    int AdbPort,
    int ProcessId,
    DateTimeOffset ProcessStartedAtUtc,
    string ExecutablePath)
{
    public string ProcessStartedAtText =>
        ProcessStartedAtUtc.ToString("O");
}

internal sealed record BlueStacksRestartResult(
    BlueStacksProcessIdentity Previous,
    BlueStacksProcessIdentity Replacement);

internal sealed class BlueStacksListenerUnavailableException :
    InvalidOperationException
{
    public BlueStacksListenerUnavailableException(string message) : base(message)
    {
    }
}

internal sealed class BlueStacksInstanceController
{
    private const int AddressFamilyInterNetwork = 2;
    private const int ErrorInsufficientBuffer = 122;
    private static readonly Regex InstanceNamePattern = new(
        @"\A[A-Za-z0-9_.-]{1,64}\z",
        RegexOptions.CultureInvariant);

    public BlueStacksProcessIdentity Inspect(
        string executablePath,
        int adbPort)
    {
        var normalizedPath = ValidateExecutablePath(executablePath);
        if (adbPort is < 1 or > 65535)
        {
            throw new ArgumentException(
                "The Windows BlueStacks ADB port must be between 1 and 65535.");
        }
        var owners = TcpListenerOwnerProcessIds(adbPort).Distinct().ToArray();
        if (owners.Length != 1)
        {
            if (owners.Length == 0)
            {
                throw new BlueStacksListenerUnavailableException(
                    $"No Windows process owns TCP listener {adbPort}.");
            }
            throw new InvalidOperationException(
                $"TCP listener {adbPort} has ambiguous process ownership.");
        }
        using var process = Process.GetProcessById(owners[0]);
        process.Refresh();
        var actualPath = process.MainModule?.FileName;
        if (string.IsNullOrWhiteSpace(actualPath)
            || !string.Equals(
                Path.GetFullPath(actualPath),
                normalizedPath,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                $"TCP listener {adbPort} is not owned by the configured "
                    + "BlueStacks player executable.");
        }
        return new BlueStacksProcessIdentity(
            Environment.MachineName,
            adbPort,
            process.Id,
            process.StartTime.ToUniversalTime(),
            normalizedPath);
    }

    public async Task<BlueStacksRestartResult> RestartAsync(
        string executablePath,
        string instanceName,
        int adbPort,
        CancellationToken cancellationToken)
    {
        var normalizedPath = ValidateExecutablePath(executablePath);
        var normalizedInstance = ValidateInstanceName(instanceName);
        var previous = Inspect(normalizedPath, adbPort);
        return await RestartAcknowledgedAsync(
            previous,
            normalizedPath,
            normalizedInstance,
            adbPort,
            cancellationToken);
    }

    public async Task<BlueStacksRestartResult> RestartAcknowledgedAsync(
        BlueStacksProcessIdentity previous,
        string executablePath,
        string instanceName,
        int adbPort,
        CancellationToken cancellationToken)
    {
        var normalizedPath = ValidateExecutablePath(executablePath);
        var normalizedInstance = ValidateInstanceName(instanceName);
        ValidateAcknowledgedTarget(previous, normalizedPath, adbPort);
        await StopExactProcessAsync(previous, cancellationToken);

        return await StartReplacementAsync(
            previous,
            normalizedPath,
            normalizedInstance,
            adbPort,
            cancellationToken);
    }

    public Task<BlueStacksRestartResult> StartAfterAcknowledgedStopAsync(
        BlueStacksProcessIdentity previous,
        string executablePath,
        string instanceName,
        int adbPort,
        CancellationToken cancellationToken)
    {
        var normalizedPath = ValidateExecutablePath(executablePath);
        var normalizedInstance = ValidateInstanceName(instanceName);
        ValidateAcknowledgedTarget(previous, normalizedPath, adbPort);
        try
        {
            _ = Inspect(normalizedPath, adbPort);
            throw new InvalidOperationException(
                "A process already owns the acknowledged BlueStacks listener.");
        }
        catch (BlueStacksListenerUnavailableException)
        {
            return StartReplacementAsync(
                previous,
                normalizedPath,
                normalizedInstance,
                adbPort,
                cancellationToken);
        }
    }

    public async Task<BlueStacksRestartResult> ConfirmReplacementAsync(
        BlueStacksProcessIdentity previous,
        string executablePath,
        int adbPort,
        CancellationToken cancellationToken)
    {
        var normalizedPath = ValidateExecutablePath(executablePath);
        ValidateAcknowledgedTarget(previous, normalizedPath, adbPort);
        return await AwaitReplacementAsync(
            previous,
            normalizedPath,
            adbPort,
            TimeSpan.FromSeconds(15),
            cancellationToken);
    }

    private async Task<BlueStacksRestartResult> StartReplacementAsync(
        BlueStacksProcessIdentity previous,
        string normalizedPath,
        string normalizedInstance,
        int adbPort,
        CancellationToken cancellationToken)
    {
        try
        {
            _ = Inspect(normalizedPath, adbPort);
            throw new InvalidOperationException(
                "A process already owns the acknowledged BlueStacks listener.");
        }
        catch (BlueStacksListenerUnavailableException)
        {
            // Expected exact start boundary.
        }

        var start = new ProcessStartInfo
        {
            FileName = normalizedPath,
            UseShellExecute = false,
            WorkingDirectory = Path.GetDirectoryName(normalizedPath) ?? "",
        };
        start.ArgumentList.Add("--instance");
        start.ArgumentList.Add(normalizedInstance);
        using var launched = Process.Start(start)
            ?? throw new InvalidOperationException(
                "Windows did not start the configured BlueStacks player.");

        return await AwaitReplacementAsync(
            previous,
            normalizedPath,
            adbPort,
            TimeSpan.FromMinutes(2),
            cancellationToken);
    }

    private async Task<BlueStacksRestartResult> AwaitReplacementAsync(
        BlueStacksProcessIdentity previous,
        string normalizedPath,
        int adbPort,
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
                var candidate = Inspect(normalizedPath, adbPort);
                if (candidate.ProcessId == previous.ProcessId
                    && candidate.ProcessStartedAtUtc == previous.ProcessStartedAtUtc)
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
            catch (InvalidOperationException)
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
            $"BlueStacks did not restore stable exact listener {adbPort} within "
                + $"{timeout.TotalSeconds:F0} seconds.");
    }

    private static void ValidateAcknowledgedTarget(
        BlueStacksProcessIdentity previous,
        string normalizedPath,
        int adbPort)
    {
        if (previous.AdbPort != adbPort
            || !string.Equals(
                previous.HostId,
                Environment.MachineName,
                StringComparison.OrdinalIgnoreCase)
            || !string.Equals(
                previous.ExecutablePath,
                normalizedPath,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "The acknowledged BlueStacks identity does not match the configured target.");
        }
    }

    private BlueStacksProcessIdentity InspectSame(
        BlueStacksProcessIdentity expected)
    {
        var current = Inspect(expected.ExecutablePath, expected.AdbPort);
        if (current.ProcessId != expected.ProcessId
            || current.ProcessStartedAtUtc != expected.ProcessStartedAtUtc)
        {
            throw new InvalidOperationException(
                "The BlueStacks listener owner changed before the stop boundary.");
        }
        return current;
    }

    private async Task StopExactProcessAsync(
        BlueStacksProcessIdentity expected,
        CancellationToken cancellationToken)
    {
        InspectSame(expected);
        using var process = Process.GetProcessById(expected.ProcessId);
        process.Refresh();
        var closeRequested = process.CloseMainWindow();
        if (closeRequested
            && await WaitForExitAsync(
                process,
                TimeSpan.FromSeconds(30),
                cancellationToken))
        {
            return;
        }

        // The force fallback is permitted only after repeating the exact
        // listener/path/start-time proof immediately before termination.
        InspectSame(expected);
        using var forced = Process.GetProcessById(expected.ProcessId);
        forced.Kill(entireProcessTree: true);
        if (!await WaitForExitAsync(
                forced,
                TimeSpan.FromSeconds(30),
                cancellationToken))
        {
            throw new TimeoutException(
                "The exact BlueStacks process did not stop within 30 seconds.");
        }
    }

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
                "BlueStacks instance name must contain 1-64 letters, digits, dots, underscores, or hyphens.");
        }
        return normalized;
    }

    private static IReadOnlyList<int> TcpListenerOwnerProcessIds(int port)
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
            var rows = new List<int>();
            var offset = sizeof(uint);
            for (var index = 0; index < count; index++)
            {
                var row = Marshal.PtrToStructure<MibTcpRowOwnerPid>(
                    IntPtr.Add(buffer, offset + index * rowSize));
                var localPort = (ushort)IPAddress.NetworkToHostOrder(
                    unchecked((short)(row.LocalPort & 0xffff)));
                if (localPort == port && row.OwningPid > 0)
                {
                    rows.Add(unchecked((int)row.OwningPid));
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
