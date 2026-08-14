namespace TheTower.ControlSurface;

public sealed class HostPerformanceTracker : IDisposable
{
    private const int SampleIntervalMilliseconds = 1000;
    private const int AggregateSampleCount = 10;
    private const int RawRingCapacity = 120;
    private const int MaximumGpuCompetitors = 5;
    private const int MaximumProcessAttributionPerResource = 4;
    private static readonly TimeSpan RunContextFreshness =
        TimeSpan.FromSeconds(15);

    private readonly object _stateGate = new();
    private readonly ControlSurfaceApi _api;
    private readonly HostPerformanceSpool _spool = new();
    private readonly WindowsHostPerformanceSampler _sampler;
    private readonly Queue<HostPerformanceSample> _rawSamples = new();
    private readonly ManualResetEvent _stopEvent = new(false);
    private readonly AutoResetEvent _samplingStateChanged = new(false);
    private readonly SemaphoreSlim _uploadSignal = new(0, 1);
    private readonly CancellationTokenSource _uploadCancellation = new();
    private readonly string _sessionId = Guid.NewGuid().ToString();
    private HostPerformanceContext _context = new(
        null,
        null,
        DateTimeOffset.MinValue,
        null);
    private Thread? _sampleThread;
    private Task? _uploadTask;
    private long _sequence;
    private bool _samplingEnabled = true;
    private bool _uploadEnabled;
    private DateTimeOffset? _lastUploadedAtUtc;
    private string? _uploadError;
    private string? _samplerError;
    private bool _started;
    private bool _disposed;
    private int _resetRateBaselinesRequested;

    public HostPerformanceTracker(ControlSurfaceApi api) : this(
        api,
        new BlueStacksInstanceController())
    {
    }

    internal HostPerformanceTracker(
        ControlSurfaceApi api,
        IBlueStacksInstanceController blueStacksController)
    {
        _api = api;
        _sampler = new WindowsHostPerformanceSampler(blueStacksController);
        _sequence = _spool.NextSequence;
    }

    public event EventHandler<HostPerformanceSnapshot>? SnapshotUpdated;

    public bool SamplingEnabled
    {
        get
        {
            lock (_stateGate)
            {
                return _samplingEnabled;
            }
        }
    }

