import queue
import threading
import time
from pathlib import Path
from typing import Optional, Callable

from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from mlrpc.client import MLRPCClient, MLRPCResponse


def get_gitignore_specs(dir_to_watch) -> Optional[PathSpec]:
    ignore_file = Path(dir_to_watch) / ".gitignore"
    if ignore_file is not None and ignore_file.exists() and ignore_file.is_file():
        ignore_file = str(ignore_file)
        with open(ignore_file, 'r') as f:
            gitignore = f.read()
        return PathSpec.from_lines(GitWildMatchPattern, gitignore.splitlines())

    return None


def hot_reload_on_change(dir_to_watch, rpc_client: MLRPCClient, frequency_seconds: int = 1,
                         logging_function: Callable = None,
                         error_logging_function: Callable = None,
                         success_logging_function: Callable = None):
    logging_function = logging_function or print
    error_logging_function = error_logging_function or print
    event_queue: queue.Queue[FileSystemEvent] = queue.Queue()

    class FileChangeHandler(FileSystemEventHandler):
        def on_any_event(self, event: FileSystemEvent):
            event_queue.put(event)

    def consumer():
        logging_function(f"Starting file watcher for {dir_to_watch}...")

        def handle_response(_response: MLRPCResponse):
            if response.status_code != 200:
                error_logging_function(f"Hot reload failed with status: {_response.status_code} - {_response.body}")
                return

            success_logging_function(f"Hot reload status: {_response.status_code} - {_response.body}")

        while True:
            # Collect all changes made in the last 5 seconds
            any_changes = []
            start_time = time.time()
            while time.time() - start_time < frequency_seconds:
                try:
                    # Try to get an event from the queue
                    event = event_queue.get(timeout=1)
                    any_changes.append(event)
                except queue.Empty:
                    pass

            # If there are any changes, call full_sync
            if any_changes:
                ignore_specs = get_gitignore_specs(dir_to_watch)
                valid_changes = []
                for change in any_changes:
                    if ignore_specs is not None and not ignore_specs.match_file(change.src_path) \
                            and change.is_directory is False and change.src_path.endswith("~") is False:
                        valid_changes.append(change)

                if not valid_changes:
                    continue

                logging_function(f"Files changed firing hot reload for {dir_to_watch}...")
                responses = rpc_client.hot_reload(dir_to_watch)

                if responses is not None:
                    if isinstance(responses, list):
                        for response in responses:
                            handle_response(response)
                    else:
                        handle_response(responses)

    consumer_thread = threading.Thread(target=consumer)
    consumer_thread.start()

    event_handler = FileChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, dir_to_watch, recursive=True)
    observer.start()

    return [consumer_thread, observer]
