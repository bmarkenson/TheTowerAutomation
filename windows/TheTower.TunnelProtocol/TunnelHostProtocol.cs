using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace TheTower.TunnelProtocol;

public static class TunnelHostProtocol
{
    public const int CurrentVersion = 1;
    public const int MinimumSupportedVersion = 1;
    public const int MaximumSupportedVersion = 1;
    public const int MaximumFrameBytes = 1024 * 1024;
    public const string ProductVersion = "1.0";

    public static bool IsSupportedVersion(int version) =>
        version is >= MinimumSupportedVersion and <= MaximumSupportedVersion;
}

public static class TunnelHostJson
{
    public static JsonSerializerOptions Options { get; } = CreateOptions();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = false,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };
        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
        return options;
    }
}

public enum TunnelHostCommand
{
    GetStatus,
    Configure,
    StartTunnel,
    StopTunnel,
    RestartTunnel,
    QueryLinuxApiService,
    ChangeLinuxApiService,
    ShutdownHost,
}

public enum TunnelKind
{
    Api,
    Adb,
}

public enum TunnelObservedState
{
    Stopped,
    Starting,
    Running,
    Stopping,
    RetryWaiting,
    Conflict,
    Faulted,
}

public enum LinuxApiServiceAction
{
    Start,
    Stop,
    Restart,
}

public enum SshFailureKind
{
    None,
    ForwardConflict,
    Authentication,
    HostKey,
    Connection,
    StartFailure,
    UnexpectedExit,
    Cancelled,
}

public sealed record TunnelHostRequest
{
    public int ProtocolVersion { get; init; } = TunnelHostProtocol.CurrentVersion;
    public string RequestId { get; init; } = Guid.NewGuid().ToString("N");
    public string ClientInstanceId { get; init; } = "";
    public TunnelHostCommand Command { get; init; }
    public TunnelKind? Tunnel { get; init; }
    public TunnelHostConfiguration? Configuration { get; init; }
    public LinuxApiServiceAction? ServiceAction { get; init; }
    public bool ConfirmShutdown { get; init; }
}

public sealed record TunnelHostResponse
{
    public int ProtocolVersion { get; init; } = TunnelHostProtocol.CurrentVersion;
    public string RequestId { get; init; } = "";
    public bool Ok { get; init; }
    public string? ErrorCode { get; init; }
    public string? ErrorMessage { get; init; }
    public TunnelHostSnapshot? Snapshot { get; init; }
    public TunnelHostCompatibility? Compatibility { get; init; }
    public bool ShutdownRequested { get; init; }
}

public sealed record TunnelHostCompatibility
{
    public int MinimumProtocolVersion { get; init; }
    public int MaximumProtocolVersion { get; init; }
    public string HostVersion { get; init; } = "";
    public int HostProcessId { get; init; }
    public string HostInstanceId { get; init; } = "";
    public DateTimeOffset HostStartedAt { get; init; }
    public string HostExecutablePath { get; init; } = "";
}

public sealed record TunnelHostConfiguration
{
    public const int CurrentSchemaVersion = 1;

    public int SchemaVersion { get; init; } = CurrentSchemaVersion;
    public string SshDestination { get; init; } = "";
    public int LocalApiPort { get; init; } = 8787;
    public int RemoteApiPort { get; init; } = 8787;
    public int WindowsBlueStacksAdbPort { get; init; } = 5555;
    public int LinuxAdbPort { get; init; } = 5555;
}

public sealed record TunnelHostSnapshot
{
    public int ProtocolVersion { get; init; } = TunnelHostProtocol.CurrentVersion;
    public string HostVersion { get; init; } = TunnelHostProtocol.ProductVersion;
    public string HostInstanceId { get; init; } = "";
    public int HostProcessId { get; init; }
    public DateTimeOffset HostStartedAt { get; init; }
    public long StateRevision { get; init; }
    public int ConnectedGuiClients { get; init; }
    public TunnelHostConfiguration Configuration { get; init; } = new();
    public TunnelStateSnapshot ApiTunnel { get; init; } =
        TunnelStateSnapshot.Stopped(TunnelKind.Api);
    public TunnelStateSnapshot AdbTunnel { get; init; } =
        TunnelStateSnapshot.Stopped(TunnelKind.Adb);
    public LinuxApiServiceSnapshot LinuxApiService { get; init; } = new();
}

public sealed record TunnelStateSnapshot
{
    public TunnelKind Kind { get; init; }
    public bool Desired { get; init; }
    public TunnelObservedState ObservedState { get; init; }
    public int? ProcessId { get; init; }
    public TunnelEndpoint? ActiveEndpoint { get; init; }
    public int RetryAttempt { get; init; }
    public DateTimeOffset? RetryAt { get; init; }
    public SshDiagnostic? LastDiagnostic { get; init; }
    public DateTimeOffset StateChangedAt { get; init; }
    public long Revision { get; init; }

    public static TunnelStateSnapshot Stopped(TunnelKind kind) => new()
    {
        Kind = kind,
        ObservedState = TunnelObservedState.Stopped,
        StateChangedAt = DateTimeOffset.UtcNow,
    };
}

public sealed record TunnelEndpoint
{
    public string SshDestination { get; init; } = "";
    public string ForwardOption { get; init; } = "";
    public string ForwardSpecification { get; init; } = "";
    public int SourcePort { get; init; }
    public int DestinationPort { get; init; }

