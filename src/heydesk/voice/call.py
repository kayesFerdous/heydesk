"""One voice call: the browser's audio in, speech out (design §10).

Each thing the caller says becomes a `start_turn(channel="voice")`, the same
turn the web chat runs; the reply's tokens go to the TTS, which speaks it
sentence by sentence as they arrive.
"""

import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
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

from heydesk.config import Settings
from heydesk.runtime.events import HandOff, Token
from heydesk.services import Services
from heydesk.services.agents import LiveAgent
from heydesk.services.conversations import ConversationService
from heydesk.voice.chorus import ChorusSTT

VOICE = "en_US-lessac-medium"  # Piper, free and local, until the real TTS lands
PAUSE_S = 0.5  # silence that ends the caller's turn; 0.2 (the default) splits at a comma


class Agent(FrameProcessor):
    """Runs a turn for each thing the caller says and passes the reply on to be spoken."""

    def __init__(self, conversations: ConversationService, agent: LiveAgent) -> None:
        super().__init__()
        self.conversations = conversations
        self.agent = agent
        self.conversation_id: str | None = None  # one conversation per call

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await self.reply(frame.text)
        else:
            await self.push_frame(frame, direction)

    async def reply(self, text: str) -> None:
        turn = await self.conversations.start_turn(self.agent, self.conversation_id, text, "voice")
        self.conversation_id = turn.conversation_id
        spoken = ""
        await self.push_frame(LLMFullResponseStartFrame())
        async for event in turn.events:
            if isinstance(event, Token):
                spoken += event.text
                await self.push_frame(LLMTextFrame(event.text))
            elif isinstance(event, HandOff) and event.message not in spoken:
                # There's no card on a call: a failure's polite line is said out loud
                await self.push_frame(LLMTextFrame(f" {event.message}"))
        await self.push_frame(LLMFullResponseEndFrame())


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
    connection: SmallWebRTCConnection, settings: Settings, services: Services, agent_key: str
) -> None:
    agent = await services.agents.by_key(agent_key)
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    stt = ChorusSTT(settings.GIGALOGY_PROJECT_KEY, settings.GIGALOGY_API_KEY)
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice=VOICE))
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
