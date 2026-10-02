"""One voice call: the browser's audio in, speech out (design §10).

Each thing the caller says becomes a `start_turn(channel="voice")`, the same
turn the web chat runs; the reply's tokens go to the TTS, which speaks it
sentence by sentence as they arrive.

Barge-in: talking over the agent for `BARGE_IN_S` stops the audio and the turn,
which still saves what it had. A short "mm-hmm" doesn't stop it and isn't a turn.
"""

import asyncio
import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.piper.tts import PiperTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.utils.text.base_text_filter import BaseTextFilter

from heydesk.config import Settings
from heydesk.runtime.events import HandOff, Token, ToolStarted
from heydesk.runtime.speech import speakable
from heydesk.services import Services
from heydesk.services.agents import LiveAgent
from heydesk.services.conversations import ConversationService
from heydesk.voice.chorus import ChorusSTT

VOICE = "en_US-lessac-medium"  # Piper, free and local, until the real TTS lands
PAUSE_S = 0.5  # silence that ends the caller's turn; 0.2 (the default) splits at a comma
BARGE_IN_S = 0.6  # talking this long over the agent stops it; "mm-hmm" is shorter
BACKCHANNEL = {"mm-hmm", "mhm", "mm", "hmm", "uh-huh", "okay", "ok", "right", "i see"}


class Agent(FrameProcessor):
    """Runs a turn for each thing the caller says and passes the reply on to be spoken."""

    def __init__(self, conversations: ConversationService, agent: LiveAgent) -> None:
        super().__init__()
        self.conversations = conversations
        self.agent = agent
        self.conversation_id: str | None = None  # one conversation per call
        self.turn: asyncio.Task[None] | None = None  # the running turn, in its own task
        self.interrupt: asyncio.Event | None = None  # that turn's stop flag
        self.speaking = False  # the agent's audio is playing
        self.over_agent = False  # the caller started talking while the agent was busy
        self.barge_in: asyncio.Task[None] | None = None

    def busy(self) -> bool:
        return self.speaking or (self.turn is not None and not self.turn.done())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await self.heard(frame.text)
            return
        if isinstance(frame, BotStartedSpeakingFrame | BotStoppedSpeakingFrame):
            self.speaking = isinstance(frame, BotStartedSpeakingFrame)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.over_agent = self.busy()
            if self.over_agent:
                self.barge_in = self.create_task(self.stop_after(BARGE_IN_S))
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self.barge_in:
            await self.cancel_task(self.barge_in)
            self.barge_in = None
        await self.push_frame(frame, direction)

    async def heard(self, text: str) -> None:
        if self.over_agent and text.lower().strip(" .,!?") in BACKCHANNEL:
            return  # "mm-hmm" while the agent talks: let it go on
        await self.stop()  # anything else is a new turn: the old one stops first
        if self.turn:
            await asyncio.wait([self.turn])  # wait, don't cancel: it saves what it had
        self.turn = self.create_task(self.reply(text))

    async def stop_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.stop()

    async def stop(self) -> None:
        """Stops the turn at its next token and the audio now."""
        if self.interrupt:
            self.interrupt.set()
        if self.busy():
            await self.broadcast_interruption()

    async def reply(self, text: str) -> None:
        turn = await self.conversations.start_turn(self.agent, self.conversation_id, text, "voice")
        self.conversation_id = turn.conversation_id
        self.interrupt = turn.interrupt
        spoken = ""
        await self.push_frame(LLMFullResponseStartFrame())
        async for event in turn.events:
            if turn.interrupt.is_set():
                continue  # read to the end, so the turn is saved; say nothing more
            if isinstance(event, Token):
                spoken += event.text
                await self.push_frame(LLMTextFrame(event.text))
            elif isinstance(event, ToolStarted) and event.filler and not spoken.strip():
                # Fills the silence while the tool runs; only the turn's own words are stored
                await self.push_frame(LLMTextFrame(event.filler + " "))
            elif isinstance(event, HandOff) and event.message not in spoken:
                # There's no card on a call: a failure's polite line is said out loud
                await self.push_frame(LLMTextFrame(f" {event.message}"))
        await self.push_frame(LLMFullResponseEndFrame())


class Speakable(BaseTextFilter):
    """Each sentence in words just before it's spoken: "৳450" → "four hundred and fifty taka"."""

    async def filter(self, text: str) -> str:
        return speakable(text)


class FirstSound(BaseObserver):
    """Logs the time from the caller's last word (the VAD's stop) to the agent's first sound."""

    def __init__(self) -> None:
        super().__init__()
        self.stopped: float | None = None

    async def on_push_frame(self, data: FramePushed) -> None:
        if isinstance(data.frame, VADUserStoppedSpeakingFrame):
            self.stopped = time.perf_counter()
        elif isinstance(data.frame, BotStartedSpeakingFrame) and self.stopped:
            logger.info(
                f"first sound: {time.perf_counter() - self.stopped:.2f}s after the last word"
            )
            self.stopped = None


async def run_call(
    connection: SmallWebRTCConnection, settings: Settings, services: Services, agent: LiveAgent
) -> None:
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    stt = ChorusSTT(settings.GIGALOGY_PROJECT_KEY, settings.GIGALOGY_API_KEY)
    tts = PiperTTSService(
        settings=PiperTTSService.Settings(voice=VOICE), text_filters=[Speakable()]
    )
    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=PAUSE_S))),
            stt,
            Agent(services.conversations, agent),
            tts,
            transport.output(),
        ]
    )
    task = PipelineTask(
        pipeline, params=PipelineParams(), observers=[FirstSound()], enable_rtvi=False
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport: SmallWebRTCTransport, client: object) -> None:
        await task.queue_frame(TTSSpeakFrame(agent.greeting))  # what the first turn stores (D8)

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport: SmallWebRTCTransport, client: object) -> None:
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)
