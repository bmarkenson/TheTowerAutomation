using System.IO;
using System.Text;
using System.Text.Json;

namespace TheTower.ControlSurface;

internal sealed class HostPerformanceSpool
{
    public const int MaximumPendingAggregates = 24 * 60 * 6;

    private static readonly UTF8Encoding Utf8NoBom = new(false);
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly object _gate = new();
    private readonly string _spoolPath;
    private readonly List<HostPerformanceAggregate> _pending = [];
    private long _acknowledgedSinceRewrite;
    private string? _storageError;

    public HostPerformanceSpool()
    {
        var directory = Path.Combine(
            Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData),
            "TheTower");
        _spoolPath = Path.Combine(directory, "host-performance-pending.jsonl");
        try
        {
            Directory.CreateDirectory(directory);
            HostId = LoadOrCreateHostId(
                Path.Combine(directory, "host-performance-host-id"));
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

    private void RewritePending()
    {
        try
        {
            var contents = string.Join(
                "\n",
                _pending.Select(aggregate =>
                    JsonSerializer.Serialize(aggregate, Json)));
            if (contents.Length > 0)
            {
                contents += "\n";
            }
            WriteAtomic(_spoolPath, contents);
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
