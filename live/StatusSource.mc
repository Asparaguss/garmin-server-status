import Toybox.Application;
import Toybox.Communications;
import Toybox.Lang;

(:glance)
module StatusSource {
    function endpoint() {
        var url = Application.Properties.getValue("endpoint");
        if (!(url instanceof String) || url.length() <= 8 || url.length() > 200 ||
            !url.substring(0, 8).equals("https://") || url.find(" ") != null ||
            url.find("\n") != null || url.find("\r") != null || url.find("\\") != null ||
            url.find("@") != null || url.find("?") != null || url.find("#") != null) {
            return null;
        }
        var pathAt = url.substring(8, null).find("/");
        return pathAt != null && pathAt > 0 && url.length() > pathAt + 9 ? url : null;
    }

    function token() {
        var value = Application.Properties.getValue("token");
        if (!(value instanceof String) || value.length() < 32 || value.length() > 128) {
            return null;
        }
        var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        for (var i = 0; i < value.length(); i++) {
            if (chars.find(value.substring(i, i + 1)) == null) {
                return null;
            }
        }
        return value;
    }

    function cached() {
        var url = endpoint();
        if (url == null || token() == null || !url.equals(Application.Storage.getValue("cachedEndpoint"))) {
            return null;
        }
        var data = Application.Storage.getValue("reading");
        return StatusReading.valid(data) ? data : null;
    }

    function clear() {
        Application.Storage.deleteValue("reading");
        Application.Storage.deleteValue("cachedEndpoint");
    }

    function save(data) {
        Application.Storage.setValue("reading", data);
        Application.Storage.setValue("cachedEndpoint", endpoint());
    }

    function request(callback) {
        var url = endpoint();
        var secret = token();
        if (url == null || secret == null) {
            return false;
        }
        Communications.makeWebRequest(url, null, {
            :method => Communications.HTTP_REQUEST_METHOD_GET,
            :headers => {"Authorization" => "Bearer " + secret},
            :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON,
            :context => [url, secret]
        }, callback);
        return true;
    }

    function connection() {
        return [endpoint(), token()];
    }

    function current(context) {
        var url = endpoint();
        var secret = token();
        return context instanceof Array &&
            (url == null ? context[0] == null : url.equals(context[0])) &&
            (secret == null ? context[1] == null : secret.equals(context[1]));
    }
}
