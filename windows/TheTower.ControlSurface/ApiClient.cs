using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;

namespace TheTower.ControlSurface;

public sealed class ControlSurfaceApi : IDisposable
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(120) };
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNameCaseInsensitive = true,
    };
    private string _baseUrl = "http://127.0.0.1:8787";
    private string _token = "";

    public void Configure(string baseUrl, string token)
    {
        if (!Uri.TryCreate(baseUrl.Trim().TrimEnd('/'), UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            throw new ArgumentException("API URL must be an absolute HTTP or HTTPS URL.");
        }
        _baseUrl = uri.ToString().TrimEnd('/');
        _token = token.Trim();
    }

    public Task<StatusResponse> GetStatusAsync(CancellationToken cancellationToken) =>
        GetAsync<StatusResponse>("/api/v1/status", cancellationToken);

    public Task<BattleListResponse> GetBattlesAsync(CancellationToken cancellationToken) =>
        GetAsync<BattleListResponse>("/api/v1/battles?limit=100", cancellationToken);

    public Task<ActivityResponse> GetActivityAsync(
        IEnumerable<string> levels,
        CancellationToken cancellationToken)
    {
        var selectedLevels = levels
            .Select(level => level.Trim().ToUpperInvariant())
            .Where(level => !string.IsNullOrEmpty(level))
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        var path = "/api/v1/activity?limit=250";
        if (selectedLevels.Length > 0)
        {
            path += "&levels=" + Uri.EscapeDataString(string.Join(",", selectedLevels));
        }
        return GetAsync<ActivityResponse>(path, cancellationToken);
    }

    public async Task<JsonDocument> GetBattleAsync(
        string battleId,
        CancellationToken cancellationToken)
    {
        using var request = CreateRequest(
            HttpMethod.Get,
            $"/api/v1/battles/{Uri.EscapeDataString(battleId)}");
        using var response = await _http.SendAsync(request, cancellationToken);
        await EnsureSuccess(response, cancellationToken);
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        return await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
    }

    public async Task<DiscardBattleResponse> DiscardBattleAsync(
        string battleId,
        CancellationToken cancellationToken)
    {
        using var request = CreateRequest(
            HttpMethod.Delete,
            $"/api/v1/battles/{Uri.EscapeDataString(battleId)}");
        using var response = await _http.SendAsync(request, cancellationToken);
        await EnsureSuccess(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<DiscardBattleResponse>(
            _json,
            cancellationToken)
            ?? throw new InvalidOperationException(
                "The Linux service returned an empty discard response.");
    }

    public Task<StatusResponse> PostControlAsync(
        object payload,
        CancellationToken cancellationToken) =>
        PostAsync<StatusResponse>("/api/v1/control", payload, cancellationToken);

    public Task<StatusResponse> PostProcessAsync(
        object payload,
        CancellationToken cancellationToken) =>
        PostAsync<StatusResponse>("/api/v1/process", payload, cancellationToken);

    private async Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
    {
        using var request = CreateRequest(HttpMethod.Get, path);
        using var response = await _http.SendAsync(request, cancellationToken);
        await EnsureSuccess(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<T>(_json, cancellationToken)
            ?? throw new InvalidOperationException("The Linux service returned an empty response.");
    }

    private async Task<T> PostAsync<T>(
        string path,
        object payload,
        CancellationToken cancellationToken)
    {
        using var request = CreateRequest(HttpMethod.Post, path);
        request.Content = new StringContent(
            JsonSerializer.Serialize(payload, _json),
            Encoding.UTF8,
            "application/json");
        using var response = await _http.SendAsync(request, cancellationToken);
        await EnsureSuccess(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<T>(_json, cancellationToken)
            ?? throw new InvalidOperationException("The Linux service returned an empty response.");
    }

    private HttpRequestMessage CreateRequest(HttpMethod method, string path)
    {
        var request = new HttpRequestMessage(method, _baseUrl + path);
        if (!string.IsNullOrEmpty(_token))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _token);
        }
        request.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
        return request;
    }

    private static async Task EnsureSuccess(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }
        var message = $"Linux service returned {(int)response.StatusCode} {response.ReasonPhrase}.";
        try
        {
            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
            using var document = await JsonDocument.ParseAsync(
                stream,
                cancellationToken: cancellationToken);
            if (document.RootElement.TryGetProperty("error", out var error))
            {
                message = error.GetString() ?? message;
            }
        }
        catch (JsonException)
        {
            // Keep the HTTP status when the error body is not JSON.
        }
        throw new InvalidOperationException(message);
    }

    public void Dispose() => _http.Dispose();
}

public static class SettingsStore
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    private static string SettingsPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TheTower",
        "control-surface.json");

    public static ClientSettings Load()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                return JsonSerializer.Deserialize<ClientSettings>(
                    File.ReadAllText(SettingsPath),
                    Json) ?? new ClientSettings();
            }
        }
        catch (Exception)
        {
            // Invalid local preferences must not prevent the app from starting.
        }
        return new ClientSettings();
    }

    public static void Save(ClientSettings settings)
    {
        var directory = Path.GetDirectoryName(SettingsPath)
            ?? throw new InvalidOperationException("Unable to resolve settings directory.");
        Directory.CreateDirectory(directory);
        File.WriteAllText(SettingsPath, JsonSerializer.Serialize(settings, Json));
    }
}
