using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;

namespace TheTower.ControlSurface;

public sealed class SshTunnelManager : IDisposable
{
    private static readonly Regex DestinationPattern = new(
        "^[A-Za-z0-9][A-Za-z0-9_.@-]*$",
        RegexOptions.CultureInvariant);

    private readonly object _gate = new();
    private readonly StringBuilder _stderr = new();
    private Process? _process;
    private bool _stopping;

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

    public async Task StartAsync(
        string destination,
        int localPort,
        int remotePort,
        CancellationToken cancellationToken)
    {
        destination = destination.Trim();
        if (!DestinationPattern.IsMatch(destination))
        {
            throw new ArgumentException(
                "SSH destination must be a host, SSH alias, or user@host using only letters, numbers, '.', '_', and '-'.");
        }
        ValidatePort(localPort, nameof(localPort));
        ValidatePort(remotePort, nameof(remotePort));

        lock (_gate)
        {
            if (_process is { HasExited: false })
            {
                throw new InvalidOperationException("The SSH tunnel is already running.");
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
                     "-L", $"{localPort}:127.0.0.1:{remotePort}",
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
            throw new InvalidOperationException(BuildExitMessage(process.ExitCode));
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

    private void OnProcessExited(Process process)
    {
        bool expected;
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
            message = expected ? "SSH tunnel stopped." : BuildExitMessage(exitCode);
            if (ReferenceEquals(_process, process))
            {
                _process = null;
            }
        }
        Exited?.Invoke(this, new TunnelExitedEventArgs(expected, message));
    }

    private string BuildExitMessage(int exitCode)
    {
        string detail;
        lock (_gate)
        {
            detail = _stderr.ToString().Trim();
        }
        return string.IsNullOrEmpty(detail)
            ? $"SSH tunnel exited with code {exitCode}."
            : $"SSH tunnel exited with code {exitCode}: {detail}";
    }

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
}

public sealed record TunnelExitedEventArgs(bool Expected, string Message);
