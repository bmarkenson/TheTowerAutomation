using System.Diagnostics;
using System.Net;
using System.Net.NetworkInformation;
using System.Text;
using System.Text.RegularExpressions;

namespace TheTower.ControlSurface;

public sealed class SshTunnelManager : IDisposable
{
    private const string ControlSurfaceService = "thetower-control-surface.service";
    private static readonly Regex DestinationPattern = new(
        "^[A-Za-z0-9][A-Za-z0-9_.@-]*$",
        RegexOptions.CultureInvariant);

    private readonly object _gate = new();
    private readonly StringBuilder _stderr = new();
    private readonly string _displayName;
    private Process? _process;
    private bool _stopping;

    public SshTunnelManager(string displayName = "SSH tunnel")
    {
        _displayName = string.IsNullOrWhiteSpace(displayName)
            ? "SSH tunnel"
            : displayName.Trim();
    }

    public event EventHandler<TunnelExitedEventArgs>? Exited;

    public bool IsRunning
    {
        get
        {
            lock (_gate)
            {
                return _process is { HasExited: false };
            }
        }
    }

    public Task StartLocalForwardAsync(
        string destination,
        int localPort,
        int remotePort,
        CancellationToken cancellationToken) =>
        StartAsync(
            destination,
            "-L",
            $"{localPort}:127.0.0.1:{remotePort}",
            localPort,
            remotePort,
            cancellationToken);

    public Task StartReverseForwardAsync(
        string destination,
        int linuxPort,
        int windowsPort,
        CancellationToken cancellationToken) =>
        StartAsync(
            destination,
            "-R",
            $"127.0.0.1:{linuxPort}:127.0.0.1:{windowsPort}",
            linuxPort,
            windowsPort,
            cancellationToken);

    private async Task StartAsync(
        string destination,
        string forwardOption,
        string forwardSpecification,
        int firstPort,
        int secondPort,
        CancellationToken cancellationToken)
    {
        destination = destination.Trim();
        if (!IsValidDestination(destination))
        {
            throw new ArgumentException(
                "SSH destination must be a host, SSH alias, or user@host using only letters, numbers, '.', '_', and '-'.");
        }
        ValidatePort(firstPort, nameof(firstPort));
        ValidatePort(secondPort, nameof(secondPort));

        lock (_gate)
        {
            if (_process is { HasExited: false })
            {
                throw new InvalidOperationException($"The {_displayName} is already running.");
            }
            _process?.Dispose();
            _process = null;
            _stderr.Clear();
            _stopping = false;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = "ssh.exe",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        foreach (var argument in new[]
                 {
                     "-N",
                     "-o", "BatchMode=yes",
                     "-o", "StrictHostKeyChecking=yes",
                     "-o", "ConnectTimeout=10",
                     "-o", "ExitOnForwardFailure=yes",
                     "-o", "ServerAliveInterval=30",
                     "-o", "ServerAliveCountMax=3",
                     forwardOption, forwardSpecification,
                     destination,
                 })
        {
            startInfo.ArgumentList.Add(argument);
        }

        var process = new Process
        {
            StartInfo = startInfo,
            EnableRaisingEvents = true,
        };
        process.ErrorDataReceived += (_, args) =>
        {
            if (string.IsNullOrWhiteSpace(args.Data))
            {
                return;
            }
            lock (_gate)
            {
                if (_stderr.Length < 4000)
                {
                    _stderr.AppendLine(args.Data);
                }
            }
        };
        process.Exited += (_, _) => OnProcessExited(process);

        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException("Windows OpenSSH did not start.");
            }
            process.BeginErrorReadLine();
        }
        catch (Exception exc) when (exc is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            process.Dispose();
            throw new InvalidOperationException(
                "Unable to start ssh.exe. Install the Windows OpenSSH Client optional feature.",
                exc);
        }

        lock (_gate)
        {
            _process = process;
        }

