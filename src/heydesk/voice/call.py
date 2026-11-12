"""One voice call: the browser's audio in, speech out (design §10).

STT_PROVIDER hears the caller: Deepgram as they talk, or Chorus one whole utterance at a
time. Each thing they say becomes a `start_turn(channel="voice")`, the same turn the web chat
runs; the reply's tokens go to the voice, which speaks it sentence by sentence as they arrive.

Barge-in: talking over the agent for `INTERRUPT_SECONDS` stops the audio and the turn,
which still saves what it had. A short "mm-hmm" doesn't stop it and isn't a turn.
"""

import asyncio
import time
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    ErrorFrame,
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
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.utils.text.base_text_filter import BaseTextFilter
from pipecat.utils.time import time_now_iso8601

from heydesk.config import Settings
from heydesk.errors import HeyDeskError
from heydesk.ports.transcriber import Transcriber, TranscriberUnavailable
from heydesk.runtime.events import HandOff, Token, ToolStarted, TurnEnded
from heydesk.runtime.speech import speakable
from heydesk.services import Services
from heydesk.services.agents import LiveAgent
from heydesk.services.calls import Answered
from heydesk.services.conversations import ConversationService
from heydesk.voice.bangla_voice import BanglaVoice

VOICE = "en_US-lessac-medium"  # Piper, free and local, when VOICE_LANGUAGE is `en`
END_OF_SPEECH_SECONDS = (
    0.5  # silence that ends the caller's turn; 0.2 (the default) splits at a comma
)
INTERRUPT_SECONDS = 0.6  # talking this long over the agent stops it; "mm-hmm" is shorter
HANG_UP_SECONDS = 1.5  # after the last word of a settled shop call, it hangs up
SHORT_ACKNOWLEDGEMENTS = {"mm-hmm", "mhm", "mm", "hmm", "uh-huh", "okay", "ok", "right", "i see"}


class CallAgent(FrameProcessor):
    """Runs a turn for each thing the caller says and passes the reply on to be spoken."""

    def __init__(
        self,
        conversations: ConversationService,
        agent: LiveAgent,
        visitor: str,
        conversation_id: str | None = None,
    ) -> None:
        super().__init__()
        self.conversations = conversations
        self.agent = agent
        self.visitor = visitor  # the caller's page token, for the limits
        # One conversation per call: made by the first turn, or by the shop's call
        self.conversation_id = conversation_id
        self.turn: asyncio.Task[None] | None = None  # the running turn, in its own task
        self.interrupt: asyncio.Event | None = None  # that turn's stop flag
        self.speaking = False  # the agent's audio is playing
        self.over_agent = False  # the caller started talking while the agent was busy
        self.barge_in: asyncio.Task[None] | None = None
        self.over = False  # the shop's call settled its order: hang up once the goodbye is said
        self.hang_up: asyncio.Task[None] | None = None

    def busy(self) -> bool:
        return self.speaking or (self.turn is not None and not self.turn.done())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await self.on_transcript(frame.text)
            return
        if isinstance(frame, BotStartedSpeakingFrame | BotStoppedSpeakingFrame):
            self.speaking = isinstance(frame, BotStartedSpeakingFrame)
            if self.hang_up:  # more words (the next sentence): not yet
                await self.cancel_task(self.hang_up)
                self.hang_up = None
            if self.over and not self.busy():
                self.hang_up = self.create_task(self.end_after(HANG_UP_SECONDS))
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.over_agent = self.busy()
            if self.over_agent:
                self.barge_in = self.create_task(self.stop_after(INTERRUPT_SECONDS))
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self.barge_in:
            await self.cancel_task(self.barge_in)
            self.barge_in = None
        await self.push_frame(frame, direction)

    async def on_transcript(self, text: str) -> None:
        if self.over_agent and text.lower().strip(" .,!?") in SHORT_ACKNOWLEDGEMENTS:
            return  # "mm-hmm" while the agent talks: let it go on
        await self.stop()  # anything else is a new turn: the old one stops first
        if self.turn:
            await asyncio.wait([self.turn])  # wait, don't cancel: it saves what it had
        self.turn = self.create_task(self.reply(text))

    async def end_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.push_frame(EndWorkerFrame())  # ends the call once the audio has played

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
        try:
            turn = await self.conversations.start_turn(
                self.agent,
                self.conversation_id,
                text,
                "voice",
                self.visitor,
                self.visitor.removeprefix("web:"),  # the caller's browser, as in web chat
            )
        except HeyDeskError as error:  # over a limit, or a full conversation: say why
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(error.message))
            await self.push_frame(LLMFullResponseEndFrame())
            return
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
            elif isinstance(event, TurnEnded) and event.state.get("calling", {}).get("over"):
                self.over = True  # the goodbye is on its way
        await self.push_frame(LLMFullResponseEndFrame())


