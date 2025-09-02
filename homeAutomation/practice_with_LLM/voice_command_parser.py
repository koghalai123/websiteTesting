#pip install requests openai json speech_recognition pyaudio pyttsx3
# For webm file processing, also install: sudo apt install ffmpeg
# Optional: pip install pydub (alternative webm processing method)

import requests
from openai import OpenAI  # Or xAI equivalent
import json
import os
import speech_recognition as sr
import pyttsx3
import threading
import time
import warnings
import sys
import collections
import audioop
import wave
import pyaudio
import tempfile
import subprocess

# Suppress ALSA warnings
import ctypes
from ctypes import *
from contextlib import contextmanager

@contextmanager
def suppress_alsa_warnings():
    """Suppress ALSA warnings from PyAudio"""
    try:
        # Save original stderr file descriptor
        original_stderr_fd = os.dup(2)
        # Redirect stderr to /dev/null
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 2)
        os.close(devnull_fd)
        yield
    finally:
        # Restore original stderr
        os.dup2(original_stderr_fd, 2)
        os.close(original_stderr_fd)

class VoiceCommandParser:
    def __init__(self, device_list=None, intent_list=None, use_voice=True, wake_word="computer"):
        # Get API key from environment variable Use the command below in your terminal to set it
        # export OPENAI_API_KEY= [PUT YOUR API KEY HERE]


        # sudo apt update && sudo apt install portaudio19-dev python3-pyaudio
        if device_list is not None:
            self.set_device_list(device_list)

        if intent_list is not None:
            self.set_intent_list(intent_list)

        self.api_key = os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            raise ValueError("Please set OPENAI_API_KEY environment variable")

        self.client = OpenAI(api_key=self.api_key)
        
        # Wake word setup
        self.wake_word = wake_word.lower()
        self.wake_words = [wake_word.lower(), "hey " + wake_word.lower(), wake_word.lower() + " please"]
        
        # Audio buffering setup
        self.audio_buffer = collections.deque(maxlen=50)  # Store last ~5 seconds of audio chunks
        self.buffer_lock = threading.Lock()
        self.listening_thread = None
        self.stop_listening = False
        self.wake_word_detected = False
        self.speaking = True
        self.command_buffer = []
        self.wake_check_thread = None
        
        # Voice recognition setup
        self.use_voice = use_voice
        if use_voice:
            with suppress_alsa_warnings():
                self.recognizer = sr.Recognizer()
                self.microphone = sr.Microphone()
                self.tts_engine = pyttsx3.init()
                self.setup_voice()

    def setup_voice(self):
        """Configure voice recognition and text-to-speech settings"""
        # Adjust for ambient noise
        #print("Calibrating microphone for ambient noise...")
        with suppress_alsa_warnings():
            with self.microphone as source:
                # Faster calibration
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
        
        self.recognizer.pause_threshold = 0.5  # Faster pause detection
        self.recognizer.phrase_threshold = 0.3  # Minimum audio length
        self.recognizer.non_speaking_duration = 0.3  # Faster silence detection
        
        #print("Microphone calibrated!")
        
        self.tts_engine.setProperty('rate', 180)  # Faster speech
        voices = self.tts_engine.getProperty('voices')
        if voices:
            self.tts_engine.setProperty('voice', voices[0].id)  # Use first available voice

    def start_continuous_listening(self):
        """Start the continuous audio buffering thread"""
        self.stop_listening = False
        self.listening_thread = threading.Thread(target=self._continuous_listen_worker, daemon=True)
        self.listening_thread.start()
        
        # Give the thread a moment to start
        time.sleep(0.25)

    def _continuous_listen_worker(self):
        """Worker thread that continuously buffers audio"""
        CHUNK = 1024  # Audio chunk size
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        SILENCE_THRESHOLD = 1000  # Adjust based on your microphone
        SILENCE_DURATION = 15  # Duration to consider for silence (in frames)
        self.silence_counter = 0

        with suppress_alsa_warnings():
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
        
        print("🎙️ Audio stream started")
        
        # Buffer to accumulate audio data
        audio_frames = []
        frames_per_second = RATE // CHUNK  # How many chunks per second
        frames_per_buffer = int(0.5 * frames_per_second)  # 0.5 second buffers
        
        while not self.stop_listening:
            with suppress_alsa_warnings():
                data = stream.read(CHUNK, exception_on_overflow=False)

            energy = audioop.rms(data, 2)
            if energy > SILENCE_THRESHOLD:
                self.silence_counter = 0
            else:
                self.silence_counter += 1
            if self.silence_counter >= SILENCE_DURATION and self.wake_word_detected:
                self.speaking = False
                self.silence_counter = 0

            audio_frames.append(data)

            if self.wake_word_detected and not self.speaking:
                print("🎯 Wake word detected!")
                entire_buffer = list(self.audio_buffer)
                combined_audio = self._combine_audio_chunks(entire_buffer)
                spoken_text = self.recognizer.recognize_google(combined_audio).lower()
                
                audio_start_ind = spoken_text.rfind(self.wake_words[0])
                command_text = spoken_text[audio_start_ind:]
                
                self.wake_word_detected = False
                with self.buffer_lock:
                    self.audio_buffer.clear()
                print(f"💭 Complete command: '{command_text}'")
                response = self.LLM_API_call(command_text)
                self.make_api_call(response)
            
            # When we have enough frames, create an AudioData object
            if len(audio_frames) >= frames_per_buffer:
                # Combine frames into single audio data
                combined_data = b''.join(audio_frames)
                audio_data = sr.AudioData(combined_data, RATE, 2)  # 16-bit = 2 bytes per sample
                # Add to circular buffer
                with self.buffer_lock:
                    self.audio_buffer.append(audio_data)
                
                # Check for wake word periodically
                if len(self.audio_buffer) % 1 == 0 and not self.wake_word_detected:  
                    self.wake_check_thread = threading.Thread(target=self._check_for_wake_word, daemon=True)
                    self.wake_check_thread.start()
                
                audio_frames = []

    def _check_for_wake_word(self):
        """Check the recent audio buffer for the wake word"""
        try:
            with self.buffer_lock:
                recent_chunks = list(self.audio_buffer)[-4:]  # Last 2 seconds

            combined_audio = self._combine_audio_chunks(recent_chunks)
            spoken_text = self.recognizer.recognize_google(combined_audio).lower()
            for wake_variant in self.wake_words:
                if wake_variant in spoken_text:
                    print(f"🎯 Wake word '{wake_variant}' detected!")
                    self.wake_word_detected = True
                    self.silence_counter = 0
                    self.speaking = True
                    return 
                
        except sr.UnknownValueError:
            # This is normal - means no clear speech was detected
            pass
        return None

    def _combine_audio_chunks(self, chunks):
        """Combine multiple AudioData chunks into one"""
            
        first_chunk = chunks[0]
        combined_frame_data = first_chunk.frame_data
        # Append remaining chunks
        for chunk in chunks[1:]:
            combined_frame_data += chunk.frame_data
        
        # Create new AudioData object
        return sr.AudioData(combined_frame_data, first_chunk.sample_rate, first_chunk.sample_width)

    def speak_response(self, text):
        """Convert text to speech"""
        if self.use_voice:
            print(f"Speaking: {text}")
            try:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
            except (ReferenceError, Exception) as e:
                # Suppress TTS cleanup warnings - they don't affect functionality
                pass
        else:
            print(text)

    def process_webm_file(self, webm_file_path):
        """
        Process a .webm audio file to extract voice command and interpret it
        
        Args:
            webm_file_path (str): Path to the .webm audio file
            
        Returns:
            dict: Response from the LLM API call or error information
        """
        try:
            print(f"🎵 Processing audio file: {webm_file_path}")
            
            # Convert webm to wav for better compatibility with speech recognition
            wav_file_path = self._convert_webm_to_wav(webm_file_path)
            
            # Load the converted audio file
            with sr.AudioFile(wav_file_path) as source:
                # Read the entire audio file
                audio_data = self.recognizer.record(source)
            
            # Perform speech recognition
            print("🎯 Converting speech to text...")
            try:
                spoken_text = self.recognizer.recognize_google(audio_data).lower()
                print(f"💭 Recognized text: '{spoken_text}'")
                
                # Send to LLM for interpretation
                print("🤖 Sending to AI for interpretation...")
                response = self.LLM_API_call(spoken_text)
                
                # Parse and structure the AI response
                parsed_command = self._parse_ai_response(response)
                
                # Process the response (execute the command if it's valid)
                if parsed_command and parsed_command.get('device') and parsed_command.get('action'):
                    self.make_api_call(response)
                    success_msg = f"Successfully executed: {parsed_command['action']} {parsed_command['device']}"
                    ai_response_text = success_msg
                else:
                    ai_response_text = "I understood your speech but couldn't identify a specific device command."
                
                # Clean up temporary wav file
                if os.path.exists(wav_file_path):
                    os.remove(wav_file_path)
                
                return {
                    "transcription": spoken_text,
                    "raw_ai_response": response,
                    "parsed_command": parsed_command,
                    "ai_response_text": ai_response_text,
                    "command_executed": bool(parsed_command and parsed_command.get('device') and parsed_command.get('action'))
                }
                
            except sr.UnknownValueError:
                error_msg = "Could not understand the audio. Please speak more clearly."
                print(f"❌ {error_msg}")
                self.speak_response(error_msg)
                return {
                    "success": False,
                    "error": "speech_recognition_failed",
                    "message": error_msg
                }
            except sr.RequestError as e:
                error_msg = f"Could not request results from speech recognition service: {e}"
                print(f"❌ {error_msg}")
                return {
                    "success": False,
                    "error": "speech_service_error",
                    "message": error_msg
                }
                
        except Exception as e:
            error_msg = f"Error processing audio file: {str(e)}"
            print(f"❌ {error_msg}")
            return {
                "success": False,
                "error": "file_processing_error",
                "message": error_msg
            }
    
    def _convert_webm_to_wav(self, webm_file_path):
        """
        Convert .webm file to .wav format using ffmpeg
        
        Args:
            webm_file_path (str): Path to the input .webm file
            
        Returns:
            str: Path to the converted .wav file
        """
        try:
            # Create a temporary wav file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_wav:
                wav_file_path = temp_wav.name
            
            # Use ffmpeg to convert webm to wav
            # ffmpeg -i input.webm -acodec pcm_s16le -ar 16000 -ac 1 output.wav
            cmd = [
                'ffmpeg', '-y',  # -y to overwrite output file
                '-i', webm_file_path,
                '-acodec', 'pcm_s16le',  # 16-bit PCM
                '-ar', '16000',  # 16kHz sample rate
                '-ac', '1',  # Mono channel
                wav_file_path
            ]
            
            # Run ffmpeg conversion
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            print(f"✅ Converted {webm_file_path} to {wav_file_path}")
            return wav_file_path
            
        except subprocess.CalledProcessError as e:
            print(f"❌ FFmpeg conversion failed: {e}")
            print(f"FFmpeg stderr: {e.stderr}")
            raise Exception(f"Audio conversion failed: {e}")
        except FileNotFoundError:
            raise Exception("FFmpeg not found. Please install ffmpeg: sudo apt install ffmpeg")
    
    def process_webm_file_alternative(self, webm_file_path):
        """
        Alternative method to process .webm files using pydub (requires ffmpeg)
        This is a backup method if the primary conversion doesn't work
        
        Args:
            webm_file_path (str): Path to the .webm audio file
            
        Returns:
            dict: Response from the LLM API call or error information
        """
        try:
            # Try importing pydub
            from pydub import AudioSegment
            from pydub.utils import which
            
            # Check if ffmpeg is available
            if not which("ffmpeg"):
                raise Exception("FFmpeg not found. Please install: sudo apt install ffmpeg")
            
            print(f"🎵 Processing audio file (alternative method): {webm_file_path}")
            
            # Load webm file using pydub
            audio = AudioSegment.from_file(webm_file_path, format="webm")
            
            # Convert to mono and set sample rate to 16kHz
            audio = audio.set_channels(1).set_frame_rate(16000)
            
            # Export to temporary wav file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_wav:
                wav_file_path = temp_wav.name
                audio.export(wav_file_path, format="wav")
            
            # Use the regular processing method
            return self.process_webm_file_from_wav(wav_file_path)
            
        except ImportError:
            print("❌ pydub not installed. Install with: pip install pydub")
            return {
                "success": False,
                "error": "missing_dependency",
                "message": "pydub library not installed"
            }
        except Exception as e:
            error_msg = f"Alternative processing method failed: {str(e)}"
            print(f"❌ {error_msg}")
            return {
                "success": False,
                "error": "alternative_method_failed",
                "message": error_msg
            }
    
    def process_webm_file_from_wav(self, wav_file_path):
        """
        Process an already converted WAV file
        
        Args:
            wav_file_path (str): Path to the .wav audio file
            
        Returns:
            dict: Response from the LLM API call or error information
        """
        try:
            # Load the wav audio file
            with sr.AudioFile(wav_file_path) as source:
                audio_data = self.recognizer.record(source)
            
            # Perform speech recognition
            print("🎯 Converting speech to text...")
            spoken_text = self.recognizer.recognize_google(audio_data).lower()
            print(f"💭 Recognized text: '{spoken_text}'")
            
            # Send to LLM for interpretation
            print("🤖 Sending to AI for interpretation...")
            response = self.LLM_API_call(spoken_text)
            
            # Process the response
            self.make_api_call(response)
            
            # Clean up temporary wav file
            if os.path.exists(wav_file_path):
                os.remove(wav_file_path)
            
            return {
                "success": True,
                "recognized_text": spoken_text,
                "ai_response": response
            }
            
        except Exception as e:
            error_msg = f"Error processing WAV file: {str(e)}"
            print(f"❌ {error_msg}")
            return {
                "success": False,
                "error": "wav_processing_error",
                "message": error_msg
            }

    def set_device_list(self, device_list):
        self.device_list = device_list

    def set_intent_list(self, intent_list):
        self.intent_list = intent_list

    def LLM_API_call(self, command):
        response = self.client.chat.completions.create(
            model="gpt-4o",  # Or 'grok-3'
            messages=[{"role": "system", "content": "Parse home commands to valid JSON format with double quotes. "
            "Only accept commands for these devices: "
            +self.device_list+
            "For unsupported devices or invalid commands, return "
            '{\"intent\": \"error\", \"device\": \"unsupported\", \"message\": \"Device not supported\"}. '
            "Valid intents are: "
            +self.intent_list+
            "Example: {\"intent\": \"turn_on\", \"device\": \"light\"}."},
                    {"role": "user", "content": command}]
        )
        return response.choices[0].message.content  # Parse to dict

    def make_api_call(self, response):
        # Parse the JSON string if needed
        if response.startswith('```json'):
            response = response.strip('```json').strip('```').strip()
        response_cleaned = response.replace("'", '"')
        response = json.loads(response_cleaned)

        # Success response
        success_msg = f"Executing: {response.get('intent', 'unknown')} {response.get('device', 'unknown device')}"
        print(f"Making API call: {response}")
        self.speak_response(success_msg)

    def _parse_ai_response(self, ai_response_raw):
        """
        Parse AI response to extract structured command information
        
        Args:
            ai_response_raw (str): Raw response from the AI API
            
        Returns:
            dict: Parsed command with device, action/intent, and other info
        """
        try:
            if not ai_response_raw:
                return None
            
            # Remove markdown code block formatting if present
            json_content = ai_response_raw.strip()
            
            if '```json' in json_content:
                # Extract content between ```json and ```
                start_marker = '```json'
                end_marker = '```'
                start_index = json_content.find(start_marker)
                if start_index != -1:
                    start_index += len(start_marker)
                    end_index = json_content.find(end_marker, start_index)
                    if end_index != -1:
                        json_content = json_content[start_index:end_index].strip()
                    else:
                        json_content = json_content[start_index:].strip()
            elif '```' in json_content:
                # Handle generic code blocks
                lines = json_content.split('\n')
                json_lines = []
                in_code_block = False
                for line in lines:
                    if line.strip().startswith('```'):
                        in_code_block = not in_code_block
                    elif in_code_block:
                        json_lines.append(line)
                json_content = '\n'.join(json_lines).strip()
            
            # Parse the cleaned JSON
            parsed = json.loads(json_content)
            
            # Normalize the response format
            normalized = {}
            if 'device' in parsed:
                normalized['device'] = parsed['device']
            if 'action' in parsed:
                normalized['action'] = parsed['action']
            elif 'intent' in parsed:
                normalized['action'] = parsed['intent']
            
            # Add any additional fields
            for key, value in parsed.items():
                if key not in ['device', 'action', 'intent']:
                    normalized[key] = value
            
            print(f"✅ Parsed AI command: {normalized}")
            return normalized
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON parsing error: {e}")
            print(f"   Attempted to parse: {json_content}")
            return None
        except Exception as e:
            print(f"❌ Error parsing AI response: {e}")
            return None

