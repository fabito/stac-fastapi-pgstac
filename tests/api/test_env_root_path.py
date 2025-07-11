import os
from unittest import mock

import pytest
from starlette.testclient import TestClient

# Import the global app instance
# This app instance will have its root_path configured when app.py is first loaded,
# likely based on os.environ at that time (probably no ROOT_PATH set).
from stac_fastapi.pgstac.app import app


@pytest.mark.asyncio
@mock.patch.dict(os.environ, {"ROOT_PATH": "/custom/api/root"}, clear=True)
async def test_pagination_link_with_env_root_path(database_load_main_test_data):
    """
    Test that pagination links are correct when ROOT_PATH is simulated via os.environ
    and TestClient sets the scope['root_path'].
    This aims to simulate a Uvicorn-like setup where app.root_path might not be
    set from this specific env var if app was initialized before patch,
    but scope['root_path'] is.
    """
    # database_load_main_test_data should ensure there's enough data for pagination

    # We use the global `app` instance.
    # We set root_path in TestClient to ensure request.scope["root_path"] is set.
    # app.root_path will be whatever it was when app.py was imported.
    # If ROOT_PATH env var (from patch) isn't read by Settings() at app import,
    # then app.root_path will likely be "" or None.
    # This means request.scope["root_path"] = "/custom/api/root"
    # and request.app.root_path = "" (or None)
    # This matches the condition in BaseLinks.url for stripping path using scope_root_path.

    custom_root = "/custom/api/root"

import json # For loading test data
from urllib.parse import urlparse

# Import db management and settings directly for setup
from stac_fastapi.pgstac.db import connect_to_db, close_db_connection
# from stac_fastapi.pgstac.config import PostgresSettings # Not directly needed if using `database` fixture PG env vars

# Placeholder for DATA_DIR, assuming it's similar to conftest.py
# This might need adjustment if tests are run from a different working directory.
# A better way would be to make this relative to this file's location.
try:
    from tests.conftest import DATA_DIR
except ImportError:
    # Fallback if running standalone or conftest is not in sys.path
    DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@pytest.mark.asyncio
