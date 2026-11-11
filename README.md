# Garmin Server Status

Server Status is a standalone Connect IQ watch app and a small Linux metrics service. The watch requests a reading over HTTPS through Garmin Connect on the paired phone. The dashboard refreshes when opened, every minute while visible by default, and when you press GPS/START. The glance shows the cached CPU and RAM values with the reading age. Selecting it opens the dashboard and requests a fresh reading.

The dashboard shows CPU utilization, RAM and root disk usage, uptime, reading age, and unsuccessful SSH connections logged in the last 24 hours. On Instinct 2, Instinct 3 Solar, and Instinct E, the round inset displays CPU. The service reads `/proc` and `statvfs` directly, with a two-second sampling interval.

The SSH count is refreshed about once a minute from the retained system journal for OpenSSH services. It includes rejected keys or certificates, failed SSH negotiation, and logged disconnects before authentication, with multiple messages grouped by connection. OpenSSH cannot tell why a client disconnected. The dashboard displays `--` until a fresh count is available. Rerun `sudo sh install.sh` from the updated installer to add the collector to an existing installation.

## Linux setup

Download `setup.sh` to the Linux host and run it from the directory where you want to keep the installer. It uses `curl` to download a specific release and verifies its pinned SHA-256 before extracting it. The script asks before running the installer with administrator privileges. [Download setup.sh](https://github.com/Asparaguss/garmin-server-status/releases/latest/download/setup.sh)

```sh
sh setup.sh
```

To inspect the package first, run `sh setup.sh --download-only`. You can also download and extract the installer archive manually or clone this repository. From the extracted or cloned directory, run `sudo sh install.sh`. [Installer releases](https://github.com/Asparaguss/garmin-server-status/releases)

The installer checks systemd 247+, Python 3.10+, and the HTTPS trust store. On hosts with APT or DNF it offers to install missing Python or CA certificates from the configured package repositories. On other distributions, install those dependencies using your package manager.

Use a running Caddy proxy configured by a Caddyfile. On a fresh host, install and start the Caddy systemd package first. For Docker, use an existing running Caddy container on a local Linux Docker engine with the Caddyfile mounted from the host and a persistent volume or directory mounted at `/data`.

Enter your public hostname, such as `status.example.org`, and an unused exact URL path. Press Enter to use `/status`. The installer lists running Caddy containers, including Docker Compose services named `caddy`, and defaults to Docker when it detects one without an active native Caddy service. If more than one container is listed, enter the name of the proxy that serves your hostname. For Docker, give the Caddyfile path inside the container, commonly `/etc/caddy/Caddyfile`. The installer mounts no additional Docker volumes. Native Caddy connects over host loopback. Docker Caddy connects through a Unix socket in the existing `/data` mount. The metrics process runs on the host as a systemd dynamic user in both cases.

The installer adds an exact host and path match to an existing site block. Keep that block in the selected Caddyfile. The block may name other hosts, including Docker environment substitutions. A separate hostname with no trusted certificate gets a new site with Let's Encrypt issuance and renewal. An existing site with a trusted certificate retains its TLS settings. Sites defined in imported files, wildcard-only sites, and custom TLS policies may require a manual route integration.

For issuance, point the hostname's public DNS at Caddy, make its ACME challenge ports reachable, and ensure the server itself can reach the endpoint. The installer validates the proposed Caddyfile and verifies certificate trust, a rejected anonymous request, and a successful authenticated request before printing the endpoint. If HTTPS verification fails after applying a change, it restores the previous configuration. The backup is at `/etc/garmin-server-status/Caddyfile.previous`. An existing Caddy with its admin API disabled needs a short restart. The installer asks before restarting it.

```sh
sudo sh install.sh show-token
sudo sh install.sh rotate-token
```

In an interactive terminal, `show-token` displays your full HTTPS endpoint, including the path such as `/status`, and the token. Install `qrencode` through your system package manager to also display a QR code. The QR code contains the full token as plain text. Scan it on your phone and paste the result into **Read-only status token** in Connect IQ. Redirected output contains only the token.

Rotation replaces the current bearer token. Update the watch settings with the new value. The token authorizes only the read-only status response.

For troubleshooting, use `journalctl -u caddy` or `docker logs <caddy-container>` for TLS and routing, and `journalctl -u garmin-server-status.service` for metrics collection. The public endpoint should return HTTP 401 without the token.

## Watch setup

Set **HTTPS endpoint** to the full URL printed by the installer and **Read-only status token** to the value from `show-token`. Choose **Refresh interval** from manual only, 30 seconds, 1 minute, 2 minutes, or 5 minutes. Manual only still fetches on opening and on GPS/START. For Store-installed builds, use the app's settings in Connect IQ. Keep Garmin Connect paired and connected on the phone.

Set **Server name** to label the dashboard and glance. The field accepts up to 24 characters. The watch shortens names that exceed the space on its display.

For a USB sideload, copy the model-specific `.prg` from `build/` to `GARMIN/APPS`. Garmin's mobile settings UI relies on Store metadata. To configure a sideload, use the Garmin SDK app settings editor with the matching build, enter the URL and token, and copy its generated `.SET` file to `GARMIN/APPS/SETTINGS` with the same basename and case as the `.prg`. Treat the `.SET` file as a credential. [Garmin developer discussion on sideload settings](https://forums.garmin.com/developer/connect-iq/f/discussion/429848/solved-settings-connect-iq-app-for-sideloaded-app---is-this-possible/2005107)

SDK build targets include Instinct 2, 2S, 2X, Crossover, Crossover AMOLED, Instinct 3 Solar 45/50 mm, Instinct 3 AMOLED 45 mm and 50 mm, and Instinct E 40 mm and 45 mm. Garmin uses `instinct3solar45mm` for both Solar sizes. Device-app glances require Connect IQ 4.0 or later. The Instinct 2 family and original Crossover run the dashboard from the app launcher. [Garmin device reference](https://developer.garmin.com/connect-iq/device-reference/) · [Garmin entry points](https://developer.garmin.com/connect-iq/articles/user-experience-guidelines/Entry_Points.html)

Garmin can close the dashboard after inactivity when it was opened from the glance. Open Server Status from the app launcher to keep the dashboard open until you exit. [Garmin entry points](https://developer.garmin.com/connect-iq/articles/user-experience-guidelines/Entry_Points.html)

## Build and test

Use Connect IQ SDK 9.2 or later and your own developer key.

```sh
mkdir -p build
monkeyc -f monkey.jungle -d instinct3solar45mm -y developer_key.der -o build/ServerStatus-instinct3solar45mm.prg
```

`preview.jungle` uses the same dashboard and glance with sample readings. Use it for simulator captures. `monkey.jungle` uses the HTTPS client and settings.

Build the package for the separate Connect IQ Store listing with `release.jungle`. Its manifest has its own app ID.

```sh
monkeyc -e -r -f release.jungle -y developer_key.der -o build/ServerStatus-release.iq
```

```sh
python3 server/test_status.py -v
CADDY_TEST_BINARY=/path/to/caddy python3 server/test_status.py -v
```

The Caddy integration test uses a temporary certificate store and loopback ports. It tests both paths, preserves a pre-existing certificate and route across reloads, and validates the new-host Let's Encrypt configuration. Public ACME issuance, a Docker deployment, and the paired-phone relay require testing in their target environments.
