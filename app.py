import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

import config
import data
import lti
import views

LIS_CLAIM = "https://purl.imsglobal.org/spec/lti/claim/lis"
REDACTED_CLAIMS = ("sub", "email", "picture")
REDACTED = "<redacted>"

app = FastAPI(title=config.TITLE, docs_url=None, redoc_url=None)
last_claims: dict | None = None


@app.api_route("/lti/login", methods=["GET", "POST"])
async def login(request: Request) -> RedirectResponse:
    params = dict(request.query_params)
    if request.method == "POST":
        params.update(await request.form())
    url = lti.begin_login(
        client_id=params.get("client_id", ""),
        login_hint=params.get("login_hint", ""),
        target_link_uri=params.get("target_link_uri", config.LAUNCH_URL),
        lti_message_hint=params.get("lti_message_hint", ""),
    )
    return RedirectResponse(url, status_code=302)


@app.post("/lti/launch")
def launch(id_token: str = Form(...), state: str = Form(...)) -> HTMLResponse:
    global last_claims
    pending = lti.claim(state)
    if pending is None:
        return PlainTextResponse(
            "unknown or expired launch state, start the launch again from Canvas", status_code=400
        )
    try:
        claims = lti.verify_id_token(id_token, pending)
    except lti.Refused as refusal:
        return HTMLResponse(views.refused(str(refusal)), status_code=401)
    last_claims = claims
    verified = lti.launch_from_claims(claims)
    return HTMLResponse(views.page(verified, sections_for(verified)))


@app.get("/.well-known/jwks.json")
def jwks() -> dict:
    return {"keys": [lti.public_jwk()]}


@app.get("/config.json")
def tool_config() -> dict:
    return config.tool_configuration()


ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M3 20h18"/><rect x="5" y="10" width="3" height="8"/><rect x="10.5" y="5" width="3" height="13"/>'
    '<rect x="16" y="13" width="3" height="5"/></svg>'
)


@app.get("/icon.svg")
def icon() -> PlainTextResponse:
    return PlainTextResponse(ICON_SVG, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return views.landing()


@app.get("/debug/last-claims")
def debug_last_claims() -> JSONResponse:
    if last_claims is None:
        return JSONResponse({"detail": "no verified launch yet"}, status_code=404)
    return JSONResponse({key: _redact(key, value) for key, value in last_claims.items()})


def _redact(key: str, value):
    if key in REDACTED_CLAIMS:
        return REDACTED
    if key == LIS_CLAIM:
        # Keep the LIS key names so the claim's shape is documentable, redact every value.
        return {inner: REDACTED for inner in value} if isinstance(value, dict) else REDACTED
    return value


def sections_for(verified: lti.Launch) -> list[data.Section]:
    course_id = verified.course_id
    if verified.is_educator:
        history, steps, live = _gather(
            partial(data.cohort_rows, course_id),
            partial(data.next_steps, course_id),
            partial(data.live_cohort, course_id),
        )
        return [history.join(steps, on="user_id").join(live, on="user_id")]
    user_id = verified.canvas_user_id
    if user_id is None:
        return []
    return _gather(
        partial(data.student_row, course_id, user_id),
        partial(data.assignment_rows, course_id, user_id),
        partial(data.recommendation_rows, course_id, user_id),
        partial(data.live_enrollment, course_id, user_id),
        partial(data.live_submissions, course_id, user_id),
    )


def _gather(*fetch) -> list[data.Section]:
    with ThreadPoolExecutor(len(fetch)) as pool:
        return list(pool.map(lambda one: one(), fetch))


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8800)
