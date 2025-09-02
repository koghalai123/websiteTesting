from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import json
import os
import sys
from datetime import datetime

# Add the practice_with_LLM directory to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
practice_llm_path = os.path.join(project_root, 'practice_with_LLM')
if practice_llm_path not in sys.path:
    sys.path.append(practice_llm_path)

# Import the voice command parser
try:
    from voice_command_parser import VoiceCommandParser
    VOICE_PARSER_AVAILABLE = True
except ImportError as e:
    print(f"Voice parser not available: {e}")
    VOICE_PARSER_AVAILABLE = False

def home(request):
    """Main home page view"""
    return render(request, 'home.html')

def device_control(request):
    """Device control page view"""
    if request.method == 'POST':
        # Handle device control commands here
        import json
        try:
            data = json.loads(request.body)
            device_id = data.get('device_id')
            action = data.get('action')
            
            # Update the global device state
            global DEVICE_STATES
            if device_id in DEVICE_STATES:
                if action == 'on':
                    DEVICE_STATES[device_id] = True
                elif action == 'off':
                    DEVICE_STATES[device_id] = False
                
                response_data = {
                    'success': True,
                    'message': f'Device {device_id} turned {action}',
                    'device_id': device_id,
                    'action': action,
                    'new_state': DEVICE_STATES[device_id]
                }
                
                print(f"Manual device control: {device_id} -> {action}")
                return JsonResponse(response_data)
            else:
                return JsonResponse({
                    'error': 'Device not found',
                    'message': f'Device {device_id} not found'
                }, status=404)
            
        except Exception as e:
            return JsonResponse({
                'error': 'Invalid request',
                'message': str(e)
            }, status=400)
    else:
        # Pass device configuration to template for dynamic generation
        context = {
            'device_config': DEVICE_CONFIG,
            'device_states': DEVICE_STATES
        }
        return render(request, 'device_control.html', context)

def loadMainPage(request):
    if request.method == 'POST':
        return handle_audio_upload(request)
    else:
        return render(request, 'audioCollection.html')

def handle_audio_upload(request):
    """Handle audio file upload from the frontend"""
    try:
        if 'audio' not in request.FILES:
            return JsonResponse({'error': 'No audio file provided'}, status=400)
        
        audio_file = request.FILES['audio']
        
        # Generate a unique filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'recording_{timestamp}.webm'
        
        # Create uploads directory if it doesn't exist
        upload_dir = 'uploads/audio/'
        os.makedirs(upload_dir, exist_ok=True)
        
        # Save the file
        file_path = os.path.join(upload_dir, filename)
        with open(file_path, 'wb+') as destination:
            for chunk in audio_file.chunks():
                destination.write(chunk)
        
        print(f"Audio file saved: {file_path} (Size: {audio_file.size} bytes)")
        
        # Process with voice command parser if available
        voice_result = None
        if VOICE_PARSER_AVAILABLE:
            try:
                voice_result = process_voice_command(file_path)
            except Exception as e:
                print(f"Voice processing error: {str(e)}")
                voice_result = {
                    'success': False,
                    'error': 'voice_processing_failed',
                    'message': str(e)
                }
        
        response_data = {
            'success': True,
            'message': 'Audio received successfully',
            'filename': filename,
            'file_size': audio_file.size,
            'timestamp': timestamp,
            'voice_processing': voice_result
        }
        
        return JsonResponse(response_data)
        
    except Exception as e:
        print(f"Error handling audio upload: {str(e)}")
        return JsonResponse({
            'error': 'Internal server error',
            'message': str(e)
        }, status=500)

def process_voice_command(audio_file_path):
    """
    Process audio file using the voice command parser
    
    Args:
        audio_file_path (str): Path to the audio file
        
    Returns:
        dict: Processing result from voice command parser
    """
    if not VOICE_PARSER_AVAILABLE:
        return {
            'success': False,
            'error': 'voice_parser_unavailable',
            'message': 'Voice command parser is not available'
        }
    
    try:
        # Get device list from centralized configuration
        device_list = get_device_list_string()
        intent_list = "turn_on, turn_off"
        
        # Create voice parser instance with error handling for TTS
        try:
            # Try with voice enabled first
            parser = VoiceCommandParser(
                device_list=device_list,
                intent_list=intent_list,
                use_voice=True,
                wake_word="computer"
            )
        except Exception as tts_error:
            print(f"TTS initialization failed: {tts_error}")
            # Fallback: Create parser without TTS but initialize recognizer manually
            parser = VoiceCommandParser(
                device_list=device_list,
                intent_list=intent_list,
                use_voice=False,
                wake_word="computer"
            )
            # Manually initialize recognizer for webm processing
            import speech_recognition as sr
            parser.recognizer = sr.Recognizer()
            parser.recognizer.pause_threshold = 0.5
            parser.recognizer.phrase_threshold = 0.3
            parser.recognizer.non_speaking_duration = 0.3
        
        # Process the webm file
        result = parser.process_webm_file(audio_file_path)
        parsed_command = json.loads(result.get('ai_response'))
        # The voice command parser now returns a clean, structured response
        if parsed_command['intent'] != 'error':
            
            # Execute device control if we have a valid command
            device_control_result = control_device_via_voice(
                parsed_command['device'], 
                parsed_command['intent']
            )
            result['device_control_result'] = device_control_result
            
            # Update the response text based on device control result
            if device_control_result['success']:
                result['ai_response_text'] = device_control_result['message']
            else:
                result['ai_response_text'] = f"Sorry, I couldn't control {parsed_command['device']}: {device_control_result['message']}"
        else:

            result['ai_response_text'] = result.get('message', "Voice processing failed. Please try again.")
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'error': 'voice_processing_exception',
            'message': str(e)
        }

