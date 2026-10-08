import re

from .fakes import sql_block
from .test_api import api, ask  # noqa: F401  (api is a fixture)


def test_index_served_with_security_headers(api):
    r = api.get("/")
    assert r.status_code == 200 and "Ask the Bike Data" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"


def test_static_assets_served(api):
    assert api.get("/ui/app.js").status_code == 200
    assert api.get("/ui/style.css").status_code == 200
    assert api.get("/ui/../app/main.py").status_code in (400, 404)


def test_html_has_nothing_the_csp_would_block(api):
    html = api.get("/").text
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html)


def test_docs_not_given_the_ui_csp(api):
    assert "content-security-policy" not in api.get("/docs").headers


def test_failed_flag_after_repeated_execution_errors(api):
    api.install_llm([sql_block("SELECT nope FROM trips"), sql_block("SELECT nope2 FROM trips"), "Sorry."])
    body = ask(api).json()
    assert body["failed"] is True and body["blocked"] is False


def test_sql_is_pretty_printed_for_display(api):
    api.install_llm([sql_block("SELECT member_casual, COUNT(*) AS n FROM trips WHERE rideable_type = 'classic_bike' GROUP BY member_casual"), "ok"])
    body = ask(api).json()
    assert "\n" in body["sql"] and body["failed"] is False


def test_head_request_to_index_works(api):
    assert api.head("/").status_code == 200
