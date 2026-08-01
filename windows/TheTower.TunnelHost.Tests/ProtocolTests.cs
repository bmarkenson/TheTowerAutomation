using System.Buffers.Binary;
using System.IO.Pipes;
using TheTower.TunnelProtocol;

namespace TheTower.TunnelHost.Tests;

public sealed class ProtocolTests
{
    [Fact]
    public async Task FramingRoundTripsVersionedRequest()
    {
        var request = new TunnelHostRequest
        {
            RequestId = "request-1",
            ClientInstanceId = "client-1",
            Command = TunnelHostCommand.StartTunnel,
            Tunnel = TunnelKind.Adb,
            Configuration = TestConfiguration(),
        };
        await using var stream = new MemoryStream();

        await ProtocolFraming.WriteAsync(stream, request, CancellationToken.None);
        stream.Position = 0;
        var decoded = await ProtocolFraming.ReadAsync<TunnelHostRequest>(
            stream,
            CancellationToken.None);

        Assert.NotNull(decoded);
        Assert.Equal(TunnelHostProtocol.CurrentVersion, decoded.ProtocolVersion);
        Assert.Equal(TunnelHostCommand.StartTunnel, decoded.Command);
        Assert.Equal(TunnelKind.Adb, decoded.Tunnel);
        Assert.Equal(5565, decoded.Configuration?.LinuxAdbPort);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(TunnelHostProtocol.MaximumFrameBytes + 1)]
    public async Task FramingRejectsInvalidLengths(int length)
    {
        var bytes = new byte[sizeof(int)];
        BinaryPrimitives.WriteInt32LittleEndian(bytes, length);
        await using var stream = new MemoryStream(bytes);

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            ProtocolFraming.ReadAsync<TunnelHostRequest>(
                stream,
                CancellationToken.None));
    }

    [Fact]
    public async Task FramingRejectsTruncatedPayload()
    {
        var bytes = new byte[sizeof(int) + 2];
        BinaryPrimitives.WriteInt32LittleEndian(bytes, 20);
        await using var stream = new MemoryStream(bytes);

        await Assert.ThrowsAsync<EndOfStreamException>(() =>
            ProtocolFraming.ReadAsync<TunnelHostRequest>(
                stream,
                CancellationToken.None));
    }

    [Fact]
    public void UserScopedIdentitySeparatesUsersAndPipeRequiresCurrentUser()
    {
        var first = UserScopedIpcIdentity.FromIdentity("S-1-5-21-1000");
        var same = UserScopedIpcIdentity.FromIdentity("s-1-5-21-1000");
        var other = UserScopedIpcIdentity.FromIdentity("S-1-5-21-2000");

        Assert.Equal(first.PipeName, same.PipeName);
        Assert.NotEqual(first.PipeName, other.PipeName);
        Assert.Contains(first.IdentityHash, first.MutexName);
        Assert.True(
            TunnelHostPipeSecurity.ServerOptions.HasFlag(
                PipeOptions.CurrentUserOnly));
    }

    [Fact]
    public void AdbEndpointRemainsLinuxLoopbackOnlyWithDistinctPorts()
    {
        var endpoint = TunnelHostConfigurationValidator.EndpointFor(
            TunnelKind.Adb,
            TestConfiguration());

        Assert.Equal("-R", endpoint.ForwardOption);
        Assert.Equal(
            "127.0.0.1:5565:127.0.0.1:5555",
            endpoint.ForwardSpecification);
        Assert.Equal(5565, endpoint.SourcePort);
        Assert.Equal(5555, endpoint.DestinationPort);
    }

    [Fact]
    public void ProtocolVersionGateRejectsOlderAndNewerPeers()
    {
        Assert.True(TunnelHostProtocol.IsSupportedVersion(
            TunnelHostProtocol.CurrentVersion));
        Assert.False(TunnelHostProtocol.IsSupportedVersion(
            TunnelHostProtocol.MinimumSupportedVersion - 1));
        Assert.False(TunnelHostProtocol.IsSupportedVersion(
            TunnelHostProtocol.MaximumSupportedVersion + 1));
        var mismatch = new TunnelHostProtocolMismatchException(
            new TunnelHostCompatibility
            {
                MinimumProtocolVersion = 2,
                MaximumProtocolVersion = 3,
            });
        Assert.Contains("protocol mismatch", mismatch.Message.ToLowerInvariant());
    }

    internal static TunnelHostConfiguration TestConfiguration() => new()
    {
        SshDestination = "tower@example-host",
        LocalApiPort = 8787,
        RemoteApiPort = 8787,
        WindowsBlueStacksAdbPort = 5555,
        LinuxAdbPort = 5565,
    };
}
