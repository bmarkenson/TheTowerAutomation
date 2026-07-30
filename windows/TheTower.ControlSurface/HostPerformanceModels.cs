using System.Text.Json.Serialization;

namespace TheTower.ControlSurface;

public sealed class HostPerformanceBatch
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("aggregates")]
    public List<HostPerformanceAggregate> Aggregates { get; set; } = [];
}

public sealed class HostPerformanceAggregate
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("aggregate_id")]
    public string AggregateId { get; set; } = "";

    [JsonPropertyName("session_id")]
    public string SessionId { get; set; } = "";

    [JsonPropertyName("sequence")]
    public long Sequence { get; set; }

    [JsonPropertyName("host_id")]
    public string HostId { get; set; } = "";

    [JsonPropertyName("host_name")]
    public string HostName { get; set; } = "";

    [JsonPropertyName("logical_processor_count")]
    public int LogicalProcessorCount { get; set; }

    [JsonPropertyName("window_start_utc")]
    public string WindowStartUtc { get; set; } = "";

    [JsonPropertyName("window_end_utc")]
    public string WindowEndUtc { get; set; } = "";

    [JsonPropertyName("sample_count")]
    public int SampleCount { get; set; }

    [JsonPropertyName("sample_interval_ms")]
    public int SampleIntervalMs { get; set; } = 1000;

    [JsonPropertyName("adb_port")]
    public int? AdbPort { get; set; }

    [JsonPropertyName("run_id")]
    public string? RunId { get; set; }

    [JsonPropertyName("context_observed_at_utc")]
    public string? ContextObservedAtUtc { get; set; }

    [JsonPropertyName("metrics")]
    public Dictionary<string, double> Metrics { get; set; } = [];
}

public sealed class HostPerformancePublishResponse
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; }

    [JsonPropertyName("received")]
    public int Received { get; set; }

    [JsonPropertyName("accepted")]
    public int Accepted { get; set; }

    [JsonPropertyName("duplicates")]
    public int Duplicates { get; set; }

    [JsonPropertyName("ingested_at_utc")]
    public string? IngestedAtUtc { get; set; }

    [JsonPropertyName("server_run_id")]
    public string? ServerRunId { get; set; }
}

internal sealed record HostPerformanceContext(
    int? AdbPort,
    string? RunId,
    DateTimeOffset ObservedAtUtc);

internal sealed record HostPerformanceSample
{
    public required DateTimeOffset TimestampUtc { get; init; }
    public required HostPerformanceContext Context { get; init; }
    public double? HostCpuPercent { get; init; }
    public double? HostMemoryUsedPercent { get; init; }
    public ulong? HostAvailableMemoryBytes { get; init; }
    public double? HostCpuFrequencyMhz { get; init; }
    public double? HostCpuFrequencyRatio { get; init; }
    public int BlueStacksProcessCount { get; init; }
    public double? BlueStacksCpuPercent { get; init; }
    public double? BlueStacksCpuCorePercent { get; init; }
    public long BlueStacksWorkingSetBytes { get; init; }
    public long BlueStacksPrivateBytes { get; init; }
    public double? BlueStacksIoReadBytesPerSecond { get; init; }
    public double? BlueStacksIoWriteBytesPerSecond { get; init; }
    public int BlueStacksThreadCount { get; init; }
    public int BlueStacksHandleCount { get; init; }
    public double? ControlSurfaceCpuPercent { get; init; }
    public double SampleDurationMilliseconds { get; init; }
}

public enum HostPerformanceHealthState
{
    Starting,
    Paused,
    Healthy,
    Attention,
    Critical,
    BlueStacksNotDetected,
    Stale,
}

public sealed record HostPerformanceSnapshot
{
    public required string HostName { get; init; }
    public required HostPerformanceHealthState State { get; init; }
    public required string StateLabel { get; init; }
    public required bool SamplingEnabled { get; init; }
    public DateTimeOffset? SampledAtUtc { get; init; }
    public double? HostCpuPercent { get; init; }
    public double? HostMemoryUsedPercent { get; init; }
    public ulong? HostAvailableMemoryBytes { get; init; }
    public double? HostCpuFrequencyMhz { get; init; }
    public double? HostCpuFrequencyRatio { get; init; }
    public int BlueStacksProcessCount { get; init; }
    public double? BlueStacksCpuPercent { get; init; }
    public double? BlueStacksCpuCorePercent { get; init; }
    public long? BlueStacksWorkingSetBytes { get; init; }
    public double? BlueStacksIoReadBytesPerSecond { get; init; }
    public double? BlueStacksIoWriteBytesPerSecond { get; init; }
    public double? SampleDurationMilliseconds { get; init; }
    public int PendingAggregateCount { get; init; }
    public long DroppedAggregateCount { get; init; }
    public bool UploadEnabled { get; init; }
    public DateTimeOffset? LastUploadedAtUtc { get; init; }
    public string? UploadError { get; init; }
    public string? StorageError { get; init; }
    public string? SamplerError { get; init; }
}
