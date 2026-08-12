namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class HostProcessAttributionTests
{
    [Fact]
    public void SustainedPressureArmsAndRecoveryIsBounded()
    {
        var gate = new HostProcessAttributionGate();
        var started = DateTimeOffset.Parse("2026-08-10T20:00:00+00:00");
        var healthyMemory = 2UL << 30;

        Assert.Equal(
            HostProcessAttributionState.Arming,
            gate.Update(started, 75.0, 50.0, healthyMemory));
        Assert.Equal(
            HostProcessAttributionState.Arming,
            gate.Update(started.AddSeconds(29), 75.0, 50.0, healthyMemory));
        Assert.Equal(
            HostProcessAttributionState.Active,
            gate.Update(started.AddSeconds(30), 75.0, 50.0, healthyMemory));
        Assert.Equal(
            HostProcessAttributionState.Active,
            gate.Update(started.AddSeconds(31), null, 50.0, healthyMemory));
        Assert.Equal(
            HostProcessAttributionState.Recovering,
            gate.Update(started.AddSeconds(32), 40.0, 50.0, healthyMemory));
        Assert.Equal(
            HostProcessAttributionState.Recovering,
            gate.Update(started.AddSeconds(151), 40.0, 50.0, healthyMemory));
        Assert.Equal(
            HostProcessAttributionState.Inactive,
            gate.Update(started.AddSeconds(152), 40.0, 50.0, healthyMemory));
    }

    [Fact]
    public void LowMemoryTriggersAndRenewedPressureCancelsRecovery()
    {
        var gate = new HostProcessAttributionGate();
        var started = DateTimeOffset.Parse("2026-08-10T20:00:00+00:00");

        Assert.Equal(
            HostProcessAttributionState.Arming,
            gate.Update(started, 40.0, 50.0, 512UL << 20));
        Assert.Equal(
            HostProcessAttributionState.Active,
            gate.Update(started.AddSeconds(30), 40.0, 50.0, 512UL << 20));
        Assert.Equal(
            HostProcessAttributionState.Recovering,
            gate.Update(started.AddSeconds(31), 40.0, 50.0, 2UL << 30));
        Assert.Equal(
            HostProcessAttributionState.Active,
            gate.Update(started.AddSeconds(32), 80.0, 50.0, 2UL << 30));

        var memoryGate = new HostProcessAttributionGate();
        Assert.Equal(
            HostProcessAttributionState.Arming,
            memoryGate.Update(started, 40.0, 96.0, 2UL << 30));
        Assert.Equal(
            HostProcessAttributionState.Active,
            memoryGate.Update(
                started.AddSeconds(30),
                40.0,
                96.0,
                2UL << 30));
    }

    [Fact]
    public void SelectionRetainsBoundedCpuAndMemoryLeaders()
    {
        var observations = Enumerable.Range(1, 6)
            .Select(index => new HostProcessObservation(
                index,
                $"cpu-{index}",
                20.0 - index,
                index * 1024,
                index * 2048))
            .Concat(
                Enumerable.Range(7, 6)
                    .Select(index => new HostProcessObservation(
                        index,
                        $"memory-{index}",
                        null,
                        index * 1_000_000,
                        index * 2_000_000)))
            .ToArray();

        var selected = HostProcessAttributionSelector.Select(observations);
        var selectedIds = selected
            .Select(process => process.ProcessId)
            .ToHashSet();

        Assert.Equal(8, selected.Count);
        Assert.Equal(
            new[] { 1, 2, 3, 4, 9, 10, 11, 12 },
            selectedIds.OrderBy(processId => processId));
        Assert.Equal(selected.Count, selectedIds.Count);
    }
}
