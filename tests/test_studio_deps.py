"""The Sanity Studio pins adm-zip (a transitive dep of @sanity/runtime-cli) above the
GHSA-7q85-xj36-vmfc / GHSA-8238-w5pm-2374 / GHSA-c6fg-446q-cg94 fixes (>= 0.6.1)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.join(os.path.dirname(HERE), "sanity", "studio")


def _read(name):
    with open(os.path.join(STUDIO, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_adm_zip_is_pinned_to_a_fixed_version():
    pkg = _read("package.json")
    lock = _read("package-lock.json")

    assert pkg["overrides"]["adm-zip"] == "0.6.1"
    resolved = lock["packages"]["node_modules/adm-zip"]
    assert resolved["version"] == "0.6.1"
    assert resolved["resolved"].endswith("/adm-zip-0.6.1.tgz")


def test_js_yaml_is_pinned_to_a_fixed_version():
    pkg = _read("package.json")
    lock = _read("package-lock.json")

    assert pkg["overrides"]["js-yaml"] == "3.15.2"
    resolved = lock["packages"]["node_modules/js-yaml"]
    assert resolved["version"] == "3.15.2"
    assert resolved["resolved"].endswith("/js-yaml-3.15.2.tgz")


def test_source_map_js_is_resolved_to_a_fixed_version():
    lock = _read("package-lock.json")

    resolved = lock["packages"]["node_modules/source-map-js"]
    assert resolved["version"] == "1.2.2"
    assert resolved["resolved"].endswith("/source-map-js-1.2.2.tgz")


def test_sprintf_js_is_resolved_to_a_fixed_version():
    pkg = _read("package.json")
    lock = _read("package-lock.json")

    assert pkg["overrides"]["sprintf-js"] == "1.1.3"
    resolved = lock["packages"]["node_modules/sprintf-js"]
    assert resolved["version"] == "1.1.3"
    assert resolved["resolved"].endswith("/sprintf-js-1.1.3.tgz")
