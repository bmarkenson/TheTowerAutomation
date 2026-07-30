namespace TheTower.ControlSurface;

public sealed class HostPerformanceTracker : IDisposable
{
    private const int SampleIntervalMilliseconds = 1000;
    private const int AggregateSampleCount = 10;
    private const int RawRingCapacity = 120;
    private const int UploadBatchSize = 120;
    private static readonly TimeSpan RunContextFreshness =
        TimeSpan.FromSeconds(15);

    private readonly object _stateGate = new();
    private readonly ControlSurfaceApi _api;
    private readonly HostPerformanceSpool _spool = new();
    private readonly WindowsHostPerformanceSampler _sampler = new();
    private readonly Queue<HostPerformanceSample> _rawSamples = new();
    private readonly ManualResetEvent _stopEvent = new(false);
    private readonly SemaphoreSlim _uploadSignal = new(0, 1);
    private readonly CancellationTokenSource _uploadCancellation = new();
    private readonly string _sessionId = Guid.NewGuid().ToString();
    private HostPerformanceContext _context = new(
        null,
        null,
        DateTimeOffset.MinValue);
    private Thread? _sampleThread;
    private Task? _uploadTask;
    private long _sequence;
    private bool _uploadEnabled;
    private DateTimeOffset? _lastUploadedAtUtc;
    private string? _uploadError;
    private string? _samplerError;
    private bool _started;
    private bool _disposed;

    public HostPerformanceTracker(ControlSurfaceApi api)
    {
        _api = api;
        _sequence = _spool.NextSequence;
    }

    public event EventHandler<HostPerformanceSnapshot>? SnapshotUpdated;

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

    public void UpdateServerContext(
        int? adbPort,
        string? runId,
        bool uploadEnabled)
    {
        lock (_stateGate)
        {
            _context = new HostPerformanceContext(
                adbPort,
                string.IsNullOrWhiteSpace(runId) ? null : runId.Trim(),
                DateTimeOffset.UtcNow);
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
        try
        {
            while (!_stopEvent.WaitOne(0))
            {
                nextSampleAt += TimeSpan.FromMilliseconds(
                    SampleIntervalMilliseconds);
                try
                {
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
                        && !SameCorrelation(
                            aggregateWindow[0].Context,
                            sample.Context))
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
                _stopEvent.WaitOne(delay);
            }
        }
        finally
        {
            if (aggregateWindow.Count > 0)
            {
                EnqueueAggregate(aggregateWindow);
            }
        }
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
        && string.Equals(left.RunId, right.RunId, StringComparison.Ordinal);

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
            Metrics = metrics,
        };
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

            var batch = _spool.Peek(UploadBatchSize);
            try
            {
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(
                    cancellationToken);
                timeout.CancelAfter(TimeSpan.FromSeconds(12));
                var response = await _api.PostHostPerformanceAsync(
                    new HostPerformanceBatch
                    {
                        Aggregates = batch.ToList(),
                    },
                    timeout.Token);
                if (response.Received != batch.Count)
                {
                    throw new InvalidOperationException(
                        "The Linux service acknowledged an unexpected "
                        + "host-performance batch size.");
                }
                _spool.Acknowledge(batch.Select(item => item.AggregateId));
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
        bool uploadEnabled;
        DateTimeOffset? lastUploadedAtUtc;
        string? uploadError;
        string? samplerError;
        lock (_stateGate)
        {
            recent = _rawSamples.TakeLast(AggregateSampleCount).ToArray();
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
        var sampleDuration = Average(
            recent.Select(sample =>
                (double?)sample.SampleDurationMilliseconds));

        var (state, label) = EvaluateHealth(
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
            BlueStacksIoReadBytesPerSecond = Average(
                recent.Select(sample =>
                    sample.BlueStacksIoReadBytesPerSecond)),
            BlueStacksIoWriteBytesPerSecond = Average(
                recent.Select(sample =>
                    sample.BlueStacksIoWriteBytesPerSecond)),
            SampleDurationMilliseconds = sampleDuration,
            PendingAggregateCount = _spool.PendingCount,
            DroppedAggregateCount = _spool.DroppedCount,
            UploadEnabled = uploadEnabled,
            LastUploadedAtUtc = lastUploadedAtUtc,
            UploadError = uploadError,
            StorageError = _spool.StorageError,
            SamplerError = samplerError,
        };
    }

    private static (HostPerformanceHealthState State, string Label)
        EvaluateHealth(
            HostPerformanceSample? last,
            double? hostCpu,
            double? memory,
            double? frequencyRatio,
            string? samplerError)
    {
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
        _uploadCancellation.Dispose();
        _uploadSignal.Dispose();
    }
}
