"""
TAPD Bug 统计报表（仅标准库）
- 按时间范围 / 未解决等条件筛选
- 输出 Excel（stats + raw）与 JSON（含 bugs 明细列表）
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

DEFAULT_PAGE_SIZE = 200
DONE_STATUS_SET = {"closed", "resolved"}
SIMPLE_LABEL = "简单问题"
BUG_FIELDS = (
    "id,title,status,severity,priority_label,module,platform,"
    "current_owner,reporter,label,created"
)
DEFAULT_TAPD_BASE_URL = "https://www.tapd.cn"


def workspace_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".cursor").is_dir():
            return parent
    raise RuntimeError("无法定位项目根目录（未找到 .cursor 目录）")


def find_tapd_client_script() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tapd-plus" / "scripts" / "tapd_client_stdlib.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 tapd 客户端脚本: tapd-plus/scripts/tapd_client_stdlib.py")


DEFAULT_CONFIG_FILE = workspace_root() / ".tapd" / "project_config.json"


def load_tapd_client():
    script_path = find_tapd_client_script()
    spec = importlib.util.spec_from_file_location("tapd_client_stdlib", script_path)
    if not spec or not spec.loader:
        raise RuntimeError("加载 tapd_client_stdlib 失败")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_project_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    path = (config_path or DEFAULT_CONFIG_FILE).resolve()
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        obj = json.load(f)
    return obj if isinstance(obj, dict) else {}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TAPD Bug 统计（Excel + 明细 JSON）")
    p.add_argument("--config-file", help="project_config.json 路径")
    p.add_argument("--workspace-id", help="工作空间 ID")
    p.add_argument("--project-name", dest="workspace_name", help="工作空间名称")
    p.add_argument("--workspace-name", dest="workspace_name", help="同 --project-name")
    p.add_argument("--nick", default=os.environ.get("CURRENT_USER_NICK", ""))
    p.add_argument("--start-date", help="起始日期 YYYY-MM-DD（按创建时间）")
    p.add_argument("--end-date", help="结束日期 YYYY-MM-DD（按创建时间）")
    p.add_argument(
        "--open-only",
        action="store_true",
        help="仅未解决：排除 closed、resolved",
    )
    p.add_argument("--exclude-status", help="排除状态，逗号分隔（与 --open-only 互斥）")
    p.add_argument(
        "--include-status",
        help="仅包含指定 status，逗号分隔；如 resolved（已解决未关闭）",
    )
    p.add_argument(
        "--reporter",
        help="创建人/提单人筛选，昵称模糊匹配；多个用逗号分隔",
    )
    p.add_argument(
        "--creator",
        dest="reporter",
        help="同 --reporter（创建人）",
    )
    p.add_argument(
        "--platform",
        help="软件平台筛选（TAPD platform 字段，如 前端/后端）；多个用逗号分隔",
    )
    p.add_argument(
        "--simple-label",
        choices=["any", "only", "exclude", "no_label"],
        default="any",
        help="简单问题标签筛选：any=不限 only=仅简单问题 exclude=排除简单问题 no_label=无标签",
    )
    p.add_argument(
        "--group-by",
        choices=["module", "owner", "module_owner", "platform", "simple_label"],
        default="module_owner",
    )
    p.add_argument("--output-xlsx", help="Excel 输出路径")
    p.add_argument("--output-dir", help="默认输出目录，默认 .tapd/")
    p.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    p.add_argument("--max-pages", type=int, default=200)
    args = p.parse_args()
    if args.open_only and str(args.exclude_status or "").strip():
        p.error("--open-only 与 --exclude-status 不能同时使用")
    if args.open_only and str(args.include_status or "").strip():
        p.error("--open-only 与 --include-status 不能同时使用")
    if str(args.exclude_status or "").strip() and str(args.include_status or "").strip():
        p.error("--exclude-status 与 --include-status 不能同时使用")
    return args


def normalize_date_str(s: str) -> Optional[datetime]:
    text = (s or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def get_bug_time(bug: Dict[str, Any]) -> Optional[datetime]:
    for key in ("created", "created_at", "create_time"):
        dt = normalize_date_str(str(bug.get(key, "")))
        if dt:
            return dt
    return None


def in_date_range(bug: Dict[str, Any], start: Optional[datetime], end: Optional[datetime]) -> bool:
    if not start and not end:
        return True
    dt = get_bug_time(bug)
    if dt is None:
        return False
    if start and dt < start:
        return False
    if end and dt > end.replace(hour=23, minute=59, second=59):
        return False
    return True


def parse_exclude_statuses(raw: Optional[str]) -> set:
    return {x.strip().lower() for x in str(raw or "").split(",") if x.strip()}


def match_status(bug: Dict[str, Any], args: argparse.Namespace) -> bool:
    status = str(bug.get("status", "")).strip().lower()
    included = parse_exclude_statuses(getattr(args, "include_status", None))
    if included:
        return status in included
    if args.open_only:
        return status not in DONE_STATUS_SET
    excluded = parse_exclude_statuses(args.exclude_status)
    if excluded:
        return status not in excluded
    return True


def pick_text(d: Dict[str, Any], keys: Sequence[str], fallback: str = "未分类") -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return fallback


def bug_label_text(bug: Dict[str, Any]) -> str:
    return pick_text(bug, ["label"], "")


def label_tokens(label: str) -> List[str]:
    if not label:
        return []
    return [p.strip() for p in re.split(r"[|,;]", label) if p.strip()]


def is_simple_problem(bug: Dict[str, Any]) -> bool:
    return SIMPLE_LABEL in label_tokens(bug_label_text(bug))


def simple_label_category(bug: Dict[str, Any]) -> str:
    label = bug_label_text(bug)
    if not label:
        return "无标签"
    return SIMPLE_LABEL if is_simple_problem(bug) else "非简单问题"


def bug_platform(bug: Dict[str, Any]) -> str:
    return pick_text(bug, ["platform", "module"], "未分类")


def match_multi_value(text: str, raw_filter: Optional[str]) -> bool:
    filt = str(raw_filter or "").strip()
    if not filt:
        return True
    val = (text or "").strip()
    targets = [t.strip() for t in filt.split(",") if t.strip()]
    return any(t in val or val == t for t in targets)


def match_reporter(bug: Dict[str, Any], reporter_filter: Optional[str]) -> bool:
    return match_multi_value(pick_text(bug, ["reporter"], ""), reporter_filter)


def match_platform_filter(bug: Dict[str, Any], platform_filter: Optional[str]) -> bool:
    return match_multi_value(bug_platform(bug), platform_filter)


def match_simple_label_filter(bug: Dict[str, Any], mode: str) -> bool:
    if mode == "any":
        return True
    if mode == "only":
        return is_simple_problem(bug)
    if mode == "exclude":
        return not is_simple_problem(bug)
    if mode == "no_label":
        return not bug_label_text(bug)
    return True


def resolve_workspace_id(client: Any, args: argparse.Namespace, config: Dict[str, Any]) -> Tuple[str, str]:
    wid = str(args.workspace_id or config.get("workspace_id") or "").strip()
    if wid:
        return wid, ""
    name = str(getattr(args, "workspace_name", None) or "").strip()
    nick = str(args.nick or "").strip()
    if name:
        if not nick:
            raise ValueError("按名称解析工作空间需要 --nick 或 CURRENT_USER_NICK")
        resp = client.request(
            "GET", "workspaces/user_participant_projects", params={"nick": nick}
        )
        for row in resp.get("data", []) or []:
            ws = row.get("Workspace", row) if isinstance(row, dict) else {}
            if isinstance(ws, dict) and str(ws.get("name", "")).strip() == name:
                return str(ws["id"]), str(ws.get("name", ""))
        raise ValueError(f"未找到工作空间：{name}")
    raise ValueError("请配置 project_config.json 的 workspace_id 或传 --workspace-id")


def fetch_workspace_name(client: Any, workspace_id: str) -> str:
    try:
        resp = client.request(
            "GET", "workspaces/get_workspace_info", params={"workspace_id": workspace_id}
        )
        ws = resp.get("data", {}).get("Workspace", {})
        if isinstance(ws, dict):
            return str(ws.get("name", "")).strip()
    except Exception:
        pass
    return ""


def extract_bugs_from_response(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    data = resp.get("data", [])
    if not isinstance(data, list):
        return out
    for row in data:
        bug = row.get("Bug", row) if isinstance(row, dict) else {}
        if isinstance(bug, dict) and bug.get("id"):
            out.append(bug)
    return out


def fetch_all_bugs(
    client: Any, workspace_id: str, page_size: int, max_pages: int
) -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        resp = client.request(
            "GET",
            "bugs",
            params={
                "workspace_id": workspace_id,
                "page": page,
                "limit": page_size,
                "fields": BUG_FIELDS,
            },
        )
        rows = extract_bugs_from_response(resp)
        if not rows:
            break
        all_items.extend(rows)
        if len(rows) < page_size:
            break
    return all_items


def build_filter_label(args: argparse.Namespace) -> str:
    parts: List[str] = []
    if args.open_only:
        parts.append("未解决（排除 closed/resolved）")
    elif str(getattr(args, "include_status", None) or "").strip():
        parts.append(f"仅 status={args.include_status.strip()}")
    elif str(args.exclude_status or "").strip():
        parts.append(f"排除：{args.exclude_status.strip()}")
    s, e = (args.start_date or "").strip(), (args.end_date or "").strip()
    if s and e:
        parts.append(f"{s}～{e}")
    elif s:
        parts.append(f"{s} 起")
    elif e:
        parts.append(f"至 {e}")
    if str(args.reporter or "").strip():
        parts.append(f"创建人≈{args.reporter.strip()}")
    if str(args.platform or "").strip():
        parts.append(f"软件平台≈{args.platform.strip()}")
    sl = str(getattr(args, "simple_label", "any") or "any")
    if sl == "only":
        parts.append("标签=简单问题")
    elif sl == "exclude":
        parts.append("排除简单问题")
    elif sl == "no_label":
        parts.append("无标签")
    return "｜".join(parts) if parts else "全量"


def _inc_bucket(row: Dict[str, int], bug: Dict[str, Any]) -> None:
    row["total"] += 1
    st = str(bug.get("status", "")).lower()
    if st not in DONE_STATUS_SET:
        row["leftover"] += 1
        if str(bug.get("severity", "")).lower() in ("fatal", "serious"):
            row["severe"] += 1


def aggregate(bugs: Sequence[Dict[str, Any]], group_by: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, int]] = {}
    for b in bugs:
        if group_by == "module":
            key = pick_text(b, ["module"], "未分类模块")
        elif group_by == "owner":
            key = pick_text(b, ["current_owner", "owner"], "未分配负责人")
        elif group_by == "platform":
            key = bug_platform(b)
        elif group_by == "simple_label":
            key = simple_label_category(b)
        else:
            mod = pick_text(b, ["module"], "未分类模块")
            own = pick_text(b, ["current_owner", "owner"], "未分配负责人")
            key = f"{mod}｜{own}"
        row = buckets.setdefault(key, {"total": 0, "leftover": 0, "severe": 0})
        _inc_bucket(row, b)
    out = []
    for key, r in sorted(buckets.items(), key=lambda x: (-x[1]["total"], x[0])):
        if group_by == "module_owner":
            mod, _, own = key.partition("｜")
            out.append(
                {
                    "dim1": mod,
                    "dim2": own,
                    "label": key,
                    **r,
                }
            )
        else:
            out.append({"dim1": key, "dim2": "", "label": key, **r})
    return out


def aggregate_platform(bugs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return aggregate(bugs, "platform")


def aggregate_simple_label(bugs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return aggregate(bugs, "simple_label")


def bug_detail_rows(bugs: Sequence[Dict[str, Any]], workspace_id: str) -> List[Dict[str, str]]:
    base = os.environ.get("TAPD_BASE_URL", DEFAULT_TAPD_BASE_URL).rstrip("/")
    rows = []
    for b in bugs:
        bid = str(b.get("id", ""))
        rows.append(
            {
                "id": bid,
                "title": str(b.get("title", "")),
                "status": str(b.get("status", "")),
                "severity": str(b.get("severity", "")),
                "priority": str(b.get("priority_label", "")),
                "module": pick_text(b, ["module"], "未分类"),
                "platform": bug_platform(b),
                "reporter": pick_text(b, ["reporter"], "未知"),
                "label": bug_label_text(b) or "无",
                "is_simple": "是" if is_simple_problem(b) else "否",
                "owner": pick_text(b, ["current_owner", "owner"], "未分配"),
                "created": str(b.get("created", "")),
                "url": f"{base}/{workspace_id}/bugtrace/bugs/view/{bid}",
            }
        )
    return rows


def col_name(idx: int) -> str:
    s, n = "", idx
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def sheet_xml(rows: Sequence[Sequence[Any]]) -> str:
    body: List[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, val in enumerate(row, start=1):
            ref = f"{col_name(c_idx)}{r_idx}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val))}</t></is></c>'
                )
        body.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def write_xlsx(path: Path, sheets: Sequence[Tuple[str, List[List[Any]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_list = list(sheets)
    overrides = [
        '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for i in range(1, len(sheet_list) + 1):
        overrides.append(
            f'  <Override PartName="/xl/worksheets/sheet{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument/spreadsheetml.worksheet+xml"/>'
        )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )
    wb_sheets = "".join(
        f'<sheet name="{escape(n[:31])}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (n, _) in enumerate(sheet_list, 1)
    )
    wb = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{wb_sheets}</sheets></workbook>"
    )
    wb_rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheet_list) + 1)
    )
    wb_rels += (
        f'<Relationship Id="rId{len(sheet_list)+1}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<fonts count=\"1\"/><fills count=\"1\"/><borders count=\"1\"/>"
        "<cellStyleXfs count=\"1\"/><cellXfs count=\"1\"/></styleSheet>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{wb_rels}</Relationships>',
        )
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/styles.xml", styles)
        for i, (_, rows) in enumerate(sheet_list, 1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows))


def default_xlsx_path(args: argparse.Namespace, workspace_id: str) -> Path:
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else workspace_root() / ".tapd"
    slug = "open" if args.open_only else "all"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_dir / f"tapd_bugs_{slug}_{workspace_id}_{ts}.xlsx"


def compute_summary(bugs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(bugs)
    leftover = sum(1 for b in bugs if str(b.get("status", "")).lower() not in DONE_STATUS_SET)
    severe = sum(
        1
        for b in bugs
        if str(b.get("status", "")).lower() not in DONE_STATUS_SET
        and str(b.get("severity", "")).lower() in ("fatal", "serious")
    )
    simple = sum(1 for b in bugs if is_simple_problem(b))
    fixed = total - leftover
    rate = round(fixed * 100.0 / total, 1) if total else 0.0
    by_platform = {r["label"]: r["total"] for r in aggregate_platform(bugs)}
    by_simple = {r["label"]: r["total"] for r in aggregate_simple_label(bugs)}
    return {
        "total": total,
        "leftover": leftover,
        "fixed": fixed,
        "fix_rate": rate,
        "severe_leftover": severe,
        "simple_count": simple,
        "by_platform": by_platform,
        "by_simple_label": by_simple,
    }


def stats_sheet_rows(agg: List[Dict[str, Any]], group_by: str) -> List[List[Any]]:
    if group_by == "module_owner":
        header = ["模块", "负责人", "总数", "遗留", "高严重遗留"]
        return [header] + [
            [r["dim1"], r["dim2"], r["total"], r["leftover"], r["severe"]] for r in agg
        ]
    dim_name = {
        "module": "模块",
        "owner": "负责人",
        "platform": "软件平台",
        "simple_label": "简单问题分类",
    }.get(group_by, "维度")
    header = [dim_name, "总数", "遗留", "高严重遗留"]
    return [header] + [[r["label"], r["total"], r["leftover"], r["severe"]] for r in agg]


def build_brief(
    project_name: str,
    workspace_id: str,
    filter_label: str,
    summary: Dict[str, Any],
    xlsx_path: str,
) -> str:
    name = project_name or workspace_id
    plat = summary.get("by_platform") or {}
    plat_txt = "、".join(f"{k}:{v}" for k, v in list(plat.items())[:5]) or "无"
    simple = summary.get("by_simple_label") or {}
    simple_txt = "、".join(f"{k}:{v}" for k, v in simple.items()) or "无"
    return (
        f"## {name} · Bug 统计\n\n"
        f"- 范围：{workspace_id}｜{filter_label}\n"
        f"- 共 {summary['total']}｜遗留 {summary['leftover']}｜"
        f"修复率 {summary['fix_rate']}%｜高严重 {summary['severe_leftover']}｜简单问题 {summary.get('simple_count', 0)}\n"
        f"- 软件平台：{plat_txt}\n"
        f"- 标签分布：{simple_txt}\n"
        f"- 报告：`{xlsx_path}`（stats / 软件平台 / 简单问题 / raw）"
    )


def main() -> None:
    args = parse_args()
    config = load_project_config(
        Path(args.config_file).resolve() if args.config_file else None
    )
    client = load_tapd_client()
    workspace_id, _ = resolve_workspace_id(client, args, config)
    project_name = fetch_workspace_name(client, workspace_id)

    start = normalize_date_str(args.start_date or "")
    end = normalize_date_str(args.end_date or "")

    all_bugs = fetch_all_bugs(client, workspace_id, args.page_size, args.max_pages)
    filtered = [
        b
        for b in all_bugs
        if in_date_range(b, start, end)
        and match_status(b, args)
        and match_reporter(b, args.reporter)
        and match_platform_filter(b, args.platform)
        and match_simple_label_filter(b, args.simple_label)
    ]

    filter_label = build_filter_label(args)
    summary = compute_summary(filtered)
    agg = aggregate(filtered, args.group_by)
    plat_agg = aggregate_platform(filtered)
    simple_agg = aggregate_simple_label(filtered)
    details = bug_detail_rows(filtered, workspace_id)

    raw_sheet: List[List[Any]] = [
        [
            "id",
            "title",
            "status",
            "severity",
            "priority",
            "module",
            "platform",
            "reporter",
            "label",
            "is_simple",
            "owner",
            "created",
            "url",
        ]
    ]
    for d in details:
        raw_sheet.append(
            [
                d["id"],
                d["title"],
                d["status"],
                d["severity"],
                d["priority"],
                d["module"],
                d["platform"],
                d["reporter"],
                d["label"],
                d["is_simple"],
                d["owner"],
                d["created"],
                d["url"],
            ]
        )

    xlsx_out = Path(args.output_xlsx).resolve() if args.output_xlsx else default_xlsx_path(args, workspace_id)
    write_xlsx(
        xlsx_out,
        [
            ("stats", stats_sheet_rows(agg, args.group_by)),
            ("软件平台", stats_sheet_rows(plat_agg, "platform")),
            ("简单问题", stats_sheet_rows(simple_agg, "simple_label")),
            ("raw", raw_sheet),
        ],
    )

    result = {
        "workspace_id": workspace_id,
        "workspace_name": project_name,
        "filter_label": filter_label,
        "summary": summary,
        "group_by": args.group_by,
        "platform_stats": plat_agg,
        "simple_label_stats": simple_agg,
        "xlsx_path": str(xlsx_out),
        "brief": build_brief(project_name, workspace_id, filter_label, summary, str(xlsx_out)),
        "bugs": details,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
