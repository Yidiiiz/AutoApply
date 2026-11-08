"""Observation budgets, never interaction retries. Times use the monotonic clock."""
import time
from dataclasses import dataclass


MIN_TRANSIENT_FAILURE_GRACE = 8.0
DEFAULT_READINESS_TIMEOUT = 30.0


@dataclass(frozen=True)
class PatiencePolicy:
    grace: float = MIN_TRANSIENT_FAILURE_GRACE
    timeout: float = DEFAULT_READINESS_TIMEOUT
    extension: float = 5.0
    stable_seconds: float = .3
    poll_seconds: float = .1

    def __post_init__(self):
        import math
        if not all(math.isfinite(v) for v in (
                self.grace, self.timeout, self.extension, self.stable_seconds, self.poll_seconds)):
            raise ValueError('Patience durations must be finite')
        if self.grace < MIN_TRANSIENT_FAILURE_GRACE or self.timeout < self.grace:
            raise ValueError('Readiness timeout must cover at least eight seconds of grace')
        if not 0 <= self.extension <= 30 or self.stable_seconds <= 0 or self.poll_seconds <= 0:
            raise ValueError('Invalid bounded patience policy')


class TransitionPatience:
    def __init__(self, policy=None, *, timeout=None, interaction_at=None, clock=None):
        self.policy = policy or PatiencePolicy()
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.interaction_at = interaction_at
        # Explicit shorter adapter budgets still cannot bypass minimum grace.
        self.deadline = self.started + max(self.policy.grace, timeout if timeout is not None else self.policy.timeout)
        self.hard_deadline = self.deadline + self.policy.extension
        self.last_progress = None
        self.progress = []
        self.seen = set()
        self.failure_key = None
        self.failure_first = self.failure_stable = None
        self.failure_count = 0
        self.ready_at = None
        self.final_observation = {}

    @property
    def remaining(self):
        return max(0, self.deadline - self.clock())

    @property
    def hard_remaining(self):
        return max(0, self.hard_deadline - self.clock())

    @property
    def stable_failure(self):
        return (self.clock() - self.started >= self.policy.grace and
                self.failure_count >= 2 and self.failure_stable is not None)

    def observe(self, *, progress, failure, summary):
        now = self.clock()
        self.final_observation = summary
        # Callers supply semantic milestones, never raw DOM revisions, geometry,
        # field values, or animation ticks. Repeated states are not new progress.
        if progress not in self.seen and len(self.seen) < 64:
            if self.seen:
                self.last_progress = now
                self.progress.append({'at': now, **summary})
                self.deadline = min(self.hard_deadline, max(self.deadline, now + self.policy.extension))
            self.seen.add(progress)
        if failure != self.failure_key or self.failure_first is None:
            self.failure_key, self.failure_first = failure, now
            self.failure_stable, self.failure_count = None, 1
        else:
            self.failure_count += 1
            if self.failure_count >= 2 and now - self.failure_first >= self.policy.stable_seconds:
                self.failure_stable = self.failure_stable or now

    def ready(self):
        self.ready_at = self.clock()

    def telemetry(self):
        return dict(interaction_at=self.interaction_at, grace_started_at=self.started,
                    progress_observations=self.progress, last_meaningful_progress=self.last_progress,
                    readiness_established_at=self.ready_at,
                    terminal_condition_first_seen_at=self.failure_first,
                    terminal_condition_stable_at=self.failure_stable,
                    total_wait_duration=self.clock()-self.started,
                    grace_seconds=self.policy.grace, deadline=self.deadline,
                    hard_deadline=self.hard_deadline, final_observation=self.final_observation)


def page_policy(page):
    return getattr(page, '_autoapply_patience_policy', None) or PatiencePolicy()
