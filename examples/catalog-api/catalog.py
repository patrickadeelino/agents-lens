"""Small synthetic Catalog API, using only Python's standard library."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

PRODUCTS = [
    {"id": 1, "name": "Notebook", "category": "stationery"},
    {"id": 2, "name": "Mug", "category": "kitchen"},
    {"id": 3, "name": "Pen", "category": "stationery"},
]


class CatalogHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/products":
            self.respond(200, PRODUCTS)
            return
        if path.startswith("/products/"):
            product_id = path.removeprefix("/products/")
            product = next((item for item in PRODUCTS if str(item["id"]) == product_id), None)
            if product is not None:
                self.respond(200, product)
                return
        self.respond(404, {"error": "Not found"})

    def respond(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8091), CatalogHandler).serve_forever()
