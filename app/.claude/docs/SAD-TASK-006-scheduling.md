# System Architecture Document

**Document ID**: SAD-TASK-006-scheduling
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-006-scheduling

---

## 1. Executive Summary

This architecture adds a scheduling subsystem to AutoApply, enabling the bot to run automatically within user-defined time windows on selected days of the week. The design introduces a `ScheduleConfig` Pydantic model nested in `BotConfig`, a standalone `BotScheduler` class in `core/scheduler.py` that uses a callback-based design for testability, and corresponding API endpoints and UI controls in the settings panel.

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────────┐      ┌──────────────────┐
│ templates/        │      │   app.py          │
│ index.html        │─────▶│ Schedule API      │
│ (Settings panel)  │      │ endpoints         │
│ [toggle, days,    │      │ [GET/PUT          │
│  time pickers]    │      │  /api/schedule]   │
└──────────────────┘      └────────┬──────────┘
                                    │
                          ┌─────────▼──────────┐
                          │  BotScheduler       │
                          │  core/scheduler.py  │
                          │  [callback-based]   │
                          └─────────┬──────────┘
                                    │ callbacks:
                          ┌─────────┼──────────┐
                          ▼         ▼          ▼
                    get_schedule  start_bot  stop_bot
                    (reads config) (starts)  (stops)
                          │         │          │
                          ▼         ▼          ▼
                    ┌──────────────────────────────┐
                    │  config/settings.py           │
                    │  BotConfig.schedule            │
                    │  ScheduleConfig model          │
                    └──────────────────────────────┘
```

### 2.2 Data Flow

1. User configures schedule in Settings UI: toggle enabled, select days, set start/end time.
2. Frontend sends `PUT /api/schedule` with `ScheduleConfig` JSON body.
3. `app.py` validates and saves to `config.json` via `BotConfig.schedule`.
4. `BotScheduler._tick()` fires every 60 seconds (threading.Timer loop).
5. `_tick()` calls `get_schedule()` callback to read current `ScheduleConfig`.
6. `is_within_schedule(schedule, now)` checks if current day-of-week is in `days_of_week` and current time is within `start_time`..`end_time` window.
7. If within schedule and bot is not running: calls `start_bot()` callback, sets `_auto_started = True`.
8. If outside schedule and bot was auto-started: calls `stop_bot()` callback, clears `_auto_started`.
9. Manual starts/stops are never overridden — `_auto_started` flag distinguishes manual vs automatic.

### 2.3 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| UI | `templates/index.html` | Schedule toggle, day checkboxes, time pickers |
| API | `app.py` | GET/PUT /api/schedule endpoints, scheduler init |
| Service | `core/scheduler.py` | `BotScheduler` class, `is_within_schedule()` |
| Config | `config/settings.py` | `ScheduleConfig` Pydantic model |
| Data | `config.json` | Persisted schedule configuration |

---

## 3. Interface Contracts

### 3.1 config.settings.ScheduleConfig

**Purpose**: Pydantic model representing bot scheduling configuration.
**Category**: data model

**Fields**:
| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| enabled | `bool` | `False` | — | Whether scheduling is active |
| days_of_week | `list[int]` | `[0,1,2,3,4]` | Values 0-6 (Mon=0, Sun=6) | Days the bot should run |
| start_time | `str` | `"09:00"` | HH:MM format | Start of daily window |
| end_time | `str` | `"17:00"` | HH:MM format | End of daily window |

**Nesting**: `BotConfig.schedule: ScheduleConfig = ScheduleConfig()`.

---

### 3.2 core.scheduler.is_within_schedule(schedule, now)

**Purpose**: Determine if a given datetime falls within the schedule window.
**Category**: query (pure function)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| schedule | `ScheduleConfig` | yes | Schedule configuration |
| now | `datetime` | yes | Current datetime to check |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `bool` | `True` if `now` is on an enabled day and within the time window |

**Logic**:
1. Check `now.weekday()` is in `schedule.days_of_week`.
2. Parse `schedule.start_time` and `schedule.end_time` to `time` objects.
3. If `start_time <= end_time`: normal window, check `start <= now.time() <= end`.
4. If `start_time > end_time`: overnight window (e.g., 22:00–06:00), check `now.time() >= start OR now.time() <= end`.

**Errors**: None — returns `False` on any parse error.
**Thread Safety**: Safe — pure function with no shared state.

**Example**:
```python
from datetime import datetime
from config.settings import ScheduleConfig

