using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost.Core;

public sealed record ManagedSshProcessExit(
    int ExitCode,
    string RawStderr,
    bool Expected,
    DateTimeOffset ExitedAt);

public interface IManagedSshProcess : IAsyncDisposable
{
    int ProcessId { get; }
    Task<ManagedSshProcessExit> Completion { get; }
    Task StopAsync(CancellationToken cancellationToken);
}

public interface ISshTunnelProcessFactory
{
    Task<IManagedSshProcess> StartAsync(
        TunnelKind kind,
        TunnelEndpoint endpoint,
        CancellationToken cancellationToken);
}

public interface IAsyncDelay
{
    DateTimeOffset UtcNow { get; }
    Task Delay(TimeSpan delay, CancellationToken cancellationToken);
}

public sealed class SystemAsyncDelay : IAsyncDelay
{
    public DateTimeOffset UtcNow => DateTimeOffset.UtcNow;

    public Task Delay(TimeSpan delay, CancellationToken cancellationToken) =>
        Task.Delay(delay, cancellationToken);
}

public static class SshFailureClassifier
{
    public static SshFailureKind Classify(string detail, int? exitCode = null)
    {
        if (IsForwardSetupFailure(detail))
        {
            return SshFailureKind.ForwardConflict;
        }
        if (detail.Contains("Host key verification failed", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("REMOTE HOST IDENTIFICATION HAS CHANGED", StringComparison.OrdinalIgnoreCase))
        {
            return SshFailureKind.HostKey;
        }
        if (detail.Contains("Permission denied", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("no supported authentication methods", StringComparison.OrdinalIgnoreCase))
        {
            return SshFailureKind.Authentication;
        }
        if (detail.Contains("Connection refused", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("Connection timed out", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("Could not resolve hostname", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("No route to host", StringComparison.OrdinalIgnoreCase))
        {
            return SshFailureKind.Connection;
        }
        return exitCode is null
            ? SshFailureKind.StartFailure
            : SshFailureKind.UnexpectedExit;
    }

    public static bool IsForwardSetupFailure(string detail) =>
        detail.Contains(
            "remote port forwarding failed",
            StringComparison.OrdinalIgnoreCase)
        || detail.Contains(
            "cannot listen to port",
            StringComparison.OrdinalIgnoreCase)
        || detail.Contains(
            "address already in use",
            StringComparison.OrdinalIgnoreCase)
        || detail.Contains(
            "port forwarding is disabled",
            StringComparison.OrdinalIgnoreCase)
        || detail.Contains(
            "administratively prohibited",
            StringComparison.OrdinalIgnoreCase);
}
