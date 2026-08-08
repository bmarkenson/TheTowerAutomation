using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace TheTower.ControlSurface;

public partial class SetupCaptureWindow : Window
{
    private static readonly JsonSerializerOptions PrettyJson = new()
    {
        WriteIndented = true,
    };
    private static readonly Regex IdentifierPattern = new(
        "^[a-z][a-z0-9_]{2,47}$",
        RegexOptions.CultureInvariant);

    private readonly ControlSurfaceApi _api;
    private readonly DispatcherTimer _pollTimer = new()
    {
        Interval = TimeSpan.FromSeconds(1.5),
    };
    private SetupCaptureResponse? _response;
    private CapturedStrategyReview? _review;
    private string? _reviewInput;
    private bool _busy;
    private bool _applying;

    public SetupCaptureWindow(ControlSurfaceApi api)
    {
        InitializeComponent();
        _api = api;
        _pollTimer.Tick += async (_, _) => await PollAsync();
        Loaded += async (_, _) => await BeginAsync();
        Closed += (_, _) => _pollTimer.Stop();
    }

    private async Task BeginAsync()
    {
        await RunAsync(async cancellationToken =>
        {
            var current = await _api.GetSetupCaptureAsync(cancellationToken);
            var status = current.Capture?.Status ?? "";
            if (status == "ready" || IsInProgress(status) || IsTerminal(status))
            {
                ApplyResponse(current);
                return;
            }
            if (!current.Availability.Available)
            {
                ApplyResponse(current);
                throw new InvalidOperationException(
                    current.Availability.Reason
                    ?? "Capture current setup is unavailable.");
            }
            ApplyResponse(await _api.PostSetupCaptureAsync(
                new Dictionary<string, object> { ["operation"] = "request" },
                cancellationToken));
        });
    }

    private async Task PollAsync()
    {
        if (_busy)
        {
            return;
        }
        await RunAsync(async cancellationToken =>
        {
            ApplyResponse(await _api.GetSetupCaptureAsync(cancellationToken));
        }, showFailure: false);
    }

    private void ApplyResponse(SetupCaptureResponse response)
    {
        _response = response;
        var capture = response.Capture;
        var preview = capture?.Preview;
        var status = capture?.Status ?? "unavailable";
        StatusText.Text = string.IsNullOrWhiteSpace(capture?.Reason)
            ? Format(status)
            : $"{Format(status)} — {capture.Reason}"
                + (string.IsNullOrWhiteSpace(capture.AuthorityOutcome)
                    ? ""
                    : $" · authority: {Format(capture.AuthorityOutcome)}");
        EvidenceText.Text = preview is null
            ? "No newly serialized save preview is available. Cached evidence is not substituted."
            : $"{FormatAcquisitionSource(capture?.AcquisitionSource)} · "
                + $"Captured {preview.CapturedAt ?? "-"} · {preview.MappingId} · "
                + $"{Format(preview.MappingMaturity)} · {preview.Settings.Count} representable field(s)";
        CapturedValuesText.Text = preview is null
            ? "Waiting for a runtime-issued forced save."
            : JsonSerializer.Serialize(preview.Settings, PrettyJson);
        UnresolvedValuesText.Text = preview is null
            ? "Unresolved fields will remain explicit."
            : JsonSerializer.Serialize(preview.Unresolved, PrettyJson);

        _applying = true;
        try
        {
            var selected = BaseBox.SelectedItem as SetupCaptureBaseChoice;
            var choices = new List<SetupCaptureBaseChoice>
            {
                new("No Base (compare with an empty draft)", null, null),
            };
            choices.AddRange(
                response.Bases.Items.SelectMany(baseItem =>
                    baseItem.Revisions.Select(revision => new SetupCaptureBaseChoice(
                        $"{baseItem.DisplayName} · revision {revision.Revision}",
                        baseItem.Id,
                        revision.Revision))));
            BaseBox.ItemsSource = choices;
            BaseBox.SelectedItem = choices.FirstOrDefault(choice =>
                    choice.Id == selected?.Id
                    && choice.Revision == selected?.Revision)
                ?? choices[0];
        }
        finally
        {
            _applying = false;
        }

        if (IsInProgress(status))
        {
            _pollTimer.Start();
        }
        else
        {
            _pollTimer.Stop();
        }
        var ready = status == "ready" && preview is not null;
        TryAgainButton.Visibility = IsTerminal(status)
            ? Visibility.Visible
            : Visibility.Collapsed;
        TryAgainButton.IsEnabled = IsTerminal(status)
            && response.Availability.Available;
        CancelCaptureButton.IsEnabled = capture is not null
            && status is not "capturing"
            && status is not "saved"
            && status is not "cancelled"
            && status is not "failed"
            && status is not "interrupted"
            && status is not "unavailable";
        ReviewButton.IsEnabled = ready && IsStrategy;
        UpdateSaveAvailability();
    }

