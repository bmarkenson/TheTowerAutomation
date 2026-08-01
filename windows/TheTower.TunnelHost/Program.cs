using System.Diagnostics;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost;

internal static class Program
{
    // This reference intentionally remains rooted for the full process lifetime.
    // Windows closes the final Job Object handle when the host exits or crashes.
    private static WindowsKillOnCloseJob? _processJob;

    public static async Task<int> Main()
    {
        if (!OperatingSystem.IsWindows())
        {
            return 2;
        }

        var identity = UserScopedIpcIdentity.ForCurrentUser();
        using var instanceMutex = new Mutex(
            initiallyOwned: false,
            identity.MutexName);
        var ownsMutex = false;
        try
        {
            try
            {
                ownsMutex = instanceMutex.WaitOne(TimeSpan.Zero);
            }
            catch (AbandonedMutexException)
            {
                ownsMutex = true;
            }
            if (!ownsMutex)
            {
                return 0;
            }

            _processJob = WindowsKillOnCloseJob.Create();
            _processJob.AssignCurrentProcess();

            var processFactory = new OpenSshTunnelProcessFactory();
            await using var apiTunnel = new TunnelSupervisor(
                TunnelKind.Api,
                processFactory);
            await using var adbTunnel = new TunnelSupervisor(
                TunnelKind.Adb,
                processFactory);
            var configurationStore = new JsonTunnelConfigurationStore();
            var serviceController = new OpenSshLinuxApiServiceController();
            using var currentProcess = Process.GetCurrentProcess();
            var processStartedAt = new DateTimeOffset(
                currentProcess.StartTime.ToUniversalTime());
            await using var coordinator = new TunnelHostCoordinator(
                apiTunnel,
                adbTunnel,
                configurationStore,
                serviceController,
                hostStartedAt: processStartedAt);
            using var shutdown = new CancellationTokenSource();
            var server = new TunnelHostNamedPipeServer(
                identity,
                coordinator,
                shutdown);

            try
            {
                await server.RunAsync(shutdown.Token);
                return 0;
            }
            catch (OperationCanceledException) when (shutdown.IsCancellationRequested)
            {
                return 0;
            }
            catch (Exception exc)
            {
                HostStartupLog.Write(exc);
                return 1;
            }
            finally
            {
                await coordinator.StopAllAsync(CancellationToken.None);
                GC.KeepAlive(_processJob);
            }
        }
        catch (Exception exc)
        {
            HostStartupLog.Write(exc);
            return 1;
        }
        finally
        {
            if (ownsMutex)
            {
                instanceMutex.ReleaseMutex();
            }
        }
    }
}
