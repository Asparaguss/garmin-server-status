import Toybox.Application;
import Toybox.Lang;

(:glance)
module ServerLabel {
    function name(fallback) {
        var value = Application.Properties.getValue("serverName");
        return value instanceof String && value.length() > 0 ? value : fallback;
    }

    function fit(dc, font, text, width) {
        if (dc.getTextWidthInPixels(text, font) <= width) {
            return text;
        }
        for (var end = text.length() - 1; end > 0; end--) {
            var shortened = text.substring(0, end) + "...";
            if (dc.getTextWidthInPixels(shortened, font) <= width) {
                return shortened;
            }
        }
        return dc.getTextWidthInPixels("...", font) <= width ? "..." : "";
    }
}
