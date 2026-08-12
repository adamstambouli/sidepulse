from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .device_writer import (
    DEFAULT_FILE_NAME,
    DeviceWriteError,
    resolve_target_path,
    write_led_program,
)
from .models import MODE_PRIORITY, AgentMode, AgentStatus


class LedDisplayState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    ASK = "ask"
    BLOCKED = "blocked"


LED_STATE_LABELS: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: "Idle",
    LedDisplayState.WORKING: "Working",
    LedDisplayState.DONE: "Done",
    LedDisplayState.ASK: "Ask",
    LedDisplayState.BLOCKED: "Blocked",
}


def relative_luminance(hex_color: str) -> float:
    """Rec.709 luminance: how bright a color actually looks, not its RGB level."""
    red, green, blue = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def scaled_to_luminance(hex_color: str, target: float) -> str:
    """Dim a color until it reads as bright as `target`. Never brightens."""
    current = relative_luminance(hex_color)
    if current <= 0.0:
        return hex_color
    factor = min(1.0, target / current)
    red, green, blue = (
        round(int(hex_color[i : i + 2], 16) * factor) for i in (1, 3, 5)
    )
    return f"#{red:02X}{green:02X}{blue:02X}"


# Blue rather than cyan: cyan and the Done green are adjacent hues and read as
# the same color on a diffused LED, which hides the working/done difference.
# Blue also sits at full scale already, so it sets the luminance budget for the
# rest of the palette -- nothing else can be raised to meet it, only lowered.
WORKING_BLUE = "#0033FF"
WORKING_LUMINANCE = relative_luminance(WORKING_BLUE)

# The eye peaks near green and bottoms out near blue, so a raw #00FF66 reads
# ~3.5x brighter than the blue at the same drive level. Done is also the only
# state held steady at full duty (up to 20 minutes), which makes it both the
# loudest color and the hardest on the LED. Matching it to the blue fixes the
# glare and cuts its sustained load by the same factor.
DONE_GREEN = scaled_to_luminance("#00FF66", WORKING_LUMINANCE)
# Ask keeps some extra headroom: it is an alert, so it should out-shine the
# ambient states -- just not by 3x.
ASK_AMBER = scaled_to_luminance("#FFA000", WORKING_LUMINANCE * 1.6)
# Pure red is already at the blue's luminance; red is inherently dim to the eye,
# and Blocked earns its salience from the fast double blink instead.
BLOCKED_RED = "#FF0000"
IDLE_DIM = "#020204"
DEVICE_LED_COUNTS = {
    "sidepulsedot": 2,
    "sidepulsepro": 8,
}


@dataclass(frozen=True)
class LedStatusWrite:
    state: LedDisplayState
    target: Path | None
    program: str
    changed: bool
    error: str | None = None

    @property
    def label(self) -> str:
        return LED_STATE_LABELS[self.state]


def display_state_for_mode(mode: AgentMode) -> LedDisplayState:
    if mode == AgentMode.BLOCKED_ERROR:
        return LedDisplayState.BLOCKED
    if mode == AgentMode.WAITING_FOR_INPUT:
        return LedDisplayState.ASK
    if mode in {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }:
        return LedDisplayState.WORKING
    if mode == AgentMode.COMPLETED:
        return LedDisplayState.DONE
    return LedDisplayState.IDLE


def program_for_display_state(
    state: LedDisplayState,
    *,
    led_count: int = 8,
    brightness: int | float = 255,
) -> str:
    if state == LedDisplayState.IDLE:
        return apply_brightness(
            "\n".join(
                [
                    "off",
                    f"{IDLE_DIM} 6s pulse",
                    "repeat",
                ]
            ),
            brightness,
        )
    if state == LedDisplayState.ASK:
        # Faster than Working: this one is waiting on you.
        return apply_brightness(
            "\n".join(
                [
                    "off",
                    f"{ASK_AMBER} 0.7s pulse",
                    "repeat",
                ]
            ),
            brightness,
        )
    if state == LedDisplayState.BLOCKED:
        # Double blink then a pause: reads as an alarm, not the slow Ask breath.
        return apply_brightness(
            "\n".join(
                [
                    "off",
                    f"{BLOCKED_RED} 0.42s pulse",
                    "off 0.12s",
                    f"{BLOCKED_RED} 0.42s pulse",
                    "off 1.1s",
                    "repeat",
                ]
            ),
            brightness,
        )
    if state == LedDisplayState.DONE:
        return apply_brightness(DONE_GREEN, brightness)
    if state == LedDisplayState.WORKING:
        return apply_brightness(rolling_program(WORKING_BLUE, led_count=led_count), brightness)
    raise ValueError(f"Unknown LED display state: {state}")


