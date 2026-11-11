import Toybox.Graphics;
import Toybox.WatchUi;

(:glance)
class StatusGlance extends WatchUi.GlanceView {
    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc as Dc) as Void {
        var reading = StatusSource.cached();
        var mono = dc.getWidth() < 200;
        var font = Graphics.FONT_XTINY;
        var height = dc.getFontHeight(font);
        var y = (dc.getHeight() - height * 3) / 2;
        var title = ServerLabel.name(mono ? "Server" : "Server Status");
        var titleWidth = dc.getWidth() * (mono ? 55 : 42) / 100;

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        if (reading == null) {
            var x = mono ? 3 : 52;
            dc.drawText(x, y, font, ServerLabel.fit(dc, font, title, titleWidth), Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(x, y + height, font, "Open to", Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(x, y + height * 2, font, "refresh", Graphics.TEXT_JUSTIFY_LEFT);
            return;
        }
        var cpu = "CPU " + reading["cpu"].format("%d") + "%";
        var ram = "RAM " + reading["ram"].format("%d") + "%";
        var age = StatusReading.age(reading);
        if (mono) {
            if (age.equals("Check clock")) {
                age = "!";
            }
            var nextUnit = age.find(" ");
            if (nextUnit != null) {
                age = age.substring(0, nextUnit);
            }
            var ageSuffix = " · " + age;
            var fitted = ServerLabel.fit(dc, font, title, titleWidth - dc.getTextWidthInPixels(ageSuffix, font));
            dc.drawText(3, y, font, fitted + ageSuffix, Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(3, y + height, font, cpu, Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(3, y + height * 2, font, ram, Graphics.TEXT_JUSTIFY_LEFT);
        } else {
            var step = (dc.getHeight() - 2) / 4;
            dc.drawText(75, 0, font, ServerLabel.fit(dc, font, title, titleWidth), Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(40, step, font, cpu, Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(28, step * 2, font, ram, Graphics.TEXT_JUSTIFY_LEFT);
            dc.setColor(0xAAAAAA, Graphics.COLOR_TRANSPARENT);
            dc.drawText(28, step * 3, font, "Age " + age, Graphics.TEXT_JUSTIFY_LEFT);
        }
    }
}
