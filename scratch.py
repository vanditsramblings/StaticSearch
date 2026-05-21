import asyncio
import json
from fastapi.testclient import TestClient
from hypersearch.server import create_app

app = create_app()
client = TestClient(app, raise_server_exceptions=True)

csv_content = b"title,description,price\nProduct 0,Desc 0,10\n"
config = json.dumps({"search_template": "{title} {description}"})

resp = client.post(
    "/v1/collections",
    json={"name": "products"}
)
print("Created collection:", resp.status_code)

try:
    resp = client.post(
        "/v1/collections/products/ingest",
        files={"file": ("products.csv", csv_content, "text/csv")},
        data={"config": config},
    )
    print("Ingest:", resp.status_code, resp.text)
except Exception as e:
    import traceback
    traceback.print_exc()
