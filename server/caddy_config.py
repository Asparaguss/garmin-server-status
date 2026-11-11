"""Plan a narrow, reversible edit to the active Caddyfile."""

import re

BEGIN = "# BEGIN Garmin Server Status"
END = "# END Garmin Server Status"
ACME = "https://acme-v02.api.letsencrypt.org/directory"
DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")
PATH = re.compile(r"/[a-zA-Z0-9._~-]+(?:/[a-zA-Z0-9._~-]+)*/?")


def address(domain, path):
    domain = domain.strip().lower()
    path = path.strip() or "/status"
    if not DOMAIN.fullmatch(domain):
        raise ValueError("Enter a public DNS hostname without a scheme or port")
    if not PATH.fullmatch(path) or set(path.split("/")) & {".", ".."}:
        raise ValueError("Enter an exact URL path such as /status or /monitor/garmin")
    if len("https://" + domain + path) > 200:
        raise ValueError("Use an HTTPS endpoint of 200 characters or fewer")
    return domain, path


def expand(header, environment):
    def replace(match):
        name, _, default = match[1].partition(":")
        if name not in environment and not default:
            return match[0]
        return environment.get(name, default)
    return re.sub(r"\{\$([^}]+)\}", replace, header)


def without_managed_block(config):
    lines = config.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if not starts and not ends:
        return config
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("The managed Server Status markers are incomplete or duplicated")
    return "".join(lines[:starts[0]] + lines[ends[0] + 1:])


def find_site(lines, domain, environment):
    depth = 0
    found = None
    for index, line in enumerate(lines):
        stripped = line.split("#", 1)[0].strip()
        if stripped == "}":
            depth -= 1
        elif stripped.endswith("{"):
            if depth == 0:
                tokens = expand(stripped[:-1].strip(), environment).replace(",", " ").split()
                hosts = [re.sub(r"^https://|^http://|:(443|80)$", "", token).lower() for token in tokens]
                if domain in hosts:
                    if found is not None:
                        raise ValueError("Multiple site blocks match this hostname")
                    found = index, tokens
            depth += 1
    if depth != 0:
        raise ValueError("Use a Caddyfile with explicit site blocks and braces on separate closing lines")
    return found


def plan(original, domain, path, upstream, environment, trusted):
    base = without_managed_block(original)
    managed = original.split(BEGIN, 1)[1].split(END, 1)[0] if base != original else ""
    previous_domain = re.search(r"^\s*host ([^\s]+)$", managed, re.MULTILINE)
    retain_issuer = bool(previous_domain and previous_domain[1] == domain and ACME in managed)
    lines = base.splitlines(keepends=True)
    found = find_site(lines, domain, environment)
    route = (
        f"\t@garminServerStatus {{\n\t\thost {domain}\n\t\tpath {path}\n\t}}\n"
        f"\thandle @garminServerStatus {{\n\t\trewrite * /metrics\n"
        f"\t\treverse_proxy {upstream}\n\t}}\n"
    )
    tls = f"\ttls {{\n\t\tissuer acme {ACME}\n\t}}\n"
    if found is None:
        if trusted and not retain_issuer:
            raise ValueError("The hostname already serves HTTPS. Put its site block in the selected Caddyfile "
                             "so the installer can preserve its existing routes.")
        separator = "" if not base or base.endswith("\n") else "\n"
        return base + separator + f"{BEGIN}\n{domain} {{\n{tls}{route}}}\n{END}\n"
    index, sites = found
    if not trusted and any(site.startswith("http://") or site.endswith(":80") for site in sites):
        raise ValueError("The matching Caddy site is HTTP-only. Configure an HTTPS site for the hostname first.")
    certificate = ""
    if (not trusted or retain_issuer) and len(sites) == 1:
        directives = [line.split("#", 1)[0].split() for line in site_body(lines, index)]
        existing_tls = [parts for parts in directives if parts and parts[0] == "tls"]
        if any("internal" in parts or len(parts) >= 3 for parts in existing_tls):
            raise ValueError("The matching site has an explicit TLS certificate or internal issuer")
        # Respect an existing TLS policy. Add an explicit issuer to a plain single-host site.
        if not existing_tls:
            certificate = tls
    lines.insert(index + 1, f"\t{BEGIN}\n{certificate}{route}\t{END}\n")
    return "".join(lines)


def site_body(lines, index):
    depth = 1
    for line in lines[index + 1:]:
        stripped = line.split("#", 1)[0].strip()
        if stripped == "}":
            depth -= 1
        if depth == 0:
            break
        if depth == 1:
            yield line
        if stripped.endswith("{"):
            depth += 1