def rolling_program(color: str, *, led_count: int = 8) -> str:
    # Slow and breathing on purpose: an agent that is still working needs
    # nothing from you, so it should not compete with Ask or Blocked.
    count = max(2, min(8, int(led_count)))
    delay_ms = 400 if count == 2 else 140
    duration_ms = 1200
    segments: list[str] = []
    for active_index in range(count):
        delay = active_index * delay_ms
        segments.append(f"{active_index}:{color} {duration_ms}ms pulse {delay}ms")
    return "\n".join(
        [
            "off 160ms cosine",
            "; ".join(segments),
            "repeat",
        ]
    )


FLEET_EMPTY = "#000000"
FLEET_COLORS = {
    LedDisplayState.WORKING: WORKING_BLUE,
    LedDisplayState.ASK: ASK_AMBER,
    LedDisplayState.BLOCKED: BLOCKED_RED,
    LedDisplayState.DONE: DONE_GREEN,
    LedDisplayState.IDLE: IDLE_DIM,
}
# The controller runs one timeline, so per-slot animations cannot have their own
# loop. Instead each animated state gets a distinct duty cycle inside the shared
# loop, and rate tracks urgency: the faster it blinks, the more it wants you.
# Working needs nothing from you, so it breathes slowly.
FLEET_PULSE_MS = {
    LedDisplayState.BLOCKED: 400,
    LedDisplayState.ASK: 700,
    LedDisplayState.WORKING: 1800,
}
# A band of 3+ LEDs animates as a wave across itself instead of pulsing in
# unison: motion is far easier to catch peripherally than a change in level.
# Done stays deliberately still, so "moving vs settled" reads before color does.
FLEET_STAGGER_MS = {
    LedDisplayState.BLOCKED: 60,
    LedDisplayState.ASK: 90,
    LedDisplayState.WORKING: 200,
}
FLEET_MIN_STAGGER_WIDTH = 3
# A finished agent holds its band so you can notice it, but not for the full
# 20 minute Completed window: on a shared bar an old green crowds out the
# agents still running. Safety net for sessions that die without a SessionEnd.
FLEET_DONE_VISIBLE_SECONDS = 300.0
FLEET_STATE_ORDER = (
    LedDisplayState.BLOCKED,
    LedDisplayState.ASK,
    LedDisplayState.DONE,
    LedDisplayState.WORKING,
    LedDisplayState.IDLE,
)


@dataclass(frozen=True)
class FleetBand:
    """One agent's contiguous run of LEDs."""

    state: LedDisplayState
    width: int


def fleet_program(
    bands: Sequence[FleetBand],
    *,
    led_count: int = 8,
    brightness: int | float = 255,
) -> str:
    """Each agent owns a band of LEDs, colored and animated by its own state."""
    count = max(1, min(8, int(led_count)))
    bases: list[str] = []
    pulses: list[str] = []

    index = 0
    for band in bands:
        duration = FLEET_PULSE_MS.get(band.state)
        stagger = (
            FLEET_STAGGER_MS.get(band.state, 0)
            if band.width >= FLEET_MIN_STAGGER_WIDTH
            else 0
        )
        for offset in range(max(0, int(band.width))):
            if index >= count:
                break
            if duration is None:
                bases.append(f"{index}:{FLEET_COLORS[band.state]}")
            else:
                # Animated LEDs start dark so the pulse returns to dark rather
                # than to whatever this slot happened to show before.
                bases.append(f"{index}:{FLEET_EMPTY}")
                segment = f"{index}:{FLEET_COLORS[band.state]} {duration}ms pulse"
                delay = offset * stagger
                if delay:
                    segment += f" {delay}ms"
                pulses.append(segment)
            index += 1

    while index < count:
        bases.append(f"{index}:{FLEET_EMPTY}")
        index += 1

    lines = ["; ".join(bases)]
    if pulses:
        # Unmentioned LEDs hold the base set on the previous line.
        lines.append("; ".join(pulses))
        lines.append("repeat")
    return apply_brightness("\n".join(lines), brightness)


def fleet_representative_state(
    bands: Sequence[FleetBand],
) -> LedDisplayState:
    present = {band.state for band in bands if band.width > 0}
    for state in FLEET_STATE_ORDER:
        if state in present:
            return state
    return LedDisplayState.IDLE


class FleetSlots:
    """Sticky LED slot per agent.

    A slot is held for as long as the agent is visible, so one agent finishing
    never shuffles its neighbors out from under you.
    """

    def __init__(self, led_count: int = 8) -> None:
        self.led_count = max(1, min(8, int(led_count)))
        self._slots: dict[str, int] = {}

    def assign(self, agent_ids: Sequence[str]) -> dict[str, int]:
        wanted = list(dict.fromkeys(agent_ids))
        for agent_id in [key for key in self._slots if key not in wanted]:
            del self._slots[agent_id]

        for agent_id in wanted:
            if agent_id in self._slots:
                continue
            taken = set(self._slots.values())
            free = next(
                (index for index in range(self.led_count) if index not in taken),
                None,
            )
            if free is None:
                # More agents than LEDs: the overflow simply has no slot.
                continue
            self._slots[agent_id] = free
        return dict(self._slots)

    def resize(self, led_count: int) -> None:
        count = max(1, min(8, int(led_count)))
        if count == self.led_count:
            return
        self.led_count = count
        self._slots = {
            agent_id: index
            for agent_id, index in self._slots.items()
            if index < count
        }

    def reset(self) -> None:
        self._slots.clear()


