"""A courier that delivers every parcel on its own (D112), for development and the demo.

The parcel id carries the time it was booked, so a restarted server still knows how far a
parcel has got: one step every `step`, then delivered."""

from collections.abc import Callable
from datetime import datetime, timedelta

from heydesk.ports.courier import Parcel, Tracked

STEPS = ["Pickup requested", "Picked up by the rider", "In transit", "Out for delivery"]


class FakeCourier:
    def __init__(self, now: Callable[[], datetime], step: timedelta = timedelta(minutes=2)):
        self.now = now
        self.step = step

    async def book(self, parcel: Parcel) -> str:
        return f"FAKE-{parcel.code}-{int(self.now().timestamp())}"

    async def status(self, parcel_id: str) -> Tracked:
        booked = datetime.fromtimestamp(int(parcel_id.rsplit("-", 1)[1]), self.now().tzinfo)
        done = int((self.now() - booked) / self.step)
        if done >= len(STEPS):
            return Tracked("Delivered", "delivered")
        return Tracked(STEPS[done], None)
