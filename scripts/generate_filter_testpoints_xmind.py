#!/usr/bin/env python3
"""Generate XMind 2020+ file from filter-condition test point hierarchy."""

from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path


def new_id() -> str:
    return uuid.uuid4().hex


def topic(title: str, children: list[dict] | None = None) -> dict:
    node: dict = {
        "id": new_id(),
        "class": "topic",
        "title": title,
    }
    if children:
        node["children"] = {"attached": children}
    return node


def build_tree() -> list[dict]:
    """Return XMind content.json sheet list."""
    root_children = [
        topic(
            "输入区间（通用）",
            [
                topic("消耗", [topic("取值范围：[0, 999999999]")]),
                topic("ROI", [topic("取值范围：[0, 999999999]")]),
                topic("天数", [topic("取值范围：[1, 14]")]),
                topic(
                    "运算符",
                    [
                        topic("大于等于"),
                        topic("小于等于"),
                        topic("介于"),
                    ],
                ),
            ],
        ),
        topic(
            "新媒体-免费短剧",
            [
                topic("近3日连续分日项目消耗", [topic("运算符")]),
                topic(
                    "当日预估ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
                topic("近x天，累计消耗", [topic("运算符")]),
                topic(
                    "近3天累计预估ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
            ],
        ),
        topic(
            "头条端原生-免费",
            [
                topic("近3日连续分日项目消耗", [topic("运算符")]),
                topic(
                    "当日广告变现ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
                topic("近x天，累计消耗", [topic("运算符")]),
                topic(
                    "近3天累计广告变现",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
            ],
        ),
        topic(
            "新媒体-短剧",
            [
                topic("近3日连续分日项目消耗", [topic("运算符")]),
                topic(
                    "当日ROI_H12",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
                topic("近x天，累计消耗", [topic("运算符")]),
                topic(
                    "近3天累计ROI_H12",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
            ],
        ),
        topic(
            "头条端原生-付费",
            [
                topic("近3日连续分日项目消耗", [topic("运算符")]),
                topic(
                    "当日激活后24小时付费ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
                topic("近x天，累计消耗", [topic("运算符")]),
                topic(
                    "近3天累计激活后24小时付费ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
            ],
        ),
        topic(
            "新媒体-短篇",
            [
                topic("当日累计项目消耗", [topic("运算符")]),
                topic(
                    "当日ROI_H12",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
                topic("近x天，累计消耗", [topic("运算符")]),
                topic(
                    "近3天累计ROI_H12",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
            ],
        ),
        topic(
            "客户端-免费短剧",
            [
                topic(
                    "消耗",
                    [
                        topic("当日", [topic("运算符")]),
                        topic("近3日", [topic("运算符")]),
                        topic("近7日", [topic("运算符")]),
                    ],
                ),
                topic(
                    "整体 ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
                topic("当日ARPU", [topic("运算符")]),
            ],
        ),
        topic(
            "客户端-付费短剧",
            [
                topic(
                    "消耗",
                    [
                        topic("当日", [topic("运算符")]),
                        topic("近3日", [topic("运算符")]),
                        topic("近7日", [topic("运算符")]),
                        topic(
                            "预估ROI",
                            [
                                topic("运算符"),
                                topic("提示：输入小数，如 80% 填 0.8"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        topic(
            "客户端-付费小说",
            [
                topic(
                    "消耗",
                    [
                        topic("当日", [topic("运算符")]),
                        topic("近3日", [topic("运算符")]),
                        topic("近7日", [topic("运算符")]),
                    ],
                ),
                topic(
                    "预估ROI",
                    [
                        topic("运算符"),
                        topic("提示：输入小数，如 80% 填 0.8"),
                    ],
                ),
            ],
        ),
    ]

    sheet = {
        "id": new_id(),
        "class": "sheet",
        "title": "筛选条件测试点",
        "rootTopic": {
            "id": new_id(),
            "class": "topic",
            "title": "筛选条件测试点",
            "structureClass": "org.xmind.ui.logic.right",
            "children": {"attached": root_children},
        },
    }
    return [sheet]


def write_xmind(output_path: Path) -> None:
    content = build_tree()
    metadata = {
        "creator": {
            "name": "Cursor Agent",
            "version": "1.0",
        },
    }
    manifest = {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
            "manifest.json": {},
        }
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "content.json",
            json.dumps(content, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "筛选条件测试点.xmind"
    write_xmind(output)
    print(f"已生成: {output}")


if __name__ == "__main__":
    main()