def fleet_slot_key(status: AgentStatus) -> str:
    """A fleet slot belongs to a codebase, not a session.

    Sessions turn over inside one project all the time, so keying by session id
    shows two lights for one window you are actually watching.
    """
    return status.cwd or status.agent_id


def fleet_visible_statuses(
    statuses: Iterable[AgentStatus],
    *,
    now: object = None,
    done_visible_seconds: float = FLEET_DONE_VISIBLE_SECONDS,
) -> tuple[AgentStatus, ...]:
    """One entry per codebase, top-level sessions only.

    Subagents report their own status keyed `provider:agent:*`. They are part of
    one session's work rather than separate agents, so giving each an LED floods
    the bar: three real sessions can easily present as eight. Multiple sessions
    in the same directory collapse to their most actionable one.
    """
    best: dict[str, AgentStatus] = {}
    for status in statuses:
        if ":agent:" in (status.agent_id or ""):
            continue
        if (
            status.mode == AgentMode.COMPLETED
            and done_visible_seconds >= 0
            and status.age_seconds(now) > done_visible_seconds
        ):
            continue
        key = fleet_slot_key(status)
        current = best.get(key)
        if current is None or (
            status.priority,
            -status.updated_at.timestamp(),
        ) < (current.priority, -current.updated_at.timestamp()):
            best[key] = status
    return tuple(best.values())


def fleet_band_widths(count: int, led_count: int = 8) -> list[int]:
    """Spread the whole bar across the agents present.

    Fewer agents means wider bands, so one agent is unmistakable at a glance
    rather than a single lit pixel. Leftover LEDs go to the earliest slots.
    """
    if count <= 0:
        return []
    count = min(count, max(1, led_count))
    base, extra = divmod(led_count, count)
    return [base + (1 if index < extra else 0) for index in range(count)]


def fleet_bands_for_statuses(
    statuses: Iterable[AgentStatus],
    slots: FleetSlots,
    *,
    led_count: int = 8,
) -> list[FleetBand]:
    count = max(1, min(8, int(led_count)))
    ordered = list(statuses)
    slots.resize(count)
    assignment = slots.assign([fleet_slot_key(status) for status in ordered])

    # Sticky ordinal keeps each codebase in the same part of the row even as its
    # band widens or narrows around it.
    placed = sorted(
        (status for status in ordered if fleet_slot_key(status) in assignment),
        key=lambda status: assignment[fleet_slot_key(status)],
    )

    states = [display_state_for_mode(status.mode) for status in placed]
    if states and len(set(states)) == 1:
        # Everyone agrees, so band boundaries carry no information. Merge into
        # one full-width animation, which is far easier to read at a glance.
        return [FleetBand(state=states[0], width=count)]

    return [
        FleetBand(state=state, width=width)
        for state, width in zip(states, fleet_band_widths(len(states), count))
    ]


class CompletionAnnouncer:
    """Hold Done briefly when an agent finishes.

    Completed ranks below Working in MODE_PRIORITY, so with parallel agents a
    single long-runner would otherwise hide every completion forever.
    """

    def __init__(self, hold_seconds: float = 5.0) -> None:
        self.hold_seconds = hold_seconds
        # True only on the refresh where a completion was first seen, so the
        # caller can schedule the follow-up refresh that ends the hold.
        self.started = False
        self._previous: dict[str, AgentMode] = {}
        self._until = 0.0

    def observe(
        self,
        statuses: Iterable[AgentStatus],
        now: float | None = None,
    ) -> bool:
        current = time.monotonic() if now is None else now
        seen: dict[str, AgentMode] = {}
        finished = False
        for status in statuses:
            seen[status.agent_id] = status.mode
            previous = self._previous.get(status.agent_id)
            if (
                status.mode == AgentMode.COMPLETED
                and previous is not None
                and previous != AgentMode.COMPLETED
            ):
                finished = True
        self._previous = seen
        self.started = finished
        if finished:
            self._until = current + self.hold_seconds
        return current < self._until

    def reset(self) -> None:
        self.started = False
        self._previous.clear()
        self._until = 0.0