    private static string FormatAcquisitionSource(string? source) => source switch
    {
        "retained_return_control_refresh" =>
            "Exact retained Return Control forced save; no new device input",
        "new_setup_capture_refresh" => "New setup-capture forced save",
        _ => "Save evidence source unavailable",
    };

    private async void Review_Click(object sender, RoutedEventArgs e)
    {
        if (!TryBuildStrategyFields(out var fields, out var error))
        {
            ShowFailure(error);
            return;
        }
        var capture = ReadyCapture();
        if (capture is null)
        {
            return;
        }
        fields["operation"] = "review";
        fields["request_id"] = capture.RequestId;
        fields["expected_preview_fingerprint"] = capture.PreviewFingerprint;
        await RunAsync(async cancellationToken =>
        {
            var reviewed = await _api.PostSetupCaptureAsync(fields, cancellationToken);
            ApplyResponse(reviewed);
            _review = reviewed.Review
                ?? throw new InvalidOperationException(
                    "The Linux service returned no capture review.");
            _reviewInput = CurrentReviewInput();
            DifferenceText.Text = JsonSerializer.Serialize(
                _review.CapturedVsBase,
                PrettyJson)
                + Environment.NewLine
                + $"Unresolved fields retained: {_review.Unresolved.Count}.";
            UpdateSaveAvailability();
        });
    }

