import json
import math
from dataclasses import replace

import pytest

import scripts.windows_overlay_support as support
from scripts.windows_overlay_support import (
    OverlayState,
    QueueSnapshot,
    QuotaSnapshot,
    Rect,
    capture_relative_anchor,
    clamp_rect,
    custom_panel_rect,
    default_panel_rect,
    exceeds_drag_threshold,
    load_state,
    navigation_rect,
    overlay_hit_target,
    parse_queue_payload,
    parse_quota_payload,
    save_state,
    scale_length,
)


def quota_payload():
    """Return a representative quota response with both official windows."""
    return {
        "status": "ok",
        "fetched_at": "2026-10-19T06:30:00+00:00",
        "limits": [
            {
                "limit_id": "other",
                "primary": {
                    "windowDurationMins": 60,
                    "usedPercent": 5,
                    "resetsAt": 1_700_000_600,
                },
                "secondary": None,
            },
            {
                "limit_id": "codex",
                "limit_name": "Codex",
                "primary": {
                    "windowDurationMins": 10080,
                    "usedPercent": 40,
                    "resetsAt": 1_700_086_400,
                },
                "secondary": {
                    "windowDurationMins": 300,
                    "usedPercent": 24.5,
                    "resetsAt": 1_700_003_600,
                },
            },
        ],
    }


def queue_item(title, status, position):
    """Return one representative queue item with ignored real-world fields."""
    return {
        "id": position,
        "title": title,
        "status": status,
        "position": position,
        "prompt": "run the task",
        "cwd": r"C:\work\project",
        "model": "gpt-5",
    }


@pytest.mark.parametrize(
    ("value", "dpi", "expected"),
    [
        (1, 96, 1),
        (2, 120, 3),
        (1, 144, 2),
        (3, 144, 5),
    ],
)
def test_scale_length_uses_half_up_rounding(value, dpi, expected):
    """Scale logical lengths with half-up rather than banker's rounding."""
    assert scale_length(value, dpi) == expected


@pytest.mark.parametrize(
    ("dpi", "expected_navigation", "expected_panel"),
    [
        (
            96,
            Rect(-1914, 810, 36, 36),
            Rect(-1870, 480, 260, 270),
        ),
        (
            120,
            Rect(-1912, 787, 45, 45),
            Rect(-1857, 374, 325, 338),
        ),
        (
            144,
            Rect(-1911, 765, 54, 54),
            Rect(-1845, 270, 390, 405),
        ),
    ],
)
def test_navigation_and_default_panel_use_physical_scaled_lengths(
    dpi, expected_navigation, expected_panel
):
    """Place physical rectangles from an unscaled absolute window origin."""
    window = Rect(-1920, 100, 1200, 800)

    navigation = navigation_rect(window, dpi)

    assert navigation == expected_navigation
    assert default_panel_rect(navigation, dpi, collapsed=False) == expected_panel


def test_default_panel_preserves_eight_unit_gap_and_ninety_six_unit_lift():
    """Keep the panel right of and logically above the navigation button."""
    navigation = Rect(106, 710, 36, 36)

    panel = default_panel_rect(navigation, 96, collapsed=False)

    assert panel.x - navigation.right == 8
    assert navigation.bottom - panel.bottom == 96


def test_clamp_rect_handles_negative_monitor_coordinates():
    """Clamp every edge to a work area whose origin is negative."""
    work_area = Rect(-1920, -100, 1920, 1080)

    assert clamp_rect(Rect(-2300, -400, 260, 270), work_area) == Rect(
        -1920, -100, 260, 270
    )
    assert clamp_rect(Rect(-100, 900, 260, 270), work_area) == Rect(
        -260, 710, 260, 270
    )


def test_capture_relative_anchor_uses_logical_left_and_bottom_offsets():
    """Save logical offsets from the Codex left and top physical edges."""
    window = Rect(-1920, 50, 1200, 800)
    panel = Rect(-1800, 312, 325, 338)

    assert capture_relative_anchor(window, panel, 120) == (96.0, 480.0)


