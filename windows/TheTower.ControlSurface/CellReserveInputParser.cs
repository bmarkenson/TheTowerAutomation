using System.Globalization;
using System.Numerics;
using System.Text.RegularExpressions;

namespace TheTower.ControlSurface;

internal static class CellReserveInputParser
{
    private const int MaximumNormalizedDigits = 36;
    private const int MaximumInputCharacters = 80;
    private static readonly Regex InputPattern = new(
        @"^(?<whole>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+))(?:\.(?<fraction>[0-9]+))?[ \t]*(?<suffix>[kKmMbBtTqQsSoOnNdD])?$",
        RegexOptions.CultureInvariant);

    public static bool TryNormalize(string? input, out string? normalized)
    {
        normalized = null;
        var candidate = (input ?? "").Trim();
        if (candidate.Length == 0)
        {
            return true;
        }
        if (candidate.Length > MaximumInputCharacters)
        {
            return false;
        }

        var match = InputPattern.Match(candidate);
        if (!match.Success)
        {
            return false;
        }
        var magnitudeExponent = Exponent(match.Groups["suffix"].Value);
        if (magnitudeExponent is null)
        {
            return false;
        }

        var whole = match.Groups["whole"].Value.Replace(",", "");
        var fraction = match.Groups["fraction"].Value;
        var significantText = (whole + fraction).TrimStart('0');
        if (significantText.Length == 0)
        {
            significantText = "0";
        }
        if (!BigInteger.TryParse(
                significantText,
                NumberStyles.None,
                CultureInfo.InvariantCulture,
                out var significant))
        {
            return false;
        }

        var decimalShift = magnitudeExponent.Value - fraction.Length;
        BigInteger value;
        if (decimalShift >= 0)
        {
            value = significant * BigInteger.Pow(10, decimalShift);
        }
        else
        {
            var divisor = BigInteger.Pow(10, -decimalShift);
            if (significant % divisor != BigInteger.Zero)
            {
                return false;
            }
            value = significant / divisor;
        }

        var rendered = value.ToString(CultureInfo.InvariantCulture);
        if (rendered.Length > MaximumNormalizedDigits)
        {
            return false;
        }
        normalized = rendered;
        return true;
    }

    private static int? Exponent(string suffix) => suffix switch
    {
        "" => 0,
        "k" or "K" => 3,
        "m" or "M" => 6,
        "b" or "B" => 9,
        "t" or "T" => 12,
        "q" => 15,
        "Q" => 18,
        "s" => 21,
        "S" => 24,
        "o" or "O" => 27,
        "n" or "N" => 30,
        "d" or "D" => 33,
        _ => null,
    };
}
