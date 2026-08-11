using System.Diagnostics;
using System.Runtime.InteropServices;

namespace TheTower.ControlSurface;

internal sealed class WindowsHostPerformanceSampler : IDisposable
{
    private const int ProcessDiscoveryIntervalSamples = 10;
    private const int ProcessorPowerInformationLevel = 11;

    private readonly int _logicalProcessorCount = Math.Max(
        1,
        Environment.ProcessorCount);
    private readonly Process _controlSurfaceProcess = Process.GetCurrentProcess();
    private readonly WindowsGpuPerformanceSampler _gpuSampler = new();
    private readonly HostProcessAttributionGate _processAttributionGate = new();
    private readonly Dictionary<int, ProcessTotals> _previousProcessTotals = [];
    private readonly Dictionary<int, ProcessAttributionTotals>
        _previousProcessAttributionTotals = [];
    private readonly Dictionary<int, string> _processNames = [];
    private readonly HashSet<int> _blueStacksProcessIds = [];
    private readonly List<TrackedProcess> _blueStacksProcesses = [];
    private SystemTimes? _previousSystemTimes;
    private TimeSpan? _previousControlSurfaceCpu;
    private DateTimeOffset? _previousSampleAtUtc;
    private DateTimeOffset? _previousProcessAttributionAtUtc;
    private int _samplesUntilDiscovery;
    private double? _cpuFrequencyMhz;
    private double? _cpuFrequencyRatio;
    private bool _disposed;

    public HostPerformanceSample Sample(HostPerformanceContext context)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        var sampleTimer = Stopwatch.StartNew();
        var sampledAtUtc = DateTimeOffset.UtcNow;
        var elapsedSeconds = _previousSampleAtUtc is null
            ? (double?)null
            : Math.Max(
                0.001,
                (sampledAtUtc - _previousSampleAtUtc.Value).TotalSeconds);

        var hostCpuPercent = ReadHostCpuPercent();
        var (memoryUsedPercent, availableMemoryBytes) = ReadMemory();
        var processAttributionState = _processAttributionGate.Update(
            sampledAtUtc,
            hostCpuPercent,
            memoryUsedPercent,
            availableMemoryBytes);
        var processRefresh = ProcessRefreshResult.Empty;
        if (_samplesUntilDiscovery <= 0)
        {
            processRefresh = RefreshProcesses(
                sampledAtUtc,
                processAttributionState is HostProcessAttributionState.Active
                    or HostProcessAttributionState.Recovering);
            RefreshCpuFrequency();
            _samplesUntilDiscovery = ProcessDiscoveryIntervalSamples;
        }
        _samplesUntilDiscovery--;

        var processMetrics = ReadBlueStacksMetrics(elapsedSeconds);
        var gpuMetrics = _gpuSampler.Sample(
            _blueStacksProcessIds,
            _processNames);
        var controlSurfaceCpuPercent = ReadControlSurfaceCpuPercent(
            elapsedSeconds);
        _previousSampleAtUtc = sampledAtUtc;

