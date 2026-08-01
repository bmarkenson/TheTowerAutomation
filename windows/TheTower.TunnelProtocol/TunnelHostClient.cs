using System.IO.Pipes;
using System.Security.Principal;

namespace TheTower.TunnelProtocol;

public sealed class TunnelHostClient : IAsyncDisposable
{
    private readonly UserScopedIpcIdentity _identity;
    private readonly SemaphoreSlim _requestGate = new(1, 1);
    private readonly string _clientInstanceId = Guid.NewGuid().ToString("N");
    private NamedPipeClientStream? _pipe;

    public TunnelHostClient(UserScopedIpcIdentity? identity = null)
    {
        _identity = identity ?? UserScopedIpcIdentity.ForCurrentUser();
    }

    public bool IsConnected => _pipe?.IsConnected == true;
    public UserScopedIpcIdentity Identity => _identity;

    public async Task ConnectAsync(
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        await _requestGate.WaitAsync(cancellationToken);
        try
        {
            if (_pipe?.IsConnected == true)
            {
                return;
            }
            ResetPipe();
            var pipe = new NamedPipeClientStream(
                ".",
                _identity.PipeName,
                PipeDirection.InOut,
                PipeOptions.Asynchronous,
                TokenImpersonationLevel.Identification);
            try
            {
                var timeoutMilliseconds = checked((int)Math.Clamp(
                    timeout.TotalMilliseconds,
                    1,
                    int.MaxValue));
                await pipe.ConnectAsync(timeoutMilliseconds, cancellationToken);
                _pipe = pipe;
            }
            catch
            {
                pipe.Dispose();
                throw;
            }
        }
        finally
        {
            _requestGate.Release();
        }
    }

    public Task<TunnelHostResponse> GetStatusAsync(
        CancellationToken cancellationToken) =>
        SendAsync(
            new TunnelHostRequest { Command = TunnelHostCommand.GetStatus },
            cancellationToken);

    public async Task<TunnelHostResponse> SendAsync(
        TunnelHostRequest request,
        CancellationToken cancellationToken)
    {
        await _requestGate.WaitAsync(cancellationToken);
        try
        {
            var pipe = _pipe;
            if (pipe?.IsConnected != true)
            {
                throw new IOException("The tunnel host named pipe is not connected.");
            }

            request = request with
            {
                ProtocolVersion = TunnelHostProtocol.CurrentVersion,
                ClientInstanceId = _clientInstanceId,
                RequestId = string.IsNullOrWhiteSpace(request.RequestId)
                    ? Guid.NewGuid().ToString("N")
                    : request.RequestId,
            };
            try
            {
                await ProtocolFraming.WriteAsync(pipe, request, cancellationToken);
                var response = await ProtocolFraming.ReadAsync<TunnelHostResponse>(
                    pipe,
                    cancellationToken)
                    ?? throw new EndOfStreamException(
                        "The tunnel host closed the pipe without a response.");
                if (!string.Equals(
                        response.RequestId,
                        request.RequestId,
                        StringComparison.Ordinal))
                {
                    throw new InvalidDataException(
                        "The tunnel host response did not match the request identifier.");
                }
                if (response.Compatibility is not null
                    && response.ErrorCode == "protocol_mismatch")
                {
                    throw new TunnelHostProtocolMismatchException(
                        response.Compatibility);
                }
                if (response.ProtocolVersion != TunnelHostProtocol.CurrentVersion)
                {
                    throw new InvalidDataException(
                        $"Tunnel host responded with unexpected protocol version {response.ProtocolVersion}.");
                }
                if (!response.Ok)
                {
                    throw new TunnelHostCommandException(
                        response.ErrorCode ?? "host_error",
                        response.ErrorMessage ?? "The tunnel host rejected the request.",
                        response.Snapshot);
                }
                return response;
            }
            catch (Exception exc) when (
                exc is IOException
                    or EndOfStreamException
                    or InvalidDataException
                    or OperationCanceledException)
            {
                ResetPipe();
                throw;
            }
        }
        finally
        {
            _requestGate.Release();
        }
    }

    public async Task DisconnectAsync()
    {
        await _requestGate.WaitAsync();
        try
        {
            ResetPipe();
        }
        finally
        {
            _requestGate.Release();
        }
    }

    private void ResetPipe()
    {
        _pipe?.Dispose();
        _pipe = null;
    }

    public async ValueTask DisposeAsync()
    {
        await DisconnectAsync();
        _requestGate.Dispose();
    }
}