sched = ScheduleConfig(enabled=True, days_of_week=[0,1,2,3,4], start_time="09:00", end_time="17:00")
is_within_schedule(sched, datetime(2026, 3, 10, 12, 0))  # True (Tuesday, noon)
is_within_schedule(sched, datetime(2026, 3, 10, 20, 0))  # False (Tuesday, 8pm)
is_within_schedule(sched, datetime(2026, 3, 14, 12, 0))  # False (Saturday)
```

---

### 3.3 core.scheduler.BotScheduler

**Purpose**: Timer-based scheduler that starts/stops the bot based on schedule config.
**Category**: service (stateful)

**Constructor**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| get_schedule | `Callable[[], ScheduleConfig]` | yes | Callback to retrieve current schedule |
| start_bot | `Callable[[], None]` | yes | Callback to start the bot |
| stop_bot | `Callable[[], None]` | yes | Callback to stop the bot |
| is_bot_running | `Callable[[], bool]` | yes | Callback to check if bot is currently running |

**Methods**:

#### BotScheduler.start()
**Purpose**: Begin the 60-second tick loop.
**Side Effects**: Spawns a daemon `threading.Timer`.

#### BotScheduler.stop()
**Purpose**: Cancel the tick loop and clean up.
**Side Effects**: Cancels pending timer.

#### BotScheduler._tick()
**Purpose**: Called every 60 seconds. Reads schedule, checks window, starts/stops bot.
**Category**: internal

**Logic**:
1. Call `get_schedule()` to get current `ScheduleConfig`.
2. If `schedule.enabled` is False: if `_auto_started`, call `stop_bot()` and clear flag. Return.
3. Call `is_within_schedule(schedule, datetime.now())`.
4. If within schedule and not `is_bot_running()`: call `start_bot()`, set `_auto_started = True`.
5. If outside schedule and `_auto_started` and `is_bot_running()`: call `stop_bot()`, set `_auto_started = False`.
6. Schedule next `_tick()` in 60 seconds.

**State**:
| Field | Type | Description |
|-------|------|-------------|
| _auto_started | `bool` | True if the scheduler (not the user) started the bot |
| _timer | `Timer \| None` | Reference to the pending Timer for cancellation |

**Thread Safety**: `_tick()` runs on a timer thread. Callbacks must be thread-safe. The `_auto_started` flag is a simple boolean read/written only by the timer thread.

---

### 3.4 app.py — Schedule API Endpoints

#### GET /api/schedule

**Purpose**: Retrieve current schedule configuration.
**Category**: query

**Output**:
```json
{
  "enabled": true,
  "days_of_week": [0, 1, 2, 3, 4],
  "start_time": "09:00",
  "end_time": "17:00"
}
```

#### PUT /api/schedule

**Purpose**: Update schedule configuration.
**Category**: command

**Input**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| enabled | `bool` | no | Toggle scheduling |
| days_of_week | `list[int]` | no | Days to run |
| start_time | `str` | no | Start time HH:MM |
| end_time | `str` | no | End time HH:MM |

**Output**: Updated `ScheduleConfig` as JSON.
**Errors**: 400 if validation fails.

### 3.5 app.py — Shared Helpers

| Helper | Purpose | Used By |
|--------|---------|---------|
| `_get_schedule()` | Reads `config.bot.schedule` and returns `ScheduleConfig` | `BotScheduler` callback |
| `_scheduler_start_bot()` | Starts bot thread (same as manual start) | `BotScheduler` callback |
| `_scheduler_stop_bot()` | Stops bot thread (same as manual stop) | `BotScheduler` callback |
| `_is_bot_running()` | Returns `bot_state.is_running` | `BotScheduler` callback |
| `_init_scheduler()` | Creates and starts `BotScheduler` instance | Called at app startup |

### 3.6 templates/index.html — Schedule UI

**Location**: Settings section, new "Schedule" subsection.

**UI Elements**:
| Element | Type | Binds To |
|---------|------|----------|
| Enable Schedule | Toggle switch | `schedule.enabled` |
| Days of Week | 7 checkboxes (Mon-Sun) | `schedule.days_of_week` |
| Start Time | Time picker input | `schedule.start_time` |
| End Time | Time picker input | `schedule.end_time` |
| Save Schedule | Button | PUT /api/schedule |

---

## 4. Data Model

No new database tables. Schedule configuration is stored in `config.json` under the `bot` section.

### 4.1 Config Schema Extension

```python
class BotConfig(BaseModel):
    # ... existing fields ...
    schedule: ScheduleConfig = ScheduleConfig()

