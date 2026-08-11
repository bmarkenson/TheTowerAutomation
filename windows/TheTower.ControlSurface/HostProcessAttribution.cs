namespace TheTower.ControlSurface;

internal sealed class HostProcessAttributionGate
{
    internal const double HostCpuThresholdPercent = 70.0;
    internal const double HostMemoryThresholdPercent = 95.0;
    internal const ulong AvailableMemoryThresholdBytes = 1UL << 30;
    internal static readonly TimeSpan ActivationDelay = TimeSpan.FromSeconds(30);
    internal static readonly TimeSpan RecoveryDelay = TimeSpan.FromMinutes(2);

    private DateTimeOffset? _pressureStartedAtUtc;
    private DateTimeOffset? _recoveryStartedAtUtc;

    public HostProcessAttributionState State { get; private set; }

    public HostProcessAttributionState Update(
        DateTimeOffset sampledAtUtc,
        double? hostCpuPercent,
        double? hostMemoryUsedPercent,
        ulong? availableMemoryBytes)
    {
        var pressure = hostCpuPercent >= HostCpuThresholdPercent
            || hostMemoryUsedPercent >= HostMemoryThresholdPercent
            || availableMemoryBytes <= AvailableMemoryThresholdBytes;
        if (pressure)
        {
            _recoveryStartedAtUtc = null;
            if (State is HostProcessAttributionState.Active
                or HostProcessAttributionState.Recovering)
            {
                State = HostProcessAttributionState.Active;
                return State;
            }

            _pressureStartedAtUtc ??= sampledAtUtc;
            State = sampledAtUtc - _pressureStartedAtUtc.Value >= ActivationDelay
                ? HostProcessAttributionState.Active
                : HostProcessAttributionState.Arming;
            return State;
        }

        if (hostCpuPercent is null
            || hostMemoryUsedPercent is null
            || availableMemoryBytes is null)
        {
            return State;
        }

        _pressureStartedAtUtc = null;
        if (State is HostProcessAttributionState.Active
            or HostProcessAttributionState.Recovering)
        {
            _recoveryStartedAtUtc ??= sampledAtUtc;
            if (sampledAtUtc - _recoveryStartedAtUtc.Value < RecoveryDelay)
            {
                State = HostProcessAttributionState.Recovering;
                return State;
            }
        }

        _recoveryStartedAtUtc = null;
        State = HostProcessAttributionState.Inactive;
        return State;
    }

    public void Reset()
    {
        _pressureStartedAtUtc = null;
        _recoveryStartedAtUtc = null;
        State = HostProcessAttributionState.Inactive;
    }
}

internal static class HostProcessAttributionSelector
{
    internal const int MaximumSelectedProcesses = 8;
    private const int MaximumPerResource = MaximumSelectedProcesses / 2;

    public static IReadOnlyList<HostProcessObservation> Select(
        IEnumerable<HostProcessObservation> observations)
    {
        var candidates = observations
            .Where(observation => observation.ProcessId > 0)
            .ToArray();
        var selected = new List<HostProcessObservation>(
            MaximumSelectedProcesses);
        var identities = new HashSet<(int ProcessId, string ProcessName)>();

        AddDistinct(
            candidates
                .Where(observation => observation.CpuPercent > 0.0)
                .OrderByDescending(observation => observation.CpuPercent)
                .ThenByDescending(observation => observation.PrivateBytes)
                .Take(MaximumPerResource),
            selected,
            identities);
        AddDistinct(
            candidates
                .Where(observation => observation.WorkingSetBytes > 0)
                .OrderByDescending(observation => observation.WorkingSetBytes)
                .ThenByDescending(observation => observation.PrivateBytes)
                .Take(MaximumPerResource),
            selected,
            identities);
        return selected;
    }

    private static void AddDistinct(
        IEnumerable<HostProcessObservation> source,
        ICollection<HostProcessObservation> destination,
        ISet<(int ProcessId, string ProcessName)> identities)
    {
        foreach (var observation in source)
        {
            var identity = (observation.ProcessId, observation.ProcessName);
            if (identities.Add(identity))
            {
                destination.Add(observation);
            }
        }
    }
}
