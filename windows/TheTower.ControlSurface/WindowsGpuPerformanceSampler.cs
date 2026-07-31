using System.Diagnostics;
using System.Runtime.InteropServices;

namespace TheTower.ControlSurface;

internal sealed class WindowsGpuPerformanceSampler : IDisposable
{
    private const int RetryIntervalSamples = 60;
    private const int MaximumSampleCompetitors = 8;
    private const uint ErrorSuccess = 0;
    private const uint PdhMoreData = 0x800007D2;
    private const uint PdhNoData = 0x800007D5;
    private const uint PdhFormatDouble = 0x00000200;
    private const uint PdhFormatNoScale = 0x00001000;
    private const uint PdhFormatNoCap100 = 0x00008000;

    private readonly int _controlSurfaceProcessId =
        Environment.ProcessId;
    private IntPtr _query;
    private PdhArrayCounter? _gpuEngineUtilization;
    private PdhArrayCounter? _adapterDedicatedMemory;
    private PdhArrayCounter? _adapterSharedMemory;
    private PdhArrayCounter? _processDedicatedMemory;
    private PdhArrayCounter? _processSharedMemory;
    private int _samplesUntilRetry;
    private string? _configurationWarning;
    private string? _lastError;
    private bool _disposed;

    public GpuPerformanceMetrics Sample(
        IReadOnlySet<int> blueStacksProcessIds,
        IReadOnlyDictionary<int, string> processNames)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        var timer = Stopwatch.StartNew();
        if (_query == IntPtr.Zero)
        {
            if (_samplesUntilRetry > 0)
            {
                _samplesUntilRetry--;
                timer.Stop();
                return GpuPerformanceMetrics.Unavailable(
                    _lastError,
                    timer.Elapsed.TotalMilliseconds);
            }
            if (!TryInitialize())
            {
                timer.Stop();
                return GpuPerformanceMetrics.Unavailable(
                    _lastError,
                    timer.Elapsed.TotalMilliseconds);
            }
        }

        var collectStatus = PdhCollectQueryData(_query);
        if (collectStatus != ErrorSuccess)
        {
            ResetQuery(
                $"Windows GPU counter collection failed "
                + $"({FormatStatus(collectStatus)}).");
            timer.Stop();
            return GpuPerformanceMetrics.Unavailable(
                _lastError,
                timer.Elapsed.TotalMilliseconds);
        }

        var readErrors = new List<string>();
        var engineValues = ReadCounter(
            _gpuEngineUtilization,
            "GPU engine utilization",
            readErrors);
        var adapterDedicated = ReadCounter(
            _adapterDedicatedMemory,
            "GPU dedicated memory",
            readErrors);
        var adapterShared = ReadCounter(
            _adapterSharedMemory,
            "GPU shared memory",
            readErrors);
        var processDedicated = ReadCounter(
            _processDedicatedMemory,
            "per-process GPU dedicated memory",
            readErrors);
        var processShared = ReadCounter(
            _processSharedMemory,
            "per-process GPU shared memory",
            readErrors);

        var engineTotals = new Dictionary<string, double>(
            StringComparer.OrdinalIgnoreCase);
        var blueStacksEngineTotals = new Dictionary<string, double>(
            StringComparer.OrdinalIgnoreCase);
        var processEngineTotals =
            new Dictionary<(int ProcessId, string Engine), double>();
        var processGpu = new Dictionary<int, double>();
        var gpuProcessIds = new HashSet<int>();
        foreach (var item in engineValues)
        {
            var value = FiniteNonNegative(item.Value);
            var engine = EngineIdentity(item.InstanceName);
            Add(engineTotals, engine, value);
            if (!TryProcessId(item.InstanceName, out var processId))
            {
                continue;
            }
            gpuProcessIds.Add(processId);
            var processEngine = (processId, engine);
            processEngineTotals[processEngine] =
                processEngineTotals.GetValueOrDefault(processEngine) + value;
            if (blueStacksProcessIds.Contains(processId))
            {
                Add(blueStacksEngineTotals, engine, value);
            }
        }
        foreach (var (processEngine, value) in processEngineTotals)
        {
            processGpu[processEngine.ProcessId] = Math.Max(
                processGpu.GetValueOrDefault(processEngine.ProcessId),
                value);
        }

        var dedicatedByProcess = SumByProcess(processDedicated);
        var sharedByProcess = SumByProcess(processShared);
        gpuProcessIds.UnionWith(dedicatedByProcess.Keys);
        gpuProcessIds.UnionWith(sharedByProcess.Keys);

