import argparse
from pathlib import Path

ROUTE = '  { name: "tls"; host: "127.0.0.1"; port: "4448"; sni_hostnames: [ "push.everbright.it" ]; },'


def updated_config(content):
    existing = [line for line in content.splitlines() if '"push.everbright.it"' in line]
    if existing:
        if len(existing) != 1 or existing[0].strip() != ROUTE.strip():
            raise ValueError("Existing EverPush routing differs; no configuration changed")
        return content
    if '"4448"' in content:
        raise ValueError("Port 4448 already routed elsewhere; no configuration changed")
    if content.count("protocols: (") != 1:
        raise ValueError("Unexpected sslh configuration; no configuration changed")
    return content.replace("protocols: (", "protocols: (\n" + ROUTE, 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.write_text(updated_config(args.source.read_text()))
