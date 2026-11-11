import Toybox.Application;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Timer;
import Toybox.WatchUi;

class StatusDashboard extends WatchUi.View {
    private var _reading;
    private var _pending = false;
    private var _message = "";
    private var _clock;
    private var _refreshTimer;
    private var _visible = false;
    private var _connection;

    function initialize() {
        View.initialize();
        _clock = new Timer.Timer();
        _refreshTimer = new Timer.Timer();
    }

    function onShow() as Void {
        _visible = true;
        _connection = StatusSource.connection();
        _reading = StatusSource.cached();
        _clock.start(method(:redraw), 1000, true);
        scheduleRefresh();
        refresh();
    }

    function onHide() as Void {
        _visible = false;
        _clock.stop();
        _refreshTimer.stop();
    }

    function redraw() as Void {
        if (_reading != null) {
            WatchUi.requestUpdate();
        }
    }

    function settingsChanged() {
        if (_visible) {
            scheduleRefresh();
        }
        if (!StatusSource.current(_connection)) {
            _connection = StatusSource.connection();
            StatusSource.clear();
            _reading = null;
            refresh();
        }
    }

    private function scheduleRefresh() {
        _refreshTimer.stop();
        var seconds = Application.Properties.getValue("refreshSeconds");
        if (seconds == 0) {
            return;
        }
        if (seconds != 30 && seconds != 60 && seconds != 120 && seconds != 300) {
            seconds = 60;
        }
        _refreshTimer.start(method(:refresh), seconds * 1000, true);
    }

    function refresh() {
        if (!_visible || _pending) {
            return;
        }
        _pending = true;
        _message = "Refreshing";
        try {
            if (!StatusSource.request(method(:onResponse))) {
                _pending = false;
                _message = "Set URL and token";
            }
        } catch (error) {
            _pending = false;
            _message = "Refresh failed";
        }
        WatchUi.requestUpdate();
    }

    function onResponse(code as Number, data, context) as Void {
        _pending = false;
        if (!StatusSource.current(context)) {
            refresh();
            return;
        }
        if (code == 200 && StatusReading.valid(data)) {
            _reading = data;
            StatusSource.save(data);
            _message = "";
        } else if (code == 401 || code == 403) {
            _message = "Check status token";
        } else if (code == 200) {
            _message = "Invalid reading";
        } else if (code < 0) {
            _message = "Connection failed";
        } else {
            _message = "HTTP " + code.format("%d");
        }
        if (_visible) {
            WatchUi.requestUpdate();
        }
    }

    function onUpdate(dc as Dc) as Void {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        var hint = _message.length() > 0 ? _message : "GPS: Refresh";
        if (dc.getWidth() < 200) {
            drawSolar(dc, hint);
        } else {
            drawAmoled(dc, hint);
        }
    }

    private function drawSolar(dc, hint) {
        var width = dc.getWidth();
        var height = dc.getHeight();
        var font = Graphics.FONT_XTINY;
        var inset = WatchUi.getSubscreen();
        var cpuDial = inset != null && inset.width >= 50;
        var rightTop = cpuDial ? inset.x - 16 : width - 22;
        var left = 19;
        var right = width - 22;
        dc.drawText(26, 8, font, cpuDial ? "SERVER" : "Server Status", Graphics.TEXT_JUSTIFY_LEFT);

        if (_reading != null) {
            var dividerY = height * 42 / 100;
            if (cpuDial) {
                drawCpuDial(dc, inset);
                row(dc, left, rightTop, 28, font, "RAM", percent("ram"));
                row(dc, left, rightTop, 47, font, "Disk /", percent("disk"));
            } else {
                row(dc, left, right, 27, font, "CPU", percent("cpu"));
                row(dc, left, right, 45, font, "RAM", percent("ram"));
                row(dc, left, right, 63, font, "Disk /", percent("disk"));
                dividerY = 86;
            }
            dc.drawLine(left, dividerY, right, dividerY);
            row(dc, left, right, dividerY + 9, font, "Uptime", StatusReading.duration(_reading["uptime_s"]));
            row(dc, left, right, dividerY + 32, font, "Age", StatusReading.age(_reading));
        } else {
            dc.drawText(width / 2, height / 2 - 8, font, "No reading yet", Graphics.TEXT_JUSTIFY_CENTER);
        }
        dc.drawText(width / 2, height - 26, font, hint, Graphics.TEXT_JUSTIFY_CENTER);
    }

