import unittest
from routing import ROUTE, updated_config

LIVE = '''user: "sslh";
listen: ( { host: "0.0.0.0"; port: "443"; } );
protocols: (
  { name: "tls"; host: "127.0.0.1"; port: "4447"; sni_hostnames: [ "drive.everbright.it" ]; },
  { name: "tls"; host: "127.0.0.1"; port: "4446"; sni_hostnames: [ "servizi.everbright.it" ]; },
  { name: "tls"; host: "127.0.0.1"; port: "4443"; },
  { name: "anyprot"; host: "127.0.0.1"; port: "4443"; }
);
'''


class RoutingTests(unittest.TestCase):
    def test_only_adds_new_route_and_is_idempotent(self):
        updated = updated_config(LIVE)
        self.assertEqual(updated.replace("\n" + ROUTE, "", 1), LIVE)
        self.assertLess(updated.index(ROUTE), updated.index('port: "4443"'))
        self.assertEqual(updated_config(updated), updated)

    def test_unexpected_existing_route_is_rejected(self):
        with self.assertRaises(ValueError):
            updated_config(ROUTE.replace("4448", "4449"))

    def test_port_already_used_is_rejected(self):
        with self.assertRaises(ValueError):
            updated_config(LIVE.replace("4447", "4448"))

    def test_unexpected_format_is_rejected(self):
        with self.assertRaises(ValueError):
            updated_config("invalid configuration")
