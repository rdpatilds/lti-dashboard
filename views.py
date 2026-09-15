import html
from collections.abc import Callable, Sequence

import config
from data import Failed, Layout, Loaded, Part, Row, Section, Source
from lti import Launch

RISK_LEVELS = ("high", "medium", "low")
HIDDEN_COLUMNS = frozenset({"next_url", "path_url"})

STYLE_HTML = """<style>
body { margin: 0; padding: 18px; font: 14px/1.45 system-ui, sans-serif; color: #17212b; background: #fff; }
h1 { font-size: 19px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 8px; }
h3.group { font-size: 13px; margin: 14px 0 6px; color: #4a5a6a; text-transform: lowercase; }
p.meta { margin: 0 0 18px; color: #4a5a6a; }
section { border: 1px solid #dde3ea; border-radius: 6px; padding: 14px; margin: 0 0 14px; }
p.part { margin: 0 0 8px; font-size: 12px; }
p.part code { color: #4a5a6a; word-break: break-all; }
span.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-weight: 700;
             font-size: 11px; letter-spacing: .04em; color: #fff; }
span.badge.history { background: #b45309; }
span.badge.live { background: #12794f; }
div.error { border: 1px solid #f0b7b7; background: #fdf3f3; border-radius: 4px;
            padding: 8px 10px; margin: 0 0 10px; color: #8a1c1c; font-size: 12px; }
p.empty, p.notice { color: #4a5a6a; margin: 6px 0 0; }
div.scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 13px; }
th, td { border-bottom: 1px solid #e7ecf1; padding: 5px 10px; text-align: left; white-space: nowrap; }
thead th { color: #4a5a6a; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
table.kv th { color: #4a5a6a; font-weight: 600; }
td.risk { font-weight: 700; }
td.risk.high { color: #b91c1c; }
td.risk.medium { color: #b45309; }
td.risk.low { color: #12794f; }
ol.path { margin: 6px 0 0; padding-left: 22px; }
ol.path li { margin: 3px 0; }
ol.path span.reason { color: #4a5a6a; margin-left: 4px; }
ol.path span.rule { color: #4a5a6a; font-size: 11px; }
</style>"""


def cell(value: object) -> str:
    if value is None:
        return "&ndash;"
    if isinstance(value, bool):
        return "true" if value else "false"
    return html.escape(str(value), quote=False)


def attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def page(launch: Launch, sections: Sequence[Section]) -> str:
    role_html = cell("Educator" if launch.is_educator else "Student")
    title_html = cell(config.TITLE)
    meta_html = " &middot; ".join(
        [
            cell(launch.name),
            role_html,
            cell(launch.context_title or "course " + str(launch.course_id)),
            cell(launch.message_type),
        ]
    )
    if sections:
        body_html = "".join(_section(section) for section in sections)
    else:
        body_html = (
            '<p class="notice">This launch carried no canvas_user_id custom claim, so there is no '
            "student to look up. Launch the tool from inside a course as a student, or as a teacher "
            "for the whole-course view.</p>"
        )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title_html}</title>"
        f"{STYLE_HTML}</head><body><h1>{title_html}</h1>"
        f'<p class="meta">{meta_html}</p>{body_html}</body></html>'
    )


def landing() -> str:
    title_html = cell(config.TITLE)
    return (
        f"<!doctype html><meta charset='utf-8'><title>{title_html}</title>"
        f"<p>{title_html} LTI 1.3 tool is running. "
        '<a href="/config.json">/config.json</a> &middot; '
        '<a href="/.well-known/jwks.json">/.well-known/jwks.json</a></p>'
    )


def refused(reason: str) -> str:
    title_html = cell(config.TITLE)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title_html}</title>"
        f"{STYLE_HTML}</head><body><h1>Launch refused</h1>"
        f'<div class="error">{cell(reason)}</div></body></html>'
    )


def _section(section: Section) -> str:
    columns = _columns(section.parts)
    parts_html = "".join(_part(part) for part in section.parts)
    errors_html = "".join(_error_box(part) for part in section.parts if isinstance(part, Failed))
    body_html = _body(section, columns)
    return f"<section><h2>{cell(section.title)}</h2>{parts_html}{errors_html}{body_html}</section>"


def _columns(parts: Sequence[Part]) -> tuple[str, ...]:
    columns: list[str] = []
    for part in parts:
        if isinstance(part, Loaded):
            columns.extend(
                column
                for column in part.columns
                if column not in columns and column not in HIDDEN_COLUMNS
            )
    return tuple(columns)