@mock.patch.dict(os.environ, {}, clear=True) # Start with a clear environment for PG vars
async def test_pagination_link_with_env_root_path(monkeypatch, database): # `database` fixture from conftest
    """
    Test pagination links when ROOT_PATH is set via environment variable,
    affecting the global app instance.
    """
    custom_root = "/custom/api/root"
    monkeypatch.setenv("ROOT_PATH", custom_root)

    # Set PG env vars for global app settings, to be picked up by Settings() when app is imported
    monkeypatch.setenv("PGUSER", database.user)
    monkeypatch.setenv("PGPASSWORD", database.password)
    monkeypatch.setenv("PGHOST", database.host)
    monkeypatch.setenv("PGPORT", str(database.port))
    monkeypatch.setenv("PGDATABASE", database.dbname)
    # Ensure other potentially influential env vars are set if app.py depends on them for default config
    monkeypatch.setenv("ENABLE_TRANSACTIONS_EXTENSIONS", "TRUE")
    monkeypatch.setenv("APP_HOST", "0.0.0.0") # Default from ApiSettings
    monkeypatch.setenv("APP_PORT", "8080")   # Default from ApiSettings
    monkeypatch.setenv("RELOAD", "FALSE") # Ensure reload is false for consistent app loading

    # Reload config and app modules to pick up the new ROOT_PATH
    # Must reload config first as app depends on it at import time
    import importlib
    import stac_fastapi.pgstac.config
    import stac_fastapi.pgstac.app

    importlib.reload(stac_fastapi.pgstac.config)
    importlib.reload(stac_fastapi.pgstac.app)

    # Now get the reloaded app
    from stac_fastapi.pgstac.app import app as global_pgstac_app

    await connect_to_db(global_pgstac_app, add_write_connection_pool=True)

    try:
        # Verify that the app's root_path was configured by the env var
        assert global_pgstac_app.root_path == custom_root, \
            f"App's root_path is '{global_pgstac_app.root_path}', expected '{custom_root}'"

        # TestClient uses the app. If app.root_path is set, TestClient automatically
        # prefixes requests with it and expects paths relative to it.
        # It also sets scope['root_path'] = app.root_path.
        with TestClient(global_pgstac_app, base_url="http://testserver") as client:

            def load_json_data(filename: str) -> dict:
                # Ensure DATA_DIR is correct
                # Using a path relative to this test file for robustness
                base_path = os.path.dirname(os.path.abspath(__file__))
                # Try common test data locations relative to tests/api/
                path_options = [
                    os.path.join(base_path, '..', 'testdata', 'joplin', filename),
                    os.path.join(base_path, '..', 'data', filename), # Used by test_api.py
                    os.path.join(base_path, 'data', filename) # Used by conftest.py's load_test_data
                ]

                filepath_to_load = None
                for p_opt in path_options:
                    if os.path.exists(p_opt):
                        filepath_to_load = p_opt
                        break

                if filepath_to_load is None:
                    raise FileNotFoundError(f"Test data file '{filename}' not found in attempted paths: {path_options}")

                with open(filepath_to_load) as file:
                    return json.load(file)

            # Load a collection
            coll_data = load_json_data("test_collection.json")
            coll_data["id"] = "env_root_path_test_coll" # Avoid conflicts
            resp = client.post("/collections", json=coll_data)
            assert resp.status_code == 201, resp.text
            coll_id = resp.json()["id"]

            # Load two items into this collection
            item1_data = load_json_data("test_item.json")
            item1_data["id"] = "item1_env_test"
            item1_data["collection"] = coll_id # Ensure it's part of the created collection
            resp = client.post(f"/collections/{coll_id}/items", json=item1_data)
            assert resp.status_code == 201, resp.text

            item2_data = load_json_data("test_item2.json")
            item2_data["id"] = "item2_env_test"
            item2_data["collection"] = coll_id # Ensure it's part of the created collection
            # Ensure properties exist if we were to modify datetime, test_item2.json might be complete
            # For simplicity, assume test_item.json and test_item2.json are distinct enough
            # or that pgstac token generation handles identical datetimes if they occur.

            resp = client.post(f"/collections/{coll_id}/items", json=item2_data)
            assert resp.status_code == 201, resp.text

            # Perform a search that should result in a 'next' link
            search_path = "/search"
            response = client.get(f"{search_path}?limit=1&collections={coll_id}")

            assert response.status_code == 200, f"Response content: {response.text}"
            response_json = response.json()

            links = response_json.get("links", [])
            next_link_href = None
            for link_obj in links:
                if link_obj.get("rel") == "next":
                    next_link_href = link_obj.get("href")
                    break

            assert next_link_href is not None, "No 'next' link found in response."

            # Expected: http://testserver/custom/api/root/search?limit=1&collections=...&token=...
            # The app.root_path is /custom/api/root.
            # TestClient sends requests to paths like /search.
            # FastAPI routes /search to the endpoint.
            # Inside links.py:
            #   request.app.root_path == "/custom/api/root"
            #   request.scope['root_path'] == "/custom/api/root" (TestClient sets this from app.root_path)
            #   This means the `if scope_rp and not app_rp:` block in BaseLinks.url is SKIPPED.
            #   path = request.url.path (e.g., "/custom/api/root/search")
            #   base_url = get_base_url(request) (e.g., "http://testserver/custom/api/root" if get_base_url includes root_path)
            #   If get_base_url includes root_path, then:
            #     urljoin("http://testserver/custom/api/root", "custom/api/root/search".lstrip("/"))
            #     -> "http://testserver/custom/api/root/custom/api/root/search" (BUGGY)
            #   If get_base_url does NOT include root_path (e.g. "http://testserver"):
            #     urljoin("http://testserver", "custom/api/root/search".lstrip("/"))
            #     -> "http://testserver/custom/api/root/search" (CORRECT)

            parsed_url = urlparse(next_link_href)

            # Assert the generated link is correct and not duplicated
            expected_path = f"{custom_root}/search" # e.g., /custom/api/root/search
            assert parsed_url.path == expected_path, \
                f"Parsed path '{parsed_url.path}' is not equal to expected '{expected_path}'. " \
                f"This might indicate duplication or mis-stripping of root_path. Full href: {next_link_href}"

            assert "token=" in parsed_url.query, f"No token found in next link query: {parsed_url.query}"
            # limit=1 and collections={coll_id} should also be in the query, possibly before the token
            assert "limit=1" in parsed_url.query
            assert f"collections={coll_id}" in parsed_url.query


    finally:
        await close_db_connection(global_pgstac_app)
