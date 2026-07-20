using System.Windows;

namespace TheTower.ControlSurface;

public static class WindowPlacementStore
{
    private const double MinimumVisibleWidth = 96;
    private const double MinimumVisibleHeight = 28;

    public static WindowPlacementSettings? Capture(Window window)
    {
        var bounds = window.WindowState == WindowState.Normal
            ? new Rect(window.Left, window.Top, window.Width, window.Height)
            : window.RestoreBounds;
        if (!IsFinitePositive(bounds))
        {
            return null;
        }

        return new WindowPlacementSettings
        {
            Left = bounds.Left,
            Top = bounds.Top,
            Width = bounds.Width,
            Height = bounds.Height,
            Maximized = window.WindowState == WindowState.Maximized,
        };
    }

    public static bool Restore(Window window, WindowPlacementSettings? placement)
    {
        if (placement is null || !IsFinitePositive(placement))
        {
            return false;
        }

        var virtualScreen = new Rect(
            SystemParameters.VirtualScreenLeft,
            SystemParameters.VirtualScreenTop,
            SystemParameters.VirtualScreenWidth,
            SystemParameters.VirtualScreenHeight);
        if (!IsFinitePositive(virtualScreen))
        {
            return false;
        }

        var width = Math.Min(
            Math.Max(placement.Width, window.MinWidth),
            virtualScreen.Width);
        var height = Math.Min(
            Math.Max(placement.Height, window.MinHeight),
            virtualScreen.Height);
        var desiredBounds = new Rect(placement.Left, placement.Top, width, height);
        var visibleTitleBar = Rect.Intersect(
            new Rect(
                desiredBounds.Left,
                desiredBounds.Top,
                desiredBounds.Width,
                Math.Min(desiredBounds.Height, MinimumVisibleHeight)),
            virtualScreen);
        if (visibleTitleBar.Width < MinimumVisibleWidth
            || visibleTitleBar.Height < MinimumVisibleHeight)
        {
            return false;
        }

        window.WindowStartupLocation = WindowStartupLocation.Manual;
        window.Left = desiredBounds.Left;
        window.Top = desiredBounds.Top;
        window.Width = desiredBounds.Width;
        window.Height = desiredBounds.Height;
        window.WindowState = placement.Maximized
            ? WindowState.Maximized
            : WindowState.Normal;
        return true;
    }

    private static bool IsFinitePositive(WindowPlacementSettings placement) =>
        double.IsFinite(placement.Left)
        && double.IsFinite(placement.Top)
        && double.IsFinite(placement.Width)
        && double.IsFinite(placement.Height)
        && placement.Width > 0
        && placement.Height > 0;

    private static bool IsFinitePositive(Rect bounds) =>
        double.IsFinite(bounds.Left)
        && double.IsFinite(bounds.Top)
        && double.IsFinite(bounds.Width)
        && double.IsFinite(bounds.Height)
        && bounds.Width > 0
        && bounds.Height > 0;
}