def test_custom_panel_preserves_bottom_between_expanded_and_collapsed_sizes():
    """Restore both panel heights from the same persisted bottom anchor."""
    window = Rect(-1920, 50, 1200, 800)
    state = OverlayState(
        custom_position=True,
        relative_x=96,
        relative_bottom=480,
    )

    expanded = custom_panel_rect(window, state, 120, collapsed=False)
    collapsed = custom_panel_rect(window, state, 120, collapsed=True)

    assert expanded == Rect(-1800, 312, 325, 338)
    assert collapsed == Rect(-1800, 407, 325, 243)
    assert expanded.bottom == collapsed.bottom == 650


def test_temporary_top_clamping_does_not_mutate_saved_anchor():
    """Clamp the displayed height while retaining the persisted anchor."""
    window = Rect(0, 0, 1000, 800)
    state = OverlayState(
        custom_position=True,
        relative_x=20,
        relative_bottom=100,
    )
    original = replace(state)
    work_area = Rect(0, 0, 1000, 800)

    expanded = clamp_rect(
        custom_panel_rect(window, state, 96, collapsed=False), work_area
    )
    collapsed = clamp_rect(
        custom_panel_rect(window, state, 96, collapsed=True), work_area
    )

    assert expanded.y == collapsed.y == 0
    assert state == original


@pytest.mark.parametrize(
    ("point", "collapsed", "visible_count", "expected"),
    [
        ((230, 149), False, 5, "arrow"),
        ((230, 156), True, 5, "arrow"),
        ((100, 162), False, 1, "task:0"),
        ((68, 59), False, 5, "quota"),
        ((192, 59), False, 5, "quota"),
        ((100, 140), False, 5, "none"),
        ((100, 162), False, 0, "none"),
        ((100, 162), True, 5, "none"),
    ],
)
def test_overlay_hit_target_routes_only_explicit_interactions(
    point, collapsed, visible_count, expected
):
    """Route arrow, visible task and ring hits while leaving labels inert."""
    assert (
        overlay_hit_target(
            point,
            collapsed=collapsed,
            visible_task_count=visible_count,
            dpi=96,
        )
        == expected
    )


def test_completed_drag_suppresses_even_the_highest_priority_arrow_hit():
    """Treat every pointer release as a no-op after an actual drag."""
    assert (
        overlay_hit_target(
            (230, 149),
            collapsed=False,
            visible_task_count=5,
            dpi=96,
            dragged=True,
        )
        == "none"
    )


@pytest.mark.parametrize(
    ("dpi", "point", "expected"),
    [
        (96, (3, 0), False),
        (96, (4, 0), True),
        (96, (3, 3), True),
        (120, (4, 0), False),
        (120, (5, 0), True),
        (144, (5, 0), False),
        (144, (6, 0), True),
    ],
)
def test_drag_threshold_is_four_logical_units(dpi, point, expected):
    """Scale the four-unit Euclidean drag threshold to physical pixels."""
    assert exceeds_drag_threshold((0, 0), point, dpi) is expected


def test_parse_quota_selects_windows_by_official_duration():
    """Select five-hour and weekly values by duration rather than field order."""
    snapshot = parse_quota_payload(quota_payload(), now=1_700_000_000)

    assert snapshot == QuotaSnapshot(
        five_hour_remaining=75.5,
        five_hour_reset=1_700_003_600.0,
        weekly_remaining=60.0,
        weekly_reset=1_700_086_400.0,
        fetched_at="2026-10-19T06:30:00+00:00",
        stale=False,
        message="",
    )


def test_parse_quota_uses_valid_last_success_as_stale_data():
    """Read the bounded last-success payload when the live request failed."""
    current = quota_payload()
    payload = {
        "status": "unavailable",
        "message": "x" * 400,
        "last_success": {
            "fetched_at": current["fetched_at"],
            "limits": current["limits"],
        },
    }

    snapshot = parse_quota_payload(payload, now=1_700_000_000)

    assert snapshot.five_hour_remaining == 75.5
    assert snapshot.stale is True
    assert snapshot.message == "x" * 300


def test_parse_quota_marks_expired_windows_unavailable():
    """Do not expose remaining percentages from an already reset window."""
    payload = quota_payload()
    payload["limits"][1]["secondary"]["resetsAt"] = 1_699_999_999

    snapshot = parse_quota_payload(payload, now=1_700_000_000)

    assert snapshot.five_hour_remaining is None
    assert snapshot.five_hour_reset == 1_699_999_999.0


