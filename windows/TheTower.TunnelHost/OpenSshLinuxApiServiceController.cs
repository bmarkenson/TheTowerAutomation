using System.ComponentModel;
using System.Diagnostics;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost;

internal sealed class OpenSshLinuxApiServiceController : ILinuxApiServiceController
{
    private const string ControlSurfaceService =
        "thetower-control-surface.service";

    public async Task<LinuxApiServiceSnapshot> QueryAsync(
        TunnelHostConfiguration configuration,
        CancellationToken cancellationToken)
    {
        configuration = TunnelHostConfigurationValidator.Validate(configuration);
        var result = await RunAsync(
            configuration.SshDestination,
            "Linux control-surface status query",
            cancellationToken,
            "systemctl", "--user", "show", "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=Result",
            "--property=ExecMainStatus",
            ControlSurfaceService);
        var properties = result.Stdout
            .Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .Select(line => line.Trim().Split('=', 2))
            .Where(parts => parts.Length == 2)
            .ToDictionary(
                parts => parts[0],
                parts => parts[1],
                StringComparer.Ordinal);

        string Required(string name) => properties.TryGetValue(name, out var value)
            && !string.IsNullOrWhiteSpace(value)
                ? value
                : throw new InvalidOperationException(
                    $"Linux control-surface status omitted {name}.");
        var exitStatus = int.TryParse(
            Required("ExecMainStatus"),
            out var parsedExitStatus)
                ? parsedExitStatus
                : (int?)null;
        return new LinuxApiServiceSnapshot
        {
            QuerySucceeded = true,
            LoadState = Required("LoadState"),
            ActiveState = Required("ActiveState"),
            SubState = Required("SubState"),
            Result = Required("Result"),
            ExecMainStatus = exitStatus,
            ObservedAt = DateTimeOffset.UtcNow,
            LastDiagnostic = string.IsNullOrWhiteSpace(result.Stderr)
                ? null
                : result.Stderr,
        };
    }

    public async Task<LinuxApiServiceSnapshot> ChangeAsync(
        TunnelHostConfiguration configuration,
        LinuxApiServiceAction action,
        CancellationToken cancellationToken)
    {
        configuration = TunnelHostConfigurationValidator.Validate(configuration);
        var verb = action switch
        {
            LinuxApiServiceAction.Start => "start",
            LinuxApiServiceAction.Stop => "stop",
            LinuxApiServiceAction.Restart => "restart",
            _ => throw new ArgumentOutOfRangeException(nameof(action)),
        };
        await RunAsync(
            configuration.SshDestination,
            $"Linux control-surface {verb}",
            cancellationToken,
            "systemctl", "--user", verb, ControlSurfaceService);
        return await QueryAsync(configuration, cancellationToken);
    }

    private static async Task<SshCommandResult> RunAsync(
        string destination,
        string operation,
        CancellationToken cancellationToken,
        params string[] commandArguments)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = "ssh.exe",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in new[]
                 {
                     "-o", "BatchMode=yes",
                     "-o", "StrictHostKeyChecking=yes",
                     "-o", "ConnectTimeout=10",
                     destination,
                 }.Concat(commandArguments))
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process { StartInfo = startInfo };
        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException("Windows OpenSSH did not start.");
            }
        }
        catch (Exception exc) when (
            exc is Win32Exception or InvalidOperationException)
        {
            throw new InvalidOperationException(
                "Unable to start ssh.exe. Install the Windows OpenSSH Client optional feature.",
                exc);
        }

        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(35));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync(CancellationToken.None);
            }
            throw;
        }

        var stdout = (await stdoutTask).Trim();
        var stderr = (await stderrTask).Trim();
        if (process.ExitCode == 0)
        {
            return new SshCommandResult(stdout, Limit(stderr));
        }
        var detail = string.IsNullOrWhiteSpace(stderr) ? stdout : stderr;
        detail = Limit(detail);
        throw new InvalidOperationException(
            string.IsNullOrWhiteSpace(detail)
                ? $"{operation} failed with SSH exit code {process.ExitCode}."
                : $"{operation} failed with SSH exit code {process.ExitCode}: {detail}");
    }

    private static string Limit(string value) =>
        value.Length <= 4000 ? value : value[..4000];

    private sealed record SshCommandResult(string Stdout, string Stderr);
}
