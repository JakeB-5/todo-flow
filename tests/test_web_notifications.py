import unittest
import urllib.request

import test_web


class NotificationAssetTests(unittest.TestCase):
    setUp = test_web.HttpTests.setUp
    stop_server = test_web.HttpTests.stop_server

    def test_notification_script_is_served_read_only_after_dashboard_script(self):
        with urllib.request.urlopen(self.url + "/notify.js") as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"))
            self.assertIn("function planNotifications(", r.read().decode())
        with urllib.request.urlopen(self.url + "/") as r:
            page = r.read().decode()
        self.assertLess(page.index('src="/app.js"'), page.index('src="/notify.js"'))
        self.assertIn('id="notify"', page)


if __name__ == "__main__":
    unittest.main()
