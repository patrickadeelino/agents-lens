import json
import threading
import unittest
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from catalog import CatalogHandler


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), CatalogHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_list_and_lookup(self):
        with urlopen(self.base + "/products") as response:
            self.assertEqual(len(json.load(response)), 3)
        with urlopen(self.base + "/products/1") as response:
            self.assertEqual(json.load(response)["name"], "Notebook")
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/products/999")
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
