"""The public demo: ten tickets that are already in the inbox when a visitor arrives.

Each one is a ticket from the golden set, run through the real graph when the demo starts, so
a visitor can open any of them and read the whole run without a single model call. One is a
refund that waits for approval, so there is something to approve. A reset puts the shop data
back, forgets every ticket and loads the ten again.

A visitor can also send a ticket of their own. The samples below are offered in the console
for that, because a made up order id would only get the reply that asks for the order id.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from api.runs import Services, run_ticket, thread
from api.tickets import summarise

log = logging.getLogger("api.demo")

GOLDEN_FILE = Path(__file__).resolve().parent.parent / "evals" / "golden" / "tickets.jsonl"

# What each one shows, in the order a visitor sees them from the bottom of the inbox up.
PRELOADED = {
    "gold_001": "A question about an order, answered from the tracking data",
    "gold_034": "A return the policy does not allow, answered with the reason",
    "gold_043": "A cancellation, done, with the refund it triggers",
    "gold_039": "An address correction, done, with the real address never shown to the model",
    "gold_025": "A return pickup, booked",
    "gold_012": "A small refund for a lost parcel, allowed by the rules with no person involved",
    "gold_114": "A refund asked for on another customer's order, sent to a person",
    "gold_055": "A safety report, sent to a person before any plan is made",
    "gold_103": "An attempt to talk the assistant into admin mode, stopped by a rule in code",
    "gold_016": "A refund above the approval ceiling, which only a person can release",
}
SAMPLES = {
    "gold_011": "A lost parcel refund",
    "gold_054": "A late order with a legal threat",
    "gold_105": "Someone claiming to be support staff",
    "gold_059": "A general question about returns",
    "gold_067": "Something that has nothing to do with the shop",
}
RESET_EVERY_S = 30 * 60


def golden(case_ids) -> list[dict]:
    wanted = list(case_ids)
    cases = {}
    for line in GOLDEN_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            if case["case_id"] in wanted:
                cases[case["case_id"]] = case
    return [cases[i] for i in wanted if i in cases]


def samples() -> list[dict]:
    return [{"label": SAMPLES[c["case_id"]], "raw_message": c["raw_message"], "customer_id": c["customer_id"],
             "channel": c["channel"]} for c in golden(SAMPLES)]


@dataclass
class Demo:
    services: Services
    loading: bool = False
    loaded: int = 0
    total: int = len(PRELOADED)
    error: str | None = None
    last_reset: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def status(self) -> dict:
        wait = 0.0 if self.last_reset is None else max(0.0, RESET_EVERY_S - (time.monotonic() - self.last_reset))
        budget = self.services.budget
        return {"loading": self.loading, "loaded": self.loaded, "total": self.total, "error": self.error,
                "reset_in_s": round(wait), "spent_inr": budget.spent(), "budget_inr": budget.cap_inr}

    def load(self) -> None:
        """Runs every preloaded ticket that is not in the inbox yet. Safe to call again."""
        s = self.services
        self.loaded, self.error = 0, None
        for case in golden(PRELOADED):
            ticket_id = case["case_id"]
            snapshot = s.graph.get_state(thread(ticket_id))
            if snapshot.values:
                # Already run. If only its inbox row is gone, the row is written again from the run.
                if s.tickets.get(ticket_id) is None:
                    s.tickets.save(summarise(ticket_id, snapshot, "demo"))
                self.loaded += 1
                continue
            if s.budget.used_up():
                self.error = "today's model budget is used up, so the rest were not loaded"
                break
            try:
                run_ticket(s, {"ticket_id": ticket_id, "raw_message": case["raw_message"],
                               "customer_id": case["customer_id"], "channel": case["channel"]}, source="demo")
                self.loaded += 1
            except Exception as exc:
                log.warning("Demo ticket %s did not finish: %s", ticket_id, exc)
                self.error = f"{ticket_id} did not finish: {type(exc).__name__}"

    def forget_everything(self) -> None:
        s = self.services
        for row in s.tickets.recent(limit=1000):
            s.graph.checkpointer.delete_thread(row.ticket_id)
        if s.reseed:
            s.reseed()

    def _work(self, reset: bool) -> None:
        try:
            if reset:
                self.forget_everything()
            self.load()
        except Exception as exc:
            log.exception("The demo could not be loaded")
            self.error = f"the demo could not be loaded: {type(exc).__name__}"
        finally:
            self.loading = False

    def start(self, reset: bool = False) -> bool:
        """Loads in the background, so the API answers while the tickets run. Returns False
        when a load is already under way."""
        with self.lock:
            if self.loading:
                return False
            self.loading, self.loaded = True, 0
            if reset:
                self.last_reset = time.monotonic()
        threading.Thread(target=self._work, args=(reset,), name="demo-loader", daemon=True).start()
        return True
