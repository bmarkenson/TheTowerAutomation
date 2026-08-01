using System.Text.Json;
using TheTower.TunnelHost.Core;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost;

internal sealed class JsonTunnelConfigurationStore : ITunnelConfigurationStore
{
    private readonly object _writeGate = new();
    private readonly string _path;

    public JsonTunnelConfigurationStore(string? path = null)
    {
        _path = path ?? Path.Combine(
            Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData),
            "TheTower",
            "tunnel-host.json");
    }

    public TunnelHostConfiguration Load()
    {
        if (!File.Exists(_path))
        {
            return new TunnelHostConfiguration();
        }
        return JsonSerializer.Deserialize<TunnelHostConfiguration>(
            File.ReadAllText(_path),
            TunnelHostJson.Options)
            ?? new TunnelHostConfiguration();
    }

    public void Save(TunnelHostConfiguration configuration)
    {
        configuration = TunnelHostConfigurationValidator.Validate(configuration);
        lock (_writeGate)
        {
            var directory = Path.GetDirectoryName(_path)
                ?? throw new InvalidOperationException(
                    "Unable to resolve the tunnel-host settings directory.");
            Directory.CreateDirectory(directory);
            var temporary = Path.Combine(
                directory,
                $".{Path.GetFileName(_path)}.{Environment.ProcessId}."
                + $"{Guid.NewGuid():N}.tmp");
            try
            {
                File.WriteAllText(
                    temporary,
                    JsonSerializer.Serialize(
                        configuration,
                        TunnelHostJson.Options));
                File.Move(temporary, _path, overwrite: true);
            }
            finally
            {
                try
                {
                    File.Delete(temporary);
                }
                catch (IOException)
                {
                    // The atomic replacement already owns the authoritative result.
                }
            }
        }
    }
}

internal static class HostStartupLog
{
    public static void Write(Exception exception)
    {
        try
        {
            var directory = Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData),
                "TheTower");
            Directory.CreateDirectory(directory);
            var path = Path.Combine(directory, "tunnel-host-startup.log");
            var entry = $"[{DateTimeOffset.Now:O}] {exception}\n";
            File.AppendAllText(path, entry);
            var info = new FileInfo(path);
            if (info.Length > 256 * 1024)
            {
                File.WriteAllText(path, entry);
            }
        }
        catch
        {
            // A headless startup failure has no safer secondary reporting path.
        }
    }
}
