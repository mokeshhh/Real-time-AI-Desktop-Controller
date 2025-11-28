import threading
import queue
import time
import os
import json
import requests
import websocket
import pvporcupine
import pyaudio
from elevenlabs.client import ElevenLabs
from elevenlabs import stream
import sys
import subprocess
import shutil
import webbrowser 
import pyautogui 
import winsound
import spotipy 
import webrtcvad # <-- NEW IMPORT

# --- Import configuration and state from local files ---
from . import config
from . import state
from .skills import (
    ROUTER_PROMPT, 
    handle_whatsapp_action, 
    handle_system_action, 
    handle_spotify_fallback, 
    handle_launch_target_action,
    handle_browser_navigation
)
from .config import CONTACT_BOOK 
from . import spotify_api 


# --- AUDIO HANDLER (MODIFIED) ---
class AudioHandler(threading.Thread):
    def __init__(self, porcupine, speaker):
        super().__init__(daemon=True)
        self.porcupine = porcupine
        self.speaker = speaker
        self.pa = pyaudio.PyAudio()

        # --- Device Settings (Confirmed Working Index) ---
        MICROPHONE_DEVICE_INDEX = 1 
        
        self.stream = self.pa.open(
            rate=config.SAMPLE_RATE, channels=1, format=config.AUDIO_FORMAT,
            input=True, frames_per_buffer=self.porcupine.frame_length,
            input_device_index=MICROPHONE_DEVICE_INDEX
        )
        
        # --- VAD Settings ---
        self.vad = webrtcvad.Vad(3) # Aggressiveness 3 (most aggressive filtering)
        # FRAME_DURATION_MS must be 10, 20, or 30 for WebRTC VAD.
        # We will process audio in smaller VAD frames but read in larger Porcupine chunks.
        self.VAD_FRAME_DURATION_MS = 30
        self.VAD_FRAME_SIZE = int(config.SAMPLE_RATE * self.VAD_FRAME_DURATION_MS / 1000)
        self.MIN_VOICE_FRAMES = 1 # Minimum number of voice frames to send
        self.MAX_SILENCE_FRAMES = int(1000 / self.VAD_FRAME_DURATION_MS) * 1.5 # 1 second of silence detection
        # --------------------
        
        self.ws = None
        self.ws_thread = None
        self.ws_connected = threading.Event()
        self.CANCEL_COMMANDS = {"stop listening", "never mind", "cancel"}
        self.CONFIRM_COMMANDS = {"yes", "send it", "confirm", "go ahead", "yep"}
        self.last_transcript_time = None
        self.transcript_buffer = ""
        self.pause_threshold = 2.0 # Increased for better distance listening

        state.LISTENING_INTERFACE['stream'] = self.stream
        state.LISTENING_INTERFACE['start_transcriber'] = self._start_transcriber_session
        state.LISTENING_INTERFACE['stop_transcriber'] = self._stop_transcriber_session
        state.LISTENING_INTERFACE['ws_connected_event'] = self.ws_connected

    def _start_transcriber_session(self):
        print("\nConnecting to transcriber...")
        self.ws_connected.clear()
        url = (f"wss://audio-streaming-v2.api.fireworks.ai/v1/audio/transcriptions/streaming"
               f"?authorization=Bearer {config.FIREWORKS_API_KEY}&language=en")
        self.ws = websocket.WebSocketApp(url, on_message=self._on_message, on_open=self._on_open, on_error=self._on_error, on_close=self._on_close)
        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()

    def _stop_transcriber_session(self):
        if self.ws:
            print("[Transcriber] Closing connection.")
            try:
                self.ws.close()
            except Exception as e:
                print(f"[Transcriber] Error closing websocket: {e}")
            self.ws = None
            self.ws_thread = None

    def _on_open(self, ws):
        print("...now listening for your command...")
        self.ws_connected.set()

    def _on_error(self, ws, error):
        print(f"[Transcriber Error] {error}")
        self.ws_connected.set() 

    def _on_close(self, ws, status, msg):
        print("[Transcriber] Connection closed.")

    def _on_message(self, ws, message):
        response = json.loads(message)
        transcript = response.get("text", "")
        if transcript:
            self.transcript_buffer = transcript
            self.last_transcript_time = time.time()
            print(f"🎤 Interim: {self.transcript_buffer}\r", end="", flush=True)
            
    def _play_wake_sound(self):
        """Plays the wake word confirmation sound."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            wake_file_path = os.path.join(base_dir, "wake.wav")

            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            
            winsound.PlaySound(wake_file_path, flags)
            
        except RuntimeError as e:
            print(f"⚠️ Wake sound failed: {e}")
        except Exception as e:
            print(f"⚠️ Wake sound error: {e}")
            pass

    def run(self):
        global STATE
        print(f"🎤 Live Mode: Ready and listening. Say '{config.WAKE_WORD.capitalize()}' to begin.")
        
        # --- VAD Buffer State Variables ---
        silence_frame_count = 0
        voice_frame_count = 0
        vad_buffer = bytes()
        
        while True:
            try:
                pcm = self.stream.read(self.porcupine.frame_length, exception_on_overflow=False)
                
                with state.state_lock:
                    current_state = state.STATE

                if current_state in [state.AssistantState.IDLE, state.AssistantState.SPEAKING]:
                    # --- WAKE WORD DETECTION PHASE ---
                    pcm_unpacked = memoryview(pcm).cast('h')
                    if self.porcupine.process(pcm_unpacked) >= 0:
                        print("\n🚨 WAKE WORD DETECTED! 🚨")
                        self._play_wake_sound()
                        
                        state.interruption_event.set()
                        self.speaker.stop_playback() 
                        time.sleep(0.1) 
                        
                        self.transcript_buffer = ""
                        self.last_transcript_time = None
                        
                        state.interruption_event.clear()

                        self._start_transcriber_session()
                        
                        print("Waiting for connection...")
                        is_ready = self.ws_connected.wait(timeout=5)
                        
                        if is_ready:
                            with state.state_lock:
                                state.STATE = state.AssistantState.LISTENING
                            # Reset VAD state for the new command
                            silence_frame_count = 0
                            voice_frame_count = 0
                            vad_buffer = bytes()
                        else:
                            print("[FATAL] Could not connect to transcriber. Returning to idle.")
                            self._stop_transcriber_session()
                            with state.state_lock:
                                state.STATE = state.AssistantState.IDLE
                    
                elif current_state == state.AssistantState.LISTENING:
                    
                    # --- VAD & STREAMING PHASE ---
                    
                    # Add current pcm chunk to the VAD buffer
                    vad_buffer += pcm

                    # Process the buffer in VAD_FRAME_SIZE chunks
                    while len(vad_buffer) >= self.VAD_FRAME_SIZE * 2: # *2 because paInt16 is 2 bytes
                        vad_frame = vad_buffer[:self.VAD_FRAME_SIZE * 2]
                        vad_buffer = vad_buffer[self.VAD_FRAME_SIZE * 2:]
                        
                        is_speech = self.vad.is_speech(vad_frame, config.SAMPLE_RATE)
                        
                        if is_speech:
                            silence_frame_count = 0
                            voice_frame_count += 1
                            
                            if self.ws and self.ws.sock and self.ws.sock.connected:
                                self.ws.send(vad_frame, opcode=websocket.ABNF.OPCODE_BINARY)
                                
                        else: # is silence
                            silence_frame_count += 1
                            
                            # Only send the silence frame if we are already in the middle of a command 
                            # (i.e., we have heard some speech already) AND the silence is brief.
                            if voice_frame_count > 0 and self.ws and self.ws.sock and self.ws.sock.connected and silence_frame_count <= self.MAX_SILENCE_FRAMES:
                                self.ws.send(vad_frame, opcode=websocket.ABNF.OPCODE_BINARY)
                            
                            # If we detect prolonged silence after hearing speech, end the command
                            if voice_frame_count > 0 and silence_frame_count >= self.MAX_SILENCE_FRAMES:
                                
                                # Process the transcript
                                self._stop_transcriber_session()
                                print(" " * 80 + "\r", end="", flush=True)
                                final_transcript = self.transcript_buffer.strip().lower()
                                
                                self.transcript_buffer = ""
                                self.last_transcript_time = None
                                voice_frame_count = 0 # Reset VAD counter
                                
                                if final_transcript:
                                    print(f"💬 You said: {final_transcript}")
                                    
                                    is_confirmation = any(cmd in final_transcript for cmd in self.CONFIRM_COMMANDS)
                                    
                                    if state.DIALOGUE_CONTEXT['active'] and state.DIALOGUE_CONTEXT['slots'].get('awaiting_confirmation') and is_confirmation:
                                        state.command_queue.put("CONFIRM_SEND")
                                    else:
                                        state.command_queue.put(final_transcript)
                                
                                else:
                                    print("[Assistant] No command heard. Returning to idle.")
                                
                                with state.state_lock:
                                    state.STATE = state.AssistantState.IDLE
                                break # Exit the listening loop, return to waiting for wake word

                    # Fallback on pause_threshold (if VAD somehow missed it, or transcriber gave no interim text)
                    if self.last_transcript_time and (time.time() - self.last_transcript_time > self.pause_threshold * 2): # Use a longer timeout here
                        self._stop_transcriber_session()
                        print(" " * 80 + "\r", end="", flush=True)
                        final_transcript = self.transcript_buffer.strip().lower()
                        
                        self.transcript_buffer = ""
                        self.last_transcript_time = None

                        if final_transcript:
                            print(f"💬 You said: {final_transcript} (Timeout)")
                            state.command_queue.put(final_transcript)
                        else:
                            print("[Assistant] No command heard (Timeout). Returning to idle.")
                        
                        with state.state_lock:
                            state.STATE = state.AssistantState.IDLE
                        
            except Exception as e:
                print(f"--- [FATAL ERROR in AudioHandler] ---")
                print(f"An unexpected error occurred: {e}")
                
                with state.state_lock:
                    state.STATE = state.AssistantState.IDLE
                self._stop_transcriber_session()
                time.sleep(1)


    def stop(self):
        if self.stream: self.stream.close()
        if self.pa: self.pa.terminate()
        self._stop_transcriber_session()

# --- INTENT ROUTING RESPONDER (UNCHANGED) ---
class FireworksResponder(threading.Thread):
# ... (rest of the FireworksResponder class remains unchanged) ...
# ...
# ...

    def __init__(self):
        super().__init__(daemon=True)
        self.url = config.FIREWORKS_URL
        self.headers = {"Accept": "text/event-stream", "Content-Type": "application/json", "Authorization": f"Bearer {config.FIREWORKS_API_KEY}"}
        
        self.llm_model = config.LLM_MODEL
        self.router_model = config.ROUTER_MODEL

    def _get_intent(self, query):
        """Calls LLM to get a JSON intent and slots."""
        print("🔍 Routing command...")
        
        payload = { 
            "model": self.router_model, 
            "max_tokens": 256, 
            "messages": [
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": query}
            ]
        }
        
        try:
            response = requests.post(self.url, headers=self.headers, data=json.dumps(payload))
            response.raise_for_status()
            
            raw_json = response.json()['choices'][0]['message']['content'].strip()
            
            if raw_json.startswith("```json"):
                raw_json = raw_json.strip('`').strip('json').strip()
            
            return json.loads(raw_json)
            
        except Exception as e:
            print(f"\n[Router Error] Failed to get/parse intent: {e}")
            return {"intent": "GENERAL_QUERY", "slots": {"query": query}} 

    def run(self):
        global STATE
        while True:
            command = state.command_queue.get()
            
            with state.state_lock:
                state.STATE = state.AssistantState.THINKING
            print("🧠 Thinking...")
            state.interruption_event.clear()
            
            final_response_text = None
            
            try:
                command_text = command
                intent = None
                slots = {}

                # --- 1. HANDLE ACTIVE DIALOGUE ---
                if state.DIALOGUE_CONTEXT['active']:
                    is_awaiting_conf = state.DIALOGUE_CONTEXT['slots'].get('awaiting_confirmation')
                    
                    if is_awaiting_conf:
                        confirmation_intent = self._get_intent(command_text) 
                        
                        if confirmation_intent['intent'] == "CONFIRM" or command_text == "CONFIRM_SEND":
                            slots = state.DIALOGUE_CONTEXT['slots']
                            contact_name = slots['contact']
                            message = slots['message']
                            phone_number = config.CONTACT_BOOK[contact_name.lower()]
                            
                            final_response_text = handle_whatsapp_action(contact_name, message, phone_number, action="send")
                            state.DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}}
                            
                        elif confirmation_intent['intent'] == "CANCEL":
                            final_response_text = "Message cancelled. Returning to idle."
                            state.DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}}
                            
                        else:
                            final_response_text = "I'm sorry, I didn't understand. Should I send the message or cancel?"
                        
                    elif state.DIALOGUE_CONTEXT['intent'] == "SEND_WHATSAPP":
                        slots = state.DIALOGUE_CONTEXT['slots']
                        
                        if not slots.get('contact'):
                            slots['contact'] = command_text.title()
                        elif not slots.get('message'):
                            slots['message'] = command_text
                            
                        intent = "SEND_WHATSAPP" 

                # --- 2. INTENT CLASSIFICATION ---
                else:
                    intent_data = self._get_intent(command_text)
                    intent = intent_data.get('intent', 'GENERAL_QUERY')
                    slots = intent_data.get('slots', {})


                # --- PHASE 3: EXECUTION LOGIC ---
                
                if intent == "SEND_WHATSAPP":
                    current_slots = slots
                    contact_name = current_slots.get('contact', '').title()
                    message = current_slots.get('message', '')
                    
                    if not contact_name or not message:
                        state.DIALOGUE_CONTEXT.update({"active": True, "intent": intent, "slots": current_slots})
                        
                        if not contact_name:
                            final_response_text = "Who should I send that message to?"
                        elif not message:
                            final_response_text = f"What should the message to {contact_name} say?"

                    else:
                        phone_number = config.CONTACT_BOOK.get(contact_name.lower())
                        
                        if not phone_number:
                            final_response_text = f"I could not find a number for {contact_name}. Please try a different name."
                            state.DIALOGUE_CONTEXT['active'] = False
                        else:
                            final_response_text = handle_whatsapp_action(contact_name, message, phone_number, action="prepare")
                            
                            state.DIALOGUE_CONTEXT.update({"active": True, "intent": intent, "slots": current_slots})
                            state.DIALOGUE_CONTEXT['slots']['contact'] = contact_name
                            state.DIALOGUE_CONTEXT['slots']['message'] = message
                            state.DIALOGUE_CONTEXT['slots']['awaiting_confirmation'] = True 

                # --- SYSTEM CONTROL LOGIC (UNCHANGED) ---
                elif intent == "SYSTEM_CONTROL":
                    action = slots.get('action')
                    value = slots.get('value')
                    
                    if not action:
                        final_response_text = "I received a system command but I'm not sure what action to take."
                    else:
                        final_response_text = handle_system_action(action, value)
                        
                        # --- NEW SLEEP LOGIC ---
                        if action == "sleep":
                            # Stop any current transcription session immediately
                            state.LISTENING_INTERFACE['stop_transcriber']()
                            with state.state_lock:
                                state.STATE = state.AssistantState.IDLE
                                print("😴 Assistant is now in IDLE/SLEEP mode.")
                            
                        state.DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}} 
                            
                # --- SPOTIFY CONTROL LOGIC (UNCHANGED) ---
                elif intent == "SPOTIFY_CONTROL":
                    action = slots.get('action')
                    query = slots.get('query')
                    
                    if not action:
                        final_response_text = "I received a Spotify command but I'm not sure which action to take."
                    else:
                        api_response = spotify_api.api_control_playback(spotify_api.SPOTIFY_CLIENT, action, query)
                        
                        if api_response:
                            final_response_text = api_response
                        else:
                            final_response_text = handle_spotify_fallback(action, query)
                            
                        state.DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}} 
                            
                # --- LAUNCH TARGET LOGIC (UNCHANGED) ---
                elif intent == "LAUNCH_TARGET":
                    target = slots.get('target')
                    target_type = slots.get('target_type')
                    search_query = slots.get('search_query')
                    
                    if not target or not target_type:
                        final_response_text = "I'm sorry, what exactly would you like me to open?"
                    else:
                        final_response_text = handle_launch_target_action(target, target_type, search_query)
                        
                    state.DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}}

                # --- NEW: BROWSER NAVIGATOR LOGIC (UNCHANGED) ---
                elif intent == "BROWSER_NAVIGATOR":
                    action = slots.get('action')
                    
                    if not action:
                        final_response_text = "I'm not sure what navigation action you want me to perform in the browser."
                    else:
                        final_response_text = handle_browser_navigation(action)
                        
                    state.DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}}
                            
                elif intent == "GENERAL_QUERY":
                    query = slots.get('query', command_text)
                    
                    payload = { "model": self.llm_model, "max_tokens": 150, "stream": True, "messages": [{"role": "user", "content": query}] }
                    
                    response_stream = requests.post(self.url, headers=self.headers, data=json.dumps(payload), stream=True)
                    response_stream.raise_for_status()
                    
                    with state.state_lock:
                        state.STATE = state.AssistantState.SPEAKING
                    print("🗣️ AI Response (speaking)...")
                    
                    sentence_buffer = []
                    for line in response_stream.iter_lines():
                        if state.interruption_event.is_set(): break
                        if line:
                            decoded_line = line.decode('utf-8')
                            if decoded_line.startswith('data: '):
                                json_str = decoded_line[len('data: '):]
                                if json_str.strip() == "[DONE]": break
                                
                                data = json.loads(json_str)
                                if 'content' in data['choices'][0]['delta']:
                                    token = data['choices'][0]['delta']['content']
                                    print(token, end="", flush=True)
                                    sentence_buffer.append(token)
                                    
                                    if any(c in token for c in ".?!"):
                                        sentence = "".join(sentence_buffer).strip()
                                        if sentence: state.tts_sentence_queue.put(sentence)
                                        sentence_buffer.clear()
                                    
                    if not state.interruption_event.is_set() and sentence_buffer:
                        sentence = "".join(sentence_buffer).strip()
                        if sentence: state.tts_sentence_queue.put(sentence + '.')
                        
                    print("\n")
                    state.tts_sentence_queue.put(None) 
                    continue

                # Fallback for all non-streaming paths
                if final_response_text:
                    state.tts_sentence_queue.put(final_response_text)
                    
                    if state.DIALOGUE_CONTEXT['active']:
                           time.sleep(1.5) 
                           
                           state.LISTENING_INTERFACE['stop_transcriber']() 
                           
                           with state.state_lock:
                               state.STATE = state.AssistantState.IDLE
                               print("👂 Dialogue turn complete. Say WAKE WORD to continue.")
                
            except Exception as e:
                print(f"\n[Responder Fatal Error]: {e}")
                final_response_text = "I'm sorry, I encountered a critical error while processing your request."
                state.tts_sentence_queue.put(final_response_text)
            finally:
                state.tts_sentence_queue.put(None)

# --- ELEVENLABS SPEAKER (UNCHANGED) ---
class ElevenLabsSpeaker(threading.Thread):
# ... (rest of the ElevenLabsSpeaker class remains unchanged) ...
# ...
# ...

    def __init__(self, client):
        super().__init__(daemon=True)
        self.client = client
        self.player_command = self._find_player()
        self.playback_process = None
        self.process_lock = threading.Lock()
        self.is_interrupted = False 

    def _find_player(self):
        for player in ["mpv", "ffplay"]:
            if shutil.which(player):
                print(f"✅ Audio player found: {player}")
                return player
        print("⚠️ WARNING: No audio player (mpv or ffplay) found in PATH. Audio will not play.")
        return None

    def stop_playback(self):
        with self.process_lock:
            self.is_interrupted = True 
            
            if self.playback_process and self.playback_process.poll() is None:
                print("\n[Speaker] Killing audio playback process...")
                try:
                    if self.playback_process.stdin:
                        self.playback_process.stdin.close() 
                    
                    self.playback_process.kill() 
                    self.playback_process.wait(timeout=1)
                except Exception as e:
                    print(f"[Speaker] Error during kill: {e}")
            self.playback_process = None 

    def run(self):
        global STATE
        while True:
            sentence = state.tts_sentence_queue.get()
            
            if sentence is None:
                if not state.interruption_event.is_set():
                    with state.state_lock: state.STATE = state.AssistantState.IDLE
                continue
            
            if state.interruption_event.is_set() or not self.player_command: continue
            
            self.is_interrupted = False 
            
            try:
                audio_stream = self.client.text_to_speech.stream(text=sentence, voice_id="pNInz6obpgDQGcFmaJgB", model_id="eleven_turbo_v2")
                
                if self.player_command == "mpv":
                    command = [self.player_command, "--no-cache", "--audio-buffer=0.1", "-", "--no-msg-color"]
                else:
                    command = [self.player_command, "-autoexit", "-", "-nodisp"]
                
                with self.process_lock:
                    if self.is_interrupted: continue 
                    self.playback_process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                for chunk in audio_stream:
                    if self.is_interrupted: 
                        break 

                    if self.playback_process and self.playback_process.stdin:
                        try:
                            self.playback_process.stdin.write(chunk)
                        except (BrokenPipeError, OSError):
                            break 
                
                if self.playback_process and self.playback_process.stdin: self.playback_process.stdin.close()
                if self.playback_process: self.playback_process.wait()
                
            except Exception as e:
                print(f"\n[Speaker Error] {e}")
            finally:
                self.is_interrupted = False 
                with self.process_lock: self.playback_process = None


# --- MAIN EXECUTION BLOCK (MODIFIED) ---
if __name__ == "__main__":
    if not (config.FIREWORKS_API_KEY and config.PICOVOICE_ACCESS_KEY and config.ELEVENLABS_API_KEY):
        print("CRITICAL ERROR: Please ensure all API keys in config.py are set correctly.")
        sys.exit(1)
    
    # NOTE: The Spotify client ID and Secret are still placeholders in config.py. 
    # You should replace them with the full values if you want the API control to work.
    elevenlabs_client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    
    spotify_api.SPOTIFY_CLIENT = spotify_api.get_spotify_client()

    try:
        # --- ADJUSTED WAKE WORD SENSITIVITY ---
        SENSITIVITY = 0.80 # Made even easier to detect from a distance
        # -------------------------------------
        porcupine = pvporcupine.create(access_key=config.PICOVOICE_ACCESS_KEY, keywords=[config.WAKE_WORD],sensitivities=[SENSITIVITY])
    except Exception as e:
        print(f"Error initializing Porcupine: {e}")
        sys.exit(1)

    responder = FireworksResponder()
    speaker = ElevenLabsSpeaker(client=elevenlabs_client)
    
    responder.start()
    speaker.start()

    if config.TEST_MODE:
        print("Test Mode is not implemented in this final version. Set TEST_MODE = False.")
    else:
        audio_handler = AudioHandler(porcupine=porcupine, speaker=speaker)
        audio_handler.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping assistant.")

    speaker.stop_playback()
    if 'audio_handler' in locals() and audio_handler.is_alive():
        audio_handler.stop()
    porcupine.delete()
    print("Cleanup complete. Exiting.")