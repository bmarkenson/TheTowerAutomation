using System.Text.Json;

namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class HostPerformanceUploadBatchTests
{
    [Fact]
    public void EnrichedAggregatesUseLargestPrefixWithinByteLimit()
    {
        var aggregates = Enumerable.Range(0, 120)
            .Select(CreateEnrichedAggregate)
            .ToArray();

        Assert.True(
            HostPerformanceUploadBatch.Serialize(aggregates).Length
                > HostPerformanceUploadBatch.MaximumPayloadBytes);

        var upload = HostPerformanceUploadBatch.Prepare(aggregates);

        Assert.InRange(upload.Aggregates.Count, 1, aggregates.Length - 1);
        Assert.True(
            upload.Json.Length
                <= HostPerformanceUploadBatch.MaximumPayloadBytes);
        Assert.True(
            HostPerformanceUploadBatch.Serialize(
                aggregates.Take(upload.Aggregates.Count + 1).ToArray()).Length
                > HostPerformanceUploadBatch.MaximumPayloadBytes);
        var decoded = JsonSerializer.Deserialize<HostPerformanceBatch>(
            upload.Json);
        Assert.NotNull(decoded);
        Assert.Equal(
            upload.Aggregates.Select(item => item.AggregateId),
            decoded.Aggregates.Select(item => item.AggregateId));
    }

    [Fact]
    public void OversizedSingleAggregateFailsWithoutDiscardingIt()
    {
        var exception = Assert.Throws<InvalidOperationException>(() =>
            HostPerformanceUploadBatch.Prepare(
                [CreateEnrichedAggregate(0)],
                maximumPayloadBytes: 64));

        Assert.Contains(
            "telemetry remains in the local spool",
            exception.Message,
            StringComparison.OrdinalIgnoreCase);
    }

    private static HostPerformanceAggregate CreateEnrichedAggregate(int index)
    {
        var processName = $"diagnostic-process-{index}-" + new string('x', 160);
        return new HostPerformanceAggregate
        {
            AggregateId = Guid.NewGuid().ToString(),
            SessionId = Guid.NewGuid().ToString(),
            Sequence = index,
            HostId = Guid.NewGuid().ToString(),
            HostName = "ALIEN",
            LogicalProcessorCount = 32,
            WindowStartUtc = "2026-08-12T23:00:00.0000000+00:00",
            WindowEndUtc = "2026-08-12T23:00:10.0000000+00:00",
            SampleCount = 10,
            AdbPort = 5555,
            RunId = Guid.NewGuid().ToString(),
            ContextObservedAtUtc = "2026-08-12T23:00:09.0000000+00:00",
            Metrics = Enumerable.Range(0, 32).ToDictionary(
                metric => $"diagnostic_metric_{metric}",
                metric => 1000.123456789 + metric),
            GpuCompetitors = Enumerable.Range(0, 5)
                .Select(process => new HostPerformanceGpuCompetitor
                {
                    ProcessId = 10_000 + process,
                    ProcessName = processName + $"-gpu-{process}",
                    SampleCount = 10,
                    GpuPercentAverage = 12.3456789 + process,
                    GpuPercentMaximum = 23.4567891 + process,
                    DedicatedMemoryBytesMaximum = 500_000_000 + process,
                    SharedMemoryBytesMaximum = 50_000_000 + process,
                })
                .ToList(),
            ProcessAttribution = Enumerable.Range(0, 8)
                .Select(process => new HostPerformanceProcessAttribution
                {
                    ProcessId = 20_000 + process,
                    ProcessName = processName + $"-cpu-{process}",
                    SampleCount = 10,
                    CpuPercentAverage = 12.3456789 + process,
                    CpuPercentMaximum = 23.4567891 + process,
                    WorkingSetBytesMaximum = 600_000_000 + process,
                    PrivateBytesMaximum = 700_000_000 + process,
                })
                .ToList(),
        };
    }
}
