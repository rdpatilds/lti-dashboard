import json
import logging
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import cache

import boto3
from botocore.config import Config

log = logging.getLogger(__name__)

REGION = "us-east-1"
WORKGROUP = "canvas"
DATABASE = "dev"
VALUE_KEYS = ("longValue", "doubleValue", "stringValue", "booleanValue")
# A paused Serverless workgroup pays a resume cost on the first statement; 25s was observed to
# time out on a cold workgroup that then answered in 3s.
POLL_DEADLINE_SECONDS = 60

CANVAS_URL = os.environ.get("CANVAS_URL", "http://localhost:3100").rstrip("/")
CANVAS_TOKEN = os.environ.get("CANVAS_TOKEN", "cplatform-dev-token")
# This Canvas answers in ~6s per call and serializes concurrent ones, so two in flight need ~12s.
CANVAS_TIMEOUT = 30

Cell = str | int | float | bool | None
Row = dict[str, Cell]


class Source(StrEnum):
    HISTORY = "HISTORY (Redshift nudges.*)"
    LIVE = "LIVE (Canvas API, fetched now)"


class Layout(StrEnum):
    TABLE = "table"
    KEY_VALUE = "key_value"
    GROUPED = "grouped"
    LIST = "list"


@dataclass(frozen=True, slots=True)
class Loaded:
    source: Source
    origin: str
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Failed:
    source: Source
    origin: str
    error: str


Part = Loaded | Failed


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    parts: tuple[Part, ...]
    rows: tuple[Row, ...]
    layout: Layout = Layout.TABLE
    group_by: str | None = None

    def join(self, other: "Section", on: str) -> "Section":
        parts = self.parts + other.parts
        if not any(isinstance(part, Loaded) for part in self.parts):
            return Section(other.title, parts, other.rows, other.layout, other.group_by)
        extra = {row.get(on): row for row in other.rows}
        rows = tuple(
            {**row, **{key: value for key, value in extra.get(row.get(on), {}).items() if key != on}}
            for row in self.rows
        )
        return Section(self.title, parts, rows, self.layout, self.group_by)


def _section(
    title: str,
    part: Loaded,
    fetch: Callable[[], list[Row]],
    *,
    layout: Layout = Layout.TABLE,
    group_by: str | None = None,
) -> Section:
    try:
        rows = fetch()
    except Exception as error:
        log.exception("%s failed", part.origin)
        failed = Failed(part.source, part.origin, f"{type(error).__name__}: {error}"[:400])
        return Section(title, (failed,), (), layout, group_by)
    return Section(title, (part,), tuple(rows), layout, group_by)


@cache
def _client():
    return boto3.client(
        "redshift-data",
        region_name=REGION,
        config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1}),
    )


