import Toybox.Time;

(:glance)
module StatusSource {
    function cached() {
        return {
            "version" => 1, "cpu" => 24, "ram" => 57, "disk" => 68,
            "uptime_s" => 1051200, "ssh_failed_24h" => 12,
            "sampled_at" => Time.now().value() - 18
        };
    }

    function request(callback) {
        callback.invoke(200, cached(), null);
        return true;
    }

    function connection() { return null; }
    function current(context) { return true; }
    function clear() {}
    function save(data) {}
}
