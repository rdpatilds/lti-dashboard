import argparse
import importlib
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("CANVAS_URL", "http://localhost:3100").rstrip("/")
TOKEN = os.environ.get("CANVAS_TOKEN", "cplatform-dev-token")
ACCOUNT_ID = "1"
COURSE_ID = "1"
KEY_NAME = "Kaplan Performance Dashboard"
TOOL_NAME = "Performance Dashboard"

# One account-level install carries both placements; a second course-level
# install would add an identical course navigation tab.
INSTALL_TARGETS = (
    ("account", f"accounts/{ACCOUNT_ID}"),
)

INLINE_TOOL_CONFIGURATION = {
    "title": "Performance Dashboard",
    "description": "Unified student performance dashboard, history from Redshift, live state from Canvas",
    "target_link_uri": "http://localhost:8800/lti/launch",
    "oidc_initiation_url": "http://localhost:8800/lti/login",
    # Canvas runs in Docker and fetches JWKS server-side, so this host has to resolve from inside the container.
    "public_jwk_url": "http://host.docker.internal:8800/.well-known/jwks.json",
    "scopes": [],
    "custom_fields": {"canvas_user_id": "$Canvas.user.id", "canvas_course_id": "$Canvas.course.id"},
    "extensions": [
        {
            "platform": "canvas.instructure.com",
            "domain": "localhost",
            "privacy_level": "public",
            "settings": {
                "placements": [
                    {
                        "placement": "course_navigation",
                        "message_type": "LtiResourceLinkRequest",
                        "text": "Performance Dashboard",
                        "target_link_uri": "http://localhost:8800/lti/launch",
                        "enabled": True,
                        "default": "enabled",
                    },
                    {
                        "placement": "global_navigation",
                        "message_type": "LtiResourceLinkRequest",
                        "text": "Performance Dashboard",
                        "target_link_uri": "http://localhost:8800/lti/launch",
                    "icon_url": "http://localhost:8800/icon.svg",
                        "enabled": True,
                    },
                ]
            },
        }
    ],
}


def api(method, path, body=None):
    url = f"{BASE}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        print(f"{method} {url} returned HTTP {error.code}", file=sys.stderr)
        print(error.read().decode("utf-8", errors="replace"), file=sys.stderr)
        sys.exit(1)
    if text.startswith("while(1);"):
        text = text[len("while(1);"):]
    if not text.strip():
        return None
    return json.loads(text)


def load_tool_configuration():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(root, "config.py")
    if not os.path.exists(config_path):
        print(f"no {config_path}, using the tool configuration inlined in this script")
        return INLINE_TOOL_CONFIGURATION
    sys.path.insert(0, root)
    try:
        return importlib.import_module("config").tool_configuration()
    except Exception as error:
        print(f"{config_path} unusable ({type(error).__name__}: {error}), using the tool configuration inlined in this script")
        return INLINE_TOOL_CONFIGURATION


RESOURCES = ("developer_key", "account_tool")


def read_state():
    developer_keys = api("GET", f"/api/v1/accounts/{ACCOUNT_ID}/developer_keys?per_page=100") or []
    key = next((key for key in developer_keys if key.get("name") == KEY_NAME), None)
    state = {"developer_key": key, "registration": None}
    if key is not None and key.get("lti_registration_id"):
        state["registration"] = api(
            "GET", f"/api/v1/accounts/{ACCOUNT_ID}/lti_registrations/{key['lti_registration_id']}"
        )
    for label, context in INSTALL_TARGETS:
        tools = api("GET", f"/api/v1/{context}/external_tools?per_page=100") or []
        state[f"{label}_tool"] = next((tool for tool in tools if tool.get("name") == TOOL_NAME), None)
    return state


def create_developer_key(config):
    response = api(
        "POST",
        f"/api/lti/accounts/{ACCOUNT_ID}/developer_keys/tool_configuration",
        {
            "developer_key": {"name": KEY_NAME, "redirect_uris": [config["target_link_uri"]]},
            "tool_configuration": {"settings": config, "privacy_level": "public"},
        },
    )
    return response["developer_key"]


def bind_developer_key(client_id):
    api(
        "POST",
        f"/api/v1/accounts/{ACCOUNT_ID}/developer_keys/{client_id}/developer_key_account_bindings",
        {"developer_key_account_binding": {"workflow_state": "on"}},
    )


def install_tool(context, client_id):
    api("POST", f"/api/v1/{context}/external_tools", {"client_id": client_id})


def unlock_registration(registration_id):
    # Lti::CreateRegistrationService hardcodes lock_deploying: true, and the tool_configuration
    # endpoint passes no override, so every key it mints refuses client_id installs until cleared.
    api(
        "PUT",
        f"/api/v1/accounts/{ACCOUNT_ID}/lti_registrations/{registration_id}",
        {"lock_deploying": False},
    )


def update_tool_configuration(client_id, config):
    api(
        "PUT",
        f"/api/lti/developer_keys/{client_id}/tool_configuration",
        {"developer_key": {"name": KEY_NAME}, "tool_configuration": {"settings": config, "privacy_level": "public"}},
    )


def converge(state, config):
    created = set()
    created_key_json = None
    if state["developer_key"] is None:
        created_key_json = create_developer_key(config)
        key = created_key_json
        created.add("developer_key")
    else:
        key = state["developer_key"]
    client_id = str(key["id"])
    if "developer_key" not in created:
        update_tool_configuration(client_id, config)
    bind_developer_key(client_id)
    if key.get("lti_registration_id"):
        unlock_registration(key["lti_registration_id"])
    for label, context in INSTALL_TARGETS:
        if state[f"{label}_tool"] is None:
            install_tool(context, client_id)
            created.add(f"{label}_tool")
    return created, created_key_json


def print_state(state, created=(), created_key_json=None):
    status = {}
    for name in RESOURCES:
        resource = state[name]
        if resource is None:
            status[name] = "not registered"
        elif name in created:
            status[name] = "created by this run"
        else:
            status[name] = "already registered"

    key = state["developer_key"]
    print(f"developer key: {status['developer_key']}")
    if key is not None:
        binding = (key.get("developer_key_account_binding") or {}).get("workflow_state", "unknown")
        print(f"  name: {key['name']}")
        print(f"  client id: {key['id']}")
        print(f"  workflow state: {key.get('workflow_state', 'unknown')}")
        print(f"  account binding: {binding}")
        registration = state.get("registration") or {}
        print(f"  registration id: {key.get('lti_registration_id', 'unknown')}")
        print(f"  lock_deploying: {registration.get('lock_deploying', 'unknown')}")

    account_tool = state["account_tool"]
    print(f"account tool (course and global navigation): {status['account_tool']}")
    if account_tool is not None:
        print(f"  id: {account_tool['id']}")
        print(f"  course launch: {BASE}/courses/{COURSE_ID}/external_tools/{account_tool['id']}")
        print(f"  global launch: {BASE}/accounts/{ACCOUNT_ID}/external_tools/{account_tool['id']}")

    if created_key_json is not None:
        print("developer key as returned by Canvas:")
        print(json.dumps(created_key_json, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description=f"Register the {TOOL_NAME} LTI 1.3 tool in the Canvas at {BASE}. Safe to run repeatedly."
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the current registration state without changing anything",
    )
    args = parser.parse_args()

    if args.show:
        print_state(read_state())
        return

    config = load_tool_configuration()
    created, created_key_json = converge(read_state(), config)
    print_state(read_state(), created, created_key_json)


if __name__ == "__main__":
    main()
