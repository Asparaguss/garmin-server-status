import Toybox.Application;
import Toybox.WatchUi;

(:glance)
class ServerStatusApp extends Application.AppBase {
    private var _dashboard;

    function initialize() {
        AppBase.initialize();
    }

    function getInitialView() {
        _dashboard = new StatusDashboard();
        return [_dashboard, new RefreshDelegate(_dashboard)];
    }

    function getGlanceView() {
        return [new StatusGlance()];
    }

    function onSettingsChanged() as Void {
        if (_dashboard != null) {
            _dashboard.settingsChanged();
        }
    }
}
