using System.Buffers.Binary;
using System.Text.Json;

namespace TheTower.TunnelProtocol;

public static class ProtocolFraming
{
    public static async Task WriteAsync<T>(
        Stream stream,
        T message,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(stream);
        var payload = JsonSerializer.SerializeToUtf8Bytes(
            message,
            TunnelHostJson.Options);
        if (payload.Length is <= 0 or > TunnelHostProtocol.MaximumFrameBytes)
        {
            throw new InvalidDataException(
                $"Protocol frame length {payload.Length} is outside the allowed range.");
        }

        var header = new byte[sizeof(int)];
        BinaryPrimitives.WriteInt32LittleEndian(header, payload.Length);
        await stream.WriteAsync(header, cancellationToken);
        await stream.WriteAsync(payload, cancellationToken);
        await stream.FlushAsync(cancellationToken);
    }

    public static async Task<T?> ReadAsync<T>(
        Stream stream,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(stream);
        var header = new byte[sizeof(int)];
        var headerBytes = await ReadExactlyOrEofAsync(
            stream,
            header,
            cancellationToken);
        if (headerBytes == 0)
        {
            return default;
        }
        if (headerBytes != header.Length)
        {
            throw new EndOfStreamException("Protocol frame ended inside its length prefix.");
        }

        var length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length is <= 0 or > TunnelHostProtocol.MaximumFrameBytes)
        {
            throw new InvalidDataException(
                $"Protocol frame length {length} is outside the allowed range.");
        }

        var payload = new byte[length];
        var payloadBytes = await ReadExactlyOrEofAsync(
            stream,
            payload,
            cancellationToken);
        if (payloadBytes != length)
        {
            throw new EndOfStreamException(
                $"Protocol frame declared {length} bytes but ended after {payloadBytes}.");
        }

        try
        {
            return JsonSerializer.Deserialize<T>(payload, TunnelHostJson.Options)
                ?? throw new InvalidDataException("Protocol frame contained JSON null.");
        }
        catch (JsonException exc)
        {
            throw new InvalidDataException("Protocol frame contained invalid JSON.", exc);
        }
    }

    private static async Task<int> ReadExactlyOrEofAsync(
        Stream stream,
        byte[] buffer,
        CancellationToken cancellationToken)
    {
        var total = 0;
        while (total < buffer.Length)
        {
            var read = await stream.ReadAsync(
                buffer.AsMemory(total, buffer.Length - total),
                cancellationToken);
            if (read == 0)
            {
                break;
            }
            total += read;
        }
        return total;
    }
}
