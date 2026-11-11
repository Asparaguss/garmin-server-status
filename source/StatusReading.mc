import Toybox.Lang;
import Toybox.Time;

(:glance)
module StatusReading {
    function valid(data) {
        if (!(data instanceof Dictionary) || data["version"] != 1) {
            return false;
        }
        var percentages = ["cpu", "ram", "disk"];
        for (var i = 0; i < percentages.size(); i++) {
            var value = data[percentages[i]];
            if (!(value instanceof Number) || value < 0 || value > 100) {
                return false;
            }
        }
        var uptime = data["uptime_s"];
        var sampledAt = data["sampled_at"];
        return uptime instanceof Number && uptime >= 0 &&
            sampledAt instanceof Number && sampledAt > 0;
    }

    function duration(seconds) {
        if (seconds < 60) {
            return seconds.format("%d") + "s";
        }
        if (seconds < 3600) {
            return (seconds / 60).format("%d") + "m";
        }
        if (seconds < 86400) {
            return (seconds / 3600).format("%d") + "h " + ((seconds % 3600) / 60).format("%d") + "m";
        }
        return (seconds / 86400).format("%d") + "d " + ((seconds % 86400) / 3600).format("%d") + "h";
    }

    function age(reading) {
        var seconds = Time.now().value() - reading["sampled_at"];
        return seconds < -60 ? "Check clock" : duration(seconds < 0 ? 0 : seconds);
    }
}