def announcement_may_show(aggregate_mode: AgentMode) -> bool:
    """A completion must never mask an agent that actually needs the user."""
    unknown = MODE_PRIORITY[AgentMode.UNKNOWN]
    return MODE_PRIORITY.get(aggregate_mode, unknown) > MODE_PRIORITY[
        AgentMode.WAITING_FOR_INPUT
    ]


def write_mode_to_leds(
    mode: AgentMode,
    *,
    device_path: Path | None = None,
    file_name: str = DEFAULT_FILE_NAME,
    dry_run: bool = False,
    brightness: int | float = 255,
) -> LedStatusWrite:
    target = resolve_target_path(device_path=device_path, file_name=file_name)
    state = display_state_for_mode(mode)
    program = program_for_display_state(
        state,
        led_count=led_count_for_target(target),
        brightness=brightness,
    )
    written_target = write_led_program(
        program,
        device_path=target,
        file_name=file_name,
        dry_run=dry_run,
    )
    return LedStatusWrite(
        state=state,
        target=written_target,
        program=program,
        changed=True,
    )


def led_count_for_target(target: Path) -> int:
    name = normalized_device_name(target.parent.name)
    for hint, led_count in DEVICE_LED_COUNTS.items():
        if hint in name:
            return led_count
    return 8


def normalized_device_name(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def normalize_brightness(value: int | float | None) -> int:
    if value is None:
        return 255
    return max(0, min(255, int(round(float(value)))))


def brightness_percent(value: int | float | None) -> int:
    return round(normalize_brightness(value) / 255 * 100)


def apply_brightness(program: str, brightness: int | float = 255) -> str:
    value = normalize_brightness(brightness)
    if value >= 255:
        return program
    return f"brightness {value}\n{program}"


class AgentLedController:
    def __init__(
        self,
        *,
        device_path: Path | None = None,
        file_name: str = DEFAULT_FILE_NAME,
        dry_run: bool = False,
        error_retry_seconds: float = 10.0,
        brightness: int | float = 255,
    ) -> None:
        self.device_path = device_path
        self.file_name = file_name
        self.dry_run = dry_run
        self.error_retry_seconds = error_retry_seconds
        self.brightness = normalize_brightness(brightness)
        self.last_state: LedDisplayState | None = None
        self.last_brightness: int | None = None
        self.last_error: str | None = None
        self.last_target: Path | None = None
        self.last_attempt_monotonic = 0.0
        self.last_program: str | None = None
        self.fleet_slots = FleetSlots()

    def reset(self) -> None:
        self.last_state = None
        self.last_brightness = None
        self.last_error = None
        self.last_target = None
        self.last_attempt_monotonic = 0.0
        self.last_program = None
        self.fleet_slots.reset()

    def sync_mode(self, mode: AgentMode) -> LedStatusWrite:
        state = display_state_for_mode(mode)
        brightness = normalize_brightness(self.brightness)
        now = time.monotonic()
        if state == self.last_state and brightness == self.last_brightness and self.last_error is None:
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
            )
        if (
            state == self.last_state
            and brightness == self.last_brightness
            and self.last_error is not None
            and now - self.last_attempt_monotonic < self.error_retry_seconds
        ):
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_attempt_monotonic = now
        try:
            result = write_mode_to_leds(
                mode,
                device_path=self.device_path,
                file_name=self.file_name,
                dry_run=self.dry_run,
                brightness=brightness,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_state = state
            self.last_brightness = brightness
            self.last_error = str(exc)
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_state = state
        self.last_brightness = brightness
        self.last_error = None
        self.last_target = result.target
        self.last_program = result.program
        return result

    def sync_fleet(self, statuses: Iterable[AgentStatus]) -> LedStatusWrite:
        ordered = list(statuses)
        brightness = normalize_brightness(self.brightness)
        now = time.monotonic()

        try:
            target = resolve_target_path(
                device_path=self.device_path,
                file_name=self.file_name,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_error = str(exc)
            return LedStatusWrite(
                state=LedDisplayState.IDLE,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        led_count = led_count_for_target(target)
        bands = fleet_bands_for_statuses(ordered, self.fleet_slots, led_count=led_count)
        state = fleet_representative_state(bands)
        program = fleet_program(bands, led_count=led_count, brightness=brightness)

        unchanged = program == self.last_program and self.last_error is None
        retrying = (
            program == self.last_program
            and self.last_error is not None
            and now - self.last_attempt_monotonic < self.error_retry_seconds
        )
        if unchanged or retrying:
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error if retrying else None,
            )

        self.last_attempt_monotonic = now
        try:
            written = write_led_program(
                program,
                device_path=target,
                file_name=self.file_name,
                dry_run=self.dry_run,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_error = str(exc)
            self.last_program = program
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_state = state
        self.last_brightness = brightness
        self.last_error = None
        self.last_target = written
        self.last_program = program
        return LedStatusWrite(
            state=state,
            target=written,
            program=program,
            changed=True,
        )
