namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class CellReserveInputParserTests
{
    [Theory]
    [InlineData(null, null)]
    [InlineData("", null)]
    [InlineData("20000000", "20000000")]
    [InlineData("20,000,000", "20000000")]
    [InlineData("20M", "20000000")]
    [InlineData("20m", "20000000")]
    [InlineData("20.5M", "20500000")]
    [InlineData("1.5q", "1500000000000000")]
    [InlineData("1Q", "1000000000000000000")]
    public void NormalizesWholeCellInputs(string? input, string? expected)
    {
        Assert.True(CellReserveInputParser.TryNormalize(input, out var normalized));
        Assert.Equal(expected, normalized);
    }

    [Theory]
    [InlineData("20,00,000")]
    [InlineData("20,000,00")]
    [InlineData("-20M")]
    [InlineData("20.25")]
    [InlineData("20.0000001M")]
    [InlineData("1e6")]
    [InlineData("20MM")]
    [InlineData("1000D")]
    public void RejectsMalformedFractionalOrOversizedInputs(string input)
    {
        Assert.False(CellReserveInputParser.TryNormalize(input, out var normalized));
        Assert.Null(normalized);
    }
}
