# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import requests


PROVIDER_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROVIDER_ROOT / "skills" / "form-designer"
SCRIPTS_DIR = SKILL_ROOT / "scripts"


class FakeResponse:
    """Minimal requests response double used by form designer tests."""

    def __init__(self, payload, *, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload, ensure_ascii=False)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def run_main(module_path: Path, argv: list[str], monkeypatch, *, fake_get=None):
    module_name = f"test_{module_path.stem}_module"
    monkeypatch.setenv("CMP_URL", "https://cmp.example.com")
    monkeypatch.setenv("CMP_COOKIE", "CloudChef-Authenticate=test-token")
    if fake_get is not None:
        monkeypatch.setattr(requests, "get", fake_get)

    stdout = io.StringIO()
    stderr = io.StringIO()
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            spec.loader.exec_module(module)
            exit_code = module.main(argv)
    finally:
        sys.modules.pop(module_name, None)

    return exit_code, stdout.getvalue(), stderr.getvalue()


def extract_meta(stderr: str, block_name: str):
    match = re.search(rf"##{block_name}_START##\s*(.*?)\s*##{block_name}_END##", stderr, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def run_node_expression(expression: str, setup_js: str) -> dict:
    if shutil.which("node") is None:
        raise AssertionError("node is required for JavaScript expression regression tests")
    script = f"""
const fn = ({expression});
{setup_js}
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_visible_value_expression_field(field: dict):
    assert field.get("hidden") is not True
    assert field.get("condition") != "1 === 2"
    assert field["widget"] == {"id": "string"}
    assert field.get("hideTitle") is not True
    input_class = field.get("inputClass", "")
    assert "hidden" not in input_class.split()


def test_skill_metadata_converges_to_generic_javascript_extensions():
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow_text = (SKILL_ROOT / "references" / "WORKFLOW.md").read_text(encoding="utf-8")
    design_text = (SCRIPTS_DIR / "design_form.py").read_text(encoding="utf-8")
    normalize_text = (SCRIPTS_DIR / "_schema_normalize.py").read_text(encoding="utf-8")
    value_expression_text = (SCRIPTS_DIR / "_value_expressions.py").read_text(encoding="utf-8")
    combined = "\n".join((skill_text, workflow_text, design_text, normalize_text, value_expression_text))

    for marker in (
        "General JavaScript Form Extensions",
        "`config.value.expression`",
        "requested_fields_json",
        "Prefer explicit field-level JavaScript",
        "value_expressions_json is an optional compatibility helper",
        "Do not use value_expressions_json as the default path",
        "Do not rely on catalog_fields_json to satisfy catalog context needs",
        "The design tool rejects literal ellipsis placeholder JavaScript",
        "All string values inside the returned JSON are opaque",
    ):
        assert marker in skill_text

    for marker in (
        "User wording",
        "The script layer is not the source of business semantics",
        "deterministic compatibility helper",
        "empty string until at least one source value resolves",
    ):
        assert marker in workflow_text

    for removed_marker in (
        "catalog_context_sync_json",
        "--catalog-context-sync-json",
        "CATALOG_CONTEXT_SYNC_TEMPLATE_V1",
        "FIELD_SPECS",
        "catalog-context-expression.js",
        "apply_catalog_context_sync",
        "parts.join",
        "schemaFormValid",
        "AUTO_SYNC_PENDING",
    ):
        assert removed_marker not in combined

def test_design_form_script_does_not_add_schema_form_valid_by_default(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "mixture": {
                "id": "mixture",
                "type": "string",
                "widget": {"id": "string"},
            }
        },
    }

    exit_code, _stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema),
        ],
        monkeypatch,
    )

    assert exit_code == 0
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    assert meta["schema"]["properties"]["mixture"]["id"] == "mixture"
    assert "schemaFormValid" not in meta["schema"]["properties"]


def test_normalize_schema_preserves_handwritten_dynamic_js_without_auto_sync_rewrite():
    module = load_module("schema_normalize_generic_js", SCRIPTS_DIR / "_schema_normalize.py")
    expression = (
        "function(model){var FIELD_SPECS=[{state:'owner',output:'Owner'}];"
        "var out={Owner:{value:model.owner}};"
        "if(!model.owner)return 'AUTO_SYNC_PENDING';"
        "return JSON.stringify(out);}"
    )
    schema = {
        "type": "object",
        "properties": {
            "expansion": {
                "id": "expansion",
                "type": "string",
                "hidden": True,
                "condition": "1 === 2",
                "widget": {"id": "hidden"},
                "config": {
                    "value": {
                        "source": "mock",
                        "method": "mock",
                        "expression": expression,
                    }
                },
            },
            "serverName": {
                "id": "serverName",
                "type": "string",
                "widget": {"id": "string"},
            },
        },
        "fieldsets": [{"id": "base", "fields": ["serverName"]}],
        "widget": {"id": "object"},
    }

    normalized, warnings = module.normalize_schema(schema)

    field = normalized["properties"]["expansion"]
    assert field["hidden"] is True
    assert field["condition"] == "1 === 2"
    assert field["widget"]["id"] == "hidden"
    assert field["config"]["value"]["expression"] == expression
    assert "default" not in field
    assert "schemaFormValid" not in normalized["properties"]
    assert normalized["fieldsets"][0]["fields"] == ["serverName"]
    assert not any("auto-sync" in warning.lower() for warning in warnings)
    assert not any("catalog context" in warning.lower() for warning in warnings)


def test_normalize_schema_repairs_basic_top_level_field_and_preserves_unknowns():
    module = load_module("schema_normalize_basic", SCRIPTS_DIR / "_schema_normalize.py")
    schema = {
        "schema": {
            "properties": {
                "serverName": {
                    "title": "Server Name",
                    "widget": {"id": "text", "selectDatas": [{"label": "A", "value": "a"}]},
                    "x-custom": {"keep": True},
                }
            }
        }
    }

    normalized, warnings = module.normalize_schema(schema)

    field = normalized["properties"]["serverName"]
    assert normalized["type"] == "object"
    assert normalized["widget"]["id"] == "object"
    assert field["id"] == "serverName"
    assert field["index"] == 0
    assert field["type"] == "string"
    assert field["widget"]["id"] == "string"
    assert field["selectDatas"] == [{"label": "A", "value": "a"}]
    assert field["x-custom"] == {"keep": True}
    assert field["config"]["visibility"]["allowInRequest"] is True
    assert field["config"]["visibility"]["allowInApproval"] is True
    assert any("Unwrapped top-level schema container" in warning for warning in warnings)


def test_normalize_schema_promotes_widget_config_to_field_config():
    module = load_module("schema_normalize_widget_config", SCRIPTS_DIR / "_schema_normalize.py")
    expression = "function(model, sourceParams, schema, unused, cfg) { return model.projects || ''; }"
    schema = {
        "type": "object",
        "properties": {
            "expansion": {
                "id": "expansion",
                "type": "string",
                "widget": {
                    "id": "string",
                    "config": {
                        "readonly": True,
                        "visibility": {"allowInRequest": False},
                        "value": {
                            "source": "mock",
                            "method": "mock",
                            "expression": expression,
                        },
                    },
                },
                "config": {"visibility": {"allowInApproval": True}},
            }
        },
        "widget": {"id": "object"},
    }

    normalized, warnings = module.normalize_schema(schema)

    field = normalized["properties"]["expansion"]
    assert "config" not in field["widget"]
    assert field["config"]["readonly"] is True
    assert field["config"]["visibility"] == {
        "allowInRequest": False,
        "allowInApproval": True,
    }
    assert field["config"]["value"]["expression"] == expression
    assert any("Moved widget.config to field-level config" in warning for warning in warnings)


def test_normalize_schema_warns_for_risky_javascript_expression():
    module = load_module("schema_normalize_risky_js", SCRIPTS_DIR / "_schema_normalize.py")
    schema = {
        "type": "object",
        "properties": {
            "danger": {
                "id": "danger",
                "type": "string",
                "widget": {"id": "string"},
                "config": {
                    "value": {
                        "source": "mock",
                        "method": "mock",
                        "expression": "function(model){ return eval(model.payload); }",
                    }
                },
            }
        },
        "widget": {"id": "object"},
    }

    _normalized, warnings = module.normalize_schema(schema)

    assert any("eval" in warning for warning in warnings)


def test_design_form_script_rejects_abbreviated_javascript_expression(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "mixture": {
                "type": "string",
                "widget": {"id": "textarea"},
                "config": {
                    "value": {
                        "source": "mock",
                        "method": "mock",
                        "expression": "function(model, sourceParams, schema, unused, cfg) { ... }",
                    }
                },
            }
        },
    }

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema),
        ],
        monkeypatch,
    )

    assert exit_code == 1
    assert "literal ellipsis placeholder" in stdout
    assert "Schema JSON:" not in stdout
    assert "FORM_DESIGN_META" not in stderr


def test_design_form_script_rejects_mixed_value_expression_shapes(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "mixture": {
                "id": "mixture",
                "type": "string",
                "widget": {"id": "string"},
            }
        },
        "widget": {"id": "object"},
    }

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema, ensure_ascii=False),
            "--value-expressions-json",
            json.dumps(
                [
                    {
                        "fieldKey": "mixture",
                        "fields": [{"label": "key1", "field": "name"}],
                        "compose": {"key1": {"field": "name"}},
                    }
                ],
                ensure_ascii=False,
            ),
        ],
        monkeypatch,
    )

    assert exit_code == 1
    assert "exactly one of fields or compose" in stdout
    assert "Schema JSON:" not in stdout
    assert "FORM_DESIGN_META" not in stderr


def test_schema_scripts_does_not_write_to_ng_model_substring_match():
    module = load_module("schema_scripts_target_substring_match", SCRIPTS_DIR / "_schema_scripts.py")

    expression = module.build_model_composition_expression(
        {"value": {"$literal": "computed"}},
        target_field_key="name",
    )
    result = run_node_expression(
        expression,
        """
const username = {
  value: '',
  getAttribute: function(name) {
    return name === 'ng-model' ? 'vm.username' : '';
  },
  closest: function() { return null; },
  dispatchEvent: function() {}
};
global.Event = function(type, opts) { return {type, opts}; };
global.document = {
  querySelectorAll: function(selector) {
    if (selector === 'input,textarea,select') return [username];
    return [];
  }
};
const model = {};
const out = fn(model, {}, {}, null, {});
console.log(JSON.stringify({out: out, model: model, usernameValue: username.value}));
""",
    )

    assert result["out"] == "{\"value\":\"computed\"}"
    assert result["model"]["name"] == "{\"value\":\"computed\"}"
    assert result["usernameValue"] == ""


def test_schema_scripts_does_not_publish_empty_json_when_sources_are_unresolved():
    module = load_module("schema_scripts_empty_sources", SCRIPTS_DIR / "_schema_scripts.py")

    expression = module.build_model_composition_expression(
        {
            "application": {"$path": "catalogServiceRequest.exts.project.name"},
            "owner": {"$path": "catalogServiceRequest.exts.owner.name"},
        },
        target_field_key="mixture",
    )
    result = run_node_expression(
        expression,
        """
const model = {mixture: '{"application":"old"}'};
const out = fn(model, {}, {}, null, {});
console.log(JSON.stringify({out: out, model: model}));
""",
    )

    assert result["out"] == ""
    assert result["model"]["mixture"] == ""
    assert result["out"] != "{\"application\":\"\",\"owner\":\"\"}"


def test_design_form_script_can_generate_projection_field_with_arbitrary_target_key(monkeypatch):
    schema = {"type": "object", "properties": {}, "widget": {"id": "object"}}
    target_key = "customTargetField"

    exit_code, _stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema),
            "--value-expressions-json",
            json.dumps(
                [
                    {
                        "fieldKey": target_key,
                        "title": "Custom Target Field",
                        "fields": [
                            {"label": "名称", "field": "名称"},
                            {"label": "所有者", "field": "所有者", "path": "owners.name"},
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
        ],
        monkeypatch,
    )

    assert exit_code == 0
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    target_field = meta["schema"]["properties"][target_key]
    assert set(meta["schema"]["properties"]) == {target_key}
    assert "mixture" not in meta["schema"]["properties"]
    value_config = target_field["config"]["value"]
    expression = value_config["expression"]
    assert target_field["id"] == target_key
    assert target_field["title"] == "Custom Target Field"
    assert_visible_value_expression_field(target_field)
    assert "modification" not in target_field["config"]
    assert "default" not in target_field
    assert "defaultValue" not in target_field
    assert value_config["source"] == "mock"
    assert value_config["method"] == "mock"
    assert "function hideUi" not in expression
    assert "\n" not in expression
    assert '"名称":{"$paths":["name","Name"],"$label":"名称","$labels":["名称","Name","name","catalog name","service catalog name"]}' in expression
    assert '"所有者":{"$path":"owners.name","$label":"所有者"}' in expression
    assert "fetch" not in expression
    assert "XMLHttpRequest" not in expression


def test_schema_scripts_writes_target_dom_without_hiding_by_default():
    module = load_module("schema_scripts_write_target_dom", SCRIPTS_DIR / "_schema_scripts.py")

    expression = module.build_model_composition_expression(
        {
            "名称": {"$paths": ["name", "Name"], "$label": "名称"},
            "所有者": {
                "$paths": ["catalogServiceRequest.exts.owner.name", "owners"],
                "$label": "所有者",
            },
            "应用系统": {
                "$paths": ["catalogServiceRequest.exts.project.name", "projects"],
                "$label": "应用系统",
            },
        },
        target_field_key="mixture",
    )
    result = run_node_expression(
        expression,
        """
const hiddenBox = {
  style: {},
  attrs: {},
  setAttribute: function(k, v) { this.attrs[k] = v; }
};
const blocks = [
  { textContent: '所有者\\n平台管理员 (admin)', innerText: '所有者\\n平台管理员 (admin)', querySelector: function() { return null; } },
  { textContent: '应用系统\\nEIP应用', innerText: '应用系统\\nEIP应用', querySelector: function() { return null; } }
];
const target = {
  value: '',
  attrs: {},
  getAttribute: function(name) { return name === 'name' ? 'mixture' : ''; },
  setAttribute: function(k, v) { this.attrs[k] = v; },
  closest: function(selector) {
    if (selector === '[data-key]') return null;
    return hiddenBox;
  },
  dispatchEvent: function() {}
};
global.Event = function(type, opts) { return {type, opts}; };
global.document = {
  querySelectorAll: function(selector) {
    if (selector === 'input,textarea,select') return [target];
    return blocks;
  }
};
const model = {name: 'test-ui'};
const out = fn(model, {}, {}, null, {});
console.log(JSON.stringify({out: out, model: model, targetValue: target.value, boxStyle: hiddenBox.style, boxAttrs: hiddenBox.attrs, targetAttrs: target.attrs}));
""",
    )

    assert result["out"] == "{\"名称\":\"test-ui\",\"所有者\":\"平台管理员 (admin)\",\"应用系统\":\"EIP应用\"}"
    assert result["model"]["mixture"] == result["out"]
    assert result["targetValue"] == result["out"]
    assert result["boxStyle"] == {}
    assert "aria-hidden" not in result["boxAttrs"]
    assert "tabindex" not in result["targetAttrs"]


def test_schema_scripts_reads_catalog_values_from_nested_request_contexts():
    module = load_module("schema_scripts_nested_request_contexts", SCRIPTS_DIR / "_schema_scripts.py")

    expression = module.build_model_composition_expression(
        {
            "applicationSystem": {
                "$paths": ["catalogServiceRequest.exts.project.name", "projects"],
                "$label": "applicationSystem",
            },
            "businessGroup": {
                "$paths": [
                    "catalogServiceRequest.exts.businessGroup.name",
                    "businessGroupName",
                    "businessGroup",
                ],
                "$label": "businessGroup",
            },
        },
        target_field_key="expansion",
        output_type="object",
    )
    result = run_node_expression(
        expression,
        """
const cases = [
  {
    label: 'genericRequest catalogServiceRequest',
    sourceParams: {
      genericRequest: {
        catalogServiceRequest: {
          exts: {
            project: {name: 'EIP app'},
            businessGroup: {name: 'R&D group'}
          }
        }
      }
    }
  },
  {
    label: 'params genericRequest processForm',
    sourceParams: {
      params: {
        genericRequest: {
          processForm: {
            projects: {name: 'EIP app'},
            businessGroup: {name: 'R&D group'}
          }
        }
      }
    }
  }
];
const results = cases.map(function(item) {
  const model = {};
  const out = fn(model, item.sourceParams, {}, null, {});
  return {label: item.label, out: out, model: model};
});
console.log(JSON.stringify({results: results}));
""",
    )

    expected = {"applicationSystem": "EIP app", "businessGroup": "R&D group"}
    for item in result["results"]:
        assert item["out"] == expected
        assert item["model"]["expansion"] == expected


def test_schema_scripts_exact_path_does_not_fallback_to_parent_object_text():
    module = load_module("schema_scripts_exact_path_no_parent_fallback", SCRIPTS_DIR / "_schema_scripts.py")

    expression = module.build_model_composition_expression(
        {"code": {"$path": "application.code"}},
        target_field_key="mixture",
    )
    result = run_node_expression(
        expression,
        """
const model = {};
const sourceParams = {application: {name: 'EIP应用'}};
const out = fn(model, sourceParams, {}, null, {});
console.log(JSON.stringify({out: out, model: model}));
""",
    )

    assert result["out"] == ""
    assert result["model"]["mixture"] == ""


def test_design_form_script_sets_object_value_expression_target_type(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "payload": {
                "id": "payload",
                "type": "string",
                "widget": {"id": "string"},
            }
        },
        "fieldsets": [{"id": "base", "fields": ["payload"]}],
        "widget": {"id": "object"},
    }

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema, ensure_ascii=False),
            "--value-expressions-json",
            json.dumps(
                [
                    {
                        "fieldKey": "payload",
                        "valueType": "object",
                        "compose": {
                            "名称": {"$field": "名称"},
                            "所有者": {"$field": "所有者"},
                        },
                    }
                ],
                ensure_ascii=False,
            ),
        ],
        monkeypatch,
    )

    assert exit_code == 0, stdout
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    field = meta["schema"]["properties"]["payload"]
    assert field["type"] == "object"
    assert field["widget"] == {"id": "object"}
    expression = field["config"]["value"]["expression"]
    result = run_node_expression(
        expression,
        """
const model = {name: 'test-eip', owners: 'admin'};
const out = fn(model, {}, {}, null, {});
console.log(JSON.stringify({out: out, model: model, outType: typeof out}));
""",
    )

    assert result["outType"] == "object"
    assert result["out"] == {"名称": "test-eip", "所有者": "admin"}
    assert result["model"]["payload"] == {"名称": "test-eip", "所有者": "admin"}


def test_design_form_script_constrains_schema_to_requested_field_set(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "sourceName": {
                "id": "sourceName",
                "type": "string",
                "widget": {"id": "string"},
            },
            "sourceOwner": {
                "id": "sourceOwner",
                "type": "string",
                "widget": {"id": "string"},
            },
            "targetOne": {
                "id": "targetOne",
                "type": "string",
                "widget": {"id": "string"},
            },
            "targetTwo": {
                "id": "targetTwo",
                "type": "string",
                "widget": {"id": "string"},
            },
            "targetThree": {
                "id": "targetThree",
                "type": "object",
                "widget": {"id": "object"},
            },
        },
        "fieldsets": [
            {
                "id": "base",
                "fields": ["sourceName", "targetOne", "sourceOwner", "targetTwo", "targetThree"],
            }
        ],
        "widget": {"id": "object"},
    }

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema, ensure_ascii=False),
            "--requested-fields-json",
            json.dumps(["targetOne", "targetTwo", "targetThree"], ensure_ascii=False),
            "--value-expressions-json",
            json.dumps(
                [
                    {
                        "fieldKey": "targetOne",
                        "compose": {"$concat": [{"$path": "source.name"}]},
                    },
                    {
                        "fieldKey": "targetTwo",
                        "compose": {"$concat": [{"$path": "source.owner"}]},
                    },
                    {
                        "fieldKey": "targetThree",
                        "compose": {
                            "name": {"$path": "source.name"},
                            "owner": {"$path": "source.owner"},
                        },
                    },
                ],
                ensure_ascii=False,
            ),
        ],
        monkeypatch,
    )

    assert exit_code == 0, stdout
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    assert list(meta["schema"]["properties"]) == ["targetOne", "targetTwo", "targetThree"]
    assert meta["schema"]["fieldsets"][0]["fields"] == ["targetOne", "targetTwo", "targetThree"]
    assert any("Removed unrequested schema properties" in warning for warning in meta["warnings"])
    assert any("Removed unrequested fieldset references" in warning for warning in meta["warnings"])


def test_design_form_script_rejects_missing_requested_schema_fields(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "targetOne": {
                "id": "targetOne",
                "type": "string",
                "widget": {"id": "string"},
            }
        },
        "fieldsets": [{"id": "base", "fields": ["targetOne"]}],
        "widget": {"id": "object"},
    }

    exit_code, stdout, _stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema, ensure_ascii=False),
            "--requested-fields-json",
            json.dumps(["targetOne", "targetTwo"], ensure_ascii=False),
        ],
        monkeypatch,
    )

    assert exit_code == 1
    assert "schema.properties is missing requested fields: 'targetTwo'" in stdout


def test_design_form_script_does_not_treat_code_suffixes_as_catalog_aliases(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "expansion": {
                "id": "expansion",
                "index": 0,
                "type": "string",
                "widget": {"id": "string"},
            }
        },
        "widget": {"id": "object"},
    }

    exit_code, _stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema, ensure_ascii=False),
            "--value-expressions-json",
            json.dumps(
                [
                    {
                        "fieldKey": "expansion",
                        "compose": {
                            "应用系统": {"$field": "application.code"},
                            "业务组": {"$field": "businessGroup.code"},
                        },
                    }
                ],
                ensure_ascii=False,
            ),
        ],
        monkeypatch,
    )

    assert exit_code == 0
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    expression = meta["schema"]["properties"]["expansion"]["config"]["value"]["expression"]
    assert "catalogServiceRequest.exts.project.code" not in expression
    assert "catalogServiceRequest.exts.businessGroup.code" not in expression
    assert "applicationCode" not in expression
    assert "businessGroupCode" not in expression
    result = run_node_expression(
        expression,
        """
const model = {};
const sourceParams = {
  Projects: {name: 'EIP应用'},
  businessGroupName: '研发业务组',
  applicationCode: 'APP-CODE',
  businessGroupCode: 'BG-CODE'
};
const out = fn(model, sourceParams, {}, null, {});
console.log(JSON.stringify({out: out, model: model}));
""",
    )

    assert result["out"] == ""
    assert result["model"]["expansion"] == ""


def test_design_form_script_can_insert_catalog_standard_fields(monkeypatch):
    schema = {"type": "object", "properties": {}, "widget": {"id": "object"}}

    exit_code, _stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
            [
                "--mode",
                "new",
                "--schema-json",
                json.dumps(schema),
                "--catalog-fields-json",
                json.dumps([{"field": "businessGroup"}, {"field": "projects"}]),
            ],
            monkeypatch,
        )

    assert exit_code == 0
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    properties = meta["schema"]["properties"]
    assert "businessGroup" in properties
    assert "projects" in properties
    assert properties["businessGroup"]["x-smartcmp"]["builtinCatalogField"] == "businessGroup"


def test_design_form_script_ignores_catalog_fields_outside_fixed_set(monkeypatch):
    schema = {"type": "object", "properties": {}, "widget": {"id": "object"}}

    exit_code, _stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "new",
            "--schema-json",
            json.dumps(schema),
            "--catalog-fields-json",
            json.dumps([{"field": "attachments"}]),
        ],
        monkeypatch,
    )

    assert exit_code == 0
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    assert meta["schema"]["properties"] == {}
    assert any("attachments" in warning for warning in meta["warnings"])


def test_normalize_schema_still_repairs_table_array_fields():
    module = load_module("schema_normalize_table_array", SCRIPTS_DIR / "_schema_normalize.py")
    schema = {
        "type": "object",
        "properties": {
            "servers": {
                "id": "servers",
                "type": "array",
                "widget": {"id": "array"},
                "items": {
                    "properties": {
                        "name": {"widget": {"id": "text"}},
                        "description": {},
                    }
                },
            }
        },
        "widget": {"id": "object"},
    }

    normalized, warnings = module.normalize_schema(schema)

    field = normalized["properties"]["servers"]
    assert field["widget"]["id"] == "table-head"
    assert field["items"]["type"] == "object"
    assert field["items"]["widget"]["id"] == "table-body"
    assert field["items"]["properties"]["name"]["type"] == "string"
    assert field["items"]["properties"]["name"]["widget"]["id"] == "string"
    assert field["items"]["fieldsets"][0]["fields"] == ["name", "description"]
    assert any("Changed widget.id=array to table-head" in warning for warning in warnings)


def test_design_form_modify_mode_can_read_and_normalize_source_when_schema_omitted(monkeypatch):
    def fake_get(url, headers=None, timeout=None, **kwargs):
        return FakeResponse(
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "Original",
                "content": {"schema": {"properties": {"owner": {"title": "Owner"}}}},
            }
        )

    exit_code, _stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "modify",
            "--form-url",
            "https://cmp.example.com/#/main/service-model/forms/edit/123e4567-e89b-12d3-a456-426614174000",
        ],
        monkeypatch,
        fake_get=fake_get,
    )

    assert exit_code == 0
    meta = extract_meta(stderr, "FORM_DESIGN_META")
    assert meta["source"]["formId"] == "123e4567-e89b-12d3-a456-426614174000"
    assert meta["schema"]["properties"]["owner"]["id"] == "owner"


def test_design_form_regenerate_with_form_url_marks_schema_as_manual_replacement(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "expansion": {
                "id": "expansion",
                "type": "string",
                "widget": {"id": "string"},
            }
        },
        "widget": {"id": "object"},
    }

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "regenerate",
            "--form-url",
            "https://cmp.example.com/#/main/service-model/forms/edit/123e4567-e89b-12d3-a456-426614174000",
            "--schema-json",
            json.dumps(schema),
            "--change-summary",
            "Regenerated expansion.",
        ],
        monkeypatch,
    )

    assert exit_code == 0
    assert "does not save changes to CMP" in stdout
    assert "replacement Schema JSON" in stdout
    assert "FORM_DESIGN_META" in stderr


def test_design_form_modify_mode_requires_schema_json_for_value_expression_changes(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls.append(url)
        return FakeResponse({})

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "design_form.py",
        [
            "--mode",
            "modify",
            "--form-url",
            "https://cmp.example.com/#/main/service-model/forms/edit/123e4567-e89b-12d3-a456-426614174000",
            "--value-expressions-json",
            json.dumps(
                [{"fieldKey": "expansion", "compose": {"名称": {"$field": "name"}}}],
                ensure_ascii=False,
            ),
        ],
        monkeypatch,
        fake_get=fake_get,
    )

    assert exit_code == 1
    assert calls == []
    assert "schema_json is required for modify mode when applying value_expressions_json" in stdout
    assert "FORM_DESIGN_META" not in stderr


def test_read_form_script_reads_and_normalizes_existing_form(monkeypatch):
    def fake_get(url, headers=None, timeout=None, **kwargs):
        return FakeResponse(
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "Existing",
                "content": {"schema": {"properties": {"owner": {"title": "Owner"}}}},
            }
        )

    exit_code, stdout, stderr = run_main(
        SCRIPTS_DIR / "read_form.py",
        [
            "https://cmp.example.com/#/main/service-model/forms/edit/123e4567-e89b-12d3-a456-426614174000"
        ],
        monkeypatch,
        fake_get=fake_get,
    )

    assert exit_code == 0
    assert "SmartCMP Form: Existing" in stdout
    meta = extract_meta(stderr, "FORM_SCHEMA_META")
    assert meta["schema"]["properties"]["owner"]["id"] == "owner"


def test_form_designer_scripts_do_not_use_cmp_write_methods():
    for script_path in SCRIPTS_DIR.glob("*.py"):
        text = script_path.read_text(encoding="utf-8")
        assert "requests.post" not in text
        assert "requests.put" not in text
        assert "requests.patch" not in text
        assert "requests.delete" not in text
        assert "session.post" not in text
        assert "session.put" not in text
        assert "session.patch" not in text
        assert "session.delete" not in text


def test_form_designer_scripts_stay_reviewable_in_size():
    oversized = {
        script_path.name: len(script_path.read_text(encoding="utf-8").splitlines())
        for script_path in SCRIPTS_DIR.glob("*.py")
        if len(script_path.read_text(encoding="utf-8").splitlines()) > 400
    }

    assert oversized == {}