        sampleTimer.Stop();
        return new HostPerformanceSample
        {
            TimestampUtc = sampledAtUtc,
            Context = context,
            HostCpuPercent = hostCpuPercent,
            HostMemoryUsedPercent = memoryUsedPercent,
            HostAvailableMemoryBytes = availableMemoryBytes,
            HostCpuFrequencyMhz = _cpuFrequencyMhz,
            HostCpuFrequencyRatio = _cpuFrequencyRatio,
            BlueStacksProcessCount = processMetrics.ProcessCount,
            BlueStacksCpuPercent = processMetrics.CpuPercent,
            BlueStacksCpuCorePercent = processMetrics.CpuCorePercent,
            BlueStacksWorkingSetBytes = processMetrics.WorkingSetBytes,
            BlueStacksPrivateBytes = processMetrics.PrivateBytes,
            BlueStacksIoReadBytesPerSecond =
                processMetrics.IoReadBytesPerSecond,
            BlueStacksIoWriteBytesPerSecond =
                processMetrics.IoWriteBytesPerSecond,
            BlueStacksThreadCount = processMetrics.ThreadCount,
            BlueStacksHandleCount = processMetrics.HandleCount,
            GpuCountersAvailable = gpuMetrics.Available,
            HostGpuPercent = gpuMetrics.HostGpuPercent,
            HostGpuDedicatedMemoryBytes =
                gpuMetrics.HostDedicatedMemoryBytes,
            HostGpuSharedMemoryBytes = gpuMetrics.HostSharedMemoryBytes,
            BlueStacksGpuPercent = gpuMetrics.BlueStacksGpuPercent,
            BlueStacksGpuDedicatedMemoryBytes =
                gpuMetrics.BlueStacksDedicatedMemoryBytes,
            BlueStacksGpuSharedMemoryBytes =
                gpuMetrics.BlueStacksSharedMemoryBytes,
            GpuProcessCount = gpuMetrics.ProcessCount,
            GpuCompetitors = gpuMetrics.Competitors,
            GpuSampleDurationMilliseconds =
                gpuMetrics.SampleDurationMilliseconds,
            GpuError = gpuMetrics.Error,
            ProcessAttributionState = processAttributionState,
            ProcessAttributionProcessCount = processRefresh.ProcessCount,
            ProcessAttribution = processRefresh.Attribution,
            ProcessAttributionSampleDurationMilliseconds =
                processRefresh.SampleDurationMilliseconds,
            ControlSurfaceCpuPercent = controlSurfaceCpuPercent,
            SampleDurationMilliseconds = sampleTimer.Elapsed.TotalMilliseconds,
        };
    }

    private ProcessRefreshResult RefreshProcesses(
        DateTimeOffset sampledAtUtc,
        bool collectAttribution)
    {
        var attributionElapsedTicks = 0L;
        foreach (var tracked in _blueStacksProcesses)
        {
            tracked.Process.Dispose();
        }
        _blueStacksProcesses.Clear();
        _blueStacksProcessIds.Clear();
        _processNames.Clear();
        var attributionTotals = new Dictionary<int, ProcessAttributionTotals>();

        foreach (var process in Process.GetProcesses())
        {
            var retained = false;
            try
            {
                var processId = process.Id;
                var processName = BoundedProcessName(processId, process.ProcessName);
                _processNames[processId] = processName;
                if (IsBlueStacksProcessName(processName))
                {
                    process.Refresh();
                    _blueStacksProcessIds.Add(processId);
                    _blueStacksProcesses.Add(
                        new TrackedProcess(
                            process,
                            SafeProcessValue(() => process.Threads.Count),
                            SafeProcessValue(() => process.HandleCount)));
                    retained = true;
                    continue;
                }
                if (collectAttribution
                    && processId != _controlSurfaceProcess.Id
                    && processId > 0)
                {
                    var attributionStarted = Stopwatch.GetTimestamp();
                    try
                    {
                        process.Refresh();
                        attributionTotals[processId] =
                            new ProcessAttributionTotals(
                                processName,
                                process.TotalProcessorTime,
                                Math.Max(0, process.WorkingSet64),
                                Math.Max(0, process.PrivateMemorySize64));
                    }
                    finally
                    {
                        attributionElapsedTicks +=
                            Stopwatch.GetTimestamp() - attributionStarted;
                    }
                }
            }
            catch (Exception exception) when (
                exception is InvalidOperationException
                    or System.ComponentModel.Win32Exception
                    or NotSupportedException)
            {
                // Processes can exit or deny inspection during discovery.
            }
            finally
            {
                if (!retained)
                {
                    process.Dispose();
                }
            }
        }

        if (!collectAttribution)
        {
            _previousProcessAttributionTotals.Clear();
            _previousProcessAttributionAtUtc = null;
            return ProcessRefreshResult.Empty;
        }

        var attributionProcessingStarted = Stopwatch.GetTimestamp();
        var elapsedSeconds = _previousProcessAttributionAtUtc is null
            ? (double?)null
            : Math.Max(
                0.001,
                (sampledAtUtc - _previousProcessAttributionAtUtc.Value)
                    .TotalSeconds);
        var observations = new List<HostProcessObservation>(
            attributionTotals.Count);
        foreach (var (processId, totals) in attributionTotals)
        {
            double? cpuPercent = null;
            if (elapsedSeconds is > 0
                && _previousProcessAttributionTotals.TryGetValue(
                    processId,
                    out var previous)
                && string.Equals(
                    totals.ProcessName,
                    previous.ProcessName,
                    StringComparison.Ordinal)
                && totals.CpuTime >= previous.CpuTime)
            {
                cpuPercent = Math.Clamp(
                    (totals.CpuTime - previous.CpuTime).TotalSeconds
                        / elapsedSeconds.Value
                        * 100.0
                        / _logicalProcessorCount,
                    0.0,
                    100.0);
            }
            observations.Add(
                new HostProcessObservation(
                    processId,
                    totals.ProcessName,
                    cpuPercent,
                    totals.WorkingSetBytes,
                    totals.PrivateBytes));
        }

        _previousProcessAttributionTotals.Clear();
        foreach (var (processId, totals) in attributionTotals)
        {
            _previousProcessAttributionTotals[processId] = totals;
        }
        _previousProcessAttributionAtUtc = sampledAtUtc;
        var selected = HostProcessAttributionSelector.Select(observations);
        attributionElapsedTicks +=
            Stopwatch.GetTimestamp() - attributionProcessingStarted;
        return new ProcessRefreshResult(
            attributionTotals.Count,
            selected,
            attributionElapsedTicks * 1000.0 / Stopwatch.Frequency);
    }

    internal static bool IsBlueStacksProcessName(string processName) =>
        processName.StartsWith("HD-", StringComparison.OrdinalIgnoreCase)
        || processName.StartsWith("BlueStacks", StringComparison.OrdinalIgnoreCase)
        || processName.StartsWith("Bstk", StringComparison.OrdinalIgnoreCase);

    private BlueStacksMetrics ReadBlueStacksMetrics(double? elapsedSeconds)
    {
        var processCount = 0;
        var workingSetBytes = 0L;
        var privateBytes = 0L;
        var threadCount = 0;
        var handleCount = 0;
        var cpuDeltaSeconds = 0.0;
        var ioReadDelta = 0UL;
        var ioWriteDelta = 0UL;
        var cpuDeltaAvailable = false;
        var ioDeltaAvailable = false;
        var currentTotals = new Dictionary<int, ProcessTotals>();

        foreach (var tracked in _blueStacksProcesses)
        {
            var process = tracked.Process;
            try
            {
                if (process.HasExited)
                {
                    continue;
                }
                process.Refresh();
                var totals = ReadProcessTotals(process);
                currentTotals[process.Id] = totals;
                processCount++;
                workingSetBytes += Math.Max(0, totals.WorkingSetBytes);
                privateBytes += Math.Max(0, totals.PrivateBytes);
                threadCount += tracked.ThreadCount;
                handleCount += tracked.HandleCount;

                if (_previousProcessTotals.TryGetValue(
                        process.Id,
                        out var previous))
                {
                    var cpuDelta = totals.CpuTime - previous.CpuTime;
                    if (cpuDelta >= TimeSpan.Zero)
                    {
                        cpuDeltaSeconds += cpuDelta.TotalSeconds;
                        cpuDeltaAvailable = true;
                    }
                    if (totals.IoReadBytes >= previous.IoReadBytes
                        && totals.IoWriteBytes >= previous.IoWriteBytes)
                    {
                        ioReadDelta += totals.IoReadBytes - previous.IoReadBytes;
                        ioWriteDelta +=
                            totals.IoWriteBytes - previous.IoWriteBytes;
                        ioDeltaAvailable = true;
                    }
                }
            }
            catch (Exception exception) when (
                exception is InvalidOperationException
                    or System.ComponentModel.Win32Exception
                    or NotSupportedException)
            {
                // A process can exit between discovery and sampling.
            }
        }

        _previousProcessTotals.Clear();
        foreach (var (processId, totals) in currentTotals)
        {
            _previousProcessTotals[processId] = totals;
        }

        var cpuCorePercent = elapsedSeconds is > 0 && cpuDeltaAvailable
            ? Math.Max(0.0, cpuDeltaSeconds / elapsedSeconds.Value * 100.0)
            : (double?)null;
        var cpuPercent = cpuCorePercent / _logicalProcessorCount;
        return new BlueStacksMetrics(
            processCount,
            cpuPercent,
            cpuCorePercent,
            workingSetBytes,
            privateBytes,
            elapsedSeconds is > 0 && ioDeltaAvailable
                ? ioReadDelta / elapsedSeconds.Value
                : null,
            elapsedSeconds is > 0 && ioDeltaAvailable
                ? ioWriteDelta / elapsedSeconds.Value
                : null,
            threadCount,
            handleCount);
    }

    private static ProcessTotals ReadProcessTotals(Process process)
    {
        var ioReadBytes = 0UL;
        var ioWriteBytes = 0UL;
        try
        {
            if (GetProcessIoCounters(process.Handle, out var counters))
            {
                ioReadBytes = counters.ReadTransferCount;
                ioWriteBytes = counters.WriteTransferCount;
            }
        }
        catch (System.ComponentModel.Win32Exception)
        {
            // Some short-lived helper processes deny a query during exit.
        }
        return new ProcessTotals(
            process.TotalProcessorTime,
            process.WorkingSet64,
            process.PrivateMemorySize64,
            ioReadBytes,
            ioWriteBytes);
    }

    private double? ReadHostCpuPercent()
    {
        if (!GetSystemTimes(out var idle, out var kernel, out var user))
        {
            return null;
        }
        var current = new SystemTimes(
            ToUInt64(idle),
            ToUInt64(kernel),
            ToUInt64(user));
        var previous = _previousSystemTimes;
        _previousSystemTimes = current;
        if (previous is null
            || current.Kernel < previous.Value.Kernel
            || current.User < previous.Value.User
            || current.Idle < previous.Value.Idle)
        {
            return null;
        }
        var kernelDelta = current.Kernel - previous.Value.Kernel;
        var userDelta = current.User - previous.Value.User;
        var idleDelta = current.Idle - previous.Value.Idle;
        var totalDelta = kernelDelta + userDelta;
        if (totalDelta == 0 || idleDelta > totalDelta)
        {
            return null;
        }
        return Math.Clamp(
            (totalDelta - idleDelta) * 100.0 / totalDelta,
            0.0,
            100.0);
    }

    private static (double? UsedPercent, ulong? AvailableBytes) ReadMemory()
    {
        var status = new MemoryStatusEx
        {
            Length = (uint)Marshal.SizeOf<MemoryStatusEx>(),
        };
        return GlobalMemoryStatusEx(ref status)
            ? (status.MemoryLoad, status.AvailablePhysical)
            : (null, null);
    }

    private double? ReadControlSurfaceCpuPercent(double? elapsedSeconds)
    {
        try
        {
            _controlSurfaceProcess.Refresh();
            var current = _controlSurfaceProcess.TotalProcessorTime;
            var previous = _previousControlSurfaceCpu;
            _previousControlSurfaceCpu = current;
            if (previous is null || elapsedSeconds is not > 0)
            {
                return null;
            }
            var deltaSeconds = (current - previous.Value).TotalSeconds;
            return deltaSeconds < 0
                ? null
                : Math.Max(
                    0.0,
                    deltaSeconds / elapsedSeconds.Value * 100.0
                        / _logicalProcessorCount);
        }
        catch (InvalidOperationException)
        {
            return null;
        }
    }

    private void RefreshCpuFrequency()
    {
        var information = new ProcessorPowerInformation[_logicalProcessorCount];
        var result = CallNtPowerInformation(
            ProcessorPowerInformationLevel,
            IntPtr.Zero,
            0,
            information,
            (uint)(Marshal.SizeOf<ProcessorPowerInformation>()
                * information.Length));
        if (result != 0)
        {
            _cpuFrequencyMhz = null;
            _cpuFrequencyRatio = null;
            return;
        }
        var current = information
            .Where(value => value.CurrentMhz > 0)
            .Select(value => (double)value.CurrentMhz)
            .ToArray();
        var ratios = information
            .Where(value => value.CurrentMhz > 0 && value.MaxMhz > 0)
            .Select(value => (double)value.CurrentMhz / value.MaxMhz)
            .ToArray();
        _cpuFrequencyMhz = current.Length == 0 ? null : current.Average();
        _cpuFrequencyRatio = ratios.Length == 0 ? null : ratios.Average();
    }

    private static int SafeProcessValue(Func<int> read)
    {
        try
        {
            return Math.Max(0, read());
        }
        catch (Exception exception) when (
            exception is InvalidOperationException
                or System.ComponentModel.Win32Exception
                or NotSupportedException)
        {
            return 0;
        }
    }

    private static string BoundedProcessName(int processId, string value)
    {
        var normalized = new string(
                value.Where(character => !char.IsControl(character)).ToArray())
            .Trim();
        if (normalized.Length == 0)
        {
            return $"PID {processId}";
        }
        return normalized.Length <= 128 ? normalized : normalized[..128];
    }

    private static ulong ToUInt64(FileTime value) =>
        ((ulong)value.High << 32) | value.Low;

    public void ResetRateBaselines()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        _previousSystemTimes = null;
        _previousControlSurfaceCpu = null;
        _previousSampleAtUtc = null;
        _previousProcessTotals.Clear();
        _previousProcessAttributionTotals.Clear();
        _previousProcessAttributionAtUtc = null;
        _processAttributionGate.Reset();
        _gpuSampler.ResetRateBaseline();
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        foreach (var tracked in _blueStacksProcesses)
        {
            tracked.Process.Dispose();
        }
        _blueStacksProcesses.Clear();
        _gpuSampler.Dispose();
        _controlSurfaceProcess.Dispose();
    }

    private sealed record TrackedProcess(
        Process Process,
        int ThreadCount,
        int HandleCount);

    private readonly record struct ProcessTotals(
        TimeSpan CpuTime,
        long WorkingSetBytes,
        long PrivateBytes,
        ulong IoReadBytes,
        ulong IoWriteBytes);

    private readonly record struct ProcessAttributionTotals(
        string ProcessName,
        TimeSpan CpuTime,
        long WorkingSetBytes,
        long PrivateBytes);

    private readonly record struct ProcessRefreshResult(
        int ProcessCount,
        IReadOnlyList<HostProcessObservation> Attribution,
        double? SampleDurationMilliseconds)
    {
        public static ProcessRefreshResult Empty { get; } = new(
            0,
            [],
            null);
    }

    private readonly record struct SystemTimes(
        ulong Idle,
        ulong Kernel,
        ulong User);

    private readonly record struct BlueStacksMetrics(
        int ProcessCount,
        double? CpuPercent,
        double? CpuCorePercent,
        long WorkingSetBytes,
        long PrivateBytes,
        double? IoReadBytesPerSecond,
        double? IoWriteBytesPerSecond,
        int ThreadCount,
        int HandleCount);

    [StructLayout(LayoutKind.Sequential)]
    private struct FileTime
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatusEx
    {
        public uint Length;
        public uint MemoryLoad;
        public ulong TotalPhysical;
        public ulong AvailablePhysical;
        public ulong TotalPageFile;
        public ulong AvailablePageFile;
        public ulong TotalVirtual;
        public ulong AvailableVirtual;
        public ulong AvailableExtendedVirtual;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessorPowerInformation
    {
        public uint Number;
        public uint MaxMhz;
        public uint CurrentMhz;
        public uint MhzLimit;
        public uint MaxIdleState;
        public uint CurrentIdleState;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetSystemTimes(
        out FileTime idleTime,
        out FileTime kernelTime,
        out FileTime userTime);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx buffer);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetProcessIoCounters(
        IntPtr processHandle,
        out IoCounters counters);

    [DllImport("powrprof.dll")]
    private static extern uint CallNtPowerInformation(
        int informationLevel,
        IntPtr inputBuffer,
        uint inputBufferLength,
        [Out] ProcessorPowerInformation[] outputBuffer,
        uint outputBufferLength);
}