def main():
    use_voice = True
    wake_word = "computer"  # Change this to whatever wake word you want
    
    parser = VoiceCommandParser(
        device_list="light, lamp, fan, TV, air_conditioner, heater, smart_plug", 
        intent_list="turn_on, turn_off, dim, brighten",
        use_voice=use_voice,
        wake_word=wake_word
    )

    # Check if a webm file path was provided as command line argument
    if len(sys.argv) > 1:
        webm_file_path = sys.argv[1]
        if os.path.exists(webm_file_path) and webm_file_path.lower().endswith('.webm'):
            print(f"🎵 Processing webm file: {webm_file_path}")
            result = parser.process_webm_file(webm_file_path)
            print(f"📋 Processing result: {result}")
            return
        else:
            print(f"❌ Invalid file: {webm_file_path}")
            print("Please provide a valid .webm file path")
            return

    if use_voice:
        parser.speak_response(f"Voice command system ready. Say '{wake_word}' to wake me up!")
        print(f"\n🎤 Wake word options:")
        print(f"- '{wake_word}'")
        print(f"\n💡 Usage modes:")
        print(f"1. Continuous listening: python voice_command_parser.py")
        print(f"2. Process webm file: python voice_command_parser.py /path/to/audio.webm")
        
        # Start continuous audio buffering
        parser.start_continuous_listening()
        
        print("🎧 Listening continuously for wake word... (Press Ctrl+C to exit)")
        
        try:
            # Keep main thread alive while background thread handles audio
            while True:
                time.sleep(1)
                
                # Optional: Show periodic status
                if int(time.time()) % 30 == 0:  # Every 30 seconds
                    print(f"👂 Still listening for '{wake_word}'...")
                    
        except KeyboardInterrupt:
            print("\n🛑 Stopping...")
            parser.stop_continuous_listening()
            parser.speak_response("Goodbye!")
            print("✨ Exiting...")
    
    else:
        # Text mode
        print("\n💡 Text mode - You can also process webm files:")
        print("Usage: python voice_command_parser.py /path/to/audio.webm")
        print("\nOr enter commands manually:")
        
        while True:
            try:
                command = input("\nEnter command (or 'quit' to exit, or 'file:/path/to/file.webm'): ")
                if command and command.lower() in ['quit', 'exit', 'stop']:
                    break
                if command.startswith('file:'):
                    # Process webm file
                    file_path = command[5:]  # Remove 'file:' prefix
                    if os.path.exists(file_path) and file_path.lower().endswith('.webm'):
                        result = parser.process_webm_file(file_path)
                        print(f"📋 Processing result: {result}")
                    else:
                        print(f"❌ Invalid webm file: {file_path}")
                elif command:
                    response = parser.LLM_API_call(command)
                    parser.make_api_call(response)
            except KeyboardInterrupt:
                print("\n👋 Exiting...")
                break

# Function to test webm processing independently
def test_webm_processing(webm_file_path):
    """
    Standalone function to test webm file processing
    
    Args:
        webm_file_path (str): Path to the .webm file to process
    """
    parser = VoiceCommandParser(
        device_list="light, lamp, fan, TV, air_conditioner, heater, smart_plug", 
        intent_list="turn_on, turn_off, dim, brighten",
        use_voice=False,  # No TTS for file processing
        wake_word="computer"
    )
    
    result = parser.process_webm_file(webm_file_path)
    return result

if __name__ == "__main__":
    main()