class SpeakableText(BaseTextFilter):
    """Each sentence in words just before it's spoken: "৳450" → "four hundred and fifty taka"."""

    def __init__(self, language: str) -> None:
        self.language = language

    async def filter(self, text: str) -> str:
        return speakable(text, self.language)


class WholeUtterances(SegmentedSTTService):
    """Hears the caller one whole utterance at a time, as a voice note is heard (Chorus).
    Pipecat collects the audio while they talk and hands it over as a WAV when the VAD says
    they stopped."""

    def __init__(self, transcriber: Transcriber) -> None:
        super().__init__()
        self.transcriber = transcriber

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        started = time.perf_counter()
        try:
            text = await self.transcriber.transcribe(audio, "speech.wav")
        except TranscriberUnavailable as error:  # logged; the caller says it again
            yield ErrorFrame(error=str(error))
            return
        logger.info(f"heard: {time.perf_counter() - started:.2f}s")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())


class FirstSoundTimer(BaseObserver):
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
    connection: SmallWebRTCConnection,
    settings: Settings,
    services: Services,
    agent: LiveAgent,
    visitor: str,
    answered: Answered | None = None,
) -> None:
    """A call the visitor made, or with `answered`, the shop's call they answered on
    `/phone`: that one starts with its own first line, not the greeting."""
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    language = settings.VOICE_LANGUAGE  # what every call hears and speaks
    hear: FrameProcessor
    if settings.STT_PROVIDER == "chorus" and services.transcriber:
        hear = WholeUtterances(services.transcriber)  # the voice notes' Chorus
    else:
        hear = DeepgramSTTService(
            api_key=settings.DEEPGRAM_API_KEY,
            settings=DeepgramSTTService.Settings(language=Language(language)),
        )
    speak: FrameProcessor
    if language == "bn":
        speak = BanglaVoice(settings.BANGLA_VOICE_URL, text_filters=[SpeakableText(language)])
    else:
        speak = PiperTTSService(
            settings=PiperTTSService.Settings(voice=VOICE),
            text_filters=[SpeakableText(language)],
        )
    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(
                vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=END_OF_SPEECH_SECONDS))
            ),
            hear,
            CallAgent(
                services.conversations,
                agent,
                visitor,
                answered.conversation_id if answered else None,
            ),
            speak,
            transport.output(),
        ]
    )
    task = PipelineTask(
        pipeline, params=PipelineParams(), observers=[FirstSoundTimer()], enable_rtvi=False
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport: SmallWebRTCTransport, client: object) -> None:
        if answered:  # already stored in its conversation
            await task.queue_frame(TTSSpeakFrame(answered.said))
        else:
            await task.queue_frame(TTSSpeakFrame(agent.greeting))  # what the first turn stores

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport: SmallWebRTCTransport, client: object) -> None:
        await task.cancel()

    async def hang_up_later() -> None:
        await asyncio.sleep(settings.MAX_CALL_SECONDS)
        await task.cancel()

    limit = asyncio.create_task(hang_up_later())
    try:
        await PipelineRunner(handle_sigint=False).run(task)
    finally:
        limit.cancel()