@pytest.mark.parametrize(
    "case",
    [
        "payload",
        "status",
        "limits",
        "limit",
        "used",
        "duration",
        "reset",
    ],
)
def test_parse_quota_rejects_malformed_types(case):
    """Reject malformed quota structures instead of coercing API values."""
    payload = quota_payload()
    if case == "payload":
        payload = []
    elif case == "status":
        payload["status"] = True
    elif case == "limits":
        payload["limits"] = {}
    elif case == "limit":
        payload["limits"].append("not-a-limit")
    elif case == "used":
        payload["limits"][1]["primary"]["usedPercent"] = "40"
    elif case == "duration":
        payload["limits"][1]["primary"]["windowDurationMins"] = True
    else:
        payload["limits"][1]["primary"]["resetsAt"] = math.inf

    with pytest.raises(ValueError):
        parse_quota_payload(payload, now=1_700_000_000)


def test_parse_quota_rejects_oversized_limit_collection():
    """Bound quota collections before walking attacker-controlled content."""
    payload = quota_payload()
    payload["limits"] = payload["limits"] * 51

    with pytest.raises(ValueError, match="limits"):
        parse_quota_payload(payload, now=1_700_000_000)


def test_parse_quota_rejects_integer_too_large_for_float_conversion():
    """Treat an extreme JSON integer as malformed instead of leaking OverflowError."""
    payload = quota_payload()
    payload["limits"][1]["primary"]["usedPercent"] = int("9" * 309)

    with pytest.raises(ValueError):
        parse_quota_payload(payload, now=1_700_000_000)


def test_parse_queue_filters_status_sorts_counts_and_limits_titles():
    """Summarize actionable queue rows before limiting display titles."""
    long_title = "长" * 205
    payload = {
        "state": "待派发",
        "items": [
            queue_item("第六项", "pending", 6),
            queue_item("已完成", "completed", 1),
            queue_item("第二项", "waiting_quota", 2),
            queue_item("失败", "failed", 3),
            queue_item(long_title, "bridge_pending", 1),
            queue_item("已派发", "dispatched", 4),
            queue_item("第五项", "needs_attention", 5),
            queue_item("监控", "watching", 5),
            queue_item("第四项", "resuming", 4),
            queue_item("第三项", "running", 3),
        ],
        "discovery": {"error": None},
    }

    snapshot = parse_queue_payload(payload)

    assert snapshot == QueueSnapshot(
        count=6,
        titles=("长" * 200, "第二项", "第三项", "第四项", "第五项"),
        state="待派发",
        error="",
    )


def test_parse_queue_caps_status_and_error_text():
    """Cap non-title API text retained by the compact snapshot."""
    snapshot = parse_queue_payload(
        {
            "state": "s" * 400,
            "items": [],
            "discovery": {"error": "e" * 400},
        }
    )

    assert snapshot.state == "s" * 300
    assert snapshot.error == "e" * 300


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"items": {}},
        {"items": ["not-an-item"]},
        {"items": [queue_item("x", 5, 1)]},
        {"items": [queue_item(5, "pending", 1)]},
        {"items": [queue_item("x", "pending", True)]},
        {"items": [], "state": 5},
        {"items": [], "discovery": []},
        {"items": [], "discovery": {"error": 5}},
    ],
)
def test_parse_queue_rejects_malformed_types(payload):
    """Reject wrong queue types instead of producing misleading summaries."""
    with pytest.raises(ValueError):
        parse_queue_payload(payload)


def test_parse_queue_rejects_oversized_item_collection():
    """Bound queue collections before sorting or copying titles."""
    payload = {
        "items": [
            queue_item(f"任务 {index}", "pending", index)
            for index in range(support.MAX_QUEUE_ITEMS + 1)
        ]
    }

    with pytest.raises(ValueError, match="items"):
        parse_queue_payload(payload)


def test_load_state_returns_defaults_for_missing_file(tmp_path):
    """Use product defaults when no state has been persisted."""
    assert load_state(tmp_path / "overlay-state.json") == OverlayState()


def test_state_round_trip_preserves_strict_valid_values(tmp_path):
    """Atomically save and load the complete validated state schema."""
    path = tmp_path / "state" / "overlay-state.json"
    state = OverlayState(
        overlay_visible=True,
        queue_collapsed=True,
        custom_position=True,
        relative_x=-123.5,
        relative_bottom=456,
    )

    assert save_state(path, state) is True
    assert load_state(path) == state


