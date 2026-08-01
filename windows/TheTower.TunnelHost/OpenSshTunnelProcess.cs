using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost;

internal sealed class OpenSshTunnelProcessFactory : ISshTunnelProcessFactory
{
    public Task<IManagedSshProcess> StartAsync(
        TunnelKind kind,
        TunnelEndpoint endpoint,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
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
                     endpoint.ForwardOption,
                     endpoint.ForwardSpecification,
                     endpoint.SshDestination,
                 })
        {
            startInfo.ArgumentList.Add(argument);
        }

        try
        {
            return Task.FromResult<IManagedSshProcess>(
                OpenSshTunnelProcess.Start(startInfo));
        }
        catch (Exception exc) when (
            exc is Win32Exception or InvalidOperationException)
        {
            throw new InvalidOperationException(
                "Unable to start ssh.exe. Install the Windows OpenSSH Client optional feature.",
                exc);
        }
    }
}

internal sealed class OpenSshTunnelProcess : IManagedSshProcess
{
    private readonly object _gate = new();
    private readonly Process _process;
    private readonly StringBuilder _stderr = new();
    private readonly TaskCompletionSource<ManagedSshProcessExit> _completion =
        new(TaskCreationOptions.RunContinuationsAsynchronously);
    private bool _expected;
    private bool _disposed;

    private OpenSshTunnelProcess(Process process)
    {
        _process = process;
        process.ErrorDataReceived += OnErrorData;
    }

    public int ProcessId => _process.Id;
    public Task<ManagedSshProcessExit> Completion => _completion.Task;

    public static OpenSshTunnelProcess Start(ProcessStartInfo startInfo)
    {
        var process = new Process
        {
            StartInfo = startInfo,
        };
        var managed = new OpenSshTunnelProcess(process);
        var started = false;
        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException("Windows OpenSSH did not start.");
            }
            started = true;
            process.BeginErrorReadLine();
            managed.BeginExitObservation();
            return managed;
        }
        catch
        {
            if (started)
            {
                try
                {
                    if (!process.HasExited)
                    {
                        process.Kill(entireProcessTree: true);
                        process.WaitForExit();
                    }
                }
                catch (InvalidOperationException)
                {
                    // The failed startup raced process exit.
                }
            }
            process.ErrorDataReceived -= managed.OnErrorData;
            process.Dispose();
            throw;
        }
    }

    public async Task StopAsync(CancellationToken cancellationToken)
    {
        lock (_gate)
        {
            _expected = true;
        }
        try
        {
            if (!_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
            // The process exited between inspection and the explicit stop.
        }
        await Completion.WaitAsync(cancellationToken);
    }

    private void OnErrorData(object sender, DataReceivedEventArgs args)
    {
        if (string.IsNullOrWhiteSpace(args.Data))
        {
            return;
        }
        lock (_gate)
        {
            var remaining = 4000 - _stderr.Length;
            if (remaining > 0)
            {
                var line = args.Data + Environment.NewLine;
                _stderr.Append(line.AsSpan(0, Math.Min(line.Length, remaining)));
            }
        }
    }

    private void BeginExitObservation() => _ = ObserveExitAsync();

    private async Task ObserveExitAsync()
    {
        string? observationFailure = null;
        try
        {
            await _process.WaitForExitAsync();
            // The synchronous overload drains pending asynchronous stderr events.
            _process.WaitForExit();
        }
        catch (Exception exc) when (exc is InvalidOperationException or Win32Exception)
        {
            observationFailure = exc.ToString();
        }

        bool expected;
        string detail;
        lock (_gate)
        {
            expected = _expected;
            detail = _stderr.ToString().Trim();
        }
        if (string.IsNullOrWhiteSpace(detail) && observationFailure is not null)
        {
            detail = observationFailure.Length <= 4000
                ? observationFailure
                : observationFailure[..4000];
        }
        int exitCode;
        try
        {
            exitCode = _process.ExitCode;
        }
        catch (InvalidOperationException)
        {
            exitCode = -1;
        }
        _completion.TrySetResult(new ManagedSshProcessExit(
            exitCode,
            detail,
            expected,
            DateTimeOffset.UtcNow));
    }

    public ValueTask DisposeAsync()
    {
        lock (_gate)
        {
            if (_disposed)
            {
                return ValueTask.CompletedTask;
            }
            _disposed = true;
        }
        _process.ErrorDataReceived -= OnErrorData;
        _process.Dispose();
        return ValueTask.CompletedTask;
    }
}
