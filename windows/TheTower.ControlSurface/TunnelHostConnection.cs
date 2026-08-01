using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using TheTower.TunnelProtocol;

namespace TheTower.ControlSurface;

internal sealed class TunnelHostConnection : IAsyncDisposable
{
    private readonly TunnelHostClient _client = new();
    private readonly SemaphoreSlim _connectGate = new(1, 1);

    public bool IsConnected => _client.IsConnected;

    public async Task<TunnelHostSnapshot> EnsureConnectedAsync(
        bool startIfMissing,
        CancellationToken cancellationToken)
    {
        await _connectGate.WaitAsync(cancellationToken);
        try
        {
            if (_client.IsConnected)
            {
                try
                {
                    return RequireSnapshot(
                        await _client.GetStatusAsync(cancellationToken));
                }
                catch (IOException)
                {
                    await _client.DisconnectAsync();
                }
            }

            try
            {
                await _client.ConnectAsync(
                    TimeSpan.FromMilliseconds(250),
                    cancellationToken);
                return RequireSnapshot(
                    await _client.GetStatusAsync(cancellationToken));
            }
            catch (Exception exc) when (
                !startIfMissing
                && exc is TimeoutException or IOException)
            {
                throw new InvalidOperationException(
                    "The per-user tunnel host is not running.",
                    exc);
            }
            catch (Exception exc) when (
                startIfMissing
                && exc is TimeoutException or IOException)
            {
                await _client.DisconnectAsync();
            }

            StartPackagedHost();
            Exception? lastFailure = null;
            for (var attempt = 0; attempt < 40; attempt++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (attempt > 0 && attempt % 10 == 0)
                {
                    // A prior launch can legitimately lose the singleton race
                    // to a host that is in the final moments of idle shutdown.
                    StartPackagedHost();
                }
                try
                {
                    await _client.ConnectAsync(
                        TimeSpan.FromMilliseconds(200),
                        cancellationToken);
                    return RequireSnapshot(
                        await _client.GetStatusAsync(cancellationToken));
                }
                catch (TunnelHostProtocolMismatchException)
                {
                    throw;
                }
                catch (Exception exc) when (
                    exc is TimeoutException or IOException)
                {
                    lastFailure = exc;
                    await _client.DisconnectAsync();
                    await Task.Delay(
                        TimeSpan.FromMilliseconds(100),
                        cancellationToken);
                }
            }
            throw new InvalidOperationException(
                "The packaged per-user tunnel host did not become available.",
                lastFailure);
        }
        finally
        {
            _connectGate.Release();
        }
    }

    public async Task<TunnelHostSnapshot> SendAsync(
        TunnelHostRequest request,
        CancellationToken cancellationToken)
    {
        await EnsureConnectedAsync(startIfMissing: true, cancellationToken);
        try
        {
            return RequireSnapshot(
                await _client.SendAsync(request, cancellationToken));
        }
        catch (TunnelHostCommandException exc) when (exc.Snapshot is not null)
        {
            throw;
        }
    }

    public async Task<TunnelHostSnapshot> RestartHostAsync(
        TunnelHostProtocolMismatchException? mismatch,
        CancellationToken cancellationToken)
    {
        EnsurePackagedHostExists();
        int? oldProcessId = null;
        if (mismatch is not null)
        {
            oldProcessId = mismatch.Compatibility.HostProcessId;
            await StopVerifiedIncompatibleHostAsync(
                mismatch.Compatibility,
                cancellationToken);
        }
        else
        {
            TunnelHostSnapshot? existingHost = null;
            try
            {
                existingHost = await EnsureConnectedAsync(
                    startIfMissing: false,
                    cancellationToken);
            }
            catch (InvalidOperationException exc) when (
                exc.InnerException is TimeoutException or IOException)
            {
                // No compatible host remains to shut down; start the package below.
            }
            if (existingHost is not null)
            {
                var response = await _client.SendAsync(
                    new TunnelHostRequest
                    {
                        Command = TunnelHostCommand.ShutdownHost,
                        ConfirmShutdown = true,
                    },
                    cancellationToken);
                oldProcessId = response.Snapshot?.HostProcessId
                    ?? existingHost.HostProcessId;
            }
        }

        await _client.DisconnectAsync();
        if (oldProcessId is not null)
        {
            await WaitForProcessExitAsync(oldProcessId.Value, cancellationToken);
        }
        return await EnsureConnectedAsync(
            startIfMissing: true,
            cancellationToken);
    }

    private static TunnelHostSnapshot RequireSnapshot(
        TunnelHostResponse response) =>
        response.Snapshot
        ?? throw new InvalidDataException(
            "The tunnel host returned no state snapshot.");

    private static string PackagedHostPath => Path.Combine(
        AppContext.BaseDirectory,
        "TheTower.TunnelHost.exe");

    private static void StartPackagedHost()
    {
        var path = EnsurePackagedHostExists();
        try
        {
            using var process = Process.Start(new ProcessStartInfo
            {
                FileName = path,
                WorkingDirectory = AppContext.BaseDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
            });
            if (process is null)
            {
                throw new InvalidOperationException(
                    "Windows did not start the packaged tunnel host.");
            }
        }
        catch (Exception exc) when (
            exc is Win32Exception or InvalidOperationException)
        {
            throw new InvalidOperationException(
                "Unable to start the packaged TheTower.TunnelHost.exe.",
                exc);
        }
    }

    private static string EnsurePackagedHostExists()
    {
        var path = PackagedHostPath;
        if (!File.Exists(path))
        {
            throw new FileNotFoundException(
                "TheTower.TunnelHost.exe is missing. Deploy the complete Control Surface publish directory, not only the GUI executable.",
                path);
        }
        return path;
    }

    private static async Task StopVerifiedIncompatibleHostAsync(
        TunnelHostCompatibility compatibility,
        CancellationToken cancellationToken)
    {
        var expectedPath = Path.GetFullPath(PackagedHostPath);
        var reportedPath = string.IsNullOrWhiteSpace(
            compatibility.HostExecutablePath)
                ? ""
                : Path.GetFullPath(compatibility.HostExecutablePath);
        if (!string.Equals(
                expectedPath,
                reportedPath,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "Refusing to replace the incompatible host because its executable path does not match the packaged TheTower.TunnelHost.exe.");
        }

        using var process = Process.GetProcessById(compatibility.HostProcessId);
        var actualPath = process.MainModule?.FileName;
        if (string.IsNullOrWhiteSpace(actualPath)
            || !string.Equals(
                expectedPath,
                Path.GetFullPath(actualPath),
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "Refusing to replace the incompatible host because its live process identity does not match the packaged companion.");
        }
        var actualStartedAt = new DateTimeOffset(process.StartTime.ToUniversalTime());
        if (Math.Abs(
                (actualStartedAt - compatibility.HostStartedAt).TotalSeconds) > 2)
        {
            throw new InvalidOperationException(
                "Refusing to replace the incompatible host because its process start time changed.");
        }

        process.Kill(entireProcessTree: false);
        await process.WaitForExitAsync(cancellationToken);
    }

    private static async Task WaitForProcessExitAsync(
        int processId,
        CancellationToken cancellationToken)
    {
        try
        {
            using var process = Process.GetProcessById(processId);
            await process.WaitForExitAsync(cancellationToken);
        }
        catch (ArgumentException)
        {
            // It exited before the wait began.
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _client.DisposeAsync();
        _connectGate.Dispose();
    }
}
