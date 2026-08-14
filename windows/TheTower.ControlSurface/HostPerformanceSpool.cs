using System.IO;
using System.Text;
using System.Text.Json;

namespace TheTower.ControlSurface;

internal sealed class HostPerformanceSpool
{
    public const int MaximumPendingAggregates = 24 * 60 * 6;
    public const int MaximumRejectedAggregates = 1024;

    private static readonly UTF8Encoding Utf8NoBom = new(false);
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly object _gate = new();
    private readonly string _spoolPath;
    private readonly string _rejectedPath;
    private readonly int _maximumRejectedAggregates;
    private readonly List<HostPerformanceAggregate> _pending = [];
    private readonly List<HostPerformanceRejectedAggregate> _rejected = [];
    private readonly HashSet<string> _rejectedAggregateIds =
        new(StringComparer.Ordinal);
    private long _acknowledgedSinceRewrite;
    private string? _lastRejectionReason;
    private string? _storageError;

    public HostPerformanceSpool()
        : this(Path.Combine(
            Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData),
            "TheTower"))
    {
    }

    internal HostPerformanceSpool(
        string directory,
        int maximumRejectedAggregates = MaximumRejectedAggregates)
    {
        if (maximumRejectedAggregates <= 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(maximumRejectedAggregates));
        }
        _maximumRejectedAggregates = maximumRejectedAggregates;
        _spoolPath = Path.Combine(directory, "host-performance-pending.jsonl");
        _rejectedPath = Path.Combine(
            directory,
            "host-performance-rejected.jsonl");
        try
        {
            Directory.CreateDirectory(directory);
            HostId = LoadOrCreateHostId(
                Path.Combine(directory, "host-performance-host-id"));
            LoadRejected();
            LoadPending();
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or JsonException)
        {
            HostId = Guid.NewGuid().ToString();
            _storageError =
                $"Local host-performance spool is unavailable: {exception.Message}";
        }
    }

    public string HostId { get; }

    public int PendingCount
    {
        get
        {
            lock (_gate)
            {
                return _pending.Count;
            }
        }
    }

    public long DroppedCount { get; private set; }

    public int RejectedCount
    {
        get
        {
            lock (_gate)
            {
                return _rejected.Count;
            }
        }
    }

    public string? LastRejectionReason
    {
        get
        {
            lock (_gate)
            {
                return _lastRejectionReason;
            }
        }
    }

    public long NextSequence
    {
        get
        {
            lock (_gate)
            {
                return _pending.Count == 0
                    ? 0
                    : _pending.Max(item => item.Sequence) + 1;
            }
        }
    }

    public string? StorageError
    {
        get
        {
            lock (_gate)
            {
                return _storageError;
            }
        }
    }

    public void Enqueue(HostPerformanceAggregate aggregate)
    {
        lock (_gate)
        {
            _pending.Add(aggregate);
            if (_pending.Count > MaximumPendingAggregates)
            {
                var excess = _pending.Count - MaximumPendingAggregates;
                _pending.RemoveRange(0, excess);
                DroppedCount += excess;
                RewritePending();
                return;
            }

            if (_storageError is not null)
            {
                RewritePending();
                return;
            }
            try
            {
                AppendAggregate(aggregate);
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException)
            {
                _storageError =
                    $"Unable to append host-performance telemetry: "
                    + exception.Message;
            }
        }
    }

    public IReadOnlyList<HostPerformanceAggregate> Peek(int maximum)
    {
        lock (_gate)
        {
            return _pending.Take(Math.Max(1, maximum)).ToArray();
        }
    }

    public void Acknowledge(IEnumerable<string> aggregateIds)
    {
        var acknowledged = aggregateIds.ToHashSet(StringComparer.Ordinal);
        if (acknowledged.Count == 0)
        {
            return;
        }
        lock (_gate)
        {
            var removed = _pending.RemoveAll(
                aggregate => acknowledged.Contains(aggregate.AggregateId));
            if (removed == 0)
            {
                return;
            }
            _acknowledgedSinceRewrite += removed;
            if (_pending.Count == 0 || _acknowledgedSinceRewrite >= 500)
            {
                RewritePending();
            }
        }
    }

    public bool Reject(string aggregateId, string reason)
    {
        lock (_gate)
        {
            var index = _pending.FindIndex(aggregate =>
                string.Equals(
                    aggregate.AggregateId,
                    aggregateId,
                    StringComparison.Ordinal));
            if (index < 0)
            {
                return false;
            }

            var aggregate = _pending[index];
            var normalizedReason = BoundedReason(reason);
            try
            {
                if (!_rejectedAggregateIds.Contains(aggregate.AggregateId))
                {
                    PersistRejectedAggregate(aggregate, normalizedReason);
                }

                var remaining = _pending
                    .Where((_, pendingIndex) => pendingIndex != index)
                    .ToArray();
                WritePendingSnapshot(remaining);
                _pending.RemoveAt(index);
                _acknowledgedSinceRewrite = 0;
                _lastRejectionReason = _rejected
                    .LastOrDefault(item => string.Equals(
                        item.AggregateId,
                        aggregate.AggregateId,
                        StringComparison.Ordinal))
                    ?.Reason ?? normalizedReason;
                _storageError = null;
                return true;
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException)
            {
                _storageError =
                    "Unable to quarantine rejected host-performance telemetry: "
                    + exception.Message;
                return false;
            }
        }
    }

    private void LoadRejected()
    {
        if (!File.Exists(_rejectedPath))
        {
            return;
        }
        var requiresRewrite = false;
        foreach (var line in File.ReadLines(_rejectedPath, Utf8NoBom))
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }
            try
            {
                var rejected =
                    JsonSerializer.Deserialize<HostPerformanceRejectedAggregate>(
                        line,
                        Json);
                if (rejected is null
                    || !Guid.TryParse(rejected.AggregateId, out _)
                    || !_rejectedAggregateIds.Add(rejected.AggregateId))
                {
                    requiresRewrite = true;
                    continue;
                }
                _rejected.Add(rejected);
                _lastRejectionReason = rejected.Reason;
            }
            catch (JsonException)
            {
                requiresRewrite = true;
            }
        }
        if (_rejected.Count > _maximumRejectedAggregates)
        {
            _rejected.RemoveRange(
                0,
                _rejected.Count - _maximumRejectedAggregates);
            RebuildRejectedAggregateIds();
            _lastRejectionReason = _rejected[^1].Reason;
            requiresRewrite = true;
        }
        if (requiresRewrite)
        {
            WriteRejectedSnapshot(_rejected);
        }
    }

    private void LoadPending()
    {
        if (!File.Exists(_spoolPath))
        {
            return;
        }
        var invalidLine = false;
        foreach (var line in File.ReadLines(_spoolPath, Utf8NoBom))
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }
            try
            {
                var aggregate = JsonSerializer.Deserialize<HostPerformanceAggregate>(
                    line,
                    Json);
                if (aggregate is null
                    || !Guid.TryParse(aggregate.AggregateId, out _)
                    || !Guid.TryParse(aggregate.SessionId, out _))
                {
                    invalidLine = true;
                    continue;
                }
                _pending.Add(aggregate);
            }
            catch (JsonException)
            {
                // A crash can leave one partial final line; retain valid records.
                invalidLine = true;
            }
        }
        if (_pending.Count > MaximumPendingAggregates)
        {
            var excess = _pending.Count - MaximumPendingAggregates;
            _pending.RemoveRange(0, excess);
            DroppedCount += excess;
            invalidLine = true;
        }
        if (invalidLine)
        {
            RewritePending();
        }
    }

    private static string LoadOrCreateHostId(string path)
    {
        try
        {
            var existing = File.ReadAllText(path, Utf8NoBom).Trim();
            if (Guid.TryParse(existing, out var parsed))
            {
                return parsed.ToString();
            }
        }
        catch (FileNotFoundException)
        {
            // Create the identity below.
        }

        var hostId = Guid.NewGuid().ToString();
        WriteAtomic(path, hostId + Environment.NewLine);
        return hostId;
    }

    private void AppendAggregate(HostPerformanceAggregate aggregate)
    {
        var bytes = Utf8NoBom.GetBytes(
            JsonSerializer.Serialize(aggregate, Json) + "\n");
        using var stream = new FileStream(
            _spoolPath,
            FileMode.Append,
            FileAccess.Write,
            FileShare.Read,
            bufferSize: 4096,
            FileOptions.None);
        stream.Write(bytes);
        stream.Flush(flushToDisk: true);
    }

    private void PersistRejectedAggregate(
        HostPerformanceAggregate aggregate,
        string reason)
    {
        var rejected = new HostPerformanceRejectedAggregate
        {
            AggregateId = aggregate.AggregateId,
            RejectedAtUtc = DateTimeOffset.UtcNow.ToString("O"),
            Reason = reason,
            Aggregate = aggregate,
        };
        if (_rejected.Count < _maximumRejectedAggregates)
        {
            var bytes = Utf8NoBom.GetBytes(
                JsonSerializer.Serialize(rejected, Json) + "\n");
            using var stream = new FileStream(
                _rejectedPath,
                FileMode.Append,
                FileAccess.Write,
                FileShare.Read,
                bufferSize: 4096,
                FileOptions.None);
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
            _rejected.Add(rejected);
            _rejectedAggregateIds.Add(rejected.AggregateId);
            return;
        }

        var retained = _rejected
            .Skip(1)
            .Append(rejected)
            .ToArray();
        WriteRejectedSnapshot(retained);
        _rejected.Clear();
        _rejected.AddRange(retained);
        RebuildRejectedAggregateIds();
    }

    private void RewritePending()
    {
        try
        {
            WritePendingSnapshot(_pending);
            _acknowledgedSinceRewrite = 0;
            _storageError = null;
        }
        catch (Exception exception) when (
            exception is IOException or UnauthorizedAccessException)
        {
            _storageError =
                $"Unable to checkpoint host-performance telemetry: "
                + exception.Message;
        }
    }

    private void WritePendingSnapshot(
        IEnumerable<HostPerformanceAggregate> aggregates)
    {
        var contents = string.Join(
            "\n",
            aggregates.Select(aggregate =>
                JsonSerializer.Serialize(aggregate, Json)));
        if (contents.Length > 0)
        {
            contents += "\n";
        }
        WriteAtomic(_spoolPath, contents);
    }

    private void WriteRejectedSnapshot(
        IEnumerable<HostPerformanceRejectedAggregate> rejected)
    {
        var contents = string.Join(
            "\n",
            rejected.Select(item => JsonSerializer.Serialize(item, Json)));
        if (contents.Length > 0)
        {
            contents += "\n";
        }
        WriteAtomic(_rejectedPath, contents);
    }

    private void RebuildRejectedAggregateIds()
    {
        _rejectedAggregateIds.Clear();
        foreach (var rejected in _rejected)
        {
            _rejectedAggregateIds.Add(rejected.AggregateId);
        }
    }

    private static string BoundedReason(string reason)
    {
        var normalized = string.IsNullOrWhiteSpace(reason)
            ? "Linux rejected the aggregate as invalid."
            : reason.Trim();
        return normalized.Length <= 1024
            ? normalized
            : normalized[..1024];
    }

    private static void WriteAtomic(string path, string contents)
    {
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            var bytes = Utf8NoBom.GetBytes(contents);
            using (var stream = new FileStream(
                temporary,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None,
                bufferSize: 4096,
                FileOptions.None))
            {
                stream.Write(bytes);
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, path, overwrite: true);
        }
        finally
        {
            try
            {
                File.Delete(temporary);
            }
            catch (IOException)
            {
                // A successful move already removed it; a failed cleanup can
                // be retried on the next write without affecting the spool.
            }
        }
    }
}