# Centralized Device Configuration - Single Source of Truth
DEVICE_CONFIG = {
    'living-main-light': {
        'name': 'Main Light',
        'room': 'Living Room',
        'icon': '💡',
        'default_state': True,
        'aliases': ['living room main light', 'living main light', 'main light', 'living room light']
    },
    'living-reading-lamp': {
        'name': 'Reading Lamp', 
        'room': 'Living Room',
        'icon': '🔆',
        'default_state': True,
        'aliases': ['living room reading lamp', 'reading lamp', 'living reading lamp']
    },
    'living-tv-light': {
        'name': 'TV Light',
        'room': 'Living Room', 
        'icon': '📺',
        'default_state': False,
        'aliases': ['living room tv light', 'tv light', 'living tv light']
    },
    'kitchen-ceiling-light': {
        'name': 'Ceiling Light',
        'room': 'Kitchen',
        'icon': '💡', 
        'default_state': True,
        'aliases': ['kitchen ceiling light', 'kitchen light', 'kitchen main light']
    },
    'kitchen-cabinet-light': {
        'name': 'Under Cabinet',
        'room': 'Kitchen',
        'icon': '🔆',
        'default_state': False,
        'aliases': ['kitchen cabinet light', 'cabinet light', 'under cabinet light']
    },
    'kitchen-dishwasher': {
        'name': 'Dishwasher',
        'room': 'Kitchen',
        'icon': '🌊',
        'default_state': False,
        'aliases': ['kitchen dishwasher', 'dishwasher']
    },
    'bedroom-main-light': {
        'name': 'Main Light',
        'room': 'Bedroom',
        'icon': '💡',
        'default_state': False,
        'aliases': ['bedroom main light', 'bedroom light']
    },
    'bedroom-bedside-lamps': {
        'name': 'Bedside Lamps',
        'room': 'Bedroom',
        'icon': '🛏️',
        'default_state': False,
        'aliases': ['bedroom bedside lamps', 'bedside lamps', 'bedside lamp']
    },
    'bedroom-fan': {
        'name': 'Fan',
        'room': 'Bedroom',
        'icon': '🌀',
        'default_state': False,
        'aliases': ['bedroom fan', 'fan']
    },
    'bathroom-main-light': {
        'name': 'Main Light',
        'room': 'Bathroom',
        'icon': '💡',
        'default_state': True,
        'aliases': ['bathroom main light', 'bathroom light']
    },
    'bathroom-mirror-light': {
        'name': 'Mirror Light',
        'room': 'Bathroom',
        'icon': '🪞',
        'default_state': False,
        'aliases': ['bathroom mirror light', 'mirror light']
    },
    'bathroom-exhaust-fan': {
        'name': 'Exhaust Fan',
        'room': 'Bathroom',
        'icon': '💨',
        'default_state': False,
        'aliases': ['bathroom exhaust fan', 'exhaust fan', 'bathroom fan']
    }
}

# Initialize device states from the centralized config
DEVICE_STATES = {device_id: config['default_state'] for device_id, config in DEVICE_CONFIG.items()}

def get_device_list_string():
    """Generate device list string for voice command parser"""
    return ", ".join(DEVICE_CONFIG.keys())

def get_device_aliases_mapping():
    """Generate device name mapping from aliases"""
    mapping = {}
    for device_id, config in DEVICE_CONFIG.items():
        # Add the device ID itself
        mapping[device_id] = device_id
        # Add all aliases
        for alias in config['aliases']:
            mapping[alias.lower()] = device_id
    return mapping

def get_device_states(request):
    """Return current device states for frontend"""
    return JsonResponse({'device_states': DEVICE_STATES})

def get_device_config(request):
    """Return device configuration for frontend"""
    return JsonResponse({'device_config': DEVICE_CONFIG})