def _body(section: Section, columns: Sequence[str]) -> str:
    if not any(isinstance(part, Loaded) for part in section.parts):
        return ""
    if section.layout is Layout.LIST:
        return _list(section.rows)
    if not section.rows:
        return '<p class="empty">No rows.</p>'
    if section.layout is Layout.KEY_VALUE:
        return _key_value(columns, section.rows[0])
    if section.layout is Layout.GROUPED:
        return _grouped(columns, section.rows, section.group_by or columns[0])
    return _table(columns, section.rows)


def _part(part: Part) -> str:
    badge_html = _badge(part.source)
    return f'<p class="part">{badge_html} <code>{cell(part.origin)}</code></p>'


def _badge(source: Source) -> str:
    return f'<span class="badge {attr(source.name.lower())}">{cell(source.value)}</span>'


def _error_box(part: Failed) -> str:
    return f'<div class="error">{cell(part.error)}</div>'


def _table(columns: Sequence[str], rows: Sequence[Row]) -> str:
    head_html = "".join(f"<th>{cell(column)}</th>" for column in columns)
    rows_html = "".join(_tr(columns, row) for row in rows)
    return (
        f'<div class="scroll"><table><thead><tr>{head_html}</tr></thead>'
        f"<tbody>{rows_html}</tbody></table></div>"
    )


def _tr(columns: Sequence[str], row: Row) -> str:
    cells_html = "".join(_td(column, row) for column in columns)
    return f"<tr>{cells_html}</tr>"


def _risk_class(column: str, value: object) -> str:
    return "risk " + str(value) if column == "risk_level" and value in RISK_LEVELS else ""


def _link(title: object, url: object) -> str:
    if title is None or not str(title).strip():
        return "&ndash;"
    text = cell(title)
    if url is not None and str(url).strip():
        return f'<a href="{attr(url)}" target="_top">{text}</a>'
    return text


def _next_title(row: Row) -> str:
    return _link(row.get("next_title"), row.get("next_url"))


def _next_step(row: Row) -> str:
    title = row.get("next_title")
    rule = row.get("rule")
    if title is None or not str(title).strip():
        return _link(rule, None)
    text = f"{title} ({rule})" if rule is not None and str(rule).strip() else title
    return _link(text, row.get("next_url"))


def _path(row: Row) -> str:
    title = row.get("path_title")
    if title is None or not str(title).strip():
        return "&ndash;"
    steps = row.get("path_steps")
    if steps is None or not str(steps).strip():
        return _link(title, row.get("path_url"))
    count = "1 step" if steps == 1 else f"{steps} steps"
    return _link(f"{title} ({count})", row.get("path_url"))


CELL_RENDERERS: dict[str, Callable[[Row], str]] = {
    "next_title": _next_title,
    "next_step": _next_step,
    "path": _path,
}


def _td(column: str, row: Row) -> str:
    class_html = attr(_risk_class(column, row.get(column)))
    inner_html = CELL_RENDERERS[column](row) if column in CELL_RENDERERS else cell(row.get(column))
    return f'<td class="{class_html}">{inner_html}</td>'


def _key_value(columns: Sequence[str], row: Row) -> str:
    rows_html = "".join(f"<tr><th>{cell(column)}</th>{_td(column, row)}</tr>" for column in columns)
    return f'<table class="kv"><tbody>{rows_html}</tbody></table>'


def _grouped(columns: Sequence[str], rows: Sequence[Row], group_by: str) -> str:
    rest = tuple(column for column in columns if column != group_by)
    groups: dict[object, list[Row]] = {}
    for row in rows:
        groups.setdefault(row.get(group_by), []).append(row)
    chunks = []
    for value, members in groups.items():
        table_html = _table(rest, members)
        chunks.append(f'<h3 class="group">{cell(group_by)}: {cell(value)}</h3>{table_html}')
    return "".join(chunks)


def _list(rows: Sequence[Row]) -> str:
    if not rows:
        return '<p class="empty">Nothing outstanding.</p>'
    items_html = "".join(f"<li>{_step(row)}</li>" for row in rows)
    return f'<ol class="path">{items_html}</ol>'


def _step(row: Row) -> str:
    parts = [_link(row.get("title"), row.get("url"))]
    reason = row.get("reason")
    if reason is not None and str(reason).strip():
        parts.append(f' <span class="reason">{cell(reason)}</span>')
    source_rule = row.get("source_rule")
    if source_rule is not None and str(source_rule).strip():
        parts.append(f' <span class="rule">({cell(source_rule)})</span>')
    return "".join(parts)
