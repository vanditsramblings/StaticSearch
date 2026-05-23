"""HyperSearch — BYOD semantic search engine powered by DuckDB + local embeddings.

Quick start::

    from hypersearch import HyperSearch

    with HyperSearch(data_dir="./data") as hs:
        col = hs.create_collection("products")
        col.ingest("products.csv", template="{name} {description}")
        results = col.search("waterproof boots", top_k=5)
        for hit in results:
            print(hit.score, hit.document)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hypersearch")
except PackageNotFoundError:
    __version__ = "0.2.0"  # fallback for dev installs

from hypersearch.client import HyperSearch
from hypersearch.collection import Collection
from hypersearch.results import SearchHit, SearchResults

__all__ = [
    "HyperSearch",
    "Collection",
    "SearchHit",
    "SearchResults",
    "__version__",
]