def add_device(request):
    """Add a new device to the system"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            device_id = data.get('device_id')
            device_name = data.get('name')
            room = data.get('room')
            icon = data.get('icon', '💡')
            aliases = data.get('aliases', [])
            default_state = data.get('default_state', False)
            
            if not device_id or not device_name or not room:
                return JsonResponse({
                    'error': 'Missing required fields',
                    'message': 'device_id, name, and room are required'
                }, status=400)
            
            # Add to device config
            global DEVICE_CONFIG, DEVICE_STATES
            DEVICE_CONFIG[device_id] = {
                'name': device_name,
                'room': room,
                'icon': icon,
                'default_state': default_state,
                'aliases': aliases
            }
            
            # Add to device states
            DEVICE_STATES[device_id] = default_state
            
            return JsonResponse({
                'success': True,
                'message': f'Device {device_id} added successfully',
                'device_id': device_id
            })
            
        except Exception as e:
            return JsonResponse({
                'error': 'Invalid request',
                'message': str(e)
            }, status=400)
    else:
        return JsonResponse({
            'error': 'Method not allowed',
            'message': 'POST method required'
        }, status=405)

def control_device_via_voice(device_id, action):
    """
    Control a device based on voice command
    
    Args:
        device_id (str): The device identifier
        action (str): The action to perform (turn_on, turn_off, etc.)
        
    Returns:
        dict: Result of the device control operation
    """
    global DEVICE_STATES
    
    # Normalize device ID (handle variations like "living room main light" -> "living-main-light")
    normalized_device_id = normalize_device_name(device_id)
    
    if normalized_device_id not in DEVICE_STATES:
        return {
            'success': False,
            'error': 'device_not_found',
            'message': f'Device "{device_id}" not found'
        }
    
    # Map actions to device states
    if action.lower() in ['turn_on', 'activate', 'enable', 'on']:
        DEVICE_STATES[normalized_device_id] = True
        new_state = 'on'
    elif action.lower() in ['turn_off', 'deactivate', 'disable', 'off']:
        DEVICE_STATES[normalized_device_id] = False
        new_state = 'off'
    else:
        return {
            'success': False,
            'error': 'invalid_action',
            'message': f'Action "{action}" not supported'
        }
    
    print(f"Voice command executed: {normalized_device_id} -> {new_state}")
    
    return {
        'success': True,
        'device_id': normalized_device_id,
        'action': action,
        'new_state': new_state,
        'message': f'Successfully turned {new_state} {normalized_device_id}'
    }

def normalize_device_name(device_name):
    """
    Normalize device names from natural language to device IDs using centralized config
    
    Args:
        device_name (str): Natural language device name
        
    Returns:
        str: Normalized device ID
    """
    # Convert to lowercase and handle common variations
    name = device_name.lower().strip()
    
    # Get device mappings from centralized config
    device_mappings = get_device_aliases_mapping()
    
    # Try exact match first
    if name in device_mappings:
        return device_mappings[name]
    
    # Try partial matches
    for alias, device_id in device_mappings.items():
        if name in alias or alias in name:
            return device_id
    
    # If no mapping found, try to match existing device IDs directly
    if name in DEVICE_STATES:
        return name
        
    # Replace spaces with hyphens as last resort
    normalized = name.replace(' ', '-').replace('_', '-')
    if normalized in DEVICE_STATES:
        return normalized
    
    # Return the original name if no mapping found
    return device_name

# Utility function for easily adding new devices
def add_new_device(device_id, name, room, icon='💡', aliases=None, default_state=False):
    """
    Utility function to add a new device to the system
    
    Args:
        device_id (str): Unique identifier for the device (e.g., 'office-desk-lamp')
        name (str): Display name for the device (e.g., 'Desk Lamp')
        room (str): Room where the device is located (e.g., 'Office')
        icon (str): Emoji icon for the device (default: '💡')
        aliases (list): List of alternative names for voice commands (optional)
        default_state (bool): Default on/off state (default: False)
    
    Example usage:
        add_new_device('office-desk-lamp', 'Desk Lamp', 'Office', '🏢', 
                      ['office lamp', 'desk light', 'office desk lamp'], False)
    """
    global DEVICE_CONFIG, DEVICE_STATES
    
    if aliases is None:
        aliases = [name.lower(), f"{room.lower()} {name.lower()}"]
    
    DEVICE_CONFIG[device_id] = {
        'name': name,
        'room': room,
        'icon': icon,
        'default_state': default_state,
        'aliases': aliases
    }
    
    DEVICE_STATES[device_id] = default_state
    
    print(f"✅ Added new device: {device_id} ({name}) in {room}")
    print(f"   Aliases: {', '.join(aliases)}")
    print(f"   Default state: {'ON' if default_state else 'OFF'}")

# Example of how to add new devices:
# Uncomment the lines below and modify as needed to add new devices

# add_new_device('office-desk-lamp', 'Desk Lamp', 'Office', '🏢', 
#                ['office lamp', 'desk light', 'office desk lamp'], False)

# add_new_device('garage-door', 'Garage Door', 'Garage', '🚗', 
#                ['garage', 'garage door'], False)

# add_new_device('garden-sprinkler', 'Sprinkler System', 'Garden', '💧', 
#                ['sprinkler', 'garden sprinkler', 'lawn sprinkler'], False)