    public string Display => KindFromOption(ForwardOption) == TunnelKind.Api
        ? $"127.0.0.1:{SourcePort} -> {SshDestination}:127.0.0.1:{DestinationPort}"
        : $"{SshDestination}:127.0.0.1:{SourcePort} -> 127.0.0.1:{DestinationPort}";

    private static TunnelKind KindFromOption(string option) =>
        string.Equals(option, "-L", StringComparison.Ordinal)
            ? TunnelKind.Api
            : TunnelKind.Adb;
}

public sealed record SshDiagnostic
{
    public DateTimeOffset ObservedAt { get; init; }
    public SshFailureKind FailureKind { get; init; }
    public int? ExitCode { get; init; }
    public string Summary { get; init; } = "";
    public string RawDetail { get; init; } = "";
    public bool Expected { get; init; }
}

public sealed record LinuxApiServiceSnapshot
{
    public bool QuerySucceeded { get; init; }
    public bool CommandInFlight { get; init; }
    public string? LoadState { get; init; }
    public string? ActiveState { get; init; }
    public string? SubState { get; init; }
    public string? Result { get; init; }
    public int? ExecMainStatus { get; init; }
    public DateTimeOffset? ObservedAt { get; init; }
    public string? LastDiagnostic { get; init; }

    public bool IsActive => string.Equals(
        ActiveState,
        "active",
        StringComparison.Ordinal);
}

public static partial class TunnelHostConfigurationValidator
{
    [GeneratedRegex(
        "^[A-Za-z0-9][A-Za-z0-9_.@-]*$",
        RegexOptions.CultureInvariant)]
    private static partial Regex DestinationPattern();

    public static TunnelHostConfiguration Validate(
        TunnelHostConfiguration configuration,
        bool requireDestination = true)
    {
        ArgumentNullException.ThrowIfNull(configuration);
        if (configuration.SchemaVersion != TunnelHostConfiguration.CurrentSchemaVersion)
        {
            throw new ArgumentException(
                $"Tunnel configuration schema {configuration.SchemaVersion} is unsupported; expected {TunnelHostConfiguration.CurrentSchemaVersion}.");
        }

        var destination = configuration.SshDestination.Trim();
        if (requireDestination && !IsValidDestination(destination))
        {
            throw new ArgumentException(
                "SSH destination must be a host, SSH alias, or user@host using only letters, numbers, '.', '_', '@', and '-'.");
        }
        if (!string.IsNullOrEmpty(destination) && !IsValidDestination(destination))
        {
            throw new ArgumentException(
                "SSH destination must be a host, SSH alias, or user@host using only letters, numbers, '.', '_', '@', and '-'.");
        }

        ValidatePort(configuration.LocalApiPort, nameof(configuration.LocalApiPort));
        ValidatePort(configuration.RemoteApiPort, nameof(configuration.RemoteApiPort));
        ValidatePort(
            configuration.WindowsBlueStacksAdbPort,
            nameof(configuration.WindowsBlueStacksAdbPort));
        ValidatePort(configuration.LinuxAdbPort, nameof(configuration.LinuxAdbPort));
        return configuration with { SshDestination = destination };
    }

    public static bool IsValidDestination(string destination) =>
        DestinationPattern().IsMatch(destination.Trim());

    public static TunnelEndpoint EndpointFor(
        TunnelKind kind,
        TunnelHostConfiguration configuration)
    {
        configuration = Validate(configuration);
        return kind switch
        {
            TunnelKind.Api => new TunnelEndpoint
            {
                SshDestination = configuration.SshDestination,
                ForwardOption = "-L",
                ForwardSpecification =
                    $"{configuration.LocalApiPort}:127.0.0.1:{configuration.RemoteApiPort}",
                SourcePort = configuration.LocalApiPort,
                DestinationPort = configuration.RemoteApiPort,
            },
            TunnelKind.Adb => new TunnelEndpoint
            {
                SshDestination = configuration.SshDestination,
                ForwardOption = "-R",
                ForwardSpecification =
                    $"127.0.0.1:{configuration.LinuxAdbPort}:127.0.0.1:{configuration.WindowsBlueStacksAdbPort}",
                SourcePort = configuration.LinuxAdbPort,
                DestinationPort = configuration.WindowsBlueStacksAdbPort,
            },
            _ => throw new ArgumentOutOfRangeException(nameof(kind)),
        };
    }

    private static void ValidatePort(int port, string parameterName)
    {
        if (port is < 1 or > 65535)
        {
            throw new ArgumentOutOfRangeException(
                parameterName,
                "Port must be between 1 and 65535.");
        }
    }
}

public sealed class TunnelHostCommandException : InvalidOperationException
{
    public TunnelHostCommandException(
        string errorCode,
        string message,
        TunnelHostSnapshot? snapshot = null,
        Exception? innerException = null)
        : base(message, innerException)
    {
        ErrorCode = errorCode;
        Snapshot = snapshot;
    }

    public string ErrorCode { get; }
    public TunnelHostSnapshot? Snapshot { get; }
}

public sealed class TunnelHostProtocolMismatchException : InvalidOperationException
{
    public TunnelHostProtocolMismatchException(TunnelHostCompatibility compatibility)
        : base(
            $"Tunnel host protocol mismatch: this GUI requires version {TunnelHostProtocol.CurrentVersion}, "
            + $"but the running host supports {compatibility.MinimumProtocolVersion} through {compatibility.MaximumProtocolVersion}.")
    {
        Compatibility = compatibility;
    }

    public TunnelHostCompatibility Compatibility { get; }
}
