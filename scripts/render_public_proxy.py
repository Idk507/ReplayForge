"""Render the ACA ingress proxy configuration for one exact HTTPS hostname."""

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def public_host(origin: str) -> str:
    """Reject values that could change proxy syntax or broaden the host boundary."""
    try:
        url = urlsplit(origin)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or origin != f"https://{host}"
            or url.port is not None
            or len(host) > 253
            or not re.fullmatch(
                r"[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?)+",
                host,
            )
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return host
        raise ValueError
    except ValueError:
        raise ValueError("PUBLIC_ORIGIN_INVALID") from None


def render(origin: str, template: str) -> str:
    host = public_host(origin)
    if template.count("__PUBLIC_HOST__") != 4:
        raise ValueError("PUBLIC_PROXY_TEMPLATE_INVALID")
    return template.replace("__PUBLIC_HOST__", host)


def main() -> None:
    origin = os.environ.get("REPLAYFORGE_PUBLIC_ORIGIN", "")
    if (
        os.environ.get("REPLAYFORGE_DEPLOYMENT_MODE") != "PUBLIC_DEPLOYMENT"
        or os.environ.get("REPLAYFORGE_WEB_AUTH_MODE") != "github"
        or os.environ.get("REPLAYFORGE_AUTH_API_ORIGIN") != origin
        or os.environ.get("REPLAYFORGE_AUTH_WEB_ORIGIN") != origin
    ):
        raise SystemExit("PUBLIC_PROXY_CONFIG_INVALID")
    template = Path("/app/deploy/nginx.conf.template").read_text(encoding="utf-8")
    Path("/tmp/replayforge-nginx.conf").write_text(
        render(origin, template), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