    private function drawCpuDial(dc, inset) {
        var x = inset.x + inset.width / 2;
        var y = inset.y + inset.height / 2;
        var value = percent("cpu");
        var font = Graphics.FONT_SMALL;
        if (dc.getTextWidthInPixels(value, font) > inset.width - 14) {
            font = Graphics.FONT_XTINY;
        }
        var centered = Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER;
        dc.drawText(x, y - 10, Graphics.FONT_XTINY, "CPU", centered);
        dc.drawText(x, y + 9, font, value, centered);
    }

    private function drawAmoled(dc, hint) {
        var w = dc.getWidth();
        var h = dc.getHeight();
        var small = Graphics.FONT_XTINY;
        var font = Graphics.FONT_TINY;
        var muted = 0xAAAAAA;
        var accent = 0x78E5C6;
        dc.drawText(w / 2, h * 12 / 100, Graphics.FONT_SMALL, "Server Status", Graphics.TEXT_JUSTIFY_CENTER);
        if (_reading != null) {
            meter(dc, w, h * 25 / 100, font, "CPU", "cpu", accent);
            meter(dc, w, h * 36 / 100, font, "RAM", "ram", accent);
            meter(dc, w, h * 47 / 100, font, "Disk /", "disk", accent);

            dc.setColor(0x343C3C, Graphics.COLOR_TRANSPARENT);
            dc.drawLine(w * 20 / 100, h * 58 / 100, w * 80 / 100, h * 58 / 100);
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            row(dc, w * 21 / 100, w * 79 / 100, h * 64 / 100, font, "Uptime",
                StatusReading.duration(_reading["uptime_s"]));
            dc.setColor(muted, Graphics.COLOR_TRANSPARENT);
            row(dc, w * 21 / 100, w * 79 / 100, h * 74 / 100, small, "Reading age", StatusReading.age(_reading));
        } else {
            dc.drawText(w / 2, h / 2 - 12, font, "No reading yet", Graphics.TEXT_JUSTIFY_CENTER);
        }
        dc.setColor(accent, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, h * 88 / 100, small, hint, Graphics.TEXT_JUSTIFY_CENTER);
    }

    private function meter(dc, w, y, font, label, key, accent) {
        var start = w * 37 / 100;
        var length = w * 30 / 100;
        var barY = y + dc.getFontHeight(font) / 2;
        var radius = 3;
        dc.setColor(0x343C3C, Graphics.COLOR_TRANSPARENT);
        dc.fillRoundedRectangle(start, barY - radius, length, 2 * radius, radius);
        dc.setColor(accent, Graphics.COLOR_TRANSPARENT);
        var filled = length * _reading[key] / 100;
        if (filled >= 2 * radius) {
            dc.fillRoundedRectangle(start, barY - radius, filled, 2 * radius, radius);
        } else if (filled > 0) {
            dc.fillRectangle(start, barY - radius, filled, 2 * radius);
        }
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        row(dc, w * 16 / 100, w * 84 / 100, y, font, label, percent(key));
    }

    private function row(dc, left, right, y, font, label, value) {
        dc.drawText(left, y, font, label, Graphics.TEXT_JUSTIFY_LEFT);
        dc.drawText(right, y, font, value, Graphics.TEXT_JUSTIFY_RIGHT);
    }

    private function percent(key) {
        return _reading[key].format("%d") + "%";
    }
}

class RefreshDelegate extends WatchUi.BehaviorDelegate {
    private var _view;

    function initialize(view) {
        BehaviorDelegate.initialize();
        _view = view;
    }

    function onSelect() as Boolean {
        _view.refresh();
        return true;
    }
}
