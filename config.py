"""Stdlib only. scripts/register_tool.py imports this under a bare except and silently falls
back to its own inlined copy, so a third-party import here would make the two configs diverge."""

TITLE = "Performance Dashboard"
TOOL_URL = "http://localhost:8800"
LAUNCH_URL = f"{TOOL_URL}/lti/launch"


def tool_configuration() -> dict:
    return {
        "title": TITLE,
        "description": "Unified student performance dashboard, history from Redshift, live state from Canvas",
        "target_link_uri": LAUNCH_URL,
        "oidc_initiation_url": f"{TOOL_URL}/lti/login",
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
                            "text": TITLE,
                            "target_link_uri": LAUNCH_URL,
                            "enabled": True,
                            "default": "enabled",
                        },
                        {
                            "placement": "global_navigation",
                            "message_type": "LtiResourceLinkRequest",
                            "text": TITLE,
                            "target_link_uri": LAUNCH_URL,
                            "icon_url": f"{TOOL_URL}/icon.svg",
                            "enabled": True,
                        },
                    ]
                },
            }
        ],
    }
