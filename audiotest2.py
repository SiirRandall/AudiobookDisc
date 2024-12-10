import ffmpeg
import json
import logging
import time
import subprocess
import pickle
import curses
import os

# Set up logging to output to a file
logging.basicConfig(filename='metadata_debug.log', level=logging.DEBUG, format='%(asctime)s - %(message)s')

# Define playback controls
PLAYBACK_CONTROLS = {
    'p': 'pause',     # Pause/resume playback
    's': 'stop',      # Stop playback
    'f': 'forward',   # Skip forward by 30 seconds
    'b': 'backward',  # Skip backward by 30 seconds
    'n': 'next',      # Skip to next chapter
    'm': 'previous',  # Skip to previous chapter
}

def get_metadata(file_path):
    # Get metadata using ffmpeg
    metadata = ffmpeg.probe(file_path, v='error', show_entries='chapters', format='json')
    
    # Log the full metadata for debugging
    logging.debug("Full metadata:")
    logging.debug(json.dumps(metadata, indent=4))
    
    # Access title and author from the format tags
    title = metadata['format']['tags'].get('title', 'Unknown Title')
    author = metadata['format']['tags'].get('artist', 'Unknown Author')

    # Extract chapters
    chapters = metadata.get('chapters', [])
    chapter_info = []
    for chapter in chapters:
        chapter_info.append({
            'start_time': float(chapter['start_time']),
            'end_time': float(chapter['end_time']),
            'title': chapter['tags'].get('title', 'Unknown Chapter')
        })

    # Log out what we are extracting
    logging.debug(f"Title: {title}")
    logging.debug(f"Author: {author}")
    logging.debug(f"Chapters: {chapter_info}")
    
    return title, author, chapter_info


def get_current_chapter(current_time, chapters):
    """
    Determine the current chapter based on the current playback time.
    """
    logging.debug(f"Checking current time: {current_time} against chapters")
    current_chapter = 'Unknown Chapter'
    for i, chapter in enumerate(chapters):
        logging.debug(f"Checking chapter {i+1}: {chapter['title']}, Start: {chapter['start_time']}, End: {chapter['end_time']}")
        # Check if current_time is within the chapter's start and end time
        if current_time >= chapter['start_time'] and current_time < chapter['end_time']:
            current_chapter = chapter['title']
            logging.debug(f"Found chapter: {current_chapter}")
            break
    return current_chapter


def play_audiobook(stdscr, audio_file):
    # Get metadata and prepare playback
    title, author, chapters = get_metadata(audio_file)
    total_duration = float(ffmpeg.probe(audio_file, v='error', select_streams='a', show_entries='format=duration')['format']['duration'])
    
    # Start from the saved position
    playback_position = load_playback_position()

    # Start mpv with IPC control enabled (input-ipc-server)
    ipc_socket = '/tmp/mpv-socket'
    subprocess.Popen(['mpv', '--no-video', f'--start={playback_position}', '--input-ipc-server={}'.format(ipc_socket), audio_file])

    # Loop to update the display and handle playback
    start_time = time.time()
    chapter_time = total_duration

    while True:
        current_time = time.time() - start_time + playback_position
        time_left = chapter_time - current_time
        
        # Determine the current chapter based on the current playback time
        current_chapter = get_current_chapter(current_time, chapters)
        
        # Display updated information on the terminal
        display_info(stdscr, title, author, f"{current_time:.2f}s", f"{time_left:.2f}s", f"{chapter_time:.2f}s", current_chapter)
        
        # Handle user input for playback control
        key = stdscr.getch()
        handle_playback_controls(ipc_socket, key, current_time, chapters)
        
        # Save the current playback position periodically
        if int(current_time) % 5 == 0:
            save_playback_position(current_time)
        
        # Sleep for a while to update the display every second
        time.sleep(1)


def display_info(stdscr, title, author, current_time, time_left, chapter_time, current_chapter):
    """
    Function to display the current information on the terminal using curses.
    """
    stdscr.clear()  # Clear the screen for each update
    stdscr.addstr(0, 0, f"Title: {title}")
    stdscr.addstr(1, 0, f"Author: {author}")
    stdscr.addstr(2, 0, f"Current Time: {current_time} / {chapter_time}")
    stdscr.addstr(3, 0, f"Time Left in Chapter: {time_left}")
    stdscr.addstr(4, 0, f"Current Chapter: {current_chapter}")
    stdscr.addstr(5, 0, "Controls: 'p' Pause/Resume | 's' Stop | 'f' Forward 30s | 'b' Backward 30s | 'n' Next Chapter | 'm' Previous Chapter")
    stdscr.refresh()


def handle_playback_controls(ipc_socket, key, current_time, chapters):
    """
    Function to handle playback controls via IPC commands to mpv.
    """
    logging.debug(f"Handling key press: {chr(key)}")

    if key == ord('p'):  # Pause/Resume
        send_ipc_command(ipc_socket, 'cycle pause')

    elif key == ord('s'):  # Stop
        send_ipc_command(ipc_socket, 'stop')
        # Prevent screenshot error on stop by adding 'no-screenshot' option
        send_ipc_command(ipc_socket, 'no-screenshot')

    elif key == ord('f'):  # Skip forward 30s
        send_ipc_command(ipc_socket, f'seek 30 relative')

    elif key == ord('b'):  # Skip backward 30s
        send_ipc_command(ipc_socket, f'seek -30 relative')

    elif key == ord('n'):  # Skip to next chapter
        next_chapter_time = get_next_chapter_time(current_time, chapters)
        logging.debug(f"Skipping to next chapter at time: {next_chapter_time}")
        send_ipc_command(ipc_socket, f'seek {next_chapter_time} absolute')

    elif key == ord('m'):  # Skip to previous chapter
        prev_chapter_time = get_previous_chapter_time(current_time, chapters)
        logging.debug(f"Skipping to previous chapter at time: {prev_chapter_time}")
        send_ipc_command(ipc_socket, f'seek {prev_chapter_time} absolute')


def get_next_chapter_time(current_time, chapters):
    """Find the start time of the next chapter."""
    for chapter in chapters:
        if current_time < chapter['start_time']:
            return chapter['start_time']
    return current_time  # If no next chapter, return current time


def get_previous_chapter_time(current_time, chapters):
    """Find the start time of the previous chapter."""
    for chapter in reversed(chapters):
        if current_time > chapter['start_time']:
            return chapter['start_time']
    return current_time  # If no previous chapter, return current time


def send_ipc_command(ipc_socket, command):
    """
    Send command to mpv via IPC.
    """
    logging.debug(f"Sending command to mpv: {command}")
    try:
        with open(ipc_socket, 'w') as ipc:
            ipc.write(command + '\n')
            ipc.flush()
    except Exception as e:
        logging.error(f"Error sending command to mpv: {e}")


def load_playback_position():
    try:
        with open('playback_position.pkl', 'rb') as f:
            return pickle.load(f)
    except FileNotFoundError:
        return 0  # Start from the beginning

def save_playback_position(position):
    with open('playback_position.pkl', 'wb') as f:
        pickle.dump(position, f)

def main():
    curses.wrapper(play_audiobook, "TheDarkTowerITheGunslinger_ep6.m4b")

if __name__ == "__main__":
    main()
