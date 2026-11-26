import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_static_ml_studio_exposes_generator_training_form():
    html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

    assert 'id="ml-generator-form"' in html
    assert 'onsubmit="App.runMLGeneratorExperiment(event)"' in html
    assert 'id="ml-generator-model"' in html
    assert 'id="ml-generator-result"' in html
    assert "runMLGeneratorExperiment(event)" in app_js
    assert "/api/ml/generator/run-experiment" in app_js
    assert "await this.fetchPaperGeneratorModels()" in app_js
    assert "/api/ml/generator-models" in app_js
    assert "generatorData.models.length" in app_js
    # The version query string is a cache-buster that changes on every UI release,
    # so assert the script is referenced with one rather than pinning its value.
    assert re.search(r"/static/js/app\.js\?v=\d{8}-\d+", html)


def test_static_paper_config_keeps_generator_and_telegram_controls_separate():
    html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

    assert 'id="paper-config-generator-model"' in html
    assert 'id="paper-config-active"' in html
    assert 'id="paper-config-telegram"' in html
    assert "ml_generator_model_id: generatorModelId || null" in app_js
    assert "telegram_enabled: checked(\"paper-config-telegram\")" in app_js
    assert 'strategy === "ml_signal_generator_v1"' in app_js
