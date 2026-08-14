using System.Text.Json;

namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class HostPerformanceSpoolTests
{
    [Fact]
    public void RejectedAggregateIsDurablySeparatedFromPendingTelemetry()
    {
        var directory = Path.Combine(
            Path.GetTempPath(),
            "TheTower-host-performance-tests",
            Guid.NewGuid().ToString("N"));
        try
        {
            var before = CreateAggregate(sequence: 11);
            var rejectedAggregate = CreateAggregate(sequence: 12);
            var after = CreateAggregate(sequence: 13);
            var spool = new HostPerformanceSpool(directory);
            spool.Enqueue(before);
            spool.Enqueue(rejectedAggregate);
            spool.Enqueue(after);

            Assert.True(spool.Reject(
                rejectedAggregate.AggregateId,
                "metrics field is invalid"));
            Assert.Equal(
                [before.AggregateId, after.AggregateId],
                spool.Peek(10).Select(item => item.AggregateId));
            Assert.Equal(1, spool.RejectedCount);
            Assert.Equal(
                "metrics field is invalid",
                spool.LastRejectionReason);

            var rejectedPath = Path.Combine(
                directory,
                "host-performance-rejected.jsonl");
            var rejectedLines = File.ReadAllLines(rejectedPath);
            var rejected = JsonSerializer.Deserialize<
                HostPerformanceRejectedAggregate>(rejectedLines.Single());
            Assert.NotNull(rejected);
            Assert.Equal(rejectedAggregate.AggregateId, rejected.AggregateId);
            Assert.Equal(
                rejectedAggregate.AggregateId,
                rejected.Aggregate.AggregateId);

            var reloaded = new HostPerformanceSpool(directory);
            Assert.Equal(
                [before.AggregateId, after.AggregateId],
                reloaded.Peek(10).Select(item => item.AggregateId));
            Assert.Equal(
                before.BlueStacksListener,
                reloaded.Peek(10)[0].BlueStacksListener);
            Assert.Equal(
                "2026-08-13T00:00:00.1234567+00:00",
                reloaded.Peek(10)[0].BlueStacksListener!.ProcessStartedAt);
            Assert.Equal(1, reloaded.RejectedCount);
            Assert.Equal(
                "metrics field is invalid",
                reloaded.LastRejectionReason);

            reloaded.Enqueue(rejectedAggregate);
            Assert.True(reloaded.Reject(
                rejectedAggregate.AggregateId,
                "duplicate retry"));
            Assert.Single(File.ReadAllLines(rejectedPath));
            Assert.Equal(
                [before.AggregateId, after.AggregateId],
                reloaded.Peek(10).Select(item => item.AggregateId));
            Assert.Equal(1, reloaded.RejectedCount);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public void RejectedDiagnosticSpoolRetainsNewestBoundedRecords()
    {
        var directory = Path.Combine(
            Path.GetTempPath(),
            "TheTower-host-performance-tests",
            Guid.NewGuid().ToString("N"));
        try
        {
            var aggregates = Enumerable.Range(0, 3)
                .Select(index => CreateAggregate(index))
                .ToArray();
            var spool = new HostPerformanceSpool(
                directory,
                maximumRejectedAggregates: 2);
            foreach (var aggregate in aggregates)
            {
                spool.Enqueue(aggregate);
                Assert.True(spool.Reject(
                    aggregate.AggregateId,
                    $"reason {aggregate.Sequence}"));
            }

            Assert.Equal(2, spool.RejectedCount);
            var rejected = File.ReadAllLines(Path.Combine(
                    directory,
                    "host-performance-rejected.jsonl"))
                .Select(line => JsonSerializer.Deserialize<
                    HostPerformanceRejectedAggregate>(line))
                .OfType<HostPerformanceRejectedAggregate>()
                .ToArray();
            Assert.Equal(
                aggregates.Skip(1).Select(item => item.AggregateId),
                rejected.Select(item => item.AggregateId));

            var reloaded = new HostPerformanceSpool(
                directory,
                maximumRejectedAggregates: 2);
            Assert.Equal(2, reloaded.RejectedCount);
            Assert.Equal("reason 2", reloaded.LastRejectionReason);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public void RejectionPreservationFailureLeavesAggregatePending()
    {
        var directory = Path.Combine(
            Path.GetTempPath(),
            "TheTower-host-performance-tests",
            Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(Path.Combine(
                directory,
                "host-performance-rejected.jsonl"));
            var aggregate = CreateAggregate(sequence: 1);
            var spool = new HostPerformanceSpool(directory);
            spool.Enqueue(aggregate);

            Assert.False(spool.Reject(
                aggregate.AggregateId,
                "server rejected aggregate"));
            Assert.Equal(
                [aggregate.AggregateId],
                spool.Peek(10).Select(item => item.AggregateId));
            Assert.Equal(0, spool.RejectedCount);
            Assert.Contains(
                "Unable to quarantine",
                spool.StorageError,
                StringComparison.Ordinal);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private static HostPerformanceAggregate CreateAggregate(long sequence) => new()
    {
        AggregateId = Guid.NewGuid().ToString(),
        SessionId = Guid.NewGuid().ToString(),
        Sequence = sequence,
        HostId = Guid.NewGuid().ToString(),
        HostName = "TEST-HOST",
        LogicalProcessorCount = 8,
        WindowStartUtc = "2026-08-13T00:00:00.0000000+00:00",
        WindowEndUtc = "2026-08-13T00:00:09.0000000+00:00",
        SampleCount = 10,
        SampleIntervalMs = 1000,
        Metrics = new Dictionary<string, double>
        {
            ["host_cpu_percent_avg"] = 25.0,
        },
        BlueStacksListener = new HostPerformanceBlueStacksListener
        {
            HostId = "TEST-HOST",
            AdbPort = 5555,
            ProcessId = 90,
            ProcessStartedAt = "2026-08-13T00:00:00.1234567+00:00",
            ExecutablePath = @"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
            InstanceName = "Nougat32",
        },
    };
}
