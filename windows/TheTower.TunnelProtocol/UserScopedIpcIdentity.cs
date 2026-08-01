using System.IO.Pipes;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

namespace TheTower.TunnelProtocol;

public sealed record UserScopedIpcIdentity(
    string UserIdentity,
    string IdentityHash,
    string PipeName,
    string MutexName)
{
    public static UserScopedIpcIdentity ForCurrentUser()
    {
        string? identity = null;
        if (OperatingSystem.IsWindows())
        {
            identity = WindowsIdentity.GetCurrent().User?.Value;
        }
        identity ??= $"{Environment.UserDomainName}\\{Environment.UserName}";
        return FromIdentity(identity);
    }

    public static UserScopedIpcIdentity FromIdentity(string identity)
    {
        if (string.IsNullOrWhiteSpace(identity))
        {
            throw new ArgumentException("A non-empty user identity is required.", nameof(identity));
        }
        var normalized = identity.Trim().ToUpperInvariant();
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(normalized));
        var hash = Convert.ToHexString(digest)[..24];
        return new UserScopedIpcIdentity(
            normalized,
            hash,
            $"TheTower.TunnelHost.{hash}",
            $@"Global\TheTower.TunnelHost.{hash}");
    }
}

public static class TunnelHostPipeSecurity
{
    public const PipeOptions ServerOptions =
        PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly;
}
