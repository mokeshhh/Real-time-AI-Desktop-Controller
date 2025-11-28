import threading
import queue
import os

# --- STATE, EVENTS, & QUEUES ---
class AssistantState:
    IDLE, LISTENING, THINKING, SPEAKING = "IDLE", "LISTENING", "THINKING", "SPEAKING" 
STATE = AssistantState.IDLE
state_lock = threading.Lock()
interruption_event = threading.Event()
command_queue = queue.Queue()
tts_sentence_queue = queue.Queue()

# --- GLOBAL INTERFACE FOR LISTENING CONTROL ---
# Used to expose control methods from AudioHandler to other threads (e.g., Responder)
LISTENING_INTERFACE = {} 

# --- GLOBAL SENTINEL (End of Queue marker) ---
END_OF_AUDIO = object()

# --- GLOBAL STATE FOR MULTI-TURN DIALOGUE ---
DIALOGUE_CONTEXT = {"active": False, "intent": None, "slots": {}}