        await Task.Delay(TimeSpan.FromMilliseconds(750), cancellationToken);
        if (process.HasExited)
        {
            var exit = BuildExitState(process.ExitCode);
            throw new SshTunnelStartException(
                exit.Message,
                exit.ForwardSetupFailed);
        }
    }

    public async Task StopAsync()
    {
        Process? process;
        lock (_gate)
        {
            process = _process;
            _stopping = true;
        }
        if (process is null)
        {
            return;
        }

        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
            }
        }
        finally
        {
            lock (_gate)
            {
                if (ReferenceEquals(_process, process))
                {
                    _process = null;
                }
                _stopping = false;
            }
            process.Dispose();
        }
    }

    public async Task RestartControlSurfaceServiceAsync(
        string destination,
        CancellationToken cancellationToken)
    {
        destination = destination.Trim();
        if (!IsValidDestination(destination))
        {
            throw new ArgumentException(
                "SSH destination must be a host, SSH alias, or user@host using only letters, numbers, '.', '_', and '-'.");
        }

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
                     "systemctl", "--user", "restart", ControlSurfaceService,
                 })
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
            exc is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            throw new InvalidOperationException(
                "Unable to start ssh.exe. Install the Windows OpenSSH Client optional feature.",
                exc);
        }

        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            await process.WaitForExitAsync(cancellationToken);
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
            return;
        }
        var detail = string.IsNullOrWhiteSpace(stderr) ? stdout : stderr;
        if (detail.Length > 2000)
        {
            detail = detail[..2000];
        }
        throw new InvalidOperationException(
            string.IsNullOrWhiteSpace(detail)
                ? $"Linux control-surface restart failed with SSH exit code {process.ExitCode}."
                : $"Linux control-surface restart failed with SSH exit code {process.ExitCode}: {detail}");
    }

    public static bool IsValidDestination(string destination) =>
        DestinationPattern.IsMatch(destination.Trim());

    public static bool IsWindowsLoopbackPortListening(int port)
    {
        ValidatePort(port, nameof(port));
        return IPGlobalProperties
            .GetIPGlobalProperties()
            .GetActiveTcpListeners()
            .Any(endpoint =>
                endpoint.Port == port
                && (endpoint.Address.Equals(IPAddress.Loopback)
                    || endpoint.Address.Equals(IPAddress.Any)));
    }

    private void OnProcessExited(Process process)
    {
        bool expected;
        bool forwardSetupFailed;
        string message;
        int exitCode;
        try
        {
            exitCode = process.ExitCode;
        }
        catch (InvalidOperationException)
        {
            exitCode = -1;
        }
        lock (_gate)
        {
            expected = _stopping;
            var exit = BuildExitState(exitCode);
            message = expected ? $"{_displayName} stopped." : exit.Message;
            forwardSetupFailed = exit.ForwardSetupFailed;
            if (ReferenceEquals(_process, process))
            {
                _process = null;
            }
        }
        Exited?.Invoke(
            this,
            new TunnelExitedEventArgs(expected, message, forwardSetupFailed));
    }

    private TunnelExitState BuildExitState(int exitCode)
    {
        string detail;
        lock (_gate)
        {
            detail = _stderr.ToString().Trim();
        }
        var message = string.IsNullOrEmpty(detail)
            ? $"{_displayName} exited with code {exitCode}."
            : $"{_displayName} exited with code {exitCode}: {detail}";
        return new TunnelExitState(
            message,
            IsForwardSetupFailure(detail));
    }

    private static bool IsForwardSetupFailure(string detail) =>
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

    private static void ValidatePort(int port, string parameterName)
    {
        if (port is < 1 or > 65535)
        {
            throw new ArgumentOutOfRangeException(parameterName, "Port must be between 1 and 65535.");
        }
    }

    public void Dispose()
    {
        Process? process;
        lock (_gate)
        {
            process = _process;
            _process = null;
            _stopping = true;
        }
        if (process is null)
        {
            return;
        }
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
            // The process exited between inspection and shutdown.
        }
        finally
        {
            process.Dispose();
        }
    }

    private sealed record TunnelExitState(
        string Message,
        bool ForwardSetupFailed);
}

public sealed record TunnelExitedEventArgs(
    bool Expected,
    string Message,
    bool ForwardSetupFailed);

public sealed class SshTunnelStartException : InvalidOperationException
{
    public SshTunnelStartException(
        string message,
        bool forwardSetupFailed)
        : base(message)
    {
        ForwardSetupFailed = forwardSetupFailed;
    }

    public bool ForwardSetupFailed { get; }
}