        var competitors = gpuProcessIds
            .Where(processId =>
                processId > 0
                && processId != _controlSurfaceProcessId
                && !blueStacksProcessIds.Contains(processId))
            .Select(processId => new HostGpuProcessSample(
                processId,
                BoundedProcessName(processId, processNames),
                Math.Clamp(
                    processGpu.GetValueOrDefault(processId),
                    0.0,
                    100.0),
                ToByteCount(dedicatedByProcess.GetValueOrDefault(processId)),
                ToByteCount(sharedByProcess.GetValueOrDefault(processId))))
            .Where(process =>
                process.GpuPercent >= 0.1
                || process.DedicatedMemoryBytes > 0
                || process.SharedMemoryBytes > 0)
            .OrderByDescending(process => process.GpuPercent)
            .ThenByDescending(process =>
                (double)process.DedicatedMemoryBytes
                + process.SharedMemoryBytes)
            .Take(MaximumSampleCompetitors)
            .ToArray();

        timer.Stop();
        return new GpuPerformanceMetrics(
            true,
            MaximumUtilization(engineTotals),
            SumBytes(adapterDedicated),
            SumBytes(adapterShared),
            MaximumUtilization(blueStacksEngineTotals),
            _processDedicatedMemory is null
                ? null
                : SumForProcesses(
                    dedicatedByProcess,
                    blueStacksProcessIds),
            _processSharedMemory is null
                ? null
                : SumForProcesses(
                    sharedByProcess,
                    blueStacksProcessIds),
            gpuProcessIds.Count,
            competitors,
            timer.Elapsed.TotalMilliseconds,
            CombineErrors(readErrors));
    }

    private bool TryInitialize()
    {
        var openStatus = PdhOpenQueryW(
            null,
            UIntPtr.Zero,
            out _query);
        if (openStatus != ErrorSuccess)
        {
            ResetQuery(
                $"Windows GPU counters are unavailable "
                + $"({FormatStatus(openStatus)}).");
            return false;
        }

        var engineStatus = TryAddCounter(
            @"\GPU Engine(*)\Utilization Percentage",
            PdhFormatDouble | PdhFormatNoCap100,
            out _gpuEngineUtilization);
        if (engineStatus != ErrorSuccess)
        {
            ResetQuery(
                $"Windows GPU Engine counters are unavailable "
                + $"({FormatStatus(engineStatus)}).");
            return false;
        }

        var unavailable = new List<string>();
        TryAddOptionalCounter(
            @"\GPU Adapter Memory(*)\Dedicated Usage",
            "adapter dedicated memory",
            PdhFormatDouble | PdhFormatNoScale,
            unavailable,
            out _adapterDedicatedMemory);
        TryAddOptionalCounter(
            @"\GPU Adapter Memory(*)\Shared Usage",
            "adapter shared memory",
            PdhFormatDouble | PdhFormatNoScale,
            unavailable,
            out _adapterSharedMemory);
        TryAddOptionalCounter(
            @"\GPU Process Memory(*)\Dedicated Usage",
            "process dedicated memory",
            PdhFormatDouble | PdhFormatNoScale,
            unavailable,
            out _processDedicatedMemory);
        TryAddOptionalCounter(
            @"\GPU Process Memory(*)\Shared Usage",
            "process shared memory",
            PdhFormatDouble | PdhFormatNoScale,
            unavailable,
            out _processSharedMemory);
        _configurationWarning = unavailable.Count == 0
            ? null
            : "Unavailable GPU counters: " + string.Join(", ", unavailable)
                + ".";
        _lastError = _configurationWarning;
        return true;
    }

    private uint TryAddCounter(
        string path,
        uint format,
        out PdhArrayCounter? counter)
    {
        var status = PdhAddEnglishCounterW(
            _query,
            path,
            UIntPtr.Zero,
            out var handle);
        counter = status == ErrorSuccess
            ? new PdhArrayCounter(handle, format)
            : null;
        return status;
    }

    private void TryAddOptionalCounter(
        string path,
        string label,
        uint format,
        ICollection<string> unavailable,
        out PdhArrayCounter? counter)
    {
        var status = TryAddCounter(path, format, out counter);
        if (status != ErrorSuccess)
        {
            unavailable.Add($"{label} ({FormatStatus(status)})");
        }
    }

    private static IReadOnlyList<PdhCounterValue> ReadCounter(
        PdhArrayCounter? counter,
        string label,
        ICollection<string> errors)
    {
        if (counter is null)
        {
            return [];
        }
        var values = counter.Read(out var status);
        if (status != ErrorSuccess && status != PdhNoData)
        {
            errors.Add($"{label} read failed ({FormatStatus(status)}).");
        }
        return values;
    }

    private static Dictionary<int, double> SumByProcess(
        IReadOnlyList<PdhCounterValue> values)
    {
        var totals = new Dictionary<int, double>();
        foreach (var item in values)
        {
            if (TryProcessId(item.InstanceName, out var processId))
            {
                totals[processId] =
                    totals.GetValueOrDefault(processId)
                    + FiniteNonNegative(item.Value);
            }
        }
        return totals;
    }

    private static double? MaximumUtilization(
        IReadOnlyDictionary<string, double> engineTotals) =>
        engineTotals.Count == 0
            ? null
            : Math.Clamp(engineTotals.Values.Max(), 0.0, 100.0);

    private static long? SumBytes(IReadOnlyList<PdhCounterValue> values) =>
        values.Count == 0
            ? null
            : ToByteCount(values.Sum(item => FiniteNonNegative(item.Value)));

    private static long SumForProcesses(
        IReadOnlyDictionary<int, double> values,
        IReadOnlySet<int> processIds) =>
        ToByteCount(
            processIds.Sum(processId => values.GetValueOrDefault(processId)));

    private static long ToByteCount(double value) =>
        value <= 0.0 || !double.IsFinite(value)
            ? 0
            : (long)Math.Min(long.MaxValue, Math.Round(value));

    private static double FiniteNonNegative(double value) =>
        double.IsFinite(value) ? Math.Max(0.0, value) : 0.0;

    private static string BoundedProcessName(
        int processId,
        IReadOnlyDictionary<int, string> processNames)
    {
        var fallback = $"PID {processId}";
        if (!processNames.TryGetValue(processId, out var value))
        {
            return fallback;
        }
        var normalized = new string(
                value.Where(character => !char.IsControl(character)).ToArray())
            .Trim();
        if (normalized.Length == 0)
        {
            return fallback;
        }
        return normalized.Length <= 128 ? normalized : normalized[..128];
    }

    private static void Add(
        IDictionary<string, double> values,
        string key,
        double value) =>
        values[key] = (values.TryGetValue(key, out var current)
            ? current
            : 0.0) + value;

    private static string EngineIdentity(string instanceName)
    {
        var luid = instanceName.IndexOf(
            "_luid_",
            StringComparison.OrdinalIgnoreCase);
        return luid < 0 ? instanceName : instanceName[(luid + 1)..];
    }

    private static bool TryProcessId(
        string instanceName,
        out int processId)
    {
        processId = 0;
        if (!instanceName.StartsWith(
                "pid_",
                StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        var separator = instanceName.IndexOf('_', 4);
        return separator > 4
            && int.TryParse(instanceName.AsSpan(4, separator - 4), out processId)
            && processId >= 0;
    }

    private string? CombineErrors(IReadOnlyCollection<string> readErrors)
    {
        var errors = new List<string>();
        if (!string.IsNullOrWhiteSpace(_configurationWarning))
        {
            errors.Add(_configurationWarning);
        }
        errors.AddRange(readErrors);
        _lastError = errors.Count == 0 ? null : string.Join(" ", errors);
        return _lastError;
    }

    private void ResetQuery(string error)
    {
        DisposeQuery();
        _lastError = error;
        _configurationWarning = null;
        _samplesUntilRetry = RetryIntervalSamples;
    }

    public void ResetRateBaseline()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        DisposeQuery();
        _lastError = null;
        _configurationWarning = null;
        _samplesUntilRetry = 0;
    }

    private void DisposeQuery()
    {
        _gpuEngineUtilization?.Dispose();
        _adapterDedicatedMemory?.Dispose();
        _adapterSharedMemory?.Dispose();
        _processDedicatedMemory?.Dispose();
        _processSharedMemory?.Dispose();
        _gpuEngineUtilization = null;
        _adapterDedicatedMemory = null;
        _adapterSharedMemory = null;
        _processDedicatedMemory = null;
        _processSharedMemory = null;
        if (_query != IntPtr.Zero)
        {
            PdhCloseQuery(_query);
            _query = IntPtr.Zero;
        }
    }

    private static string FormatStatus(uint status) =>
        $"PDH 0x{status:X8}";

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        DisposeQuery();
    }

    internal sealed record GpuPerformanceMetrics(
        bool Available,
        double? HostGpuPercent,
        long? HostDedicatedMemoryBytes,
        long? HostSharedMemoryBytes,
        double? BlueStacksGpuPercent,
        long? BlueStacksDedicatedMemoryBytes,
        long? BlueStacksSharedMemoryBytes,
        int ProcessCount,
        IReadOnlyList<HostGpuProcessSample> Competitors,
        double SampleDurationMilliseconds,
        string? Error)
    {
        public static GpuPerformanceMetrics Unavailable(
            string? error,
            double durationMilliseconds) =>
            new(
                false,
                null,
                null,
                null,
                null,
                null,
                null,
                0,
                [],
                durationMilliseconds,
                error);
    }

    private sealed class PdhArrayCounter : IDisposable
    {
        private const int MaximumBufferBytes = 4 * 1024 * 1024;

        private readonly IntPtr _handle;
        private readonly uint _format;
        private IntPtr _buffer;
        private uint _capacity;

        public PdhArrayCounter(IntPtr handle, uint format)
        {
            _handle = handle;
            _format = format;
        }

        public IReadOnlyList<PdhCounterValue> Read(out uint status)
        {
            for (var attempt = 0; attempt < 2; attempt++)
            {
                var size = _capacity;
                uint count = 0;
                status = PdhGetFormattedCounterArrayW(
                    _handle,
                    _format,
                    ref size,
                    ref count,
                    _buffer);
                if (status == PdhMoreData)
                {
                    if (size == 0 || size > MaximumBufferBytes)
                    {
                        return [];
                    }
                    Resize(size);
                    continue;
                }
                if (status != ErrorSuccess || count == 0)
                {
                    return [];
                }

                var itemSize = Marshal.SizeOf<PdhFormattedCounterValueItem>();
                var values = new List<PdhCounterValue>((int)count);
                for (var index = 0; index < count; index++)
                {
                    var address = IntPtr.Add(
                        _buffer,
                        checked((int)index * itemSize));
                    var item =
                        Marshal.PtrToStructure<PdhFormattedCounterValueItem>(
                            address);
                    if (item.Value.Status is not 0 and not 1)
                    {
                        continue;
                    }
                    var name = Marshal.PtrToStringUni(item.Name);
                    if (!string.IsNullOrWhiteSpace(name)
                        && double.IsFinite(item.Value.DoubleValue))
                    {
                        values.Add(new PdhCounterValue(
                            name,
                            item.Value.DoubleValue));
                    }
                }
                return values;
            }
            status = PdhMoreData;
            return [];
        }

        private void Resize(uint size)
        {
            if (_buffer != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(_buffer);
            }
            _buffer = Marshal.AllocHGlobal(checked((int)size));
            _capacity = size;
        }

        public void Dispose()
        {
            if (_buffer == IntPtr.Zero)
            {
                return;
            }
            Marshal.FreeHGlobal(_buffer);
            _buffer = IntPtr.Zero;
            _capacity = 0;
        }
    }

    private sealed record PdhCounterValue(string InstanceName, double Value);

    [StructLayout(LayoutKind.Sequential)]
    private struct PdhFormattedCounterValueItem
    {
        public IntPtr Name;
        public PdhFormattedCounterValue Value;
    }

    [StructLayout(LayoutKind.Explicit, Size = 16)]
    private struct PdhFormattedCounterValue
    {
        [FieldOffset(0)]
        public uint Status;

        [FieldOffset(8)]
        public double DoubleValue;
    }

    [DllImport(
        "pdh.dll",
        CharSet = CharSet.Unicode,
        EntryPoint = "PdhOpenQueryW")]
    private static extern uint PdhOpenQueryW(
        string? dataSource,
        UIntPtr userData,
        out IntPtr query);

    [DllImport(
        "pdh.dll",
        CharSet = CharSet.Unicode,
        EntryPoint = "PdhAddEnglishCounterW")]
    private static extern uint PdhAddEnglishCounterW(
        IntPtr query,
        string counterPath,
        UIntPtr userData,
        out IntPtr counter);

    [DllImport("pdh.dll")]
    private static extern uint PdhCollectQueryData(IntPtr query);

    [DllImport(
        "pdh.dll",
        CharSet = CharSet.Unicode,
        EntryPoint = "PdhGetFormattedCounterArrayW")]
    private static extern uint PdhGetFormattedCounterArrayW(
        IntPtr counter,
        uint format,
        ref uint bufferSize,
        ref uint itemCount,
        IntPtr itemBuffer);

    [DllImport("pdh.dll")]
    private static extern uint PdhCloseQuery(IntPtr query);
}