    public void Start()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        lock (_stateGate)
        {
            if (_started)
            {
                return;
            }
            _started = true;
            _sampleThread = new Thread(SampleLoop)
            {
                IsBackground = true,
                Name = "TheTower host performance sampler",
                Priority = ThreadPriority.BelowNormal,
            };
            _uploadTask = Task.Run(() =>
                UploadLoopAsync(_uploadCancellation.Token));
            _sampleThread.Start();
        }
        SignalUpload();
        PublishSnapshot();
    }

    public void SetSamplingEnabled(bool enabled)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        lock (_stateGate)
        {
            if (_samplingEnabled == enabled)
            {
                return;
            }
            _samplingEnabled = enabled;
        }
        _samplingStateChanged.Set();
        PublishSnapshot();
    }

    public void ResetSamplerRateBaselines()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        Interlocked.Exchange(ref _resetRateBaselinesRequested, 1);
        _samplingStateChanged.Set();
    }

    public void UpdateServerContext(
        int? adbPort,
        string? runId,
        bool uploadEnabled)
        => UpdateServerContext(adbPort, runId, null, uploadEnabled);

    internal void UpdateServerContext(
        int? adbPort,
        string? runId,
        BlueStacksRecoveryTarget? blueStacksTarget,
        bool uploadEnabled)
    {
        lock (_stateGate)
        {
            _context = new HostPerformanceContext(
                adbPort,
                string.IsNullOrWhiteSpace(runId) ? null : runId.Trim(),
                DateTimeOffset.UtcNow,
                blueStacksTarget);
            _uploadEnabled = uploadEnabled;
        }
        if (uploadEnabled)
        {
            SignalUpload();
        }
        PublishSnapshot();
    }

    private void SampleLoop()
    {
        var aggregateWindow = new List<HostPerformanceSample>(
            AggregateSampleCount);
        var nextSampleAt = DateTimeOffset.UtcNow;
        var waitHandles = new WaitHandle[]
        {
            _stopEvent,
            _samplingStateChanged,
        };
        try
        {
            while (!_stopEvent.WaitOne(0))
            {
                if (!SamplingEnabled)
                {
                    FlushAggregateWindow(aggregateWindow);
                    PublishSnapshot();
                    if (WaitHandle.WaitAny(waitHandles) == 0)
                    {
                        break;
                    }
                    if (SamplingEnabled)
                    {
                        _sampler.ResetRateBaselines();
                    }
                    nextSampleAt = DateTimeOffset.UtcNow;
                    continue;
                }

                nextSampleAt += TimeSpan.FromMilliseconds(
                    SampleIntervalMilliseconds);
                try
                {
                    if (Interlocked.Exchange(
                        ref _resetRateBaselinesRequested,
                        0) == 1)
                    {
                        FlushAggregateWindow(aggregateWindow);
                        _sampler.ResetRateBaselines();
                    }
                    var context = CurrentSampleContext();
                    var sample = _sampler.Sample(context);
                    lock (_stateGate)
                    {
                        _samplerError = null;
                        _rawSamples.Enqueue(sample);
                        while (_rawSamples.Count > RawRingCapacity)
                        {
                            _rawSamples.Dequeue();
                        }
                    }

                    if (aggregateWindow.Count > 0
                        && (HostPerformanceAggregateWindow.HasDiscontinuity(
                                aggregateWindow[^1].TimestampUtc,
                                sample.TimestampUtc)
                            || !SameCorrelation(
                                aggregateWindow[0].Context,
                                sample.Context)
                            || HostPerformanceAggregateWindow.HasListenerDiscontinuity(
                                aggregateWindow[^1].BlueStacksListener,
                                sample.BlueStacksListener)))
                    {
                        EnqueueAggregate(aggregateWindow);
                        aggregateWindow.Clear();
                    }
                    aggregateWindow.Add(sample);
                    if (aggregateWindow.Count >= AggregateSampleCount)
                    {
                        EnqueueAggregate(aggregateWindow);
                        aggregateWindow.Clear();
                    }
                    PublishSnapshot();
                }
                catch (Exception exception) when (
                    exception is not OutOfMemoryException
                        and not StackOverflowException)
                {
                    lock (_stateGate)
                    {
                        _samplerError = exception.Message;
                    }
                    PublishSnapshot();
                }

                var delay = nextSampleAt - DateTimeOffset.UtcNow;
                if (delay <= TimeSpan.Zero)
                {
                    nextSampleAt = DateTimeOffset.UtcNow;
                    continue;
                }
                if (WaitHandle.WaitAny(waitHandles, delay) == 0)
                {
                    break;
                }
            }
        }
        finally
        {
            FlushAggregateWindow(aggregateWindow);
        }
    }

    private void FlushAggregateWindow(List<HostPerformanceSample> samples)
    {
        if (samples.Count == 0)
        {
            return;
        }
        EnqueueAggregate(samples);
        samples.Clear();
    }

    private HostPerformanceContext CurrentSampleContext()
    {
        lock (_stateGate)
        {
            var runId = DateTimeOffset.UtcNow - _context.ObservedAtUtc
                    <= RunContextFreshness
                ? _context.RunId
                : null;
            return _context with { RunId = runId };
        }
    }

    private static bool SameCorrelation(
        HostPerformanceContext left,
        HostPerformanceContext right) =>
        left.AdbPort == right.AdbPort
        && string.Equals(left.RunId, right.RunId, StringComparison.Ordinal)
        && Equals(left.BlueStacksTarget, right.BlueStacksTarget);

    private void EnqueueAggregate(IReadOnlyList<HostPerformanceSample> samples)
    {
        if (samples.Count == 0)
        {
            return;
        }
        var aggregate = BuildAggregate(samples);
        _spool.Enqueue(aggregate);
        SignalUpload();
    }

    private HostPerformanceAggregate BuildAggregate(
        IReadOnlyList<HostPerformanceSample> samples)
    {
        var first = samples[0];
        var last = samples[^1];
        var metrics = new Dictionary<string, double>(StringComparer.Ordinal);
        AddAverageAndMaximum(
            metrics,
            "host_cpu_percent",
            samples.Select(sample => sample.HostCpuPercent));
        AddAverageAndMaximum(
            metrics,
            "host_memory_used_percent",
            samples.Select(sample => sample.HostMemoryUsedPercent));
        AddMinimum(
            metrics,
            "host_available_memory_bytes_min",
            samples.Select(sample =>
                sample.HostAvailableMemoryBytes is null
                    ? null
                    : (double?)sample.HostAvailableMemoryBytes.Value));
        AddAverageAndMinimum(
            metrics,
            "host_cpu_frequency_mhz",
            samples.Select(sample => sample.HostCpuFrequencyMhz));
        AddAverageAndMinimum(
            metrics,
            "host_cpu_frequency_ratio",
            samples.Select(sample => sample.HostCpuFrequencyRatio));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_cpu_percent",
            samples.Select(sample => sample.BlueStacksCpuPercent));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_cpu_core_percent",
            samples.Select(sample => sample.BlueStacksCpuCorePercent));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_working_set_bytes",
            samples.Select(sample =>
                (double?)sample.BlueStacksWorkingSetBytes));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_private_bytes",
            samples.Select(sample =>
                (double?)sample.BlueStacksPrivateBytes));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_io_read_bytes_per_second",
            samples.Select(sample =>
                sample.BlueStacksIoReadBytesPerSecond));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_io_write_bytes_per_second",
            samples.Select(sample =>
                sample.BlueStacksIoWriteBytesPerSecond));
        AddMinimumAndMaximum(
            metrics,
            "bluestacks_process_count",
            samples.Select(sample => (double?)sample.BlueStacksProcessCount));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_thread_count",
            samples.Select(sample => (double?)sample.BlueStacksThreadCount));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_handle_count",
            samples.Select(sample => (double?)sample.BlueStacksHandleCount));
        AddAverageAndMaximum(
            metrics,
            "host_gpu_percent",
            samples.Select(sample => sample.HostGpuPercent));
        AddAverageAndMaximum(
            metrics,
            "host_gpu_dedicated_memory_bytes",
            samples.Select(sample =>
                sample.HostGpuDedicatedMemoryBytes is null
                    ? null
                    : (double?)sample.HostGpuDedicatedMemoryBytes.Value));
        AddAverageAndMaximum(
            metrics,
            "host_gpu_shared_memory_bytes",
            samples.Select(sample =>
                sample.HostGpuSharedMemoryBytes is null
                    ? null
                    : (double?)sample.HostGpuSharedMemoryBytes.Value));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_gpu_percent",
            samples.Select(sample => sample.BlueStacksGpuPercent));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_gpu_dedicated_memory_bytes",
            samples.Select(sample =>
                sample.BlueStacksGpuDedicatedMemoryBytes is null
                    ? null
                    : (double?)sample.BlueStacksGpuDedicatedMemoryBytes.Value));
        AddAverageAndMaximum(
            metrics,
            "bluestacks_gpu_shared_memory_bytes",
            samples.Select(sample =>
                sample.BlueStacksGpuSharedMemoryBytes is null
                    ? null
                    : (double?)sample.BlueStacksGpuSharedMemoryBytes.Value));
        AddMinimumAndMaximum(
            metrics,
            "gpu_process_count",
            samples.Select(sample => (double?)sample.GpuProcessCount));
        AddAverageAndMaximum(
            metrics,
            "gpu_sample_duration_ms",
            samples.Select(sample => sample.GpuSampleDurationMilliseconds));
        var processAttributionSamples = samples
            .Where(sample =>
                sample.ProcessAttributionSampleDurationMilliseconds is not null)
            .ToArray();
        AddMinimumAndMaximum(
            metrics,
            "process_attribution_process_count",
            processAttributionSamples.Select(sample =>
                (double?)sample.ProcessAttributionProcessCount));
        AddAverageAndMaximum(
            metrics,
            "process_attribution_sample_duration_ms",
            processAttributionSamples.Select(sample =>
                sample.ProcessAttributionSampleDurationMilliseconds));
        AddAverageAndMaximum(
            metrics,
            "control_surface_cpu_percent",
            samples.Select(sample => sample.ControlSurfaceCpuPercent));
        AddAverageAndMaximum(
            metrics,
            "sample_duration_ms",
            samples.Select(sample =>
                (double?)sample.SampleDurationMilliseconds));

        var sequence = Interlocked.Increment(ref _sequence) - 1;
        return new HostPerformanceAggregate
        {
            AggregateId = Guid.NewGuid().ToString(),
            SessionId = _sessionId,
            Sequence = sequence,
            HostId = _spool.HostId,
            HostName = Environment.MachineName,
            LogicalProcessorCount = Math.Max(1, Environment.ProcessorCount),
            WindowStartUtc = FormatTimestamp(first.TimestampUtc),
            WindowEndUtc = FormatTimestamp(last.TimestampUtc),
            SampleCount = samples.Count,
            SampleIntervalMs = SampleIntervalMilliseconds,
            AdbPort = first.Context.AdbPort,
            RunId = first.Context.RunId,
            ContextObservedAtUtc = last.Context.ObservedAtUtc
                    == DateTimeOffset.MinValue
                ? null
                : FormatTimestamp(last.Context.ObservedAtUtc),
            BlueStacksListener = first.BlueStacksListener,
            Metrics = metrics,
            GpuCompetitors = BuildGpuCompetitors(samples),
            ProcessAttribution = BuildProcessAttribution(samples),
        };
    }

    private static List<HostPerformanceGpuCompetitor> BuildGpuCompetitors(
        IReadOnlyList<HostPerformanceSample> samples)
    {
        var windowSampleCount = Math.Max(1, samples.Count);
        return samples
            .SelectMany(sample => sample.GpuCompetitors)
            .GroupBy(
                process => (process.ProcessId, process.ProcessName))
            .Select(group => new HostPerformanceGpuCompetitor
            {
                ProcessId = group.Key.ProcessId,
                ProcessName = group.Key.ProcessName,
                SampleCount = group.Count(),
                GpuPercentAverage =
                    group.Sum(process => process.GpuPercent)
                    / windowSampleCount,
                GpuPercentMaximum =
                    group.Max(process => process.GpuPercent),
                DedicatedMemoryBytesMaximum =
                    group.Max(process => process.DedicatedMemoryBytes),
                SharedMemoryBytesMaximum =
                    group.Max(process => process.SharedMemoryBytes),
            })
            .OrderByDescending(process => process.GpuPercentMaximum)
            .ThenByDescending(process =>
                (double)process.DedicatedMemoryBytesMaximum
                + process.SharedMemoryBytesMaximum)
            .Take(MaximumGpuCompetitors)
            .ToList();
    }

    private static List<HostPerformanceProcessAttribution>
        BuildProcessAttribution(IReadOnlyList<HostPerformanceSample> samples)
    {
        var candidates = samples
            .SelectMany(sample => sample.ProcessAttribution)
            .GroupBy(
                process => (process.ProcessId, process.ProcessName))
            .Select(group =>
            {
                var cpuValues = group
                    .Where(process => process.CpuPercent is not null)
                    .Select(process => process.CpuPercent!.Value)
                    .ToArray();
                return new HostPerformanceProcessAttribution
                {
                    ProcessId = group.Key.ProcessId,
                    ProcessName = group.Key.ProcessName,
                    SampleCount = group.Count(),
                    CpuPercentAverage = cpuValues.Length == 0
                        ? null
                        : cpuValues.Average(),
                    CpuPercentMaximum = cpuValues.Length == 0
                        ? null
                        : cpuValues.Max(),
                    WorkingSetBytesMaximum = group.Max(process =>
                        process.WorkingSetBytes),
                    PrivateBytesMaximum = group.Max(process =>
                        process.PrivateBytes),
                };
            })
            .ToArray();
        var selected = new List<HostPerformanceProcessAttribution>(
            HostProcessAttributionSelector.MaximumSelectedProcesses);
        var identities = new HashSet<(int ProcessId, string ProcessName)>();
        AddDistinctProcessAttribution(
            candidates
                .Where(process => process.CpuPercentMaximum > 0.0)
                .OrderByDescending(process => process.CpuPercentMaximum)
                .ThenByDescending(process => process.PrivateBytesMaximum)
                .Take(MaximumProcessAttributionPerResource),
            selected,
            identities);
        AddDistinctProcessAttribution(
            candidates
                .Where(process => process.WorkingSetBytesMaximum > 0)
                .OrderByDescending(process => process.WorkingSetBytesMaximum)
                .ThenByDescending(process => process.PrivateBytesMaximum)
                .Take(MaximumProcessAttributionPerResource),
            selected,
            identities);
        return selected;
    }

    private static void AddDistinctProcessAttribution(
        IEnumerable<HostPerformanceProcessAttribution> source,
        ICollection<HostPerformanceProcessAttribution> destination,
        ISet<(int ProcessId, string ProcessName)> identities)
    {
        foreach (var process in source)
        {
            if (identities.Add((process.ProcessId, process.ProcessName)))
            {
                destination.Add(process);
            }
        }
    }

    private async Task UploadLoopAsync(CancellationToken cancellationToken)
    {
        var retryDelay = TimeSpan.FromSeconds(1);
        while (!cancellationToken.IsCancellationRequested)
        {
            bool uploadEnabled;
            lock (_stateGate)
            {
                uploadEnabled = _uploadEnabled;
            }
            if (!uploadEnabled || _spool.PendingCount == 0)
            {
                try
                {
                    await _uploadSignal.WaitAsync(
                        TimeSpan.FromSeconds(10),
                        cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                continue;
            }

            var candidates = _spool.Peek(
                HostPerformanceUploadBatch.MaximumAggregateCount);
            HostPerformanceUploadPayload? upload = null;
            try
            {
                upload = HostPerformanceUploadBatch.Prepare(candidates);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(
                    cancellationToken);
                timeout.CancelAfter(TimeSpan.FromSeconds(12));
                var response = await _api.PostHostPerformanceAsync(
                    upload,
                    timeout.Token);
                if (response.Received != upload.Aggregates.Count)
                {
                    throw new InvalidOperationException(
                        "The Linux service acknowledged an unexpected "
                        + "host-performance batch size.");
                }
                _spool.Acknowledge(
                    upload.Aggregates.Select(item => item.AggregateId));
                lock (_stateGate)
                {
                    _lastUploadedAtUtc = DateTimeOffset.UtcNow;
                    _uploadError = null;
                }
                retryDelay = TimeSpan.FromSeconds(1);
                PublishSnapshot();
                if (_spool.PendingCount > 0)
                {
                    SignalUpload();
                }
            }
            catch (OperationCanceledException)
                when (!cancellationToken.IsCancellationRequested)
            {
                lock (_stateGate)
                {
                    _uploadError =
                        "Host-performance upload timed out; telemetry remains "
                        + "in the local spool.";
                }
                PublishSnapshot();
                if (!await DelayForRetry(retryDelay, cancellationToken))
                {
                    return;
                }
                retryDelay = NextRetryDelay(retryDelay);
            }
            catch (ControlSurfaceApiException exception) when (
                upload is not null
                && HostPerformanceUploadBatch.TryGetRejectedAggregateIndex(
                    exception,
                    upload.Aggregates.Count,
                    out _))
            {
                HostPerformanceUploadBatch.TryGetRejectedAggregateIndex(
                    exception,
                    upload.Aggregates.Count,
                    out var rejectedIndex);
                var rejected = upload.Aggregates[rejectedIndex];
                var quarantined = _spool.Reject(
                    rejected.AggregateId,
                    exception.Message);
                lock (_stateGate)
                {
                    _uploadError = quarantined
                        ? "Linux rejected one host-performance aggregate on "
                            + "schema validation. "
                            + "It was preserved locally and later "
                            + "telemetry will continue uploading."
                        : exception.Message
                            + " The rejected aggregate could not be preserved "
                            + "separately, so telemetry remains in the local "
                            + "spool.";
                }
                PublishSnapshot();
                if (quarantined)
                {
                    retryDelay = TimeSpan.FromSeconds(1);
                    if (!await DelayForRetry(
                        TimeSpan.FromSeconds(1),
                        cancellationToken))
                    {
                        return;
                    }
                    continue;
                }
                if (!await DelayForRetry(retryDelay, cancellationToken))
                {
                    return;
                }
                retryDelay = NextRetryDelay(retryDelay);
            }
            catch (Exception exception) when (
                exception is not OutOfMemoryException
                    and not StackOverflowException)
            {
                lock (_stateGate)
                {
                    _uploadError =
                        $"{exception.Message} Telemetry remains in the local spool.";
                }
                PublishSnapshot();
                if (!await DelayForRetry(retryDelay, cancellationToken))
                {
                    return;
                }
                retryDelay = NextRetryDelay(retryDelay);
            }
        }
    }

    private static async Task<bool> DelayForRetry(
        TimeSpan delay,
        CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(delay, cancellationToken);
            return true;
        }
        catch (OperationCanceledException)
        {
            return false;
        }
    }

    private static TimeSpan NextRetryDelay(TimeSpan current) =>
        TimeSpan.FromSeconds(Math.Min(30, Math.Max(1, current.TotalSeconds * 2)));

    private void SignalUpload()
    {
        try
        {
            if (_uploadSignal.CurrentCount == 0)
            {
                _uploadSignal.Release();
            }
        }
        catch (ObjectDisposedException)
        {
            // Shutdown owns the remaining locally persisted aggregates.
        }
    }

    private void PublishSnapshot()
    {
        var snapshot = BuildSnapshot();
        SnapshotUpdated?.Invoke(this, snapshot);
    }

    private HostPerformanceSnapshot BuildSnapshot()
    {
        HostPerformanceSample[] recent;
        bool samplingEnabled;
        bool uploadEnabled;
        DateTimeOffset? lastUploadedAtUtc;
        string? uploadError;
        string? samplerError;
        lock (_stateGate)
        {
            recent = _rawSamples.TakeLast(AggregateSampleCount).ToArray();
            samplingEnabled = _samplingEnabled;
            uploadEnabled = _uploadEnabled;
            lastUploadedAtUtc = _lastUploadedAtUtc;
            uploadError = _uploadError;
            samplerError = _samplerError;
        }

        var last = recent.LastOrDefault();
        var hostCpu = Average(recent.Select(sample => sample.HostCpuPercent));
        var memory = Average(
            recent.Select(sample => sample.HostMemoryUsedPercent));
        var frequency = Average(
            recent.Select(sample => sample.HostCpuFrequencyMhz));
        var frequencyRatio = Average(
            recent.Select(sample => sample.HostCpuFrequencyRatio));
        var blueStacksCpu = Average(
            recent.Select(sample => sample.BlueStacksCpuPercent));
        var blueStacksCoreCpu = Average(
            recent.Select(sample => sample.BlueStacksCpuCorePercent));
        var hostGpu = Average(
            recent.Select(sample => sample.HostGpuPercent));
        var blueStacksGpu = Average(
            recent.Select(sample => sample.BlueStacksGpuPercent));
        var gpuSampleDuration = Average(
            recent.Select(sample => sample.GpuSampleDurationMilliseconds));
        var controlSurfaceCpu = Average(
            recent.Select(sample => sample.ControlSurfaceCpuPercent));
        var processAttributionDuration = Average(
            recent.Select(sample =>
                sample.ProcessAttributionSampleDurationMilliseconds));
        var sampleDuration = Average(
            recent.Select(sample =>
                (double?)sample.SampleDurationMilliseconds));

        var (state, label) = EvaluateHealth(
            samplingEnabled,
            last,
            hostCpu,
            memory,
            frequencyRatio,
            samplerError);
        return new HostPerformanceSnapshot
        {
            HostName = Environment.MachineName,
            State = state,
            StateLabel = label,
            SamplingEnabled = samplingEnabled,
            SampledAtUtc = last?.TimestampUtc,
            HostCpuPercent = hostCpu,
            HostMemoryUsedPercent = memory,
            HostAvailableMemoryBytes = last?.HostAvailableMemoryBytes,
            HostCpuFrequencyMhz = frequency,
            HostCpuFrequencyRatio = frequencyRatio,
            BlueStacksProcessCount = last?.BlueStacksProcessCount ?? 0,
            BlueStacksCpuPercent = blueStacksCpu,
            BlueStacksCpuCorePercent = blueStacksCoreCpu,
            BlueStacksWorkingSetBytes = last?.BlueStacksWorkingSetBytes,
            BlueStacksThreadCount = last is { BlueStacksProcessCount: > 0 }
                ? last.BlueStacksThreadCount
                : null,
            BlueStacksHandleCount = last is { BlueStacksProcessCount: > 0 }
                ? last.BlueStacksHandleCount
                : null,
            BlueStacksListener = last?.BlueStacksListener,
            BlueStacksListenerError = last?.BlueStacksListenerError,
            BlueStacksIoReadBytesPerSecond = Average(
                recent.Select(sample =>
                    sample.BlueStacksIoReadBytesPerSecond)),
            BlueStacksIoWriteBytesPerSecond = Average(
                recent.Select(sample =>
                    sample.BlueStacksIoWriteBytesPerSecond)),
            GpuCountersAvailable = last?.GpuCountersAvailable ?? false,
            HostGpuPercent = hostGpu,
            HostGpuDedicatedMemoryBytes =
                last?.HostGpuDedicatedMemoryBytes,
            HostGpuSharedMemoryBytes = last?.HostGpuSharedMemoryBytes,
            BlueStacksGpuPercent = blueStacksGpu,
            BlueStacksGpuDedicatedMemoryBytes =
                last?.BlueStacksGpuDedicatedMemoryBytes,
            BlueStacksGpuSharedMemoryBytes =
                last?.BlueStacksGpuSharedMemoryBytes,
            GpuCompetitors = BuildGpuCompetitors(recent),
            GpuSampleDurationMilliseconds = gpuSampleDuration,
            GpuError = last?.GpuError,
            ControlSurfaceCpuPercent = controlSurfaceCpu,
            OtherWindowsCpuPercent = OtherWindowsCpuPercent(
                hostCpu,
                blueStacksCpu,
                controlSurfaceCpu,
                last?.BlueStacksProcessCount ?? 0),
            ProcessAttributionState = last?.ProcessAttributionState
                ?? HostProcessAttributionState.Inactive,
            ProcessAttributionProcessCount = recent
                .Where(sample =>
                    sample.ProcessAttributionSampleDurationMilliseconds is not null)
                .Select(sample => sample.ProcessAttributionProcessCount)
                .DefaultIfEmpty(0)
                .Max(),
            ProcessAttribution = BuildProcessAttribution(recent),
            ProcessAttributionSampleDurationMilliseconds =
                processAttributionDuration,
            SampleDurationMilliseconds = sampleDuration,
            PendingAggregateCount = _spool.PendingCount,
            DroppedAggregateCount = _spool.DroppedCount,
            RejectedAggregateCount = _spool.RejectedCount,
            LastRejectedAggregateReason = _spool.LastRejectionReason,
            UploadEnabled = uploadEnabled,
            LastUploadedAtUtc = lastUploadedAtUtc,
            UploadError = uploadError,
            StorageError = _spool.StorageError,
            SamplerError = samplerError,
        };
    }

    private static double? OtherWindowsCpuPercent(
        double? hostCpuPercent,
        double? blueStacksCpuPercent,
        double? controlSurfaceCpuPercent,
        int blueStacksProcessCount)
    {
        if (hostCpuPercent is null || controlSurfaceCpuPercent is null)
        {
            return null;
        }
        if (blueStacksProcessCount > 0 && blueStacksCpuPercent is null)
        {
            return null;
        }
        return Math.Clamp(
            hostCpuPercent.Value
                - (blueStacksCpuPercent ?? 0.0)
                - controlSurfaceCpuPercent.Value,
            0.0,
            100.0);
    }

    private static (HostPerformanceHealthState State, string Label)
        EvaluateHealth(
            bool samplingEnabled,
            HostPerformanceSample? last,
            double? hostCpu,
            double? memory,
            double? frequencyRatio,
            string? samplerError)
    {
        if (!samplingEnabled)
        {
            return (HostPerformanceHealthState.Paused, "Sampling paused");
        }
        if (last is null)
        {
            return samplerError is null
                ? (HostPerformanceHealthState.Starting, "Starting")
                : (HostPerformanceHealthState.Attention, "Sampler error");
        }
        if (DateTimeOffset.UtcNow - last.TimestampUtc > TimeSpan.FromSeconds(3))
        {
            return (HostPerformanceHealthState.Stale, "Telemetry stale");
        }
        if (hostCpu >= 95.0 || memory >= 95.0)
        {
            return (HostPerformanceHealthState.Critical, "Host saturated");
        }
        if (hostCpu >= 85.0
            || memory >= 90.0
            || (hostCpu >= 70.0 && frequencyRatio < 0.5)
            || samplerError is not null)
        {
            return (HostPerformanceHealthState.Attention, "Host load high");
        }
        if (last.BlueStacksProcessCount == 0)
        {
            return (
                HostPerformanceHealthState.BlueStacksNotDetected,
                "BlueStacks not detected");
        }
        return (HostPerformanceHealthState.Healthy, "Healthy");
    }

    private static void AddAverageAndMaximum(
        IDictionary<string, double> metrics,
        string prefix,
        IEnumerable<double?> source)
    {
        var values = FiniteValues(source);
        if (values.Length == 0)
        {
            return;
        }
        metrics[prefix + "_avg"] = values.Average();
        metrics[prefix + "_max"] = values.Max();
    }

    private static void AddAverageAndMinimum(
        IDictionary<string, double> metrics,
        string prefix,
        IEnumerable<double?> source)
    {
        var values = FiniteValues(source);
        if (values.Length == 0)
        {
            return;
        }
        metrics[prefix + "_avg"] = values.Average();
        metrics[prefix + "_min"] = values.Min();
    }

    private static void AddMinimumAndMaximum(
        IDictionary<string, double> metrics,
        string prefix,
        IEnumerable<double?> source)
    {
        var values = FiniteValues(source);
        if (values.Length == 0)
        {
            return;
        }
        metrics[prefix + "_min"] = values.Min();
        metrics[prefix + "_max"] = values.Max();
    }

    private static void AddMinimum(
        IDictionary<string, double> metrics,
        string name,
        IEnumerable<double?> source)
    {
        var values = FiniteValues(source);
        if (values.Length > 0)
        {
            metrics[name] = values.Min();
        }
    }

    private static double? Average(IEnumerable<double?> source)
    {
        var values = FiniteValues(source);
        return values.Length == 0 ? null : values.Average();
    }

    private static double[] FiniteValues(IEnumerable<double?> source) =>
        source
            .Where(value => value is not null && double.IsFinite(value.Value))
            .Select(value => value!.Value)
            .ToArray();

    private static string FormatTimestamp(DateTimeOffset value) =>
        value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'");

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _stopEvent.Set();
        _samplingStateChanged.Set();
        _uploadCancellation.Cancel();
        SignalUpload();
        _sampleThread?.Join(TimeSpan.FromSeconds(3));
        try
        {
            _uploadTask?.Wait(TimeSpan.FromSeconds(3));
        }
        catch (AggregateException aggregate) when (
            aggregate.InnerExceptions.All(
                exception => exception is OperationCanceledException))
        {
            // Expected uploader cancellation during shutdown.
        }
        _sampler.Dispose();
        _stopEvent.Dispose();
        _samplingStateChanged.Dispose();
        _uploadCancellation.Dispose();
        _uploadSignal.Dispose();
    }
}