class ScheduleConfig(BaseModel):
    enabled: bool = False
    days_of_week: list[int] = [0, 1, 2, 3, 4]  # Mon-Fri
    start_time: str = "09:00"
    end_time: str = "17:00"
```

### 4.2 config.json Representation

```json
{
  "bot": {
    "schedule": {
      "enabled": false,
      "days_of_week": [0, 1, 2, 3, 4],
      "start_time": "09:00",
      "end_time": "17:00"
    }
  }
}
```

---

## 5. Error Handling Strategy

| Scenario | Handling | User Impact |
|----------|----------|-------------|
| Invalid time format in PUT | Pydantic validation rejects, 400 response | Frontend shows error |
| Invalid day number (>6) | Pydantic validation rejects, 400 response | Frontend shows error |
| Scheduler callback raises | `_tick()` catches all exceptions, logs, continues next tick | Scheduler remains alive |
| Timer thread dies | Logged; scheduler becomes inactive | Bot does not auto-start/stop |
| Bot start fails during auto-start | `start_bot()` callback raises, caught by `_tick()` | Logged, retried on next tick |
| Manual start during scheduled window | `_auto_started` stays False, scheduler won't auto-stop it | User retains control |
| Manual stop during scheduled window | `_auto_started` cleared, scheduler will re-start on next tick | Expected behavior — schedule re-activates |

---

## 6. Architecture Decision Records

### ADR-013: Callback-Based Scheduler Design

**Status**: accepted
**Context**: The scheduler needs to start/stop the bot based on time windows. It could directly import Flask app internals, use signals/events, or use a callback pattern.
**Decision**: Use a callback-based design where the scheduler receives `get_schedule`, `start_bot`, `stop_bot`, and `is_bot_running` as constructor arguments.
**Rationale**: The callback pattern decouples the scheduler from Flask entirely. This makes the scheduler testable in isolation — unit tests can pass mock callbacks without spinning up a Flask app. It also makes the scheduler reusable if the hosting framework changes (e.g., migrating away from Flask).
**Consequences**: `app.py` must define thin wrapper functions for each callback. The scheduler cannot access any Flask state directly, which is the desired boundary.

---

## 7. Design Traceability Matrix

| Requirement | Type | Design Component | Interface | ADR |
|-------------|------|-----------------|-----------|-----|
| FR-060 | FR | config/settings.py | ScheduleConfig model | — |
| FR-061 | FR | core/scheduler.py | is_within_schedule() | ADR-013 |
| FR-062 | FR | core/scheduler.py | BotScheduler._tick(), _auto_started | ADR-013 |
| FR-063 | FR | app.py | GET/PUT /api/schedule, _init_scheduler() | ADR-013 |
| FR-064 | FR | templates/index.html | Schedule toggle, day checkboxes, time pickers | — |
| FR-062.1 | FR | core/scheduler.py | Overnight schedule support (start > end) | ADR-013 |
| FR-062.2 | FR | core/scheduler.py | Manual vs auto-start distinction | ADR-013 |

---

## 8. Implementation Plan

| Order | Task ID | Description | Depends On | Size | FR Coverage |
|-------|---------|-------------|------------|------|-------------|
| 1 | IMPL-030 | Add `ScheduleConfig` model to `config/settings.py`, nest in `BotConfig.schedule` | — | S | FR-060 |
| 2 | IMPL-031 | Create `core/scheduler.py` with `is_within_schedule()` pure function | IMPL-030 | S | FR-061 |
| 3 | IMPL-032 | Add `BotScheduler` class to `core/scheduler.py` with callback pattern, `_tick()`, `_auto_started` | IMPL-031 | M | FR-062 |
| 4 | IMPL-033 | Add schedule API endpoints to `app.py` (GET/PUT /api/schedule), shared helpers, `_init_scheduler()` | IMPL-030, IMPL-032 | M | FR-063 |
| 5 | IMPL-034 | Add schedule section to Settings UI in `templates/index.html` | IMPL-033 | M | FR-064 |
| 6 | IMPL-035 | Unit tests for `is_within_schedule()` — normal, overnight, edge cases | IMPL-031 | M | FR-061 |
| 7 | IMPL-036 | Unit tests for `BotScheduler` — mock callbacks, tick behavior, auto-started flag | IMPL-032 | M | FR-062 |
| 8 | IMPL-037 | Integration tests — schedule API + scheduler interaction | IMPL-033 | S | FR-063 |