    private async void TryAgain_Click(object sender, RoutedEventArgs e)
    {
        await RunAsync(async cancellationToken =>
        {
            _review = null;
            _reviewInput = null;
            ApplyResponse(await _api.PostSetupCaptureAsync(
                new Dictionary<string, object> { ["operation"] = "request" },
                cancellationToken));
        });
    }

    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        var capture = ReadyCapture();
        if (capture is null)
        {
            return;
        }
        Dictionary<string, object> fields;
        if (IsStrategy)
        {
            if (!TryBuildStrategyFields(out fields, out var error))
            {
                ShowFailure(error);
                return;
            }
            if (_review is null
                || _reviewInput != CurrentReviewInput()
                || string.IsNullOrWhiteSpace(_review.ReviewFingerprint))
            {
                ShowFailure("Review the exact captured-versus-Base differences before saving.");
                return;
            }
            fields["expected_review_fingerprint"] = _review.ReviewFingerprint;
        }
        else
        {
            if (!TryIdentity(out var identifier, out var displayName, out var error))
            {
                ShowFailure(error);
                return;
            }
            fields = new Dictionary<string, object>
            {
                ["kind"] = "module_preset",
                ["id"] = identifier,
                ["display_name"] = displayName,
            };
        }
        fields["operation"] = "save";
        fields["request_id"] = capture.RequestId;
        fields["expected_preview_fingerprint"] = capture.PreviewFingerprint;
        await RunAsync(async cancellationToken =>
        {
            var saved = await _api.PostSetupCaptureAsync(fields, cancellationToken);
            ApplyResponse(saved);
            var artifact = saved.Request?.SavedResult
                ?? throw new InvalidOperationException(
                    "The Linux service did not return the saved artifact receipt.");
            var message = $"Saved {artifact.DisplayName}. It was not published, selected, "
                + "activated, queued, applied, or used to change control authority.";
            if (IsStrategy && _review is not null)
            {
                var open = MessageBox.Show(
                    this,
                    message + Environment.NewLine + Environment.NewLine
                        + "Open the captured source in the ordinary Strategy editor now?",
                    "Captured Strategy draft saved",
                    MessageBoxButton.YesNo,
                    MessageBoxImage.Information);
                if (open == MessageBoxResult.Yes)
                {
                    var editor = new StrategyProfilesWindow(
                        _api,
                        _review.Source,
                        _review.Resolution)
                    {
                        Owner = Owner,
                    };
                    editor.ShowDialog();
                }
            }
            else
            {
                MessageBox.Show(
                    this,
                    message,
                    "Module preset saved",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information);
            }
        });
    }

    private async void CancelCapture_Click(object sender, RoutedEventArgs e)
    {
        var capture = _response?.Capture;
        if (capture is null)
        {
            return;
        }
        await RunAsync(async cancellationToken =>
        {
            ApplyResponse(await _api.PostSetupCaptureAsync(
                new Dictionary<string, object>
                {
                    ["operation"] = "cancel",
                    ["request_id"] = capture.RequestId,
                },
                cancellationToken));
            Close();
        });
    }

    private void CaptureInput_Changed(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded || _applying)
        {
            return;
        }
        _review = null;
        _reviewInput = null;
        DifferenceText.Text = IsStrategy
            ? "Review captured-versus-Base differences before saving a Strategy draft."
            : "A Module preset saves only the complete captured local Module loadout.";
        TierPanel.Visibility = IsStrategy ? Visibility.Visible : Visibility.Collapsed;
        BasePanel.Visibility = IsStrategy ? Visibility.Visible : Visibility.Collapsed;
        ReviewButton.Visibility = IsStrategy ? Visibility.Visible : Visibility.Collapsed;
        ReviewButton.IsEnabled = ReadyCapture() is not null && IsStrategy;
        UpdateSaveAvailability();
    }

    private bool TryBuildStrategyFields(
        out Dictionary<string, object> fields,
        out string error)
    {
        fields = [];
        if (!TryIdentity(out var identifier, out var displayName, out error))
        {
            return false;
        }
        if (!int.TryParse(TierBox.Text.Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out var tier)
            || tier is < 1 or > 20)
        {
            error = "Tier must be an integer from 1 through 20.";
            return false;
        }
        fields = new Dictionary<string, object>
        {
            ["kind"] = "strategy_draft",
            ["id"] = identifier,
            ["display_name"] = displayName,
            ["tier"] = tier,
        };
        if (BaseBox.SelectedItem is SetupCaptureBaseChoice { Id: not null, Revision: not null } selected)
        {
            fields["base"] = new Dictionary<string, object>
            {
                ["id"] = selected.Id,
                ["revision"] = selected.Revision.Value,
            };
        }
        return true;
    }

    private bool TryIdentity(
        out string identifier,
        out string displayName,
        out string error)
    {
        identifier = IdBox.Text.Trim();
        displayName = DisplayNameBox.Text.Trim();
        if (!IdentifierPattern.IsMatch(identifier))
        {
            error = "ID must start with a lowercase letter and contain 3-48 lowercase letters, digits, or underscores.";
            return false;
        }
        if (string.IsNullOrWhiteSpace(displayName) || displayName.Length > 80)
        {
            error = "Display name is required and must be at most 80 characters.";
            return false;
        }
        error = "";
        return true;
    }

    private void UpdateSaveAvailability()
    {
        var capture = ReadyCapture();
        var hasModules = capture?.Preview?.Settings.TryGetValue("modules", out var modules) == true
            && modules.ValueKind == JsonValueKind.Object
            && modules.TryGetProperty("local", out _);
        SaveButton.IsEnabled = !_busy
            && capture is not null
            && (IsStrategy
                ? _review is not null && _reviewInput == CurrentReviewInput()
                : hasModules);
    }

    private SetupCaptureStatus? ReadyCapture() =>
        _response?.Capture is { Status: "ready", Preview: not null } capture
            ? capture
            : null;

    private bool IsStrategy =>
        (KindBox.SelectedItem as ComboBoxItem)?.Tag?.ToString() != "module_preset";

    private string CurrentReviewInput()
    {
        var selected = BaseBox.SelectedItem as SetupCaptureBaseChoice;
        return string.Join(
            "\n",
            IdBox.Text.Trim(),
            DisplayNameBox.Text.Trim(),
            TierBox.Text.Trim(),
            selected?.Id ?? "",
            selected?.Revision?.ToString(CultureInfo.InvariantCulture) ?? "");
    }

    private async Task RunAsync(
        Func<CancellationToken, Task> operation,
        bool showFailure = true)
    {
        if (_busy)
        {
            return;
        }
        _busy = true;
        UpdateSaveAvailability();
        try
        {
            using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(120));
            await operation(cancellation.Token);
        }
        catch (Exception exc)
        {
            if (showFailure)
            {
                ShowFailure(exc.Message);
            }
        }
        finally
        {
            _busy = false;
            UpdateSaveAvailability();
        }
    }

    private void ShowFailure(string message)
    {
        StatusText.Text = message;
        MessageBox.Show(this, message, "Setup capture", MessageBoxButton.OK, MessageBoxImage.Warning);
    }

    private static bool IsInProgress(string status) =>
        status is "requested" or "acknowledged" or "capturing";

    private static bool IsTerminal(string status) =>
        status is "saved" or "cancelled" or "unavailable"
            or "interrupted" or "failed";

    private static string Format(string value) =>
        string.Join(" ", value.Split('_')).Trim();

    private void Close_Click(object sender, RoutedEventArgs e) => Close();

    private sealed record SetupCaptureBaseChoice(
        string Label,
        string? Id,
        int? Revision);
}
