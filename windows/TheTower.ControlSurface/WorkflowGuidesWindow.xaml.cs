using System.Windows;
using System.Windows.Controls;

namespace TheTower.ControlSurface;

public partial class WorkflowGuidesWindow : Window
{
    private readonly Action<WorkflowGuideDestination>? _navigate;
    private WorkflowGuide? _selectedGuide;

    internal WorkflowGuidesWindow(
        string? initialGuideId = null,
        Action<WorkflowGuideDestination>? navigate = null)
    {
        InitializeComponent();
        _navigate = navigate;
        NavigateButton.Visibility = navigate is null
            ? Visibility.Collapsed
            : Visibility.Visible;
        GuideList.ItemsSource = WorkflowGuideCatalog.All;
        SelectGuide(initialGuideId ?? WorkflowGuideIds.Controls);
    }

    internal void SelectGuide(string guideId)
    {
        var guide = WorkflowGuideCatalog.Get(guideId);
        GuideList.SelectedItem = guide;
        GuideList.ScrollIntoView(guide);
    }

    private void GuideList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (GuideList.SelectedItem is not WorkflowGuide guide)
        {
            return;
        }

        _selectedGuide = guide;
        GuideContentPanel.DataContext = guide;
        GuideSections.ItemsSource = guide.Sections;
    }

    private void Navigate_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedGuide is not null)
        {
            _navigate?.Invoke(_selectedGuide.Destination);
        }
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
