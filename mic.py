import pyaudio

# Get the Pyaudio object
p = pyaudio.PyAudio()

print("--- Available Audio Input Devices ---")
print("Find the index corresponding to your built-in Realtek(R) Audio Microphone.")

# Iterate through all available audio input devices
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    # Only show input devices
    if info.get('maxInputChannels') > 0:
        print(f"Index {i}: {info.get('name')} (Max Channels: {info.get('maxInputChannels')})")

p.terminate()

# -----------------------------------------------------------------------------
# *** ACTION REQUIRED AFTER RUNNING: ***
# 1. Run this script: python mic_selector.py
# 2. Look for the line that says "Realtek(R) Audio" or similar.
# 3. Note down the Index number (e.g., if it says "Index 2: Realtek(R) Audio", the index is 2).
# 4. Use this index to update the MICROPHONE_DEVICE_INDEX = 1 line in main.py.
# -----------------------------------------------------------------------------