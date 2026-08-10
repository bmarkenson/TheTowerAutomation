using System.Globalization;

namespace TheTower.ControlSurface;

public sealed record GateDecisionPresentation(
    string Title,
    string Heading,
    string CheckText,
    string Disposition)
{
    public static GateDecisionPresentation From(GateDecisionStatus decision)
    {
        var checkText =
            $"Check: {Humanize(decision.CheckId)} ({Humanize(decision.Phase)})";
        if (!decision.Blocking)
        {
            return new(
                "Preflight warning needs direction",
                "A read-only preflight found a mismatch",
                checkText,
                "Closing leaves the warning pending; Tournament observation continues.");
        }

        if (string.Equals(
                decision.Phase,
                "session_preflight",
                StringComparison.OrdinalIgnoreCase))
        {
            return new(
                "Session preflight needs direction",
                "A running-session requirement could not be satisfied",
                checkText,
                "Closing leaves Strategy and lifecycle actions blocked at this gate; " +
                "safe status and diagnostic collection continue.");
        }

        return new(
            "Startup gate needs direction",
            "A startup requirement could not be satisfied",
            checkText,
            "Closing leaves automation blocked at this gate.");
    }

    private static string Humanize(string? value)
    {
        var normalized = (value ?? "")
            .Trim()
            .Replace('_', ' ')
            .Replace('-', ' ');
        if (normalized.Length == 0)
        {
            return "Unknown";
        }

        return CultureInfo.InvariantCulture.TextInfo.ToTitleCase(
            normalized.ToLowerInvariant());
    }
}
