using System.Text.Json;

namespace TheTower.ControlSurface;

internal sealed record HostPerformanceUploadPayload(
    IReadOnlyList<HostPerformanceAggregate> Aggregates,
    byte[] Json);

internal static class HostPerformanceUploadBatch
{
    public const int MaximumAggregateCount = 120;

    // Linux accepts at most 512 KiB for this endpoint. Retain a small margin
    // for compatible envelope changes while bounding the actual UTF-8 body.
    public const int MaximumPayloadBytes = 480 * 1024;

    public static HostPerformanceUploadPayload Prepare(
        IReadOnlyList<HostPerformanceAggregate> candidates,
        int maximumPayloadBytes = MaximumPayloadBytes)
    {
        ArgumentNullException.ThrowIfNull(candidates);
        if (candidates.Count == 0)
        {
            throw new ArgumentException(
                "At least one host-performance aggregate is required.",
                nameof(candidates));
        }
        if (maximumPayloadBytes <= 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(maximumPayloadBytes),
                "The host-performance payload limit must be positive.");
        }

        var lower = 1;
        var upper = Math.Min(candidates.Count, MaximumAggregateCount);
        HostPerformanceUploadPayload? selected = null;
        while (lower <= upper)
        {
            var count = lower + ((upper - lower) / 2);
            var aggregates = candidates.Take(count).ToArray();
            var json = Serialize(aggregates);
            if (json.Length <= maximumPayloadBytes)
            {
                selected = new HostPerformanceUploadPayload(aggregates, json);
                lower = count + 1;
            }
            else
            {
                upper = count - 1;
            }
        }

        return selected ?? throw new InvalidOperationException(
            "A single host-performance aggregate exceeds the upload limit; "
            + "telemetry remains in the local spool.");
    }

    internal static byte[] Serialize(
        IReadOnlyList<HostPerformanceAggregate> aggregates) =>
        JsonSerializer.SerializeToUtf8Bytes(
            new HostPerformanceBatch
            {
                Aggregates = aggregates.ToList(),
            });
}
