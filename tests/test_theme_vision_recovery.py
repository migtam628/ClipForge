"""Regression tests: reliable theme switching and local vision model preflight."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge import app as app_module
from clipforge import storyboard

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("ready_state", ["interactive", "loading"])
def test_theme_picker_works_in_both_dom_ready_states(ready_state):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed on this test runner")
    script = ROOT / "clipforge" / "static" / "theme.js"
    harness = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const storage = new Map();
const els = {};
function make(id, opts) {
  const e = {value: id, dataset: {}, handlers: {},
    attributes: {}, classList: {toggle(n,v) {e.classes[n] = v;}},
    classes: {}, setAttribute(n,v) {this.attributes[n]=v;},
    addEventListener(n,cb) {this.handlers[n]=cb;}};
  if (opts) e.dataset.themeChoice = id;
  return e;
}
els.select = make('precision');
els.precision = make('precision', true);
els.editorial = make('editorial', true);
els.control = make('control', true);
els.reset = make('reset');
const doc = {
  readyState: process.argv[2],
  documentElement: {dataset: {}, style: {}},
  handlers: {},
  addEventListener(n,cb) {this.handlers[n] = cb;},
  querySelectorAll(selector) {
    return selector === '[data-theme-select]' ? [els.select] :
      selector === '[data-theme-choice]'
      ? [els.precision, els.editorial, els.control] : [];
  },
  getElementById(id) {return id === 'theme-reset' ? els.reset : null;}
};
const context = {document: doc, localStorage: {
  getItem(k) {return storage.get(k) ?? null;},
  setItem(k,v) {storage.set(k,v);}
}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
if (doc.readyState === 'loading') {
  assert.ok(doc.handlers.DOMContentLoaded);
  doc.handlers.DOMContentLoaded();
}
assert.equal(doc.documentElement.dataset.theme, 'precision');
assert.equal(typeof els.select.handlers.change, 'function');
els.select.value = 'editorial';
els.select.handlers.change();
assert.equal(doc.documentElement.dataset.theme, 'editorial');
assert.equal(doc.documentElement.style.colorScheme, 'light');
assert.equal(els.editorial.attributes['aria-pressed'], 'true');
assert.equal(storage.get('clipforge.appearance.theme.v2'), 'editorial');
els.control.handlers.click();
assert.equal(doc.documentElement.dataset.theme, 'control');
assert.equal(els.control.attributes['aria-pressed'], 'true');
els.reset.handlers.click();
assert.equal(doc.documentElement.dataset.theme, 'precision');
console.log('theme switching OK');
"""
    outcome = subprocess.run(
        [node, "-e", harness, str(script), ready_state],
        capture_output=True, text=True, check=False, timeout=12,
    )
    assert outcome.returncode == 0, outcome.stderr


def test_vision_status_finds_installed_ollama_tag(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("CLIPFORGE_VISION_MODEL", "moondream")
    def fake_open(url, timeout):
        assert url == "http://ollama:11434/api/tags"
        return io.BytesIO(json.dumps({"models": [
            {"name": "llama3.2:3b"}, {"name": "moondream:latest"},
        ]}).encode())
    monkeypatch.setattr(storyboard, "urlopen", fake_open)
    result = storyboard.vision_status()
    assert result["installed"] is True
    assert result["status"] == "ready"


def test_missing_vision_model_provides_install_command(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("CLIPFORGE_VISION_MODEL", "moondream")
    monkeypatch.setattr(
        storyboard, "urlopen",
        lambda url, timeout: io.BytesIO(b'{"models":[{"name":"llama3.2:3b"}]}'),
    )
    status = storyboard.vision_status()
    assert not status["installed"]
    assert status["status"] == "missing"
    assert "ollama pull moondream" in status["note"]


def test_resume_missing_model_returns_409_without_queuing(monkeypatch, tmp_path):
    opts = app_module.Options(narration="script", narration_basis="video")
    saved = {"status": "failed", "resume_count": 4, "options": opts.model_dump()}
    monkeypatch.setattr(app_module, "job_folder", lambda job_id: tmp_path)
    monkeypatch.setattr(app_module, "_read", lambda folder: saved)
    monkeypatch.setattr(app_module, "_vision_preflight",
                        lambda options: {"note": "Local vision model not installed. "
                                                  "Run ollama pull moondream"})
    client = TestClient(app_module.app)
    response = client.post("/api/jobs/" + "a" * 32 + "/resume")
    assert response.status_code == 409
    assert "ollama pull moondream" in response.json()["detail"]
    assert saved["status"] == "failed"
    assert saved["resume_count"] == 4


def test_transcript_only_mode_never_requires_visual_model(monkeypatch):
    monkeypatch.setattr(app_module, "vision_status",
                        lambda: pytest.fail("Unnecessary Ollama network request"))
    assert app_module._vision_preflight(app_module.Options(
        narration="script", narration_basis="speech")) is None
    assert app_module._vision_preflight(app_module.Options(
        narration="none", narration_basis="video")) is None