def _wait_finished(statement_id: str) -> dict:
    deadline = time.monotonic() + POLL_DEADLINE_SECONDS
    while True:
        described = _client().describe_statement(Id=statement_id)
        status = described["Status"]
        if status == "FINISHED":
            return described
        if status in ("FAILED", "ABORTED"):
            raise RuntimeError(f"{status}: {described.get('Error', '')}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"statement {statement_id} still {status} after {POLL_DEADLINE_SECONDS}s")
        time.sleep(1)


def run(sql: str, parameters: list[dict] | None = None) -> list[Row]:
    kwargs = {"WorkgroupName": WORKGROUP, "Database": DATABASE, "Sql": sql}
    if parameters:
        kwargs["Parameters"] = parameters
    statement_id = _client().execute_statement(**kwargs)["Id"]
    if not _wait_finished(statement_id).get("HasResultSet"):
        return []
    rows: list[Row] = []
    columns = None
    token = None
    while True:
        page = _client().get_statement_result(Id=statement_id, **({"NextToken": token} if token else {}))
        if columns is None:
            columns = [column["name"] for column in page["ColumnMetadata"]]
        for record in page["Records"]:
            values = [next((cell[key] for key in VALUE_KEYS if key in cell), None) for cell in record]
            rows.append(dict(zip(columns, values)))
        token = page.get("NextToken")
        if not token:
            return rows


def _canvas(path: str) -> list:
    request = urllib.request.Request(
        f"{CANVAS_URL}{path}", headers={"Authorization": f"Bearer {CANVAS_TOKEN}"}
    )
    with urllib.request.urlopen(request, timeout=CANVAS_TIMEOUT) as response:
        text = response.read().decode("utf-8", errors="replace")
    if text.startswith("while(1);"):
        text = text[len("while(1);"):]
    body = json.loads(text)
    if not isinstance(body, list):
        raise TypeError(f"GET {path} returned {type(body).__name__}, expected a list")
    return body


STATUS_COLUMNS = (
    "name",
    "enrollment_state",
    "risk_level",
    "risk_score",
    "days_inactive",
    "last_activity_at",
    "last_submission_at",
    "current_score",
    "assignments_total",
    "assignments_submitted",
    "assignments_missing",
    "assignments_late",
    "assignments_due_3d_unsubmitted",
    "quizzes_complete",
    "quizzes_total",
    "quiz_avg_percent",
    "module_requirement_completed",
    "module_requirement_count",
    "logins_7d",
    "computed_at",
)

ASSIGNMENT_COLUMNS = ("title", "due_at", "status", "submitted_at", "score", "points_possible")

RECOMMENDATION_COLUMNS = (
    "id",
    "rule",
    "surface",
    "text",
    "next_title",
    "next_url",
    "status",
    "decided_by",
    "pushed_at",
)

COHORT_COLUMNS = (
    "user_id",
    "name",
    "risk_level",
    "risk_score",
    "days_inactive",
    "assignments_missing",
    "assignments_submitted",
    "current_score",
    "module_requirement_completed",
    "module_requirement_count",
)

NEXT_STEP_FIELDS = ("user_id", "rule", "next_title", "next_url")
NEXT_STEP_COLUMNS = ("next_step",)

PATH_COLUMNS = ("position", "title", "url", "reason", "source_rule")
FIRST_PATH_COLUMNS = ("path",)


def _student(course_id: int, user_id: int) -> list[dict]:
    return [
        {"name": "course_id", "value": str(course_id)},
        {"name": "user_id", "value": str(user_id)},
    ]


def student_row(course_id: int, user_id: int) -> Section:
    sql = (
        f"select {', '.join(STATUS_COLUMNS)} from nudges.student_course_status"
        " where course_id = :course_id and user_id = :user_id limit 1"
    )
    part = Loaded(Source.HISTORY, "nudges.student_course_status", STATUS_COLUMNS)
    return _section(
        "Course status",
        part,
        lambda: run(sql, _student(course_id, user_id)),
        layout=Layout.KEY_VALUE,
    )


def assignment_rows(course_id: int, user_id: int) -> Section:
    sql = (
        f"select {', '.join(ASSIGNMENT_COLUMNS)} from nudges.assignment_status"
        " where course_id = :course_id and user_id = :user_id order by due_at"
    )
    part = Loaded(Source.HISTORY, "nudges.assignment_status", ASSIGNMENT_COLUMNS)
    return _section("Assignments", part, lambda: run(sql, _student(course_id, user_id)))


def recommendation_rows(course_id: int, user_id: int) -> Section:
    sql = (
        f"select {', '.join(RECOMMENDATION_COLUMNS)} from nudges.recommendations"
        " where course_id = :course_id and user_id = :user_id order by status, created_at"
    )
    part = Loaded(Source.HISTORY, "nudges.recommendations", RECOMMENDATION_COLUMNS)
    return _section(
        "Recommendations",
        part,
        lambda: run(sql, _student(course_id, user_id)),
        layout=Layout.GROUPED,
        group_by="status",
    )


def path_rows(course_id: int, user_id: int) -> Section:
    sql = (
        f"select {', '.join(PATH_COLUMNS)} from nudges.learning_paths"
        " where course_id = :course_id and user_id = :user_id order by position"
    )
    part = Loaded(Source.HISTORY, "nudges.learning_paths", PATH_COLUMNS)
    return _section(
        "Your path",
        part,
        lambda: run(sql, _student(course_id, user_id)),
        layout=Layout.LIST,
    )


def cohort_rows(course_id: int) -> Section:
    sql = (
        f"select {', '.join(COHORT_COLUMNS)} from nudges.student_course_status"
        " where course_id = :course_id order by risk_score desc"
    )
    part = Loaded(Source.HISTORY, "nudges.student_course_status", COHORT_COLUMNS)
    return _section(
        f"All students, course {course_id}",
        part,
        lambda: run(sql, [{"name": "course_id", "value": str(course_id)}]),
    )


def next_steps(course_id: int) -> Section:
    fields = ", ".join(NEXT_STEP_FIELDS)
    sql = (
        f"select {fields} from (\n"
        f"  select {fields},\n"
        "         row_number() over (partition by user_id order by priority, created_at) as rn\n"
        "  from nudges.recommendations\n"
        "  where course_id = :course_id and status in ('proposed', 'approved')\n"
        ") ranked where rn = 1"
    )
    part = Loaded(Source.HISTORY, "nudges.recommendations", NEXT_STEP_COLUMNS)
    return _section(
        f"All students, course {course_id}",
        part,
        lambda: run(sql, [{"name": "course_id", "value": str(course_id)}]),
    )


def first_path_steps(course_id: int) -> Section:
    sql = (
        "select user_id, title as path_title, url as path_url, steps as path_steps from (\n"
        "  select user_id, title, url,\n"
        "         row_number() over (partition by user_id order by position) as rn,\n"
        "         count(*) over (partition by user_id) as steps\n"
        "  from nudges.learning_paths\n"
        "  where course_id = :course_id\n"
        ") ranked where rn = 1"
    )
    part = Loaded(Source.HISTORY, "nudges.learning_paths", FIRST_PATH_COLUMNS)
    return _section(
        f"All students, course {course_id}",
        part,
        lambda: run(sql, [{"name": "course_id", "value": str(course_id)}]),
    )


def live_enrollment(course_id: int, user_id: int) -> Section:
    path = (
        f"/api/v1/courses/{course_id}/enrollments"
        f"?user_id={user_id}&type[]=StudentEnrollment&include[]=total_scores"
    )
    columns = ("last_activity_at", "total_activity_time", "current_score", "final_score")
    part = Loaded(Source.LIVE, f"GET {path}", columns)

    def fetch() -> list[Row]:
        found = _canvas(path)
        if not found:
            return []
        grades = found[0].get("grades") or {}
        return [
            {
                "last_activity_at": found[0].get("last_activity_at"),
                "total_activity_time": found[0].get("total_activity_time"),
                "current_score": grades.get("current_score"),
                "final_score": grades.get("final_score"),
            }
        ]

    return _section("Enrollment", part, fetch, layout=Layout.KEY_VALUE)


def live_submissions(course_id: int, user_id: int) -> Section:
    path = (
        f"/api/v1/courses/{course_id}/students/submissions"
        f"?student_ids[]={user_id}&include[]=assignment&per_page=50"
    )
    columns = ("assignment", "workflow_state", "score", "late", "missing", "submitted_at")
    part = Loaded(Source.LIVE, f"GET {path}", columns)

    def fetch() -> list[Row]:
        return [
            {
                "assignment": (item.get("assignment") or {}).get("name"),
                "workflow_state": item.get("workflow_state"),
                "score": item.get("score"),
                "late": item.get("late"),
                "missing": item.get("missing"),
                "submitted_at": item.get("submitted_at"),
            }
            for item in _canvas(path)
        ]

    return _section("Submissions", part, fetch)


def live_cohort(course_id: int) -> Section:
    path = (
        f"/api/v1/courses/{course_id}/enrollments"
        "?type[]=StudentEnrollment&include[]=total_scores&per_page=100"
    )
    columns = ("user_id", "live_current_score", "live_last_activity_at")
    part = Loaded(Source.LIVE, f"GET {path}", columns)

    def fetch() -> list[Row]:
        return [
            {
                "user_id": item.get("user_id"),
                "live_current_score": (item.get("grades") or {}).get("current_score"),
                "live_last_activity_at": item.get("last_activity_at"),
            }
            for item in _canvas(path)
        ]

    return _section(f"All students, course {course_id}", part, fetch)
