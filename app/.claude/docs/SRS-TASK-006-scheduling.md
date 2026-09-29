# Software Requirements Specification

**Document ID**: SRS-TASK-006-scheduling
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 9.4, 10

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for AutoApply Phase 6 (Scheduling & Daily Planner): a configurable schedule that automatically starts and stops the bot within user-defined time windows on selected days of the week.

### 1.2 Scope
The system SHALL provide: a `ScheduleConfig` Pydantic model nested in `BotConfig`, a background scheduler thread that checks every 60 seconds, support for overnight windows (e.g., 22:00-06:00), auto-start and auto-stop of the bot based on the schedule, REST API endpoints for reading and updating the schedule, and a dashboard UI for configuring the schedule.

The system SHALL NOT provide: multiple concurrent schedules, timezone configuration (uses system local time), holiday calendars, cron expression syntax, per-search-criteria scheduling.

### 1.3 Definitions
| Term | Definition |
|------|-----------|
| Schedule window | A contiguous time range (start_time to end_time) on specified days when the bot should run |
| Overnight window | A schedule window where start_time > end_time, spanning midnight (e.g., 22:00-06:00) |
| Auto-started | A bot run initiated by the scheduler, as opposed to manually started by the user |
| Scheduler thread | Background daemon thread that checks the schedule every 60 seconds |

---

## 2. Functional Requirements

### FR-060: ScheduleConfig Model

**Description**: The system SHALL define a `ScheduleConfig` Pydantic model with fields for enabling/disabling the schedule, selecting days of the week, and defining start/end times. This model SHALL be nested in `BotConfig` as `BotConfig.schedule`.
**Priority**: P0
**Dependencies**: Existing `BotConfig` Pydantic model

**Acceptance Criteria**:
- **AC-060-1**: `ScheduleConfig` has field `enabled` (bool, default False).
- **AC-060-2**: `ScheduleConfig` has field `days_of_week` (list of str, default empty list). Valid values: "mon", "tue", "wed", "thu", "fri", "sat", "sun".
- **AC-060-3**: `ScheduleConfig` has field `start_time` (str, default "09:00") in HH:MM format.
- **AC-060-4**: `ScheduleConfig` has field `end_time` (str, default "17:00") in HH:MM format.
- **AC-060-5**: Given `BotConfig` is loaded from settings, When accessing `config.bot.schedule`, Then it returns the `ScheduleConfig` instance with defaults if not previously set.

---

### FR-061: Scheduler Engine

**Description**: The system SHALL run a background daemon thread that checks every 60 seconds whether the current time falls within the configured schedule window and auto-starts the bot if so.
**Priority**: P0
**Dependencies**: FR-060, FR-052 (Bot Thread Integration)

**Acceptance Criteria**:
- **AC-061-1**: Given the schedule is enabled and the current day is in `days_of_week` and the current time is within the `start_time`-`end_time` window, When the scheduler checks, Then it starts the bot if it is not already running.
- **AC-061-2**: Given an overnight window (start_time > end_time, e.g., 22:00-06:00), When the current time is 23:00 on a scheduled day, Then the scheduler recognizes this as within the window and starts the bot.
- **AC-061-3**: Given an overnight window (22:00-06:00), When the current time is 03:00 and the previous day was a scheduled day, Then the scheduler recognizes this as within the window.
- **AC-061-4**: Given the schedule is disabled (`enabled` is False), When the scheduler checks, Then it takes no action.
- **AC-061-5**: Given the bot is already running (whether auto-started or manually started), When the scheduler detects the window is open, Then it does not start a duplicate bot.
- **AC-061-6**: The scheduler thread SHALL run as a daemon thread and SHALL check every 60 seconds.

---

### FR-062: Auto-Stop

**Description**: The system SHALL automatically stop the bot when the schedule window closes, but only if the bot was auto-started by the scheduler.
**Priority**: P0
**Dependencies**: FR-061

**Acceptance Criteria**:
- **AC-062-1**: Given the bot was auto-started by the scheduler and the current time moves outside the schedule window, When the scheduler checks, Then it stops the bot.
- **AC-062-2**: Given the bot was manually started by the user via `POST /api/bot/start`, When the schedule window closes, Then the scheduler does NOT stop the bot.
- **AC-062-3**: Given the bot was auto-started, When the scheduler stops it, Then the stop is graceful (sets stop_flag, waits for current operation to finish).

---

### FR-063: Schedule API

**Description**: The system SHALL expose REST API endpoints for reading and updating the bot schedule.
**Priority**: P0
**Dependencies**: FR-060

**Acceptance Criteria**:
- **AC-063-1**: Given `GET /api/bot/schedule` is called, Then it returns the current `ScheduleConfig` as JSON with fields: enabled, days_of_week, start_time, end_time.
- **AC-063-2**: Given `PUT /api/bot/schedule` is called with valid JSON, When the payload contains valid day names and time formats, Then the schedule is saved and the response confirms success.
- **AC-063-3**: Given `PUT /api/bot/schedule` is called with an invalid day name (e.g., "monday" instead of "mon"), When validating, Then it returns 400 with an error message listing valid day names.
- **AC-063-4**: Given `PUT /api/bot/schedule` is called with an invalid time format (e.g., "9:00" or "25:00"), When validating, Then it returns 400 with an error message specifying HH:MM format with hours 0-23 and minutes 0-59.
- **AC-063-5**: Given `PUT /api/bot/schedule` is called with `enabled: true` but empty `days_of_week`, When validating, Then it returns 400 requiring at least one day.

---

### FR-064: Dashboard Schedule UI

**Description**: The system SHALL display schedule configuration controls in the Settings panel of the dashboard, including an enable toggle, day-of-week checkboxes, and time pickers.
**Priority**: P1
**Dependencies**: FR-063

**Acceptance Criteria**:
- **AC-064-1**: Given the Settings panel is open, When the user views the schedule section, Then it shows a toggle for enabling/disabling the schedule.
- **AC-064-2**: Given the schedule section is visible, When the user views the day selection, Then it shows checkboxes for Mon through Sun.
- **AC-064-3**: Given the schedule section is visible, When the user views the time fields, Then it shows start_time and end_time inputs in HH:MM format.
- **AC-064-4**: Given the schedule is enabled, When viewing the dashboard header or bot status area, Then an "Active" badge or indicator is displayed.
- **AC-064-5**: Given the user modifies the schedule and clicks save, When the PUT request succeeds, Then the UI confirms the save and reflects the new settings.

---

## 3. Non-Functional Requirements

### NFR-028: Schedule Input Validation
**Description**: The schedule API SHALL validate day names against an allowlist ("mon", "tue", "wed", "thu", "fri", "sat", "sun") and time format against HH:MM with hours in range 0-23 and minutes in range 0-59.
**Metric**: All invalid inputs rejected with descriptive 400 error before any state change.
**Priority**: P0

### NFR-029: Auto-Stop Scope
**Description**: The scheduler SHALL only stop bots that it auto-started. Manually started bots SHALL never be interrupted by the scheduler.
**Metric**: A manually started bot continues running past the schedule window close without interruption.
**Priority**: P0

---

## 4. Out of Scope

- **Multiple schedules** — Only one schedule window supported
- **Timezone configuration** — Uses system local time only
- **Holiday calendars** — No holiday awareness or skip logic
- **Cron expression syntax** — Simple day + time window model only
- **Per-search-criteria scheduling** — Same schedule applies to all search criteria