@pytest.mark.parametrize(
    "field",
    ["overlay_visible", "queue_collapsed", "custom_position"],
)
def test_load_state_requires_strict_boolean_fields(tmp_path, field):
    """Reject integer lookalikes for every persisted boolean field."""
    path = tmp_path / "overlay-state.json"
    payload = {
        "overlay_visible": False,
        "queue_collapsed": False,
        "custom_position": False,
        "relative_x": None,
        "relative_bottom": None,
    }
    payload[field] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_state(path) == OverlayState()


@pytest.mark.parametrize(
    ("relative_x", "relative_bottom"),
    [
        (1, None),
        (None, 1),
        (True, 1),
        (math.nan, 1),
        (1, math.inf),
        (100_000.1, 1),
        (1, -100_000.1),
    ],
)
def test_load_state_rejects_unpaired_nonfinite_or_out_of_range_coordinates(
    tmp_path, relative_x, relative_bottom
):
    """Accept only finite paired numeric anchors within the documented range."""
    path = tmp_path / "overlay-state.json"
    payload = {
        "overlay_visible": False,
        "queue_collapsed": False,
        "custom_position": False,
        "relative_x": relative_x,
        "relative_bottom": relative_bottom,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_state(path) == OverlayState()


def test_load_state_requires_coordinates_for_custom_position(tmp_path):
    """Reject a custom-position flag that has no usable anchor."""
    path = tmp_path / "overlay-state.json"
    path.write_text(
        json.dumps(
            {
                "overlay_visible": False,
                "queue_collapsed": False,
                "custom_position": True,
                "relative_x": None,
                "relative_bottom": None,
            }
        ),
        encoding="utf-8",
    )

    assert load_state(path) == OverlayState()


def test_extreme_json_integer_falls_back_and_cannot_be_saved(tmp_path):
    """Keep load and save within their documented corrupt-state behavior."""
    path = tmp_path / "overlay-state.json"
    huge = int("9" * 309)
    payload = {
        "overlay_visible": False,
        "queue_collapsed": False,
        "custom_position": True,
        "relative_x": huge,
        "relative_bottom": 1,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_state(path) == OverlayState()
    assert save_state(
        path,
        OverlayState(
            custom_position=True,
            relative_x=huge,
            relative_bottom=1,
        ),
    ) is False


def test_load_state_rejects_files_larger_than_sixteen_kibibytes(tmp_path):
    """Reject an oversized state file before JSON parsing."""
    path = tmp_path / "overlay-state.json"
    path.write_bytes(b" " * (16 * 1024 + 1))

    assert load_state(path) == OverlayState()


def test_invalid_state_returns_defaults_without_overwriting_source(tmp_path):
    """Leave corrupt bytes untouched until a later valid user operation."""
    path = tmp_path / "overlay-state.json"
    original = b'{"overlay_visible":"false"}'
    path.write_bytes(original)

    assert load_state(path) == OverlayState()
    assert path.read_bytes() == original


def test_failed_atomic_replace_preserves_old_state(tmp_path, monkeypatch):
    """Keep the prior file and clean the temporary file after replace failure."""
    path = tmp_path / "overlay-state.json"
    old_state = OverlayState(overlay_visible=True)
    assert save_state(path, old_state) is True

    def fail_replace(source, destination):
        """Simulate an operating-system failure at the atomic commit point."""
        raise OSError("replace failed")

    monkeypatch.setattr(support.os, "replace", fail_replace)

    assert save_state(path, OverlayState(queue_collapsed=True)) is False
    assert load_state(path) == old_state
    assert list(tmp_path.glob(".overlay-state.json.*.tmp")) == []


def test_public_functions_and_dataclasses_have_docstrings():
    """Keep every Task 2 public callable documented at function level."""
    callables = [
        Rect,
        OverlayState,
        QuotaSnapshot,
        QueueSnapshot,
        scale_length,
        navigation_rect,
        default_panel_rect,
        custom_panel_rect,
        capture_relative_anchor,
        clamp_rect,
        overlay_hit_target,
        exceeds_drag_threshold,
        parse_quota_payload,
        parse_queue_payload,
        load_state,
        save_state,
    ]

    assert all(value.__doc__ and value.__doc__.strip() for value in callables)
